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
from zoneinfo import ZoneInfo
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
    SEEN_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEEN_JOBS_FILE.write_text(
        json.dumps({"seen_links": sorted(seen), "updated": datetime.now().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def _norm_link(link: str) -> str:
    """Link uden query-parametre (sporingsparametre o.l.). Undtagelse: sider hvor opslagets id
    står i queryen (psykologjob.dk: /job/?job=8656) beholder netop den parameter."""
    link = link or ""
    base = link.split("?")[0].rstrip("/")
    m = re.search(r"[?&]job=(\d+)", link) if "psykologjob.dk" in base else None
    return f"{base}/?job={m.group(1)}" if m else base

def filter_new_jobs(jobs: list[dict], seen: set) -> list[dict]:
    new = []
    for job in jobs:
        link = _norm_link(job.get("link", ""))
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
    "Accept-Encoding": "gzip, deflate",
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
    "århus", "viby", "kalundborg", "vordingborg", "syddanmark", "kolding",
]

# Generiske ord der ikke siger noget om byen. Tæller kun som KBH hvis ingen
# by udenfor KBH er nævnt.
GENERIC_LOCATION_WORDS = ["remote", "hjemmearbejde", "hybrid", "fleksibel", "hele landet", "sjælland"]

# StepStone-søgningen dækker hele landet, så her kræves et kendt KBH-område.
# Jobindex og IT Jobbank afgrænser selv til Storkøbenhavn på serversiden.
STRICT_LOCATION_SOURCES = {"StepStone"}

def is_copenhagen_area(job: dict) -> bool:
    # strict_location sættes af kilder der søger bredere end Storkøbenhavn (fx Jobindex' område
    # "sjaelland" eller psykologjob.dk's Region Sjælland). Her kræves et kendt KBH-område.
    strict = bool(job.get("strict_location"))
    location = (job.get("location") or "").lower().strip()
    if not location:
        return not strict
    for cph in CPH_LOCATIONS:
        if cph in location:
            return True
    for city in NON_CPH_LOCATIONS:
        if city in location:
            return False
    if strict:
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

# Backfill: sættes af --since. Når CUTOFF_DATE er sat, hentes der flere sider
# (op til BACKFILL_MAX_PAGES), indtil opslagene er ældre end cutoff.
CUTOFF_DATE: str = ""        # "YYYY-MM-DD" eller "" (dagligt: ingen cutoff)
BACKFILL_MAX_PAGES = 15

# StepStone: bruger /job/it/?subid=XXXX&geoareaid=15182 (Storkøbenhavn)
# subid=1008000 = IT-ledelse (43 opslag), subid=1019000 = IT-analyse
# Henter alle IT-opslag fra /job/it/ og lader Claude filtrere
STEPSTONE_SEARCHES = [
    ("it", None, "IT (alle kategorier)"),
]

# Kilder der søges i. Kan ændres pr. profil (se --profile og apply_profile).
ENABLED_SOURCES = {"itjobbank", "jobindex", "stepstone", "career_sites"}

# Jobindex-områder. storkoebenhavn afgrænser selv; øvrige områder (fx "sjaelland")
# filtreres strengt på kendte KBH-byer i CPH_LOCATIONS.
JOBINDEX_AREAS = ["storkoebenhavn"]

# psykologjob.dk (Dansk Psykolog Forenings jobbase). Regionskoder:
# 1084 = Region Hovedstaden, 1085 = Region Sjælland (filtreres strengt på KBH-byer).
PSYKOLOGJOB_REGIONS = [1084, 1085]
PSYKOLOGJOB_STRICT_REGIONS = {1085}
PSYKOLOGJOB_REGION_NAMES = {1081: "Region Nordjylland", 1082: "Region Midtjylland",
                            1083: "Region Syddanmark", 1084: "Region Hovedstaden",
                            1085: "Region Sjælland"}

# Mail: skjul røde opslag (vises kun som et antal). Sættes pr. profil.
EMAIL_HIDE_RED = False

# Krav-mønster (regex): når sat, beholdes kun opslag hvor titlen eller annoncens første
# REQUIRE_CHARS tegn matcher. Bruges til brede søgninger, hvor fritekstsøgningen også
# rammer mange andre faggrupper. Tom = intet krav (standard).
REQUIRE_PATTERN = ""
REQUIRE_CHARS = 400

# Grov forfiltrering – fanger åbenlyse ikke-IT opslag inden AI-kaldet (kan ændres pr. profil)
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
    "uopfordret": r"uopfordret|spontan|unsolicited|open application",
}
_TITLE_EXCLUDE_RE = {k: re.compile(v, re.IGNORECASE) for k, v in TITLE_EXCLUDE_PATTERNS.items()}


def title_exclude_reason(job: dict) -> str | None:
    """Returnerer navnet på det titelfilter der rammer opslaget, ellers None."""
    title = job.get("title", "")
    for name, rx in _TITLE_EXCLUDE_RE.items():
        if rx.search(title):
            return name
    return None


def require_reason(job: dict) -> str | None:
    """'krav' hvis profilens krav-mønster ikke findes i titel eller annoncens begyndelse."""
    if not REQUIRE_PATTERN:
        return None
    text = f"{job.get('title', '')} {(job.get('description') or '')[:REQUIRE_CHARS]}"
    return None if re.search(REQUIRE_PATTERN, text, re.IGNORECASE) else "krav"


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


def _stash_date(value) -> str:
    """firstdate fra Stash ('2026-10-23...' eller '20261023') som YYYY-MM-DD, ellers ''."""
    digits = re.sub(r"\D", "", str(value or ""))[:8]
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else ""


def _fetch_stash_pages(make_url, default_pages: int, page_size: int) -> list:
    """Henter Stash-resultater side for side. Uden CUTOFF_DATE hentes default_pages sider.
    Med CUTOFF_DATE fortsættes til en side er ældre end cutoff (højst BACKFILL_MAX_PAGES)."""
    max_pages = BACKFILL_MAX_PAGES if CUTOFF_DATE else default_pages
    results = []
    for page in range(1, max_pages + 1):
        resp = requests.get(make_url(page), headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            break
        m = re.search(r'var Stash = ({.*?});\s*//\]\]>', resp.text, re.DOTALL)
        if not m:
            break
        page_results = _stash_results(json.loads(m.group(1)))
        results.extend(page_results)
        if len(page_results) < page_size:
            break
        dates = [d for d in (_stash_date(r.get("firstdate")) for r in page_results) if d]
        if CUTOFF_DATE and dates and max(dates) < CUTOFF_DATE:
            break
        time.sleep(0.4)
    return results


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


CONTACT_LIMIT = 1400
_CONTACT_CUES = re.compile(
    r"kontakt|spørgsmål|vil du vide mere|hører gerne fra|høre mere|yderligere oplysninger|uddybende|"
    r"ring til|ringe til|tlf\.|telefon|\bcontact\b|questions|reach out|more information|"
    r"hiring manager|recruiter|talent acquisition|nærmeste leder|kommende leder|din leder bliver|"
    r"you will report to|reporting to|du refererer til|du kommer til at referere", re.I)


def extract_contact(text: str) -> str:
    """Uddrag af annoncen omkring kontaktoplysninger og nærmeste leder (højst CONTACT_LIMIT tegn).
    Returnerer '' hvis annoncen ikke nævner nogen."""
    if not text:
        return ""
    spans = []
    for m in _CONTACT_CUES.finditer(text):
        a, b = max(0, m.start() - 160), min(len(text), m.end() + 260)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    parts = [re.sub(r"\s+", " ", text[a:b]).strip() for a, b in spans]
    out = " … ".join(parts)
    if len(out) > CONTACT_LIMIT:   # kontaktlinjen står typisk sidst; behold også starten
        out = out[:CONTACT_LIMIT // 3] + " … " + out[-(CONTACT_LIMIT * 2 // 3):]
    return out


def _decode(resp) -> str:
    """Sidens tekst. Uden charset i headeren gætter requests på latin-1, hvilket gør 'ü' til 'Ã¼';
    så prøves UTF-8 først."""
    if "charset" not in resp.headers.get("content-type", "").lower():
        try:
            return resp.content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return resp.text


def _contact_card(page: str) -> str:
    """Jobindex-familiens kontaktkort ("Kontaktperson" i sidebaren) som 'Navn, titel, tlf, mail'."""
    out = []
    for block in re.findall(r'class="jobtext-contactperson"(.*?)</ul>', page, re.S):
        name = re.search(r'jobtext-contactperson__name"[^>]*>(.*?)</', block, re.S)
        title = re.search(r'jobtext-contactperson__(?:title|position)"[^>]*>(.*?)</', block, re.S)
        tel = re.search(r'href="tel:([^"]+)"', block)
        mail = re.search(r'href="mailto:([^"?]+)', block)
        parts = [html_lib.unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(1))).strip()
                 for m in (name, title) if m]
        if tel:
            parts.append("tlf. " + requests.utils.unquote(tel.group(1)).strip())
        if mail:
            parts.append(mail.group(1).strip())
        if parts:
            out.append(", ".join(p for p in parts if p))
    return "; ".join(out)


def fetch_ad(job: dict) -> dict:
    """Henter selve annoncen. Returnerer {"text": annoncetekst (uafkortet), "page": sidens synlige
    tekst, "card": Jobindex-familiens kontaktkort}. Rækkefølge for "text": JSON-LD JobPosting → karrieresidernes beskrivelsesfelter →
    Jobindex-familiens annoncecontainer → sidens synlige tekst. Tomme strenge hvis siden ikke kan hentes."""
    empty = {"text": "", "page": "", "card": ""}
    url = job.get("fetch_url") or job.get("link")
    if not url:
        return empty
    # Jobindex-familien: selve annoncen (med kontaktkort) ligger på /jobannonce/<id>;
    # /vis-job/ og /c?t= viser for mange opslag kun en teaser.
    jix = re.search(r"https?://(?:www\.)?(jobindex\.dk|it-jobbank\.dk|stepstone\.dk)/"
                    r"(?:vis-job|jobannonce)/([a-z]\d+)", job.get("link") or "")
    if jix:
        url = f"https://www.{jix.group(1)}/jobannonce/{jix.group(2)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return empty
        page = _decode(resp)
    except Exception:
        return empty
    if page.count("\ufffd") > 20:
        return empty   # binært eller forkert afkodet svar

    body = re.search(r'<main\b.*?</main>', page, re.S | re.I)
    page_text = _page_text(body.group(0) if body else page)
    card = _contact_card(page)

    def result(text: str) -> dict:
        return {"text": text, "page": page_text, "card": card}

    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                return result(_page_text(item["description"]))

    # SuccessFactors-karrieresider (Novo, Ambu, Tryg, Lundbeck, Nordea, Systematic m.fl.)
    start = page.find('itemprop="description"')
    if start != -1:
        text = _page_text(page[start:start + 60000].split(">", 1)[-1].split('id="reqdescription"')[0])
        if len(text) > 200:
            return result(text)

    # Talentsoft (Nykredit, PensionDanmark)
    parts = re.findall(r'id="fldjobdescription_(?:description\d|primaryprofile)"[^>]*>(.*?)</div>', page, re.S)
    if parts:
        text = _page_text("\n".join(parts))
        if len(text) > 200:
            return result(text)

    start = page.find('jobtext-jobad__body')
    if start != -1:
        end = page.find('jobtext-jobad__sidebar', start)
        return result(_page_text(page[start:end if end != -1 else start + 40000].split(">", 1)[-1]))

    if 'customlayout' in page and jix:   # Jobindex-annonce i arbejdsgiverens eget layout
        return result(_page_text(re.sub(r'<head\b.*?</head>', ' ', page, flags=re.S | re.I)))
    if re.search(r'cookie', page_text[:600], re.I):
        return {"text": "", "page": "", "card": card}   # cookie-/navigationsstøj; behold teaseren
    return result(page_text)


def fetch_full_text(job: dict) -> str:
    """Annoncens tekst, højst FULL_TEXT_LIMIT tegn ('' hvis siden ikke kan hentes)."""
    return fetch_ad(job)["text"][:FULL_TEXT_LIMIT]


def add_full_texts(jobs: list[dict], workers: int = 4) -> None:
    """Erstatter den korte beskrivelse med annoncens fulde tekst, hvor den kan hentes, og gemmer
    kontaktafsnittet i job["contact"] ud fra hele teksten (før afkortning)."""
    from concurrent.futures import ThreadPoolExecutor
    if not jobs:
        return
    print(f"\nHenter fuld annoncetekst for {len(jobs)} opslag...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        ads = list(pool.map(fetch_ad, jobs))
    ok = n_contact = 0
    for job, ad in zip(jobs, ads):
        text = ad["text"]
        if len(text) > len(job.get("description") or "") + 100:
            job["description"] = text[:FULL_TEXT_LIMIT]
            ok += 1
        found = extract_contact(text or job.get("description") or "")
        card = f"Kontaktperson (Jobindex): {ad['card']}" if ad.get("card") else ""
        job["contact"] = " … ".join(p for p in (card, found) if p)
        n_contact += bool(job["contact"])
    print(f"  Fuld tekst hentet for {ok}/{len(jobs)} (resten beholder teaseren); "
          f"kontaktafsnit fundet i {n_contact}")

def scrape_jobindex(search_term: str, area: str = "storkoebenhavn") -> list[dict]:
    """Scraper Jobindex via Stash JSON variabel embedded i HTML.
    Jobindex bruger /jobsoegning/<område>?q=TERM og embedder jobdata i siden.
    Opslag fra andre områder end storkoebenhavn får strict_location (kræver kendt KBH-by)."""
    jobs = []
    encoded = quote_plus(search_term)
    try:
        results = _fetch_stash_pages(
            lambda page: f"https://www.jobindex.dk/jobsoegning/{area}?q={encoded}&sort=date&page={page}",
            JOBINDEX_PAGES, 20)
        for job in results:
            headline = job.get("headline", "")
            if not headline:
                continue
            company   = job.get("companytext", "") or job.get("company", {}).get("name", "")
            share_url = job.get("share_url", job.get("url", ""))
            area      = job.get("area", "")
            date_str  = _stash_date(job.get("firstdate"))
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
                "strict_location": area != "storkoebenhavn",
            })
    except Exception as e:
        print(f"  [Jobindex] Fejl ved '{search_term}': {e}")
        _note_error("Jobindex", f"{search_term}: {e}")
    return jobs


