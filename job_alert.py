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

RECIPIENT_EMAIL = _require_env("RECIPIENT_EMAIL")
SENDER_EMAIL    = _require_env("SENDER_EMAIL")

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
    "storkøbenhavn", "sjælland", "nordsjælland",
    "remote", "hjemmearbejde", "hybrid", "fleksibel", "hele landet",
]

NON_CPH_LOCATIONS = [
    "aarhus", "odense", "aalborg", "esbjerg", "randers", "kolding",
    "horsens", "vejle", "silkeborg", "herning", "næstved", "fredericia",
    "viborg", "holstebro", "slagelse", "sønderborg", "hjørring",
    "holbæk", "svendborg", "nykøbing", "ringkøbing", "skive", "thisted",
    "ikast", "middelfart", "faaborg", "assens", "kerteminde", "nyborg",
    "aabenraa", "haderslev", "tønder", "varde", "billund", "struer",
    "fyn", "jylland", "midtjylland", "nordjylland", "sydjylland", "sønderjylland",
]

def is_copenhagen_area(job: dict) -> bool:
    location = (job.get("location") or "").lower().strip()
    if not location:
        return True
    for city in NON_CPH_LOCATIONS:
        if city in location:
            return False
    for cph in CPH_LOCATIONS:
        if cph in location:
            return True
    return True


# ─── AI-filtrering med Claude ─────────────────────────────────────────────────

CANDIDATE_PROFILE = """
Kandidat-profil:
- Uddannelse: Kandidat i IT-ledelse (cand.it) fra Aalborg Universitet, færdiguddannet juni 2026
- Erfaring: Nyuddannet – ingen eller meget begrænset erhvervserfaring (maks 1-2 år studiejobs)
- Kompetencer: IT-projektledelse, digital transformation, forretningsudvikling, organisationsforandring,
  IT-strategi, stakeholder management, agile metoder, kravspecifikation, forretningsanalyse,
  IT-governance, change management, systemimplementering
- Søger: Entry-level eller junior stillinger i Storkøbenhavn inden for IT-ledelse, digitalisering,
  IT-konsulentarbejde, forretningsanalyse, IT-koordinering eller lignende
- Ønsker IKKE: Rent tekniske udviklerstillinger, militær/forsvar-specialiststillinger,
  stillinger der kræver 3+ års erfaring, senior/lead/chef-roller, ph.d.-krav,
  sundhedsfaglige stillinger, håndværk, transport eller ufaglærte jobs
"""

