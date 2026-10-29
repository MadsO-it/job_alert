"""
Tjekker om de opslag, Mads har gemt på resultatsiden, stadig er slået op.

Markeringerne ligger i resultatsidens database (samlingen "marks"), hvor siden selv
skriver dem. Den daglige kørsel henter dem med ArtifactData, kører dette script og
skriver resultatet tilbage som feltet "check" på hver markering. Siden skjuler et gemt
opslag, når check.state er "removed".

Brug:
  python check_saved.py <marks-mappe eller -fil> --out mark_updates.json

Input er ArtifactData-outputtet fra `list` på samlingen "marks": enten en mappe med én
JSON-fil pr. dokument (out_dir) eller én JSON-fil med listens resultat. Output er en
liste af ArtifactData-batch-skrivninger ({op, collection, doc_id, if_version, data}),
klar til at sende som `writes`.

Afgørelse pr. opslag:
  removed  siden svarer 404/410, siger at annoncen er udløbet/besat, eller viderestiller
           væk fra opslaget (fx til forsiden eller en fejlside)
  live     siden svarer normalt
  unknown  siden kunne ikke hentes eller svarede med en anden fejl; prøves igen næste dag
Workday- og Oracle-karrieresider renderes med JavaScript, så de tjekkes via deres JSON-API.
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}
TIMEOUT = 25

# Sætninger der betyder, at opslaget er lukket. Matches i sidens tekst uden <script>-blokke,
# så oversættelsesfiler i sidens JavaScript ikke giver falske udslag.
REMOVED_PHRASES = (
    "annoncen er udløbet",
    "stillingen er besat",
    "stillingen er ikke længere ledig",
    "jobbet er ikke længere",
    "this job is no longer available",
    "this job posting is no longer available",
    "job is no longer available",
    "no longer accepting applications",
    "this position has been filled",
    "the job you are looking for is no longer",
)


def _visible_text(page: str) -> str:
    page = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)).lower()


def _left_posting(original: str, final: str) -> bool:
    """True hvis en viderestilling endte et sted, der ikke længere er selve opslaget."""
    if "error" in final.lower():
        return True
    o = urlparse(original)
    ids = re.findall(r"\d{4,}", o.path + "?" + o.query)
    if ids:
        return not any(i in final for i in ids)
    return urlparse(final).path.strip("/") in ("", "index.html", "accueil.aspx")


def _check_workday(link: str) -> tuple[str, str]:
    u = urlparse(link)
    parts = [p for p in u.path.split("/") if p]
    if "job" not in parts:
        return _check_page(link)
    i = parts.index("job")
    if i == 0:
        return _check_page(link)
    site, rest = parts[i - 1], "/".join(parts[i + 1:])
    tenant = u.netloc.split(".")[0]
    r = requests.get(f"https://{u.netloc}/wday/cxs/{tenant}/{site}/job/{rest}",
                     headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT)
    if r.status_code in (404, 410):
        return "removed", "opslaget findes ikke længere på karrieresiden"
    if r.status_code == 200:
        return "live", ""
    return "unknown", f"karrieresiden svarede {r.status_code}"


def _check_oracle(link: str) -> tuple[str, str]:
    m = re.search(r"/sites/([^/]+)/job/(\d+)", link)
    if not m:
        return _check_page(link)
    site, job_id = m.groups()
    host = urlparse(link).netloc
    finder = quote(f'ById;Id="{job_id}",siteNumber={site}', safe=";=,")
    r = requests.get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                     f"?onlyData=true&finder={finder}",
                     headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT)
    if r.status_code != 200:
        return "unknown", f"karrieresiden svarede {r.status_code}"
    return ("live", "") if r.json().get("items") else ("removed", "opslaget findes ikke længere på karrieresiden")


def _check_page(link: str) -> tuple[str, str]:
    r = requests.get(link, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    if r.status_code in (404, 410):
        return "removed", f"siden svarer {r.status_code}"
    if r.status_code >= 400:
        return "unknown", f"siden svarede {r.status_code}"
    text = _visible_text(r.text[:600000])
    if any(p in text for p in REMOVED_PHRASES):
        return "removed", "siden skriver, at opslaget er lukket"
    if r.history and _left_posting(link, r.url):
        return "removed", "linket viderestiller væk fra opslaget"
    return "live", ""


def check_url(link: str) -> tuple[str, str]:
    host = urlparse(link).netloc.lower()
    try:
        if host.endswith("myworkdayjobs.com"):
            return _check_workday(link)
        if "oraclecloud" in host and "/CandidateExperience/" in link:
            return _check_oracle(link)
        return _check_page(link)
    except (requests.RequestException, ValueError) as e:
        return "unknown", f"kunne ikke hentes ({type(e).__name__})"


def load_marks(src: str) -> list[dict]:
    """Læser ArtifactData-output og returnerer [{doc_id, version, data}]."""
    p = Path(src)
    files = sorted(p.rglob("*.json")) if p.is_dir() else [p]
    out = []
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        items = obj if isinstance(obj, list) else obj.get("docs") or obj.get("documents") or [obj]
        for it in items:
            if not isinstance(it, dict):
                continue
            body = it.get("data") if isinstance(it.get("data"), dict) else it
            doc_id = it.get("doc_id") or it.get("id") or (f.stem if p.is_dir() else None)
            version = it.get("version")
            if doc_id and body.get("link"):
                out.append({"doc_id": str(doc_id), "version": version, "data": body})
    return out


def load_versions(path: str) -> dict[str, int]:
    """Versioner fra ArtifactData-listens svartekst, fx: - "j3fa…"  328 bytes  version 4  "…"
    Accepterer også JSON {doc_id: version}."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items()}
    except ValueError:
        pass
    return {m[1]: int(m[2]) for m in re.finditer(r'"([^"]+)"\s+[\d.,]+\s*\w*bytes?\s+version\s+(\d+)', text)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("marks", help="Mappe eller fil med ArtifactData-output for samlingen 'marks'")
    ap.add_argument("--versions", help="Fil med list-svarets tekst (eller JSON {doc_id: version})")
    ap.add_argument("--out", required=True, help="Hvor batch-skrivningerne gemmes")
    args = ap.parse_args()

    marks = [m for m in load_marks(args.marks) if m["data"].get("status") == "saved"]
    if args.versions:
        versions = load_versions(args.versions)
        for m in marks:
            m["version"] = versions.get(m["doc_id"], m["version"])
    missing = [m["doc_id"] for m in marks if m["version"] is None]
    if missing:
        raise SystemExit(f"Mangler version for {', '.join(missing)}. Gem list-svarets tekst og giv den med --versions.")
    now = datetime.now(ZoneInfo("Europe/Copenhagen")).isoformat(timespec="minutes")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda m: check_url(m["data"]["link"]), marks))

    writes, counts = [], {"live": 0, "removed": 0, "unknown": 0}
    for m, (state, reason) in zip(marks, results):
        counts[state] += 1
        prev = m["data"].get("check") or {}
        check = {"at": now, "state": state, "reason": reason,
                 "removed_at": prev.get("removed_at") or (now if state == "removed" else "")}
        if state != "removed":
            check["removed_at"] = ""
        w = {"op": "update", "collection": "marks", "doc_id": m["doc_id"], "data": {"check": check}}
        if m["version"] is not None:
            w["if_version"] = m["version"]
        writes.append(w)
        print(f"  {state:8} {m['data'].get('title', '')[:60]} {('· ' + reason) if reason else ''}")

    Path(args.out).write_text(json.dumps(writes, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Gemte opslag tjekket: {len(marks)} ({counts['live']} aktive, {counts['removed']} taget ned, "
          f"{counts['unknown']} ukendt). Skrevet til {args.out}")


if __name__ == "__main__":
    main()
