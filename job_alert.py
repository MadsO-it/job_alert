"""
Job Alert
Søger på IT Jobbank og StepStone og sender ugentlig e-mail.
(Jobnet kræver NemID-login og er ikke tilgængeligt for automatisk søgning.)

Konfiguration sker via environment variables — se .env.example.
Værdier hentes fra:
  1) os.environ
  2) .env i samme mappe (læses automatisk hvis python-dotenv er installeret)
  3) lokale obfuscerede backup-filer (.credentials, .anthropic_key) for kodeord/nøgle
"""

import json
import re
import sys
import time
import smtplib
import os
import getpass
import base64
import html as html_lib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote_plus
from pathlib import Path

import requests

# Indlæs .env hvis python-dotenv er tilgængelig (valgfrit)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ─── Konfiguration ────────────────────────────────────────────────────────────

def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"FEJL: Miljøvariabel {name} er ikke sat.\n"
            f"      Se .env.example for hvilke variabler der kræves."
        )
    return value

# Mail-adresser kræves kun når der sendes mail (ikke i --export-tilstand)
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "").strip()
SENDER_EMAIL    = os.environ.get("SENDER_EMAIL", "").strip()

SMTP_HOST = os.environ.get("SMTP_HOST", "send.one.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

SCRIPT_DIR         = Path(__file__).parent
CREDENTIALS_FILE   = SCRIPT_DIR / ".credentials"
ANTHROPIC_KEY_FILE = SCRIPT_DIR / ".anthropic_key"
SEEN_JOBS_FILE     = SCRIPT_DIR / "seen_jobs.json"

# ─── Adgangskode-håndtering ───────────────────────────────────────────────────

def _obfuscate(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")

def _deobfuscate(encoded: str) -> str:
    return base64.b64decode(encoded.encode("ascii")).decode("utf-8")

def get_smtp_password() -> str:
    env_pw = os.environ.get("SMTP_PASSWORD", "")
    if env_pw:
        return env_pw
    if CREDENTIALS_FILE.exists():
        try:
            return _deobfuscate(CREDENTIALS_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    print("\n" + "="*60)
    print("JOB ALERT – Første gangs opsætning")
    print("="*60)
    print(f"\nSkriv din adgangskode til {SENDER_EMAIL} (One.com).\n")
    password = getpass.getpass(f"Adgangskode til {SENDER_EMAIL}: ")
    if password:
        CREDENTIALS_FILE.write_text(_obfuscate(password), encoding="utf-8")
        CREDENTIALS_FILE.chmod(0o600)
        print(f"\nAdgangskode gemt.\n")
    return password

def get_anthropic_key() -> str:
    """Henter Anthropic API-nøgle fra fil, env eller spørger brugeren."""
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key:
        return env_key
    if ANTHROPIC_KEY_FILE.exists():
        try:
            return _deobfuscate(ANTHROPIC_KEY_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    print("\nAnthopic API-nøgle ikke fundet.")
    key = getpass.getpass("Anthropic API-nøgle (sk-ant-...): ").strip()
    if key:
        ANTHROPIC_KEY_FILE.write_text(_obfuscate(key), encoding="utf-8")
        ANTHROPIC_KEY_FILE.chmod(0o600)
        print("API-nøgle gemt.\n")
    return key


# ─── Duplikat-hukommelse ──────────────────────────────────────────────────────

def load_seen_jobs() -> set:
    if SEEN_JOBS_FILE.exists():
        try:
            data = json.loads(SEEN_JOBS_FILE.read_text(encoding="utf-8"))
            return set(data.get("seen_links", []))
        except Exception:
            pass
    return set()

def save_seen_jobs(seen: set) -> None:
    SEEN_JOBS_FILE.write_text(
        json.dumps({"seen_links": sorted(seen), "updated": datetime.now().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def filter_new_jobs(jobs: list[dict], seen: set) -> list[dict]:
    new = []
    for job in jobs:
        link = job.get("link", "").split("?")[0].rstrip("/")
        if link and link not in seen:
            new.append(job)
    return new


# ─── HTTP Headers ─────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# ─── Geografisk grov-filtrering ───────────────────────────────────────────────

CPH_LOCATIONS = [
    "københavn", "kbh", "frederiksberg", "gentofte", "gladsaxe",
    "glostrup", "herlev", "hvidovre", "høje-taastrup", "ishøj",
    "lyngby", "rødovre", "tårnby", "vallensbæk", "albertslund",
    "ballerup", "brøndby", "dragør", "rudersdal", "furesø",
    "allerød", "egedal", "fredensborg", "helsingør", "hillerød",
    "hørsholm", "hellerup", "charlottenlund", "søborg", "birkerød",
    "farum", "holte", "vedbæk", "kokkedal", "humlebæk", "espergærde",
    "taastrup", "hedehusene", "greve", "køge", "roskilde", "solrød",
    "storkøbenhavn", "nordsjælland",
    "copenhagen", "valby", "vanløse", "brønshøj", "nordhavn", "amager", "kastrup",
    "bagsværd", "måløv", "skovlunde", "smørum", "virum", "nærum", "klampenborg",
]

NON_CPH_LOCATIONS = [
    "aarhus", "odense", "aalborg", "esbjerg", "randers", "kolding",
    "horsens", "vejle", "silkeborg", "herning", "næstved", "fredericia",
    "viborg", "holstebro", "slagelse", "sønderborg", "hjørring",
    "holbæk", "svendborg", "nykøbing", "ringkøbing", "skive", "thisted",
    "ikast", "middelfart", "faaborg", "assens", "kerteminde", "nyborg",
    "aabenraa", "haderslev", "tønder", "varde", "billund", "struer",
    "fyn", "jylland", "midtjylland", "nordjylland", "sydjylland", "sønderjylland",
    "hobro", "ringe", "vojens", "hedensted", "risskov", "lystrup", "viby j", "skanderborg",
    "karup", "warsaw", "hinnerup", "brabrand", "aabybro", "frederikshavn", "grenaa",
]

# Generiske ord der ikke siger noget om byen. Tæller kun som KBH hvis ingen
# by udenfor KBH er nævnt.
GENERIC_LOCATION_WORDS = ["remote", "hjemmearbejde", "hybrid", "fleksibel", "hele landet", "sjælland"]

# StepStone-søgningen dækker hele landet, så her kræves et kendt KBH-område.
# Jobindex og IT Jobbank afgrænser selv til Storkøbenhavn på serversiden.
STRICT_LOCATION_SOURCES = {"StepStone"}

def is_copenhagen_area(job: dict) -> bool:
    location = (job.get("location") or "").lower().strip()
    if not location:
        return True
    for cph in CPH_LOCATIONS:
        if cph in location:
            return True
    for city in NON_CPH_LOCATIONS:
        if city in location:
            return False
    if any(w in location for w in GENERIC_LOCATION_WORDS):
        return True
    return job.get("source") not in STRICT_LOCATION_SOURCES


# ─── AI-vurdering med Claude (rød / gul / grøn) ───────────────────────────────
#
# Claude afviser ikke opslag. Alle opslag der har passeret de hårde filtre
# inddeles i grøn / gul / rød, og Mads vurderer selv til sidst.
# CANDIDATE_PROFILE og RATING_RULES bruges både af API-kaldet her og af den
# planlagte sky-kørsel (se CLOUD_RUN.md), så vurderingen er ens begge steder.

CANDIDATE_PROFILE = """
Kandidat-profil:
- Uddannelse: Cand.it. i IT-ledelse, Aalborg Universitet (juni 2026). BSc i sociologi.
- Erfaring: Ca. 3 års deltidsstilling i Aalborg Kommune med governance-design fra bunden
  (processer, godkendelsesstrukturer, beslutnings-gates, rapportering, automatisering med
  Power Automate, beslutningsoplæg til ledelsen) omkring EU-finansierede projekter.
  Semesterprojekter: governance-flow for IT-anskaffelser i en kommune; proceskortlægning og
  softwarevalg for en teknisk virksomhed; risikovurdering med CIS Controls-roadmap.
  Speciale om organisatorisk cyber resilience i en stor international logistikvirksomhed.
- Profil: Struktur-, proces- og governance-menneske med teknisk forståelse. Ikke udvikler;
  kan læse og justere eksisterende kode med AI-assistance.
- Søger: Stillinger i Storkøbenhavn på junior- til mellemniveau, gerne finans og pharma.
"""

RATING_RULES = """
Inddel hvert opslag i én af tre farver ud fra opslagets fulde tekst:

GRØN = klart match. IT-governance, IT-portefølje, IT-strategi, forretnings- eller
  procesanalyse, proces-excellence, change management, digitalisering i komplekse
  organisationer, IT-risiko og compliance, DORA/operational resilience i finans,
  PMO/projektkoordinering, implementerings-, IT- eller management-konsulent inden for
  disse felter. Niveau: junior til mellem, uden et krav om mange års erfaring.

GUL = delvist match eller uklart. Rollen ligger tæt på profilen, men et element taler
  imod: fx 3-5 års erfaring krævet, tung domæneviden, mere salg eller drift end analyse,
  teknisk slagside, eller opslaget er for kortfattet til at afgøre det.
  Rene cybersikkerhedsroller (SOC, IAM, informationssikkerhedsofficer, sikkerhedsrådgiver
  i en ren sikkerhedsfunktion) er gule: kandidaten vil ikke have cybersikkerhed som
  karriereidentitet, men resilience og risiko set fra governance er relevant.
  Graduate-programmer er også gule (kandidaten søger direkte stillinger).

RØD = dårligt match. Rent tekniske roller (udvikling, infrastruktur, drift, data
  engineering), salg/marketing, økonomi/regnskab uden IT-vinkel, ledelsesroller med
  personaleansvar, eller roller der kræver mange års specialisterfaring.
  Studenterjobs, praktikpladser og ph.d.-stillinger er også røde, da de kræver
  indskrivning på en uddannelse (kandidaten er færdiguddannet).

Ved tvivl mellem to farver: vælg den mest positive. Finans (bank, pension, forsikring),
pharma/life science og konsulenthuse er kandidatens prioriterede brancher; ved tvivl
trækker de op.
"""

RATING_ORDER = {"green": 0, "yellow": 1, "red": 2, "unrated": 3}
_COLOR_WORDS = {
    "grøn": "green", "groen": "green", "green": "green",
    "gul": "yellow", "yellow": "yellow",
    "rød": "red", "roed": "red", "red": "red",
}


def ai_classify_jobs(jobs: list[dict], api_key: str) -> list[dict]:
    """
    Lader Claude inddele hvert opslag i grøn/gul/rød.
    Alle opslag returneres med feltet 'rating'. Ved API-fejl får batchen 'unrated'.
    """
    if not jobs:
        return []

    print(f"\nAI-inddeling med Claude ({len(jobs)} opslag)...")
    BATCH_SIZE = 10

    for batch_start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[batch_start:batch_start + BATCH_SIZE]
        for job in batch:
            job["rating"] = "unrated"

        job_list = ""
        for i, job in enumerate(batch):
            desc = job.get("description") or "(ingen beskrivelse)"
            job_list += f"""
[{i+1}] Titel: {job.get('title', '')}
     Virksomhed: {job.get('company', '')}
     Lokation: {job.get('location', '')}
     Beskrivelse: {desc[:1500]}
"""

        prompt = f"""{CANDIDATE_PROFILE}
{RATING_RULES}
Svar KUN med et JSON-objekt der mapper opslagets nummer til farven, f.eks.:
{{"1": "grøn", "2": "gul", "3": "rød"}}
Ingen forklaring, kun JSON.

Jobopslag:
{job_list}"""

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5",
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            if response.status_code == 200:
                content = response.json()["content"][0]["text"]
                m = re.search(r'\{.*\}', content, re.DOTALL)
                ratings = json.loads(m.group()) if m else {}
                for key, color in ratings.items():
                    idx = int(str(key).strip()) - 1
                    if 0 <= idx < len(batch):
                        batch[idx]["rating"] = _COLOR_WORDS.get(str(color).strip().lower(), "unrated")
            else:
                print(f"  Claude API fejl {response.status_code}: {response.text[:200]}")
        except Exception as e:
            print(f"  Claude fejl: {e} – batchen markeres som ikke vurderet")

        counts = {c: sum(1 for j in batch if j["rating"] == c) for c in RATING_ORDER}
        print(f"  Batch {batch_start//BATCH_SIZE + 1}: "
              f"{counts['green']} grøn, {counts['yellow']} gul, {counts['red']} rød, "
              f"{counts['unrated']} ikke vurderet")
        time.sleep(0.5)

    return jobs


# ─── Søgekonfiguration ────────────────────────────────────────────────────────

ITJOBBANK_SEARCHES = [
    ("konsulent",            "Storkøbenhavn", "Konsulent"),
    ("projektledelse",       "Storkøbenhavn", "Projektledelse"),
    ("ledelse",              "Storkøbenhavn", "Ledelse"),
    ("forretningsudvikling", "Storkøbenhavn", "Forretningsudvikling"),
]

# Jobnet kræver NemID-login – ikke tilgængeligt for automatisk søgning

# Jobindex: Søger via /jobsoegning/storkoebenhavn?q=TERM
# Jobdata er embedded som JSON i Stash variablen – samme metode som StepStone
JOBINDEX_SEARCH_TERMS = [
    # Kerneprofil: governance, risiko og resilience
    "IT governance",
    "governance",
    "compliance",
    "IT risk",
    "operational resilience",
    "DORA",
    "NIS2",
    # Portefølje, PMO og proces
    "PMO",
    "porteføljestyring",
    "portfolio manager",
    "procesoptimering",
    "process excellence",
    "change management",
    # Analyse, konsulent og digitalisering
    "IT strategi",
    "forretningsanalytiker",
    "business analyst",
    "IT konsulent",
    "implementeringskonsulent",
    "digitaliseringskonsulent",
    "digital transformation",
    "IT projektkoordinator",
]

# Jobindex sorteres efter dato, og der hentes de første sider per søgning.
# Ved daglig kørsel dækker det alle nye opslag for selv brede søgeord.
JOBINDEX_PAGES = 2

# StepStone: bruger /job/it/?subid=XXXX&geoareaid=15182 (Storkøbenhavn)
# subid=1008000 = IT-ledelse (43 opslag), subid=1019000 = IT-analyse
# Henter alle IT-opslag fra /job/it/ og lader Claude filtrere
STEPSTONE_SEARCHES = [
    ("it", None, "IT (alle kategorier)"),
]

# Grov forfiltrering – fanger åbenlyse ikke-IT opslag inden AI-kaldet
HARD_EXCLUDE = [
    "elektriker", "tømrer", "murer", "chauffør", "sygeplejerske", "læge",
    "pædagog", "socialrådgiver", "håndværker", "mekaniker", "bager",
    "kok", "rengøring", "lagermedarbejder", "butiksassistent",
]

# Titelfilter: opslag hvis titel matcher et af disse mønstre sorteres fra før
# AI-vurderingen. Mønstrene matcher fra ordstart, så danske sammensætninger
# som "seniorkonsulent", "testleder" og "supportmedarbejder" også fanges.
TITLE_EXCLUDE_PATTERNS = {
    "senior":   r"\bsenior|\bsr\b\.?",
    "test":     r"\btest|\bqa\b|quality assurance",
    "support":  r"support|servicedesk|service desk|helpdesk|help desk",
    "engineer": r"engineer|ingeniør",
    "udvikler": r"(?<!business )developer|\budvikler|softwareudvikler|systemudvikler"
                r"|webudvikler|programmør|devops|frontend|backend|full[- ]?stack",
    "tekniker": r"tekniker|technician",
}
_TITLE_EXCLUDE_RE = {k: re.compile(v, re.IGNORECASE) for k, v in TITLE_EXCLUDE_PATTERNS.items()}


def title_exclude_reason(job: dict) -> str | None:
    """Returnerer navnet på det titelfilter der rammer opslaget, ellers None."""
    title = job.get("title", "")
    for name, rx in _TITLE_EXCLUDE_RE.items():
        if rx.search(title):
            return name
    return None


def hard_exclude(job: dict) -> bool:
    """Returnerer True hvis opslaget åbenlyst er irrelevant (spare AI-kald)."""
    title = job.get("title", "").lower()
    return any(kw in title for kw in HARD_EXCLUDE)


# ─── Scraping funktioner

def _stash_results(stash: dict) -> list:
    """Finder jobresultaterne i Stash-objektet.
    Nøglen hed tidligere 'jobsearch/result_app' og hedder nu 'jobsearch/result_app/list'
    (ændret på Jobindex og StepStone i 2026). Begge understøttes."""
    for key in ("jobsearch/result_app/list", "jobsearch/result_app"):
        results = (
            stash.get(key, {})
                 .get("storeData", {})
                 .get("searchResponse", {})
                 .get("results", [])
        )
        if results:
            return results
    print("  (ingen resultater: søgningen har ingen hits, eller sidens struktur er ændret)")
    return []


def _html_to_text(raw: str, limit: int = 500) -> str:
    text = re.sub(r'<[^>]+>', ' ', raw or "")
    return re.sub(r'\s+', ' ', text).strip()[:limit]


def _deadline(job: dict) -> str:
    """Ansøgningsfrist fra Stash-data som YYYY-MM-DD, ellers ''."""
    if job.get("apply_deadline_asap"):
        return "snarest"
    raw = str(job.get("apply_deadline") or job.get("lastdate") or "")
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", raw)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


# ─── Fuld opslagstekst ────────────────────────────────────────────────────────

FULL_TEXT_LIMIT = 3000

def _page_text(fragment: str) -> str:
    fragment = re.sub(r'<(script|style|noscript|svg|nav|header|footer|form)\b.*?</\1>', ' ',
                      fragment, flags=re.S | re.I)
    fragment = re.sub(r'<br\s*/?>|</p>|</li>|</h\d>|</div>', '\n', fragment, flags=re.I)
    text = html_lib.unescape(re.sub(r'<[^>]+>', ' ', fragment))
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    return re.sub(r'\n\s*\n+', '\n', text).strip()


def fetch_full_text(job: dict) -> str:
    """Henter selve annoncen og returnerer dens tekst (max FULL_TEXT_LIMIT tegn).
    Rækkefølge: JSON-LD JobPosting → Jobindex-familiens annoncecontainer → sidens synlige tekst.
    Returnerer '' hvis siden ikke kan hentes."""
    url = job.get("fetch_url") or job.get("link")
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return ""
        page = resp.text
    except Exception:
        return ""

    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                return _page_text(item["description"])[:FULL_TEXT_LIMIT]

    start = page.find('jobtext-jobad__body')
    if start != -1:
        end = page.find('jobtext-jobad__sidebar', start)
        return _page_text(page[start:end if end != -1 else start + 40000].split(">", 1)[-1])[:FULL_TEXT_LIMIT]

    body = re.search(r'<main\b.*?</main>', page, re.S | re.I)
    return _page_text(body.group(0) if body else page)[:FULL_TEXT_LIMIT]


def add_full_texts(jobs: list[dict], workers: int = 4) -> None:
    """Erstatter den korte beskrivelse med annoncens fulde tekst, hvor den kan hentes."""
    from concurrent.futures import ThreadPoolExecutor
    if not jobs:
        return
    print(f"\nHenter fuld annoncetekst for {len(jobs)} opslag...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        texts = list(pool.map(fetch_full_text, jobs))
    ok = 0
    for job, text in zip(jobs, texts):
        if len(text) > len(job.get("description") or "") + 100:
            job["description"] = text
            ok += 1
    print(f"  Fuld tekst hentet for {ok}/{len(jobs)} (resten beholder teaseren)")

def scrape_jobindex(search_term: str) -> list[dict]:
    """Scraper Jobindex via Stash JSON variabel embedded i HTML.
    Jobindex bruger /jobsoegning/storkoebenhavn?q=TERM og embedder jobdata i siden."""
    jobs = []
    encoded = quote_plus(search_term)
    try:
        results = []
        for page in range(1, JOBINDEX_PAGES + 1):
            url = f"https://www.jobindex.dk/jobsoegning/storkoebenhavn?q={encoded}&sort=date&page={page}"
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                break
            m = re.search(r'var Stash = ({.*?});\s*//\]\]>', resp.text, re.DOTALL)
            if not m:
                break
            page_results = _stash_results(json.loads(m.group(1)))
            results.extend(page_results)
            if len(page_results) < 20:
                break
            time.sleep(0.4)
        for job in results:
            headline = job.get("headline", "")
            if not headline:
                continue
            company   = job.get("companytext", "") or job.get("company", {}).get("name", "")
            share_url = job.get("share_url", job.get("url", ""))
            firstdate = job.get("firstdate", "")
            area      = job.get("area", "")
            date_str  = ""
            if firstdate:
                try:
                    d = str(firstdate)[:8]
                    date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                except Exception:
                    pass
            if share_url and not share_url.startswith("http"):
                share_url = f"https://www.jobindex.dk{share_url}"
            jobs.append({
                "title":       headline,
                "company":     company or "Se opslag",
                "date":        date_str,
                "location":    area,
                "link":        share_url,
                "fetch_url":   job.get("url", "") or share_url,
                "deadline":    _deadline(job),
                "source":      "Jobindex",
                "category":    search_term,
                "description": _html_to_text(job.get("html", "")),
            })
    except Exception as e:
        print(f"  [Jobindex] Fejl ved '{search_term}': {e}")
    return jobs


def scrape_itjobbank(category_slug: str, location: str, category_name: str) -> list[dict]:
    """Scraper IT Jobbank via Stash JSON (samme platform som Jobindex og StepStone)."""
    jobs = []
    url = f"https://www.it-jobbank.dk/jobsoegning/{category_slug}?location={quote_plus(location.lower())}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        m = re.search(r'var Stash = ({.*?});\s*//\]\]>', resp.text, re.DOTALL)
        if not m:
            print(f"  [IT Jobbank] Ingen Stash-data for '{category_name}'")
            return jobs
        for job in _stash_results(json.loads(m.group(1))):
            headline = job.get("headline", "")
            if not headline:
                continue
            share_url = job.get("share_url") or job.get("url", "")
            if share_url and not share_url.startswith("http"):
                share_url = f"https://www.it-jobbank.dk{share_url}"
            firstdate = str(job.get("firstdate", ""))[:10].replace("-", "")
            jobs.append({
                "title":       headline,
                "company":     job.get("companytext", "") or job.get("company", {}).get("name", "") or "Se opslag",
                "date":        f"{firstdate[:4]}-{firstdate[4:6]}-{firstdate[6:8]}" if len(firstdate) >= 8 else "",
                "location":    job.get("area", ""),
                "link":        share_url,
                "fetch_url":   job.get("url", "") or share_url,
                "deadline":    _deadline(job),
                "source":      "IT Jobbank",
                "category":    category_name,
                "description": _html_to_text(job.get("html", "")),
            })
    except Exception as e:
        print(f"  [IT Jobbank] Fejl ved '{category_name}': {e}")
    return jobs


def scrape_jobnet(search_term: str) -> list[dict]:
    """Scraper Jobnet via JSON API."""
    jobs = []
    encoded = quote_plus(search_term)
    api_url = (
        f"https://job.jobnet.dk/CV/FindWork/SearchWorks"
        f"?SearchString={encoded}&RegionId=7"
        f"&SortValue=BestMatch&Offset=0&Count=20&WorkHours=0"
    )
    api_headers = {
        **HEADERS,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://job.jobnet.dk/CV/FindWork",
    }
    try:
        resp = requests.get(api_url, headers=api_headers, timeout=20, allow_redirects=False)
        if resp.status_code == 200:
            data = resp.json()
            for posting in data.get("JobPositionPostings", []):
                title    = posting.get("Title", "")
                company  = posting.get("HiringOrgName", "Se opslag")
                job_id   = posting.get("AutoMatchWorkId", "")
                date     = posting.get("PublicationStartDate", "")
                location = posting.get("WorkPlaceCityWithZipCode", "")
                desc     = posting.get("JobPositionInformation", "") or ""
                if title and job_id:
                    jobs.append({
                        "title":       title,
                        "company":     company or "Se opslag",
                        "date":        date[:10] if date else "",
                        "location":    location,
                        "link":        f"https://job.jobnet.dk/CV/FindWork/JobDetail/{job_id}",
                        "source":      "Jobnet",
                        "category":    search_term,
                        "description": desc[:500],
                    })
    except Exception as e:
        print(f"  [Jobnet] Fejl ved '{search_term}': {e}")
    return jobs


def scrape_stepstone(category_path: str, geoarea_id, category_name: str) -> list[dict]:
    """Scraper StepStone – jobdata er embedded som JSON (Stash variabel) i HTML.
    Bruger /job/it/?geoareaid=15182 (Storkøbenhavn) for at få geografisk filtrering på serversiden."""
    jobs = []
    # Byg URL med geografisk filter (Storkøbenhavn = 15182)
    if geoarea_id:
        url = f"https://www.stepstone.dk/job/{category_path}/?geoareaid={geoarea_id}"
    else:
        url = f"https://www.stepstone.dk/job/{category_path}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return jobs
        m = re.search(r'var Stash = ({.*?});\s*//\]\]>', resp.text, re.DOTALL)
        if not m:
            return jobs
        stash = json.loads(m.group(1))
        results = _stash_results(stash)
        for job in results:
            headline = job.get("headline", "")
            if not headline:
                continue
            company   = job.get("company", {}).get("name", "") or job.get("workplace_company", {}).get("name", "")
            share_url = job.get("share_url", job.get("url", ""))
            firstdate = job.get("firstdate", "")
            addresses = job.get("addresses", [])
            city      = addresses[0].get("city", "") if addresses else ""
            area      = job.get("area", "")
            location  = city or area or ""
            date_str  = ""
            if firstdate:
                try:
                    d = str(firstdate)[:8]
                    date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                except Exception:
                    pass
            if share_url and not share_url.startswith("http"):
                share_url = f"https://www.stepstone.dk{share_url}"
            desc = job.get("companytext", "") or ""
            jobs.append({
                "title":       headline,
                "company":     company or "Se opslag",
                "date":        date_str,
                "location":    location,
                "link":        share_url,
                "fetch_url":   job.get("url", "") or share_url,
                "deadline":    _deadline(job),
                "source":      "StepStone",
                "category":    category_name,
                "description": desc[:500],
            })
    except Exception as e:
        print(f"  [StepStone] Fejl ved '{category_name}': {e}")
    return jobs


# ─── Deduplicering ────────────────────────────────────────────────────────────

def _norm_text(text: str) -> str:
    text = html_lib.unescape(text or "").replace("\\/", "/").lower()
    return re.sub(r"[^0-9a-zæøåäöü]+", " ", text).strip()


def job_key(job: dict) -> tuple[str, str]:
    """(virksomhed, titel) normaliseret, til dubletfangst på tværs af links og kilder."""
    return _norm_text(job.get("company", "")), _norm_text(job.get("title", ""))


def is_near_duplicate(key: tuple[str, str], known: dict[str, list[str]], threshold: float = 0.85) -> bool:
    """Samme virksomhed og næsten samme titel (fx 'IT og ...' vs 'IT- og ...')."""
    from difflib import SequenceMatcher
    company, title = key
    for other in known.get(company, []):
        if title == other or SequenceMatcher(None, title, other).ratio() >= threshold:
            return True
    return False


def deduplicate(jobs: list[dict]) -> list[dict]:
    """Fjerner dubletter: samme link, eller samme virksomhed med næsten samme titel."""
    seen_links, known, unique = set(), {}, []
    for job in jobs:
        link = job.get("link", "").split("?")[0].rstrip("/")
        key  = job_key(job)
        if not link or link in seen_links or is_near_duplicate(key, known):
            continue
        seen_links.add(link)
        known.setdefault(key[0], []).append(key[1])
        unique.append(job)
    return unique


# ─── Hoved-scraping funktion ──────────────────────────────────────────────────

def collect_jobs() -> tuple[list[dict], list[dict]]:
    """Samler opslag fra alle kilder og kører de hårde filtre.
    Returnerer (beholdte opslag, frasorterede opslag med feltet 'excluded_by')."""
    all_jobs = []

    print(f"\n{'='*60}")
    print(f"Job Alert Scraper – {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*60}")

    print("\nSøger på IT Jobbank...")
    for slug, location, name in ITJOBBANK_SEARCHES:
        jobs = scrape_itjobbank(slug, location, name)
        all_jobs.extend(jobs)
        print(f"  {name}: {len(jobs)} opslag")
        time.sleep(0.5)

    print("\nSøger på Jobindex...")
    for term in JOBINDEX_SEARCH_TERMS:
        jobs = scrape_jobindex(term)
        all_jobs.extend(jobs)
        print(f"  '{term}': {len(jobs)} opslag")
        time.sleep(0.8)

    print("\nSøger på StepStone...")
    for path, geoid, name in STEPSTONE_SEARCHES:
        jobs = scrape_stepstone(path, geoid, name)
        all_jobs.extend(jobs)
        print(f"  {name}: {len(jobs)} opslag")
        time.sleep(0.5)

    print(f"\nTotal råopslag: {len(all_jobs)}")

    unique_jobs = deduplicate(all_jobs)
    print(f"Efter deduplicering: {len(unique_jobs)}")

    cph_jobs = [j for j in unique_jobs if is_copenhagen_area(j)]
    print(f"Efter geografisk filtrering (Storkøbenhavn): {len(cph_jobs)}")

    kept, excluded = [], []
    for job in cph_jobs:
        if hard_exclude(job):
            continue
        reason = title_exclude_reason(job)
        if reason:
            job["excluded_by"] = reason
            excluded.append(job)
        else:
            kept.append(job)
    print(f"Efter titelfilter (senior, test, support, engineer mv.): {len(kept)} "
          f"({len(excluded)} frasorteret)")
    return kept, excluded


def sort_by_rating(jobs: list[dict]) -> list[dict]:
    return sorted(jobs, key=lambda j: (
        RATING_ORDER.get(j.get("rating", "unrated"), 3),
        {"IT Jobbank": 0, "Jobindex": 1, "StepStone": 2}.get(j.get("source"), 3),
        j.get("title", ""),
    ))


# ─── E-mail funktioner ────────────────────────────────────────────────────────

def format_date(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str[:10])
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return date_str[:10]


RATING_LABELS = {
    "green":   ("Grøn",          "#1e7d45", "#e6f4ea"),
    "yellow":  ("Gul",           "#9a6b00", "#fdf3d8"),
    "red":     ("Rød",           "#b3261e", "#fbe9e7"),
    "unrated": ("Ikke vurderet", "#555555", "#f0f0f0"),
}


def build_html_email(jobs: list[dict]) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    jobs  = sort_by_rating(jobs)

    def job_card(job: dict) -> str:
        label, color, bg = RATING_LABELS[job.get("rating", "unrated")]
        meta = " · ".join(x for x in [job.get("company", ""), job.get("location", ""),
                                      job.get("source", ""), format_date(job.get("date", ""))] if x)
        return f"""
        <div style="background:#fff;border:1px solid #e0e0e0;border-left:5px solid {color};border-radius:6px;padding:12px 16px;margin-bottom:10px;">
          <a href="{job.get('link', '')}" style="color:#1a1a2e;text-decoration:none;font-size:15px;font-weight:600;">{job.get('title', '')}</a>
          <div style="color:#555;font-size:13px;margin-top:4px;">{meta}</div>
        </div>"""

    sections = ""
    for key, (label, color, bg) in RATING_LABELS.items():
        group = [j for j in jobs if j.get("rating", "unrated") == key]
        if not group:
            continue
        cards = "".join(job_card(j) for j in group)
        sections += f"""
        <h2 style="color:{color};font-size:17px;margin:24px 0 10px;border-bottom:2px solid {color};padding-bottom:6px;">
          {label} <span style="font-weight:400;color:#666;font-size:14px;">({len(group)})</span></h2>{cards}"""

    if not jobs:
        sections = '<p style="text-align:center;color:#666;padding:30px;">Ingen nye opslag denne uge.</p>'

    counts = " · ".join(f"{RATING_LABELS[k][0]}: {sum(1 for j in jobs if j.get('rating') == k)}"
                        for k in ("green", "yellow", "red"))
    return f"""<!DOCTYPE html>
<html lang="da"><head><meta charset="UTF-8"><title>Job Alert</title></head>
<body style="margin:0;background:#f4f6f9;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:20px;">
  <div style="background:#323b4c;border-radius:10px 10px 0 0;padding:24px;color:#fff;">
    <h1 style="font-size:20px;margin:0 0 4px;">Job Alert · {today}</h1>
    <p style="margin:0;color:#c9d1e0;font-size:13px;">{len(jobs)} nye opslag i Storkøbenhavn · {counts}</p>
  </div>
  <div style="background:#f8f9fb;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 10px 10px;padding:8px 24px 24px;">
    {sections}
  </div>
</div></body></html>"""


def build_plain_text_email(jobs: list[dict]) -> str:
    lines = [f"JOB ALERT · {datetime.now().strftime('%d/%m/%Y')} · {len(jobs)} nye opslag", ""]
    for key, (label, _, _) in RATING_LABELS.items():
        group = [j for j in sort_by_rating(jobs) if j.get("rating", "unrated") == key]
        if not group:
            continue
        lines += [f"== {label.upper()} ({len(group)}) =="]
        for job in group:
            lines.append(f"{job.get('title', '')} · {job.get('company', '')} · {job.get('location', '')}")
            lines.append(f"  {job.get('link', '')}")
        lines.append("")
    return "\n".join(lines)


def send_email(jobs: list[dict]) -> bool:
    if not RECIPIENT_EMAIL or not SENDER_EMAIL:
        sys.exit("FEJL: RECIPIENT_EMAIL og SENDER_EMAIL skal være sat for at sende mail. Se .env.example.")
    green   = sum(1 for j in jobs if j.get("rating") == "green")
    subject = f"Job Alert: {len(jobs)} nye opslag ({green} grønne) – {datetime.now().strftime('%d/%m/%Y')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Job Alert <{SENDER_EMAIL}>"
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(build_plain_text_email(jobs), "plain", "utf-8"))
    msg.attach(MIMEText(build_html_email(jobs),       "html",  "utf-8"))

    password = get_smtp_password()
    if not password:
        print("Ingen adgangskode – e-mail ikke sendt.")
        return False

    try:
        print(f"\nForbinder til {SMTP_HOST}:{SMTP_PORT}...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SENDER_EMAIL, password)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
        print(f"E-mail sendt til {RECIPIENT_EMAIL} ✓")
        return True
    except smtplib.SMTPAuthenticationError:
        print("\nFEJL: Forkert adgangskode til One.com.")
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
        return False
    except Exception as e:
        print(f"\nFEJL ved afsendelse: {e}")
        return False


# ─── Hoved-funktion ───────────────────────────────────────────────────────────

def _norm_link(link: str) -> str:
    return (link or "").split("?")[0].rstrip("/")


def export_jobs(out_path: str, seen_path: str | None) -> None:
    """Sky-tilstand: scrape + hårde filtre, skriv nye opslag til JSON. Ingen AI, ingen mail.
    seen_path kan pege på en tidligere jobs.json (fra resultatsiden) eller seen_jobs.json."""
    kept, excluded = collect_jobs()
    seen, known = set(), {}
    if seen_path and Path(seen_path).exists():
        data = json.loads(Path(seen_path).read_text(encoding="utf-8"))
        seen = set(data.get("seen_links", [])) | {_norm_link(j.get("link")) for j in data.get("jobs", [])}
        for company, title in data.get("seen_keys", []):
            known.setdefault(company, []).append(title)

    def is_new(job: dict) -> bool:
        return _norm_link(job.get("link")) not in seen and not is_near_duplicate(job_key(job), known)

    new = [j for j in kept if is_new(j)]
    new_excluded = [j for j in excluded if is_new(j)]
    add_full_texts(new)
    for i, j in enumerate(new, 1):
        j["id"] = i
    Path(out_path).write_text(json.dumps(
        {"run_at": datetime.now().isoformat(timespec="minutes"),
         "jobs": new, "excluded": new_excluded},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nTidligere set: {len(seen)} · Nye: {len(new)} · Nye frasorterede: {len(new_excluded)}")
    print(f"Skrevet til {out_path}")


def run_job_alert() -> None:
    """Mac-tilstand: scrape, filtrér, lad Claude inddele i grøn/gul/rød, send mail med nye opslag."""
    kept, _ = collect_jobs()

    seen     = load_seen_jobs()
    new_jobs = filter_new_jobs(kept, seen)
    print(f"\nTidligere sendte opslag: {len(seen)}")
    print(f"Nye opslag (ikke set før): {len(new_jobs)}")

    add_full_texts(new_jobs)
    api_key = get_anthropic_key()
    if api_key:
        new_jobs = ai_classify_jobs(new_jobs, api_key)
    else:
        print("Ingen Anthropic API-nøgle – opslag sendes uden inddeling")
        for j in new_jobs:
            j["rating"] = "unrated"

    print(f"\nSender e-mail til {RECIPIENT_EMAIL}...")
    success = send_email(new_jobs)

    if success and new_jobs:
        seen.update(_norm_link(j.get("link")) for j in new_jobs if j.get("link"))
        save_seen_jobs(seen)
        print(f"Gemt {len(new_jobs)} nye opslag i hukommelsen.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Job Alert")
    ap.add_argument("--export", metavar="FIL", help="Skriv nye, filtrerede opslag til JSON (ingen AI, ingen mail)")
    ap.add_argument("--seen", metavar="FIL", help="Tidligere jobs.json/seen_jobs.json at udelukke fra --export")
    ap.add_argument("--show", metavar="FIL", help="Udskriv opslag fra en --export-fil til vurdering")
    ap.add_argument("--start", type=int, default=1, help="Med --show: første id")
    ap.add_argument("--count", type=int, default=25, help="Med --show: antal opslag")
    ap.add_argument("--chars", type=int, default=1500, help="Med --show: tegn beskrivelse per opslag")
    args = ap.parse_args()
    if args.show:
        data = json.loads(Path(args.show).read_text(encoding="utf-8"))
        batch = [j for j in data["jobs"] if args.start <= j["id"] < args.start + args.count]
        for j in batch:
            desc = re.sub(r"\s+", " ", j.get("description") or "")[:args.chars]
            print(f"[{j['id']}] {j['title']} | {j['company']} | {j['location']} | {j['source']}\n{desc}\n")
        print(f"-- viste id {args.start}-{args.start + len(batch) - 1} af {len(data['jobs'])}")
    elif args.export:
        export_jobs(args.export, args.seen)
    else:
        run_job_alert()