def ai_filter_jobs(jobs: list[dict], api_key: str) -> list[dict]:
    """
    Bruger Claude til at vurdere hvert jobopslag individuelt.
    Returnerer kun de opslag Claude vurderer som relevante.
    Behandler opslag i batches for at spare API-kald.
    """
    if not jobs:
        return []

    print(f"\nAI-filtrering med Claude ({len(jobs)} opslag)...")

    # Behandl i batches af 10 for at holde prompt-størrelsen nede
    BATCH_SIZE = 10
    approved = []

    for batch_start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[batch_start:batch_start + BATCH_SIZE]

        # Byg prompt med alle opslag i batchen
        job_list = ""
        for i, job in enumerate(batch):
            job_list += f"""
[{i+1}] Titel: {job.get('title', '')}
     Virksomhed: {job.get('company', '')}
     Lokation: {job.get('location', '')}
     Beskrivelse: {job.get('description', '')[:300] if job.get('description') else '(ingen beskrivelse)'}
"""

        prompt = f"""{CANDIDATE_PROFILE}

Nedenfor er {len(batch)} jobopslag. Vurder for hvert opslag om det er relevant for kandidaten.

Regler:
- GODKEND: Entry-level/junior IT-stillinger inden for ledelse, konsulentarbejde, digitalisering, forretningsanalyse, projektkoordinering, IT-strategi, change management
- AFVIS: Stillinger der kræver 3+ års erfaring, senior/lead/chef-titler, rent tekniske udviklerstillinger (programmering/DevOps/infrastruktur), militær/forsvar-specialister, sundhedsfaglige, håndværk, ph.d.-krav, ufaglærte jobs
- VED TVIVL: Godkend hellere end afvis – det er bedre at sende et ekstra opslag end at misse et godt match

Svar KUN med en JSON-liste med numrene på de GODKENDTE opslag, f.eks.: [1, 3, 5]
Ingen forklaring, ingen tekst – kun JSON-listen.

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
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )

            if response.status_code == 200:
                content = response.json()["content"][0]["text"].strip()
                # Parse JSON-listen
                m = re.search(r'\[[\d,\s]*\]', content)
                if m:
                    approved_indices = json.loads(m.group())
                    for idx in approved_indices:
                        if 1 <= idx <= len(batch):
                            job = batch[idx - 1]
                            job["ai_approved"] = True
                            approved.append(job)
                    rejected = len(batch) - len([i for i in approved_indices if 1 <= i <= len(batch)])
                    print(f"  Batch {batch_start//BATCH_SIZE + 1}: {len(approved_indices)} godkendt, {rejected} afvist af Claude")
                else:
                    # Kan ikke parse svar – behold alle i batchen
                    print(f"  Batch {batch_start//BATCH_SIZE + 1}: Kunne ikke parse Claude-svar, beholder alle")
                    approved.extend(batch)
            else:
                print(f"  Claude API fejl {response.status_code}: {response.text[:200]}")
                # Fallback: behold alle ved API-fejl
                approved.extend(batch)

        except Exception as e:
            print(f"  Claude fejl: {e} – beholder alle opslag i batchen")
            approved.extend(batch)

        time.sleep(0.5)  # Respekter rate limits

    print(f"AI-filtrering færdig: {len(approved)}/{len(jobs)} opslag godkendt")
    return approved


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
    "IT konsulent",
    "digitaliseringskonsulent",
    "forretningsanalytiker",
    "IT koordinator",
    "business analyst",
    "digital transformation",
    "IT graduate",
    "change management konsulent",
    "systemkonsulent",
]

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
    print("  ADVARSEL: Ingen resultater fundet i Stash. Sidens struktur kan være ændret igen.")
    return []


def _html_to_text(raw: str, limit: int = 500) -> str:
    text = re.sub(r'<[^>]+>', ' ', raw or "")
    return re.sub(r'\s+', ' ', text).strip()[:limit]

def scrape_jobindex(search_term: str) -> list[dict]:
    """Scraper Jobindex via Stash JSON variabel embedded i HTML.
    Jobindex bruger /jobsoegning/storkoebenhavn?q=TERM og embedder jobdata i siden."""
    jobs = []
    encoded = quote_plus(search_term)
    url = f"https://www.jobindex.dk/jobsoegning/storkoebenhavn?q={encoded}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return jobs
        html = resp.text
        m = re.search(r'var Stash = ({.*?});\s*//\]\]>', html, re.DOTALL)
        if not m:
            return jobs
        stash = json.loads(m.group(1))
        results = _stash_results(stash)
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
                "source":      "Jobindex",
                "category":    search_term,
                "description": _html_to_text(job.get("html", "")),
            })
    except Exception as e:
        print(f"  [Jobindex] Fejl ved '{search_term}': {e}")
    return jobs


def scrape_itjobbank(category_slug: str, location: str, category_name: str) -> list[dict]:
    """Scraper IT Jobbank – jobdata er embedded som JSON i HTML."""
    jobs = []
    location_encoded = quote_plus(location.lower())
    url = f"https://www.it-jobbank.dk/jobsoegning/{category_slug}?location={location_encoded}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text
        headlines  = re.findall(r'"headline":"([^"]+)"', html)
        share_urls = re.findall(r'"share_url":"([^"]+)"', html)
        companies  = re.findall(r'"companytext":"([^"]+)"', html)
        dates      = re.findall(r'"firstdate":"([^"]+)"', html)
        areas      = re.findall(r'"area":"([^"]+)"', html)
        descriptions = re.findall(r'"html":"((?:[^"\\]|\\.)*)"', html)
        for i, (headline, share_url) in enumerate(zip(headlines, share_urls)):
            clean_url = share_url.replace("\\/", "/")
            company   = (companies[i] if i < len(companies) else "").replace("\\/", "/")
            date      = dates[i] if i < len(dates) else ""
            area      = areas[i] if i < len(areas) else ""
            # Udtræk ren tekst fra HTML-beskrivelse
            desc_raw  = descriptions[i] if i < len(descriptions) else ""
            desc_text = re.sub(r'<[^>]+>', ' ', desc_raw.replace("\\n", " ").replace('\\"', '"'))
            desc_text = re.sub(r'\s+', ' ', desc_text).strip()[:500]
            jobs.append({
                "title":       headline,
                "company":     company or "Se opslag",
                "date":        date,
                "location":    area,
                "link":        clean_url,
                "source":      "IT Jobbank",
                "category":    category_name,
                "description": desc_text,
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
                "source":      "StepStone",
                "category":    category_name,
                "description": desc[:500],
            })
    except Exception as e:
        print(f"  [StepStone] Fejl ved '{category_name}': {e}")
    return jobs


# ─── Deduplicering ────────────────────────────────────────────────────────────

def deduplicate(jobs: list[dict]) -> list[dict]:
    seen_links, seen_titles, unique = set(), set(), []
    for job in jobs:
        link       = job.get("link", "").split("?")[0].rstrip("/")
        title_norm = re.sub(r'\s+', ' ', job.get("title", "").lower().strip())
        if link and link not in seen_links and title_norm not in seen_titles:
            seen_links.add(link)
            seen_titles.add(title_norm)
            unique.append(job)
    return unique


# ─── Hoved-scraping funktion ──────────────────────────────────────────────────

def collect_jobs() -> list[dict]:
    """Samler og filtrerer alle relevante jobopslag fra alle kilder."""
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

    # Deduplicering
    unique_jobs = deduplicate(all_jobs)
    print(f"Efter deduplicering: {len(unique_jobs)}")

    # Grov geografisk forfiltrering
    cph_jobs = [j for j in unique_jobs if is_copenhagen_area(j)]
    print(f"Efter geografisk filtrering (Storkøbenhavn): {len(cph_jobs)}")

    # Grov hard-exclude (åbenlyse ikke-IT opslag)
    pre_filtered = [j for j in cph_jobs if not hard_exclude(j)]
    print(f"Efter grov forfiltrering: {len(pre_filtered)}")

    # AI-filtrering med Claude
    api_key = get_anthropic_key()
    if api_key:
        final_jobs = ai_filter_jobs(pre_filtered, api_key)
    else:
        print("Ingen Anthropic API-nøgle – springer AI-filtrering over")
        final_jobs = pre_filtered

    final_jobs.sort(key=lambda j: (
        {"IT Jobbank": 0, "Jobindex": 1, "StepStone": 2}.get(j["source"], 3),
        j.get("date", ""),
        j["title"]
    ))
    return final_jobs


# ─── E-mail funktioner ────────────────────────────────────────────────────────

def format_date(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str[:10])
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return date_str[:10]


def build_html_email(jobs: list[dict]) -> str:
    today          = datetime.now().strftime("%d. %B %Y")
    job_count      = len(jobs)
    itjobbank_jobs = [j for j in jobs if j["source"] == "IT Jobbank"]
    jobindex_jobs  = [j for j in jobs if j["source"] == "Jobindex"]
    jobnet_jobs    = [j for j in jobs if j["source"] == "Jobnet"]
    stepstone_jobs = [j for j in jobs if j["source"] == "StepStone"]

    source_colors = {
        "IT Jobbank": ("#0066cc", "#e3f2fd"),
        "Jobindex":   ("#e65100", "#fff3e0"),
        "StepStone":  ("#c0392b", "#fdecea"),
        "Jobnet":     ("#2e7d32", "#e8f5e9"),
    }

    def job_card(job: dict) -> str:
        title    = job.get("title", "")
        company  = job.get("company", "")
        location = job.get("location", "")
        date     = format_date(job.get("date", ""))
        link     = job.get("link", "")
        category = job.get("category", "")
        source   = job.get("source", "")
        color, bg = source_colors.get(source, ("#555", "#f5f5f5"))
        location_html = f'<span style="color:#666;font-size:13px;">📍 {location}</span>' if location else ""
        date_html     = f'<span style="color:#666;font-size:13px;margin-left:12px;">📅 {date}</span>' if date else ""
        ai_badge      = '<span style="background:#f3e5f5;color:#7b1fa2;font-size:10px;font-weight:600;padding:2px 7px;border-radius:10px;margin-left:6px;">✓ AI-godkendt</span>'
        return f"""
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:16px 20px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
                <div style="flex:1;min-width:200px;">
                    <a href="{link}" style="color:#1a1a2e;text-decoration:none;font-size:16px;font-weight:600;line-height:1.4;">{title}</a>
                    <div style="color:#444;font-size:14px;margin-top:4px;font-weight:500;">{company}{ai_badge}</div>
                    <div style="margin-top:6px;">{location_html}{date_html}</div>
                </div>
                <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;">
                    <span style="background:{bg};color:{color};font-size:11px;font-weight:600;padding:3px 8px;border-radius:12px;white-space:nowrap;">{source}</span>
                    <span style="background:#f5f5f5;color:#555;font-size:11px;padding:3px 8px;border-radius:12px;white-space:nowrap;">{category}</span>
                </div>
            </div>
            <div style="margin-top:12px;">
                <a href="{link}" style="display:inline-block;background:#1a1a2e;color:#fff;text-decoration:none;padding:7px 16px;border-radius:6px;font-size:13px;font-weight:500;">Se opslag →</a>
            </div>
        </div>"""

    def section(title: str, jobs_list: list[dict], color: str) -> str:
        if not jobs_list:
            return ""
        cards = "".join(job_card(j) for j in jobs_list)
        return f"""
        <div style="margin-bottom:30px;">
            <h2 style="color:{color};font-size:18px;font-weight:700;margin:0 0 16px 0;padding-bottom:8px;border-bottom:2px solid {color};">
                {title} <span style="font-size:14px;font-weight:400;color:#666;">({len(jobs_list)} opslag)</span>
            </h2>
            {cards}
        </div>"""

    no_jobs_html = """
    <div style="text-align:center;padding:40px;color:#666;">
        <p style="font-size:16px;">Ingen nye relevante jobopslag denne uge.</p>
        <p style="font-size:14px;">Søgningen kører igen næste mandag.</p>
    </div>""" if job_count == 0 else ""

    return f"""<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ugentlig Job Alert – IT-ledelse</title>
