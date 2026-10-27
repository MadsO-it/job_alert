"""
Karrieresider
Henter opslag direkte fra udvalgte arbejdsgiveres egne karrieresider (deres ATS),
så de også fanges hvis de ikke ligger på Jobindex / IT Jobbank / StepStone.

Brug:
    import career_sites
    jobs = career_sites.scrape_career_sites()

Hver arbejdsgiver er en config i EMPLOYERS (nederst). En ny arbejdsgiver tilføjes ved at
finde dens ATS og tilføje en linje med den rigtige "type" og parametre. Understøttede typer:

    workday       POST https://<host>/wday/cxs/<tenant>/<site>/jobs (Danmark-facet findes automatisk)
    oracle        Oracle Recruiting Cloud (hcmRestApi recruitingCEJobRequisitions)
    rmk           SAP SuccessFactors "Career Site Builder", klassisk HTML-søgeside (/search/)
    sf_csb        SAP SuccessFactors Career Site Builder, JSON-service (/services/recruiting/v1/jobs)
    smartrecruiters  api.smartrecruiters.com/v1/companies/<id>/postings
    cornerstone   Cornerstone OnDemand (<corp>.csod.com), token hentes fra karrieresiden
    easycruit     <kunde>.easycruit.com/export/xml/vacancy/list.xml
    hrmanager     api.hr-manager.net/jobportal.svc/<kunde>/positionlist/json/
    talentsoft    <kunde>-career.talent-soft.com/job/list-of-all-jobs.aspx (HTML)
    phenom        Phenom People karriereside (POST /widgets)
    + nogle enkelte specialtilfælde (nordea, nykredit, pfa, atp, lundbeck, valcon, netcompany)

Returnerer opslag som dicts med nøglerne:
    title, company, location, link, date ("YYYY-MM-DD" eller ""), source ("Karriereside"),
    category (arbejdsgiverens navn), description ("" eller kort tekstuddrag)

Kun afhængighed: requests.
"""

import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

# ─── Fælles indstillinger ─────────────────────────────────────────────────────

SOURCE = "Karriereside"
MAX_PER_EMPLOYER = 50
TIMEOUT = 20
SLEEP_BETWEEN_EMPLOYERS = 1.0
DESCRIPTION_LIMIT = 300

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}
JSON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Content-Type": "application/json",
    # Engelsk sprog giver forudsigelige datoformater ("Posted Yesterday" osv.)
    "Accept-Language": "en-US,en;q=0.8",
}

# Ord der markerer at en lokation ligger i Danmark (bruges når API'et ikke kan filtrere selv).
DK_WORDS = (
    "denmark", "danmark", ", dk", " dk,", "københavn", "kobenhavn", "copenhagen", "aarhus",
    "århus", "aalborg", "odense", "ballerup", "hillerød", "hellerup", "lyngby", "taastrup",
    "kalundborg", "bagsværd", "måløv", "søborg", "gentofte", "frederiksberg", "fredericia",
    "esbjerg", "kolding", "vejle", "horsens", "roskilde", "køge", "herlev", "glostrup",
)


class SiteUnavailable(Exception):
    """Karrieresiden svarede, men ikke med data (fx Workday-vedligehold)."""


# ─── Hjælpefunktioner ─────────────────────────────────────────────────────────

def _clean(text) -> str:
    """HTML → ren tekst på én linje."""
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = html.unescape(html.unescape(text))  # nogle kilder er dobbelt-escaped
    return re.sub(r"\s+", " ", text).strip()


def _snippet(text) -> str:
    text = _clean(text)
    if len(text) > DESCRIPTION_LIMIT:
        text = text[:DESCRIPTION_LIMIT].rsplit(" ", 1)[0] + " …"
    return text


def _is_dk(location: str) -> bool:
    loc = f" {(location or '').lower()} "
    return any(w in loc for w in DK_WORDS)