def scrape_itjobbank(category_slug: str, location: str, category_name: str) -> list[dict]:
    """Scraper IT Jobbank via Stash JSON (samme platform som Jobindex og StepStone)."""
    jobs = []
    base = f"https://www.it-jobbank.dk/jobsoegning/{category_slug}?location={quote_plus(location.lower())}"
    try:
        for job in _fetch_stash_pages(lambda page: f"{base}&page={page}", 1, 25):
            headline = job.get("headline", "")
            if not headline:
                continue
            share_url = job.get("share_url") or job.get("url", "")
            if share_url and not share_url.startswith("http"):
                share_url = f"https://www.it-jobbank.dk{share_url}"
            jobs.append({
                "title":       headline,
                "company":     job.get("companytext", "") or job.get("company", {}).get("name", "") or "Se opslag",
                "date":        _stash_date(job.get("firstdate")),
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
        _note_error("IT Jobbank", f"{category_name}: {e}")
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
    base = f"https://www.stepstone.dk/job/{category_path}/?" + (f"geoareaid={geoarea_id}&" if geoarea_id else "")
    try:
        results = _fetch_stash_pages(lambda page: f"{base}page={page}", 1, 25)
        for job in results:
            headline = job.get("headline", "")
            if not headline:
                continue
            company   = job.get("company", {}).get("name", "") or job.get("workplace_company", {}).get("name", "")
            share_url = job.get("share_url", job.get("url", ""))
            addresses = job.get("addresses", [])
            city      = addresses[0].get("city", "") if addresses else ""
            area      = job.get("area", "")
            location  = city or area or ""
            date_str  = _stash_date(job.get("firstdate"))
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
        _note_error("StepStone", f"{category_name}: {e}")
    return jobs


# ─── Deduplicering ────────────────────────────────────────────────────────────

def scrape_psykologjob(region: int) -> list[dict]:
    """Scraper psykologjob.dk (Dansk Psykolog Forenings jobbase) for én region.
    Listen er almindelig HTML (div.dbj_jobbox). Opslagene har ingen opslagsdato, kun frist."""
    jobs = []
    url = (f"https://www.psykologjob.dk/job/?searchit=&searchbeliggenhed="
           f"&searchRegion={region}&searchKommune=&tag2=")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  [Psykologjob] HTTP {resp.status_code} for region {region}")
            _note_error("Psykologjob", f"{PSYKOLOGJOB_REGION_NAMES.get(region, region)}: HTTP {resp.status_code}")
            return []
        page = _decode(resp)
        for box in page.split('class="dbj_jobbox"')[1:]:
            link = re.search(r'<h2 class="dbj_jobHeadline"[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', box, re.S)
            if not link:
                continue
            company = re.search(r'class="dbj_jobCompanyname">(.*?)</div>', box, re.S)
            meta = re.search(r'</div><div>([^<]*?\|[^<]*)</div>', box)
            parts = [html_lib.unescape(x).replace("\xa0", " ").strip()
                     for x in (meta.group(1).split("|") if meta else [])]
            deadline = ""
            for part in parts:
                m = re.search(r"(\d{2})-(\d{2})-(\d{4})", part)
                if m:
                    deadline = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            job_id = re.search(r"job=(\d+)", link.group(1))
            if not job_id:
                continue
            jobs.append({
                "title":       _html_to_text(link.group(2), 300),
                "company":     _html_to_text(company.group(1), 200) if company else "Se opslag",
                "date":        "",
                "location":    parts[1] if len(parts) > 1 else "",
                "link":        f"https://www.psykologjob.dk/job/?job={job_id.group(1)}",
                "deadline":    deadline,
                "source":      "Psykologjob",
                "category":    parts[0] if parts else "",
                "description": "",
                "strict_location": region in PSYKOLOGJOB_STRICT_REGIONS,
            })
    except Exception as e:
        print(f"  [Psykologjob] Fejl ved region {region}: {e}")
        _note_error("Psykologjob", f"{PSYKOLOGJOB_REGION_NAMES.get(region, region)}: {e}")
    return jobs


def _norm_text(text: str) -> str:
    text = html_lib.unescape(text or "").replace("\\/", "/").lower()
    return re.sub(r"[^0-9a-zæøåäöü]+", " ", text).strip()


_COMPANY_SUFFIX = re.compile(r"\b(a s|aps|as|a/s|i s|p s|amba|ab|asa|oyj|plc|ltd|gmbh|danmark|denmark)\b")

def job_key(job: dict) -> tuple[str, str]:
    """(virksomhed, titel) normaliseret, til dubletfangst på tværs af links og kilder."""
    company = _COMPANY_SUFFIX.sub(" ", _norm_text(job.get("company", "")))
    return re.sub(r"\s+", " ", company).strip(), _norm_text(job.get("title", ""))


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
        link = _norm_link(job.get("link", ""))
        key  = job_key(job)
        if not link or link in seen_links or is_near_duplicate(key, known):
            continue
        seen_links.add(link)
        known.setdefault(key[0], []).append(key[1])
        unique.append(job)
    return unique


# ─── Hoved-scraping funktion ──────────────────────────────────────────────────

# Kildestatus for seneste collect_jobs(): én linje pr. jobportal og pr. karriereside.
# Skrives til --export-filen, så resultatsiden kan vise kilder der gav 0 eller fejlede.
SOURCE_STATUS: dict = {}

# Fejl pr. jobportal under seneste collect_jobs() (vises i kildestatus).
SOURCE_ERRORS: dict[str, list[str]] = {}

# Tal for filtreringen i seneste kørsel (vises i mailens kildestatus).
FUNNEL: dict = {}


def _note_error(board: str, message: str) -> None:
    SOURCE_ERRORS.setdefault(board, []).append(message[:160])


def _board_status(name: str, entries: list[tuple[str, int]]) -> dict:
    """entries: (søgningens navn, antal opslag) pr. søgning på portalen."""
    counts = [c for _, c in entries]
    return {"name": name, "count": sum(counts), "searches": len(counts),
            "empty_searches": sum(1 for c in counts if c == 0),
            "empty": [label for label, c in entries if c == 0],
            "errors": SOURCE_ERRORS.get(name, [])}


def collect_jobs() -> tuple[list[dict], list[dict]]:
    """Samler opslag fra alle kilder og kører de hårde filtre.
    Returnerer (beholdte opslag, frasorterede opslag med feltet 'excluded_by').
    Antal råopslag pr. kilde gemmes i SOURCE_STATUS."""
    all_jobs = []
    board_names = {"jobindex": "Jobindex", "itjobbank": "IT Jobbank", "stepstone": "StepStone",
                   "psykologjob": "Psykologjob"}
    board_counts: dict[str, list[tuple[str, int]]] = {name: [] for key, name in board_names.items()
                                                      if key in ENABLED_SOURCES}
    SOURCE_ERRORS.clear()
    career_status: list[dict] = []

    print(f"\n{'='*60}")
    print(f"Job Alert Scraper – {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*60}")

    if "itjobbank" in ENABLED_SOURCES:
        print("\nSøger på IT Jobbank...")
        for slug, location, name in ITJOBBANK_SEARCHES:
            jobs = scrape_itjobbank(slug, location, name)
            all_jobs.extend(jobs)
            board_counts["IT Jobbank"].append((name, len(jobs)))
            print(f"  {name}: {len(jobs)} opslag")
            time.sleep(0.5)

    if "jobindex" in ENABLED_SOURCES:
        print("\nSøger på Jobindex...")
        for area in JOBINDEX_AREAS:
            for term in JOBINDEX_SEARCH_TERMS:
                jobs = scrape_jobindex(term, area)
                all_jobs.extend(jobs)
                label = term if len(JOBINDEX_AREAS) == 1 else f"{term} ({area})"
                board_counts["Jobindex"].append((label, len(jobs)))
                print(f"  '{term}' ({area}): {len(jobs)} opslag")
                time.sleep(0.8)

    if "psykologjob" in ENABLED_SOURCES:
        print("\nSøger på Psykologjob...")
        for region in PSYKOLOGJOB_REGIONS:
            jobs = scrape_psykologjob(region)
            all_jobs.extend(jobs)
            region_name = PSYKOLOGJOB_REGION_NAMES.get(region, f"Region {region}")
            board_counts["Psykologjob"].append((region_name, len(jobs)))
            print(f"  {region_name}: {len(jobs)} opslag")
            time.sleep(0.5)

    if "stepstone" in ENABLED_SOURCES:
        print("\nSøger på StepStone...")
        for path, geoid, name in STEPSTONE_SEARCHES:
            jobs = scrape_stepstone(path, geoid, name)
            all_jobs.extend(jobs)
            board_counts["StepStone"].append((name, len(jobs)))
            print(f"  {name}: {len(jobs)} opslag")
            time.sleep(0.5)

    if "career_sites" in ENABLED_SOURCES:
        print("\nSøger på målvirksomhedernes karrieresider...")
        try:
            from career_sites import scrape_career_sites
            all_jobs.extend(scrape_career_sites(career_status))
        except Exception as e:
            print(f"  [Karrieresider] Fejl: {e}")
            career_status = [{"name": "Karrieresider (alle)", "count": 0, "error": str(e)[:120]}]

    SOURCE_STATUS.clear()
    SOURCE_STATUS.update({
        "boards": [_board_status(n, c) for n, c in board_counts.items()],
        "career_sites": career_status,
    })

    print(f"\nTotal råopslag: {len(all_jobs)}")
    FUNNEL.clear()
    FUNNEL["raw"] = len(all_jobs)

    if CUTOFF_DATE:
        all_jobs = [j for j in all_jobs if not j.get("date") or j["date"] >= CUTOFF_DATE]
        print(f"Efter datofilter (fra {CUTOFF_DATE}): {len(all_jobs)}")
        FUNNEL["since"] = CUTOFF_DATE
        FUNNEL["dated"] = len(all_jobs)

    unique_jobs = deduplicate(all_jobs)
    print(f"Efter deduplicering: {len(unique_jobs)}")

    cph_jobs = [j for j in unique_jobs if is_copenhagen_area(j)]
    print(f"Efter geografisk filtrering (Storkøbenhavn): {len(cph_jobs)}")
    FUNNEL["unique"] = len(unique_jobs)
    FUNNEL["area"] = len(cph_jobs)

    kept, excluded = [], []
    for job in cph_jobs:
        if hard_exclude(job):
            continue
        reason = title_exclude_reason(job) or require_reason(job)
        if reason:
            job["excluded_by"] = reason
            excluded.append(job)
        else:
            kept.append(job)
    print(f"Efter titelfilter (senior, test, support, engineer mv.{', krav' if REQUIRE_PATTERN else ''}): {len(kept)} "
          f"({len(excluded)} frasorteret)")
    FUNNEL["kept"] = len(kept)
    return kept, excluded


def sort_by_rating(jobs: list[dict]) -> list[dict]:
    return sorted(jobs, key=lambda j: (
        RATING_ORDER.get(j.get("rating", "unrated"), 3),
        {"Karriereside": 0, "IT Jobbank": 1, "Jobindex": 2, "StepStone": 3}.get(j.get("source"), 4),
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


def source_status_lines() -> list[tuple[bool, str]]:
    """Kildestatus som (problem?, tekst): én linje pr. jobportal, karrieresider samlet,
    og til sidst filtreringens tal. Problem = 0 opslag, søgninger uden hits eller fejl."""
    out = []
    for b in SOURCE_STATUS.get("boards", []):
        text = f"{b['name']}: {b['count']} opslag"
        if b["searches"] > 1:
            text += f" fra {b['searches']} søgninger"
        if b.get("empty"):
            text += f". Uden hits: {', '.join(b['empty'])}"
        if b.get("errors"):
            text += f". Fejl: {'; '.join(b['errors'])}"
        out.append((not b["count"] or bool(b.get("empty")) or bool(b.get("errors")), text))
    sites = SOURCE_STATUS.get("career_sites", [])
    if sites:
        bad = [c for c in sites if c.get("error") or not c.get("count")]
        text = f"Karrieresider: {sum(c.get('count', 0) for c in sites)} opslag fra {len(sites)} sider"
        if bad:
            text += ". Uden opslag eller med fejl: " + ", ".join(
                f"{c['name']}{' (fejl)' if c.get('error') else ''}" for c in bad)
        out.append((bool(bad), text))
    if FUNNEL:
        parts = [f"{FUNNEL['raw']} hentet"]
        if "dated" in FUNNEL:
            parts.append(f"{FUNNEL['dated']} slået op siden {format_date(FUNNEL['since'])}")
        parts += [f"{FUNNEL.get('unique', 0)} efter dubletter",
                  f"{FUNNEL.get('area', 0)} i området",
                  f"{FUNNEL.get('kept', 0)} efter filtre"]
        if "new" in FUNNEL:
            parts.append(f"{FUNNEL['new']} nye")
        out.append((False, "Filtrering: " + " → ".join(parts)))
    return out


def build_source_status_html() -> str:
    lines = source_status_lines()
    if not lines:
        return ""
    items = "".join(
        f'<li style="margin:3px 0;color:{"#b3261e" if bad else "#555"};">{html_lib.escape(text)}</li>'
        for bad, text in lines)
    return (f'<h2 style="color:#323b4c;font-size:15px;margin:28px 0 6px;border-bottom:1px solid #ccc;'
            f'padding-bottom:4px;">Kildestatus</h2>'
            f'<ul style="margin:0;padding-left:18px;font-size:12px;line-height:1.45;">{items}</ul>')


def build_html_email(jobs: list[dict]) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    jobs  = sort_by_rating(jobs)

    def job_card(job: dict) -> str:
        label, color, bg = RATING_LABELS[job.get("rating", "unrated")]
        deadline = job.get("deadline", "")
        deadline = f"Frist: {format_date(deadline) if deadline[:1].isdigit() else deadline}" if deadline else ""
        meta = " · ".join(x for x in [job.get("company", ""), job.get("location", ""),
                                      job.get("source", ""), format_date(job.get("date", "")), deadline] if x)
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
        if key == "red" and EMAIL_HIDE_RED:
            sections += (f'<p style="color:#666;font-size:13px;margin:24px 0 0;">'
                         f'{len(group)} opslag vurderet som dårligt match er ikke vist.</p>')
            continue
        cards = "".join(job_card(j) for j in group)
        sections += f"""
        <h2 style="color:{color};font-size:17px;margin:24px 0 10px;border-bottom:2px solid {color};padding-bottom:6px;">
          {label} <span style="font-weight:400;color:#666;font-size:14px;">({len(group)})</span></h2>{cards}"""

    if not jobs:
        sections = '<p style="text-align:center;color:#666;padding:30px;">Ingen nye opslag siden sidste mail.</p>'

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
    {build_source_status_html()}
  </div>
</div></body></html>"""


def build_plain_text_email(jobs: list[dict]) -> str:
    lines = [f"JOB ALERT · {datetime.now().strftime('%d/%m/%Y')} · {len(jobs)} nye opslag", ""]
    for key, (label, _, _) in RATING_LABELS.items():
        group = [j for j in sort_by_rating(jobs) if j.get("rating", "unrated") == key]
        if not group:
            continue
        if key == "red" and EMAIL_HIDE_RED:
            lines += [f"{len(group)} opslag vurderet som dårligt match er ikke vist.", ""]
            continue
        lines += [f"== {label.upper()} ({len(group)}) =="]
        for job in group:
            lines.append(f"{job.get('title', '')} · {job.get('company', '')} · {job.get('location', '')}")
            lines.append(f"  {job.get('link', '')}")
        lines.append("")
    lines += ["== KILDESTATUS =="] + [("! " if bad else "  ") + text for bad, text in source_status_lines()]
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

# ─── Profiler ─────────────────────────────────────────────────────────────────
#
# Uden --profile kører scriptet med konfigurationen ovenfor (Mads' profil), præcis som før.
# En profil er en JSON-fil, der overskriver udvalgte indstillinger, så samme kode kan køre
# for flere personer. Alle nøgler er valgfrie:
#   candidate_profile, rating_rules        tekst til Claude-vurderingen
#   sources                                fx ["jobindex", "psykologjob"]
#                                          (mulige: itjobbank, jobindex, stepstone,
#                                          career_sites, psykologjob)
#   jobindex_search_terms, jobindex_areas  søgeord og områder på Jobindex
#   psykologjob_regions                    regionskoder på psykologjob.dk
#   hard_exclude                           titelord der sorteres fra før vurderingen
#   title_exclude                          hvilke af TITLE_EXCLUDE_PATTERNS der bruges (navne)
#   seen_file                              sti til dublethukommelsen (relativ til scriptet)
#   email_hide_red                         true = røde opslag vises kun som et antal
#   require_pattern                        regex der skal findes i titel eller annoncens start
# Profilen kan indeholde personlige oplysninger og skal ikke committes. I GitHub Actions
# ligger den som secret (se GITHUB_ACTIONS.md).

PROFILE_KEYS = {
    "candidate_profile":     "CANDIDATE_PROFILE",
    "rating_rules":          "RATING_RULES",
    "jobindex_search_terms": "JOBINDEX_SEARCH_TERMS",
    "jobindex_areas":        "JOBINDEX_AREAS",
    "psykologjob_regions":   "PSYKOLOGJOB_REGIONS",
    "hard_exclude":          "HARD_EXCLUDE",
    "email_hide_red":        "EMAIL_HIDE_RED",
    "require_pattern":       "REQUIRE_PATTERN",
}


def apply_profile(path: str) -> None:
    """Indlæser en profil-JSON og overskriver de tilsvarende indstillinger i modulet."""
    global ENABLED_SOURCES, SEEN_JOBS_FILE, _TITLE_EXCLUDE_RE
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    unknown = set(data) - set(PROFILE_KEYS) - {"name", "sources", "title_exclude", "seen_file"}
    if unknown:
        sys.exit(f"FEJL: Ukendte nøgler i profilen: {', '.join(sorted(unknown))}")
    for key, name in PROFILE_KEYS.items():
        if key in data:
            globals()[name] = data[key]
    if "sources" in data:
        valid = {"itjobbank", "jobindex", "stepstone", "career_sites", "psykologjob"}
        bad = set(data["sources"]) - valid
        if bad:
            sys.exit(f"FEJL: Ukendte kilder i profilen: {', '.join(sorted(bad))}")
        ENABLED_SOURCES = set(data["sources"])
    if "title_exclude" in data:
        bad = set(data["title_exclude"]) - set(TITLE_EXCLUDE_PATTERNS)
        if bad:
            sys.exit(f"FEJL: Ukendte titelfiltre i profilen: {', '.join(sorted(bad))}")
        _TITLE_EXCLUDE_RE = {k: re.compile(TITLE_EXCLUDE_PATTERNS[k], re.IGNORECASE)
                             for k in data["title_exclude"]}
    if "seen_file" in data:
        seen = Path(data["seen_file"])
        SEEN_JOBS_FILE = seen if seen.is_absolute() else SCRIPT_DIR / seen
    print(f"Profil indlæst: {data.get('name', path)} · kilder: {', '.join(sorted(ENABLED_SOURCES))}")


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
        {"run_at": datetime.now(ZoneInfo("Europe/Copenhagen")).isoformat(timespec="minutes"),
         "jobs": new, "excluded": new_excluded, "sources": SOURCE_STATUS},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nTidligere set: {len(seen)} · Nye: {len(new)} · Nye frasorterede: {len(new_excluded)}")
    print(f"Skrevet til {out_path}")


def run_job_alert(dry_run: bool = False) -> None:
    """Mac-tilstand: scrape, filtrér, lad Claude inddele i grøn/gul/rød, send mail med nye opslag."""
    kept, _ = collect_jobs()

    seen     = load_seen_jobs()
    new_jobs = filter_new_jobs(kept, seen)
    print(f"\nTidligere sendte opslag: {len(seen)}")
    print(f"Nye opslag (ikke set før): {len(new_jobs)}")
    FUNNEL["new"] = len(new_jobs)

    add_full_texts(new_jobs)
    api_key = get_anthropic_key()
    if api_key:
        new_jobs = ai_classify_jobs(new_jobs, api_key)
    else:
        print("Ingen Anthropic API-nøgle – opslag sendes uden inddeling")
        for j in new_jobs:
            j["rating"] = "unrated"

    if dry_run:
        out = SCRIPT_DIR / "dry_run_email.html"
        out.write_text(build_html_email(new_jobs), encoding="utf-8")
        print(f"\nDry run: mail skrevet til {out} (intet sendt, hukommelsen er ikke opdateret)")
        return

    print(f"\nSender e-mail til {RECIPIENT_EMAIL}...")
    success = send_email(new_jobs)

    if success and new_jobs:
        seen.update(_norm_link(j.get("link")) for j in new_jobs if j.get("link"))
        save_seen_jobs(seen)
        print(f"Gemt {len(new_jobs)} nye opslag i hukommelsen.")
    if not success:
        sys.exit(1)   # så en planlagt kørsel (fx GitHub Actions) melder fejl


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Job Alert")
    ap.add_argument("--profile", metavar="FIL",
                    help="Profil-JSON der overskriver standardopsætningen (se apply_profile)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Kør som normalt, men skriv mailen til dry_run_email.html i stedet for at sende")
    ap.add_argument("--export", metavar="FIL", help="Skriv nye, filtrerede opslag til JSON (ingen AI, ingen mail)")
    ap.add_argument("--seen", metavar="FIL", help="Tidligere jobs.json/seen_jobs.json at udelukke fra --export")
    ap.add_argument("--since", type=int, metavar="DAGE",
                    help="Backfill: hent flere sider tilbage og behold opslag fra de sidste DAGE dage")
    ap.add_argument("--show", metavar="FIL", help="Udskriv opslag fra en --export-fil til vurdering")
    ap.add_argument("--start", type=int, default=1, help="Med --show: første id")
    ap.add_argument("--count", type=int, default=25, help="Med --show: antal opslag")
    ap.add_argument("--chars", type=int, default=1500, help="Med --show: tegn beskrivelse per opslag")
    args = ap.parse_args()
    if args.profile:
        apply_profile(args.profile)
    if args.since:
        from datetime import timedelta
        CUTOFF_DATE = (datetime.now(ZoneInfo("Europe/Copenhagen")) - timedelta(days=args.since)).strftime("%Y-%m-%d")
    if args.show:
        data = json.loads(Path(args.show).read_text(encoding="utf-8"))
        batch = [j for j in data["jobs"] if args.start <= j["id"] < args.start + args.count]
        for j in batch:
            desc = re.sub(r"\s+", " ", j.get("description") or "")[:args.chars]
            contact = f"\nKONTAKT: {j['contact']}" if j.get("contact") else ""
            print(f"[{j['id']}] {j['title']} | {j['company']} | {j['location']} | {j['source']}\n{desc}{contact}\n")
        print(f"-- viste id {args.start}-{args.start + len(batch) - 1} af {len(data['jobs'])}")
    elif args.export:
        export_jobs(args.export, args.seen)
    else:
        run_job_alert(dry_run=args.dry_run)