</head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:20px;">
  <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);border-radius:12px 12px 0 0;padding:32px 32px 28px;text-align:center;">
    <div style="font-size:28px;margin-bottom:8px;">💼</div>
    <h1 style="color:#fff;font-size:22px;font-weight:700;margin:0 0 6px 0;">Ugentlig Job Alert</h1>
    <p style="color:#a8b8d8;font-size:14px;margin:0;">IT-ledelse, cand.it · Storkøbenhavn · {today}</p>
    <p style="color:#7b9cc8;font-size:12px;margin:6px 0 0 0;">✦ Filtreret med Claude AI</p>
  </div>
  <div style="background:#fff;border-left:1px solid #e0e0e0;border-right:1px solid #e0e0e0;padding:16px 32px;">
    <div style="display:flex;gap:16px;flex-wrap:wrap;">
      <div style="text-align:center;flex:1;min-width:80px;">
        <div style="font-size:26px;font-weight:700;color:#7b1fa2;">{job_count}</div>
        <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:0.5px;">AI-godkendte</div>
      </div>
      <div style="text-align:center;flex:1;min-width:80px;">
        <div style="font-size:26px;font-weight:700;color:#0066cc;">{len(itjobbank_jobs)}</div>
        <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:0.5px;">IT Jobbank</div>
      </div>
      <div style="text-align:center;flex:1;min-width:80px;">
        <div style="font-size:26px;font-weight:700;color:#e65100;">{len(jobindex_jobs)}</div>
        <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:0.5px;">Jobindex</div>
      </div>
      <div style="text-align:center;flex:1;min-width:80px;">
        <div style="font-size:26px;font-weight:700;color:#c0392b;">{len(stepstone_jobs)}</div>
        <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:0.5px;">StepStone</div>
      </div>
    </div>
    <p style="color:#888;font-size:12px;margin:8px 0 0 0;text-align:center;">
      Kun nye opslag · AI-vurderet relevant for dig · Storkøbenhavn
    </p>
  </div>
  <div style="background:#f8f9fb;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 12px 12px;padding:28px 32px;">
    {no_jobs_html}
    {section("IT Jobbank", itjobbank_jobs, "#0066cc")}
    {section("Jobindex", jobindex_jobs, "#e65100")}
    {section("StepStone", stepstone_jobs, "#c0392b")}
    <div style="background:#fff;border:1px solid #e8e8e8;border-radius:8px;padding:16px 20px;margin-top:20px;">
      <p style="color:#666;font-size:13px;margin:0 0 8px 0;font-weight:600;">Om denne job alert:</p>
      <p style="color:#888;font-size:12px;margin:0;line-height:1.6;">
        Sendes automatisk hver mandag. Hvert opslag er individuelt vurderet af <strong>Claude AI</strong>
        som relevant for en nyuddannet kandidat i IT-ledelse (cand.it) fra AAU.
        Opslag du allerede har fået tilsendt vises ikke igen.
        Søgningen dækker IT Jobbank, Jobindex og StepStone i Storkøbenhavn-området.
      </p>
    </div>
  </div>
  <div style="text-align:center;padding:20px;color:#aaa;font-size:12px;">
    <p style="margin:0;">Automatisk genereret af Job Alert · {today}</p>
  </div>