def _iso(value) -> str:
    """Forsøger at lave diverse datoformater om til YYYY-MM-DD."""
    if not value:
        return ""
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"/Date\((\d+)", s)  # .NET JSON-dato
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    for fmt in ("%d %b %Y", "%b %d, %Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _workday_date(posted_on: str) -> str:
    """Workday giver relative datoer: 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago',
    'Posted 30+ Days Ago'. Det sidste kan ikke omregnes præcist og giver ''."""
    s = (posted_on or "").lower()
    today = date.today()
    if "today" in s or "i dag" in s:
        return today.isoformat()
    if "yesterday" in s or "i går" in s:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*(?:days?|dage)", s)
    if m and "+" not in s:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return ""


def _job(cfg, title, location, link, date_str="", description="") -> dict:
    return {
        "title":       _clean(title),
        "company":     cfg.get("company", cfg["name"]),
        "location":    _clean(location),
        "link":        link,
        "date":        date_str or "",
        "source":      SOURCE,
        "category":    cfg["name"],
        "description": _snippet(description),
    }


def _get(url, **kw):
    r = requests.get(url, headers=kw.pop("headers", HEADERS), timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _post_json(url, body, session=None, headers=None):
    h = dict(JSON_HEADERS, **(headers or {}))
    r = (session or requests).post(url, json=body, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    if "json" not in r.headers.get("content-type", ""):
        title = re.search(r"<title>(.*?)</title>", r.text, re.S)
        raise SiteUnavailable(_clean(title.group(1)) if title else "svar var ikke JSON")
    return r.json()


# ─── Workday ──────────────────────────────────────────────────────────────────

def _workday_dk_facet(facets) -> tuple[str, str] | None:
    """Finder (facetParameter, id) for Danmark. Facetter kan være indlejret
    (fx locationMainGroup → locationCountry)."""
    for f in facets or []:
        for v in f.get("values", []):
            if "facetParameter" in v:
                hit = _workday_dk_facet([v])
                if hit:
                    return hit
            elif (v.get("descriptor") or "").strip().lower() in ("denmark", "danmark"):
                return f["facetParameter"], v["id"]
    return None


def scrape_workday(cfg) -> list[dict]:
    host, tenant, site = cfg["host"], cfg["tenant"], cfg["site"]
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

    first = _post_json(url, body)
    facet = _workday_dk_facet(first.get("facets"))
    if facet:
        body["appliedFacets"] = {facet[0]: [facet[1]]}

    jobs, offset = [], 0
    while len(jobs) < MAX_PER_EMPLOYER:
        body["offset"] = offset
        data = first if (offset == 0 and not facet) else _post_json(url, body)
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for p in postings:
            location = p.get("locationsText", "")
            # Uden Danmark-facet filtreres på lokationsteksten ("2 Locations" o.l. beholdes ikke)
            if not facet and not _is_dk(location):
                continue
            jobs.append(_job(
                cfg, p.get("title"), location,
                f"https://{host}/en-US/{site}{p.get('externalPath', '')}",
                _workday_date(p.get("postedOn")),
            ))
        offset += len(postings)
        if offset >= data.get("total", 0) and data.get("total", 0):
            break
    return jobs


# ─── Oracle Recruiting Cloud ──────────────────────────────────────────────────

def scrape_oracle(cfg) -> list[dict]:
    host, site = cfg["host"], cfg["site"]
    facet_id = cfg.get("dk_location_id")
    base = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            f"?onlyData=true&expand=requisitionList.secondaryLocations"
            f"&finder=findReqs;siteNumber={site},facetsList=LOCATIONS,limit=50,"
            f"sortBy=POSTING_DATES_DESC")
    if facet_id:
        base += f",selectedLocationsFacet={facet_id}"

    jobs, offset = [], 0
    max_pages = 2 if facet_id else 8  # uden facet skal hele listen gennemløbes og filtreres
    for _ in range(max_pages):
        data = _get(f"{base},offset={offset}", headers=JSON_HEADERS).json()
        item = (data.get("items") or [{}])[0]
        reqs = item.get("requisitionList", [])
        for r in reqs:
            locs = [r.get("PrimaryLocation", "")] + [
                s.get("Name", "") for s in r.get("secondaryLocations", []) or []]
            dk_locs = [l for l in locs if _is_dk(l)]
            if not dk_locs:
                continue
            jobs.append(_job(
                cfg, r.get("Title"), "; ".join(dict.fromkeys(dk_locs)),
                f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r.get('Id')}",
                _iso(r.get("PostedDate")), r.get("ShortDescriptionStr", ""),
            ))
        offset += len(reqs)
        if not reqs or offset >= item.get("TotalJobsCount", 0) or len(jobs) >= MAX_PER_EMPLOYER:
            break
    return jobs


# ─── SuccessFactors: klassisk RMK-søgeside (HTML) ─────────────────────────────

def scrape_rmk(cfg) -> list[dict]:
    base, query = cfg["base"], cfg.get("query", "?q=")
    jobs, seen, startrow = [], set(), 0
    for _ in range(10):
        page = _get(f"{base}/search/{query}&startrow={startrow}").text
        rows = re.findall(r'<tr class="data-row">(.*?)</tr>', page, re.S)
        if not rows:
            break
        new = 0
        for row in rows:
            m = re.search(r'href="([^"]+)" class="jobTitle-link">([^<]+)', row)
            if not m:
                continue
            link = base + html.unescape(m.group(1))
            if link in seen:
                continue
            seen.add(link)
            new += 1
            loc = re.search(r'class="jobLocation"[^>]*>\s*([^<]+)', row)
            dt = re.search(r'class="jobDate"[^>]*>\s*([^<]+)', row)
            location = loc.group(1).strip() if loc else ""
            if cfg.get("filter_dk") and not _is_dk(location):
                continue
            jobs.append(_job(cfg, m.group(2), location, link,
                             _iso(dt.group(1).strip()) if dt else ""))
        startrow += len(rows)
        total = re.search(r'paginationLabel[^>]*>.*?of\s*<b>(\d+)</b>', page, re.S)
        if new == 0 or len(jobs) >= MAX_PER_EMPLOYER or (total and startrow >= int(total.group(1))):
            break
    return jobs


# ─── SuccessFactors Career Site Builder (JSON-service) ────────────────────────

def scrape_sf_csb(cfg) -> list[dict]:
    """Nyere SuccessFactors-sider: opslag returneres kun for det sprog (locale) de er
    oprettet på, så der spørges på hver locale i cfg['locales']."""
    base = cfg["base"]
    session = requests.Session()
    session.headers.update(HEADERS)
    page = session.get(f"{base}/search/?q=", timeout=TIMEOUT).text
    csrf = re.search(r'var CSRFToken\s*=\s*"([^"]+)"', page)
    extra = {"X-CSRF-Token": csrf.group(1)} if csrf else {}

    jobs, seen = [], set()
    for locale in cfg.get("locales", ["en_US"]):
        for page_no in range(10):
            body = {"locale": locale, "pageNumber": page_no, "sortBy": "", "keywords": "",
                    "location": cfg.get("location", ""), "facetFilters": {}, "brand": "",
                    "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""}
            data = _post_json(f"{base}/services/recruiting/v1/jobs", body, session, extra)
            results = data.get("jobSearchResult", [])
            for res in results:
                r = res.get("response", {})
                location = "; ".join(x.strip() for x in r.get("jobLocationShort", []) if x)
                if r.get("id") in seen or not _is_dk(location):
                    continue
                seen.add(r.get("id"))
                jobs.append(_job(
                    cfg, r.get("unifiedStandardTitle"), location,
                    f"{base}/job/{r.get('unifiedUrlTitle') or r.get('urlTitle')}/{r.get('id')}-{locale}/",
                    _iso(r.get("unifiedStandardStart")),
                ))
            if len(results) < 10 or len(jobs) >= MAX_PER_EMPLOYER:
                break
    return jobs


# ─── SmartRecruiters ──────────────────────────────────────────────────────────

def scrape_smartrecruiters(cfg) -> list[dict]:
    company = cfg["company_id"]
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                params={"limit": 100, "country": "dk"}, headers=JSON_HEADERS).json()
    jobs = []
    for p in data.get("content", []):
        loc = p.get("location", {}) or {}
        location = ", ".join(x for x in (loc.get("city"), "Denmark") if x)
        jobs.append(_job(cfg, p.get("name"), location,
                         f"https://jobs.smartrecruiters.com/{company}/{p.get('id')}",
                         _iso(p.get("releasedDate"))))
    return jobs


# ─── Cornerstone OnDemand ─────────────────────────────────────────────────────

def scrape_cornerstone(cfg) -> list[dict]:
    corp, site = cfg["corp"], cfg["site_id"]
    page = _get(f"https://{corp}.csod.com/ux/ats/careersite/{site}/home?c={corp}").text
    token = re.search(r'"token":"([^"]+)"', page)
    cloud = re.search(r'"cloud":"([^"]+)"', page)
    if not token or not cloud:
        raise SiteUnavailable("token ikke fundet på karrieresiden")
    body = {"careerSiteId": site, "careerSitePageId": site, "pageNumber": 1, "pageSize": 100,
            "cultureId": 2, "searchText": "", "cultureName": "en-GB", "states": [],
            "countryCodes": [], "cities": [], "placeID": "", "radius": None,
            "postingsWithinDays": None, "customFieldCheckboxKeys": [],
            "customFieldDropdowns": [], "customFieldRadios": []}
    data = _post_json(cloud.group(1) + "rec-job-search/external/jobs", body,
                      headers={"Authorization": "Bearer " + token.group(1)})
    jobs = []
    for r in (data.get("data") or {}).get("requisitions", []):
        locs = r.get("locations") or []
        if not any((l.get("country") or "").upper() == "DK" for l in locs):
            continue
        title = _clean(r.get("displayJobTitle"))
        if title.startswith("<<"):  # ufærdige skabelon-opslag ("<<ENTER DISPLAY JOB TITLE>>")
            continue
        location = "; ".join(
            ", ".join(x for x in (l.get("city"), "Denmark") if x)
            for l in locs if (l.get("country") or "").upper() == "DK")
        jobs.append(_job(
            cfg, title, location,
            f"https://{corp}.csod.com/ux/ats/careersite/{site}/home/requisition/"
            f"{r.get('requisitionId')}?c={corp}",
            _iso(r.get("postingEffectiveDate")), r.get("externalDescription", ""),
        ))
    return jobs


# ─── Easycruit ────────────────────────────────────────────────────────────────

def scrape_easycruit(cfg) -> list[dict]:
    ns = {"e": "urn:EasyCruit"}
    xml = _get(f"https://{cfg['customer']}.easycruit.com/export/xml/vacancy/list.xml").content
    root = ET.fromstring(xml)
    jobs = []
    for v in root.findall("e:Vacancy", ns):
        ver = v.find("e:Versions/e:Version", ns)
        if ver is None:
            continue
        country = ver.find("e:Region/e:Country", ns)
        if country is not None and country.get("name", "").lower() not in ("danmark", "denmark"):
            continue
        url = v.findtext("e:Departments/e:Department/e:VacancyURL", default="", namespaces=ns)
        jobs.append(_job(
            cfg, ver.findtext("e:Title", default="", namespaces=ns),
            ver.findtext("e:Location", default="", namespaces=ns),
            url or f"https://{cfg['customer']}.easycruit.com/vacancy/{v.get('id')}",
            _iso(v.get("date_start")),
            ver.findtext("e:Appetizer", default="", namespaces=ns),
        ))
    return jobs


# ─── HR-Manager ───────────────────────────────────────────────────────────────

def scrape_hrmanager(cfg) -> list[dict]:
    data = _get(f"https://api.hr-manager.net/jobportal.svc/{cfg['customer']}/positionlist/json/",
                headers=JSON_HEADERS).json()
    jobs = []
    for p in data.get("Items", []):
        loc = p.get("PositionLocation") or {}
        url = p.get("AdvertisementUrlSecure") or p.get("AdvertisementUrl") or ""
        # Opslags-id'et står kun i query-strengen; job_alert.py fjerner query ved dublet-tjek,
        # så id'et lægges også ind som PATH_INFO (ApplicationInit.aspx/<id>?...), som HR-Manager accepterer.
        url = url.replace("ApplicationInit.aspx?", f"ApplicationInit.aspx/{p.get('Id')}?", 1)
        jobs.append(_job(cfg, p.get("Name"), loc.get("Name", ""), url,
                         _iso(p.get("Created")), p.get("Teaser") or ""))
    return jobs


# ─── Talentsoft ───────────────────────────────────────────────────────────────

def scrape_talentsoft(cfg) -> list[dict]:
    base = f"https://{cfg['host']}"
    jobs, seen = [], set()
    for page_no in range(1, 8):
        page = _get(f"{base}/job/list-of-all-jobs.aspx?page={page_no}&LCID=1030").text
        cards = re.findall(
            r'<a\s+class="ts-offer-card__title-link[^"]*"\s+href="([^"]+)"[^>]*>(.*?)</a>(.*?)</ul>',
            page, re.S)
        new = 0
        for href, title, rest in cards:
            link = base + html.unescape(href)
            if link in seen:
                continue
            seen.add(link)
            new += 1
            items = [_clean(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", rest, re.S)]
            date_str = next((_iso(x) for x in items if _iso(x)), "")
            jobs.append(_job(cfg, title, cfg.get("default_location", ""), link, date_str))
        if new == 0 or f"page={page_no + 1}&" not in page.replace("&amp;", "&"):
            break
    return jobs


# ─── Phenom People ────────────────────────────────────────────────────────────

def scrape_phenom(cfg) -> list[dict]:
    base = cfg["base"]  # fx https://careers.genmab.com
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(f"{base}/{cfg['path']}/search-results", timeout=TIMEOUT)
    body = {"lang": cfg["lang"], "deviceType": "desktop", "country": cfg["country"],
            "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "Most recent",
            "subsearch": "", "from": 0, "jobs": True, "counts": True, "all_fields": ["country"],
            "size": MAX_PER_EMPLOYER, "clearAll": False, "jdsource": "facets",
            "isSliderEnable": False, "pageId": "page20", "siteType": "external", "keywords": "",
            "global": True, "selected_fields": {"country": ["Denmark"]}, "locationData": {}}
    data = _post_json(f"{base}/widgets", body, session)
    jobs = []
    for j in data.get("refineSearch", {}).get("data", {}).get("jobs", []):
        locs = j.get("multi_location") or [j.get("location", "")]
        dk_locs = [l for l in locs if _is_dk(l)]
        if not dk_locs:
            continue
        jobs.append(_job(cfg, j.get("title"), "; ".join(dk_locs),
                         f"{base}/{cfg['path']}/job/{j.get('jobId')}",
                         _iso(j.get("postedDate")), j.get("descriptionTeaser", "")))
    return jobs


# ─── Specialtilfælde (egne CMS-løsninger) ─────────────────────────────────────

def scrape_nordea(cfg) -> list[dict]:
    """Nordea.com har eget JSON-endpoint; 10 pr. side; Danmark = location_tid 1331."""
    jobs = []
    for page_no in range(6):
        data = _get("https://www.nordea.com/en/api/jobs-list",
                    params={"f[0]": "location_tid:1331", "page": page_no},
                    headers=JSON_HEADERS).json()
        results = data.get("results") or []
        for r in results:
            link = html.unescape(html.unescape(r.get("field_ad_url") or "")) or r.get("url", "")
            jobs.append(_job(cfg, r.get("title"), r.get("location_name", ""),
                             link.split("?")[0], _iso(r.get("created"))))
        if len(results) < 10 or len(jobs) >= MAX_PER_EMPLOYER:
            break
    return jobs


def scrape_nykredit(cfg) -> list[dict]:
    """Nykredit.com indlejrer hele listen som JSON i data-model. Selve opslaget ligger på
    Talentsoft, hvor URL-slug er valgfrit; kun tallet efter '_' bruges."""
    page = _get("https://www.nykredit.com/karriere/stillinger/").text
    i = page.find("data-api-url")
    m = re.search(r'data-model="([^"]*)"', page[i:]) if i >= 0 else None
    if not m:
        raise SiteUnavailable("data-model ikke fundet")
    data = json.loads(html.unescape(m.group(1))).get("data", {})
    jobs = []
    for p in data.get("jobPostingsList", []):
        ref = p.get("reference", "")
        num = ref.split("-")[-1]
        slug = re.sub(r"[^a-z0-9]+", "-", _clean(p.get("title")).lower()).strip("-")[:80]
        jobs.append(_job(cfg, p.get("title"), p.get("city", ""),
                         f"https://nykredit-career.talent-soft.com/job/job-{slug}_{num}.aspx"))
    return jobs


def scrape_pfa(cfg) -> list[dict]:
    """PFA's egen side lister opslagene; selve opslaget ligger i SuccessFactors.
    Linket får job-id'et i stien, så dublet-tjekket i job_alert.py kan skelne opslagene."""
    page = _get("https://www.pfa.dk/om-pfa/job-i-pfa/ledige-job/").text
    jobs = []
    for item in page.split('<li class="jobs-listing__list-item')[1:]:
        title = re.search(r"<button[^>]*>(.*?)</button>", item, re.S)
        req = re.search(r"career_job_req_id=(\d+)", item)
        if not title or not req:
            continue
        content = re.search(r'class="inside">(.*?)</div>', item, re.S)
        jobs.append(_job(
            cfg, title.group(1), cfg.get("default_location", ""),
            f"https://career2.successfactors.eu/sfcareer/jobreqcareer/{req.group(1)}"
            f"?jobId={req.group(1)}&company=pfapensionP",
            "", content.group(1) if content else ""))
    return jobs


def scrape_atp(cfg) -> list[dict]:
    """ATP.dk: server-renderet HTML-liste (kun ansøgningsfrist, ingen opslagsdato)."""
    page = _get("https://www.atp.dk/karriere/ledige-stillinger").text
    jobs = []
    for row in page.split('class="views-row"')[1:]:
        href = re.search(r'href="([^"]+)"', row)
        title = re.search(r'field--name-title[^>]*>(.*?)</span>', row, re.S)
        place = re.search(r'Sted</div>\s*<div class="field__items?">\s*(?:<div class="field__item">)?([^<]+)', row)
        if not href or not title:
            continue
        jobs.append(_job(cfg, title.group(1), place.group(1) if place else "",
                         "https://www.atp.dk" + href.group(1)))
    return jobs


def scrape_lundbeck(cfg) -> list[dict]:
    """Lundbeck.com: AEM-komponent med JSON-model (ingen dato)."""
    data = _get("https://www.lundbeck.com/content/lundbeck-com/global/global-site/en/careers/"
                "your-job/job-opportunities/jcr:content/root/responsivegrid/responsivegrid/"
                "jobfinder.model.json", headers=JSON_HEADERS).json()
    return [_job(cfg, j.get("title"), j.get("location", ""), j.get("link", ""))
            for j in data.get("items", []) if (j.get("country") or "").lower() == "denmark"]


def scrape_valcon(cfg) -> list[dict]:
    """Valcon.com (WordPress): custom post type 'careers', taxonomy 'countries' (Danmark = 96)."""
    data = _get("https://valcon.com/wp-json/wp/v2/careers",
                params={"countries": 96, "per_page": MAX_PER_EMPLOYER,
                        "_fields": "date,link,title,excerpt"},
                headers=JSON_HEADERS).json()
    return [_job(cfg, j["title"]["rendered"], "Denmark", j["link"],
                 _iso(j.get("date")), j.get("excerpt", {}).get("rendered", ""))
            for j in data]


def scrape_netcompany(cfg) -> list[dict]:
    """SDC er nu Netcompany Banking Services. Netcompanys liste filtreres på titel + dansk by."""
    page = _get("https://netcompany.com/careers/vacancies/").text
    jobs = []
    for href, title, city in re.findall(
            r'<a href="(/job/[^"]+)"[^>]*>\s*<h6>(.*?)</h6>\s*<p class="job-city[^"]*">(.*?)</p>',
            page, re.S):
        if not re.search(cfg["title_filter"], _clean(title), re.I) or not _is_dk(city):
            continue
        jobs.append(_job(cfg, title, city, "https://netcompany.com" + href))
    return jobs


# ─── Arbejdsgivere ────────────────────────────────────────────────────────────
# Rækkefølgen her er rækkefølgen i output. Tilføj en ny arbejdsgiver med en ny linje.
# "company" kan sættes hvis visningsnavnet skal afvige fra "name" (name bruges som category).

WD_DK = "49ab063f422741e2aef271de00efeac8"  # Workdays fælles id for Danmark (findes automatisk)

EMPLOYERS = [
    # Finans
    {"name": "Danske Bank",   "type": "oracle", "host": "ejqi.fa.ocs.oraclecloud.eu",
     "site": "CX_1001", "dk_location_id": "300000000294904"},
    {"name": "Nordea",        "type": "nordea"},
    {"name": "Nykredit",      "type": "nykredit"},
    {"name": "Saxo Bank",     "type": "workday", "host": "saxobank.wd3.myworkdayjobs.com",
     "tenant": "saxobank", "site": "CareeratSaxoBank"},
    {"name": "Lån & Spar Bank", "type": "easycruit", "customer": "lsb"},
    {"name": "SDC",           "type": "netcompany", "company": "Netcompany Banking Services (SDC)",
     "title_filter": r"banking services|\bSDC\b"},
    # Pension / forsikring
    {"name": "PFA",           "type": "pfa", "default_location": "København Ø"},
    {"name": "Velliv",        "type": "easycruit", "customer": "velliv"},
    {"name": "ATP",           "type": "atp"},
    {"name": "Tryg",          "type": "sf_csb", "base": "https://careers.tryg.com",
     "locales": ["da_DK", "en_US"]},
    {"name": "If Skadeforsikring", "type": "workday", "host": "if.wd502.myworkdayjobs.com",
     "tenant": "if", "site": "Careers"},
    {"name": "PensionDanmark", "type": "talentsoft", "host": "pensiondanmark-career.talent-soft.com",
     "default_location": "København Ø"},
    # Pharma / life science / medtech
    {"name": "Novonesis",     "type": "workday", "host": "novonesis.wd103.myworkdayjobs.com",
     "tenant": "novonesis", "site": "Novonesis_Careers"},
    {"name": "Lundbeck",      "type": "lundbeck"},
    {"name": "LEO Pharma",    "type": "rmk", "base": "https://jobs.leo-pharma.com",
     "query": "?q=&locationsearch=Denmark", "filter_dk": True},
    {"name": "Ambu",          "type": "sf_csb", "base": "https://jobs.ambu.com",
     "locales": ["en_GB"], "location": "Denmark"},
    {"name": "Genmab",        "type": "phenom", "base": "https://careers.genmab.com",
     "path": "global/en", "lang": "en_global", "country": "global"},
    {"name": "Zealand Pharma", "type": "workday", "host": "zealandpharma.wd3.myworkdayjobs.com",
     "tenant": "zealandpharma", "site": "External"},
    {"name": "Novo Nordisk",  "type": "rmk", "base": "https://careers.novonordisk.com",
     "query": "?q=&optionsFacetsDD_country=DK"},
    # Finansiel IT / betaling
    {"name": "Nexi/Nets",     "type": "oracle", "host": "fa-ewwx-saasfaprod1.fa.ocs.oraclecloud.com",
     "site": "CX_1", "dk_location_id": "300000000459853"},
    {"name": "SimCorp",       "type": "workday", "host": "simcorp.wd3.myworkdayjobs.com",
     "tenant": "simcorp", "site": "SimCorp_Jobs"},
    # IT / rådgivning / ingeniør
    {"name": "NNIT",          "type": "cornerstone", "corp": "nnit", "site_id": 1},
    {"name": "NNE",           "type": "cornerstone", "corp": "nne", "site_id": 4},
    {"name": "Implement Consulting Group", "type": "smartrecruiters",
     "company_id": "ImplementConsultingGroup"},
    {"name": "Valcon",        "type": "valcon"},
    {"name": "DNV",           "type": "oracle", "host": "ecyq.fa.em2.oraclecloud.com", "site": "CX_1"},
    {"name": "Systematic",    "type": "rmk", "base": "https://jobs.systematic.com",
     "query": "?q=&optionsFacetsDD_country=DK"},
    {"name": "Energinet",     "type": "hrmanager", "customer": "energinet"},
]

SCRAPERS = {
    "workday":         scrape_workday,
    "oracle":          scrape_oracle,
    "rmk":             scrape_rmk,
    "sf_csb":          scrape_sf_csb,
    "smartrecruiters": scrape_smartrecruiters,
    "cornerstone":     scrape_cornerstone,
    "easycruit":       scrape_easycruit,
    "hrmanager":       scrape_hrmanager,
    "talentsoft":      scrape_talentsoft,
    "phenom":          scrape_phenom,
    "nordea":          scrape_nordea,
    "nykredit":        scrape_nykredit,
    "pfa":             scrape_pfa,
    "atp":             scrape_atp,
    "lundbeck":        scrape_lundbeck,
    "valcon":          scrape_valcon,
    "netcompany":      scrape_netcompany,
}


# ─── Hovedfunktion ────────────────────────────────────────────────────────────

def _short_error(e: Exception) -> str:
    if isinstance(e, SiteUnavailable):
        return f"siden utilgængelig ({e})"[:120]
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    if isinstance(e, requests.Timeout):
        return "timeout"
    if isinstance(e, requests.ConnectionError):
        return "forbindelsesfejl"
    return f"{type(e).__name__}: {e}"[:120]


def scrape_career_sites(status: list | None = None) -> list[dict]:
    """Henter opslag fra alle arbejdsgivere i EMPLOYERS. En fejl hos én arbejdsgiver
    stopper aldrig de øvrige. Max MAX_PER_EMPLOYER opslag pr. arbejdsgiver.
    Hvis status er en liste, tilføjes én linje pr. arbejdsgiver:
    {"name", "count", "error"} (error er "" når kaldet lykkedes)."""
    all_jobs = []
    for i, cfg in enumerate(EMPLOYERS):
        if i:
            time.sleep(SLEEP_BETWEEN_EMPLOYERS)
        try:
            jobs = SCRAPERS[cfg["type"]](cfg)
            # Fjern dubletter inden for samme arbejdsgiver og skær ved loftet
            seen, unique = set(), []
            for j in jobs:
                if j["title"] and j["link"] and j["link"] not in seen:
                    seen.add(j["link"])
                    unique.append(j)
            unique = unique[:MAX_PER_EMPLOYER]
            print(f"  {cfg['name']}: {len(unique)} opslag")
            all_jobs.extend(unique)
            if status is not None:
                status.append({"name": cfg["name"], "count": len(unique), "error": ""})
        except Exception as e:  # noqa: BLE001 — én arbejdsgiver må aldrig vælte resten
            print(f"  {cfg['name']}: fejl {_short_error(e)}")
            if status is not None:
                status.append({"name": cfg["name"], "count": 0, "error": _short_error(e)})
    return all_jobs


if __name__ == "__main__":
    result = scrape_career_sites()
    print(f"I alt: {len(result)} opslag")