</div>
</body>
</html>"""


def build_plain_text_email(jobs: list[dict]) -> str:
    today = datetime.now().strftime("%d. %B %Y")
    lines = [
        "UGENTLIG JOB ALERT – IT-ledelse, cand.it",
        f"Storkøbenhavn · {today} · Filtreret med Claude AI",
        "=" * 50,
        f"{len(jobs)} AI-godkendte jobopslag (kun nye opslag)",
        "",
    ]
    for job in jobs:
        lines.append(f"TITEL:      {job.get('title', '')}")
        lines.append(f"VIRKSOMHED: {job.get('company', '')}")
        if job.get("location"):
            lines.append(f"LOKATION:   {job.get('location', '')}")
        if job.get("date"):
            lines.append(f"DATO:       {format_date(job.get('date', ''))}")
        lines.append(f"KILDE:      {job.get('source', '')}")
        lines.append(f"LINK:       {job.get('link', '')}")
        lines.append("-" * 40)
    return "\n".join(lines)


def send_email(jobs: list[dict]) -> bool:
    today   = datetime.now().strftime("%d. %B %Y")
    subject = f"Job Alert: {len(jobs)} AI-godkendte IT-stillinger i KBH – {today}"

    html_body  = build_html_email(jobs)
    plain_body = build_plain_text_email(jobs)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Job Alert <{SENDER_EMAIL}>"
    msg["To"]      = RECIPIENT_EMAIL

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body,  "html",  "utf-8"))

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

def run_job_alert() -> None:
    """
    Kører den komplette job alert:
    1. Scraper jobopslag fra IT Jobbank, Jobnet og StepStone
    2. Grov geografisk filtrering (Storkøbenhavn)
    3. Claude AI vurderer hvert opslag individuelt
    4. Fjerner opslag der allerede er sendt tidligere
    5. Sender e-mail med kun NYE, AI-godkendte opslag
    6. Gemmer de sendte opslag så de ikke sendes igen
    """
    all_relevant = collect_jobs()

    seen     = load_seen_jobs()
    new_jobs = filter_new_jobs(all_relevant, seen)

    print(f"\nTidligere sendte opslag: {len(seen)}")
    print(f"Nye opslag (ikke set før): {len(new_jobs)}")

    print(f"\nSender e-mail til {RECIPIENT_EMAIL}...")
    success = send_email(new_jobs)

    if success and new_jobs:
        new_links = {job.get("link", "").split("?")[0].rstrip("/") for job in new_jobs if job.get("link")}
        seen.update(new_links)
        save_seen_jobs(seen)
        print(f"Gemt {len(new_links)} nye opslag i hukommelsen.")


if __name__ == "__main__":
    run_job_alert()
