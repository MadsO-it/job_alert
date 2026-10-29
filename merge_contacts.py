"""
Kontaktarket (Google Sheet i Mads' Drive): kontaktpersoner fra jobopslagene.

Arket har to faner:
  Kontakter  én række pr. person. Faste felter skrives én gang, når personen dukker op første
             gang. Fokus, opslag, antal og datoer er formler, der henter fra fanen Opslag via
             nøglen i kolonne R, så rækken aldrig skal omskrives og kan sorteres frit.
             Status og Noter er Mads' egne kolonner.
  Opslag     én linje pr. person pr. opslag. Får kun nye linjer.

Den daglige kørsel tilføjer derfor kun rækker (append), uanset hvor stort arket bliver.
Hvilke kontakter der allerede er skrevet, står i jobs.json: hver kontakt i en kørsel får
"synced": true, og personnøglerne i arket står i "contact_keys".

Brug:
  python merge_contacts.py plan --jobs site/jobs.json --out sync.json
      skriver {"opslag_rows": [...], "kontakter_rows": [...]} med de rækker, der skal tilføjes
  python merge_contacts.py mark --jobs site/jobs.json
      efter vellykket skrivning: markerer kontakterne som synced, gemmer nøglerne og bygger
      index.html igen ved siden af jobs.json
  python merge_contacts.py setup --out setup.json
      overskrifter og formatering til et tomt ark (første gang)
"""

import argparse
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

KONTAKTER = ["Navn", "Titel", "Virksomhed", "Afdeling", "Rolle", "Hvad de arbejder på",
             "Hvorfor relevant", "Opslag", "Seneste opslag", "Antal opslag", "Først set",
             "Sidst set", "E-mail", "Telefon", "LinkedIn-søgning", "Status", "Noter", "Nøgle"]
OPSLAG = ["Nøgle", "Dato", "Navn", "Virksomhed", "Fokus", "Opslag", "Link", "Rolle"]
ROLES = ("Leder", "HR og rekruttering", "Rekrutteringsbureau", "Andet")

_KEY = 'INDIRECT("R"&ROW())'
_IN = f'Opslag!$A$2:$A={_KEY}'
FORMULAS = {
    "Hvad de arbejder på": (f'=IFERROR(TEXTJOIN(CHAR(10),TRUE,UNIQUE(SORT(FILTER(Opslag!$E$2:$E,{_IN},'
                            f'Opslag!$E$2:$E<>""),FILTER(Opslag!$B$2:$B,{_IN},Opslag!$E$2:$E<>""),FALSE))),"")'),
    "Opslag": (f'=IFERROR(TEXTJOIN(CHAR(10),TRUE,UNIQUE(SORT(FILTER(Opslag!$F$2:$F,{_IN}),'
               f'FILTER(Opslag!$B$2:$B,{_IN}),FALSE))),"")'),
    "Seneste opslag": f'=IFERROR(INDEX(SORT(FILTER(Opslag!$G$2:$G,{_IN}),FILTER(Opslag!$B$2:$B,{_IN}),FALSE),1),"")',
    "Antal opslag": f'=IFERROR(COUNTUNIQUE(FILTER(Opslag!$F$2:$F,{_IN})),0)',
    "Først set": f'=MINIFS(Opslag!$B$2:$B,Opslag!$A$2:$A,{_KEY})',
    "Sidst set": f'=MAXIFS(Opslag!$B$2:$B,Opslag!$A$2:$A,{_KEY})',
}

_SUFFIX = re.compile(r"\b(a s|aps|as|a/s|i s|p s|amba|ab|asa|plc|ltd|gmbh|danmark|denmark|group|koncern)\b")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zæøåäöüé ]+", " ", text)).strip()


def person_key(name: str, company: str) -> str:
    company = re.sub(r"\s+", " ", _SUFFIX.sub(" ", _norm(company))).strip()
    return f"{_norm(name)} | {company}"


def _day(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{int(m[3])}/{int(m[2])}" if m else ""


def _text(value) -> str:
    """Tekst der ellers ville blive tolket som tal, dato eller formel får en apostrof foran."""
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    if s and (re.fullmatch(r"[+\d][\d\s\-./()]*", s) or s[0] in "=+-@"):
        return "'" + s
    return s


def _valid(c: dict) -> bool:
    return bool((c.get("name") or "").strip() and (c.get("company") or "").strip())


def plan(history: dict) -> dict:
    keys = set(history.get("contact_keys", []))
    opslag_rows, kontakter_rows, seen_log = [], [], set()
    pending = [c for r in history.get("runs", []) for c in r.get("contacts", [])
               if not c.get("synced") and _valid(c)]
    for c in sorted(pending, key=lambda c: c.get("date", "")):
        key = person_key(c["name"], c["company"])
        date = (c.get("date") or "")[:10]
        job = re.sub(r"\s+", " ", c.get("job_title") or "").strip()
        log_id = (key, _norm(job))
        if log_id not in seen_log:
            seen_log.add(log_id)
            focus = re.sub(r"\s+", " ", c.get("focus") or "").strip()
            opslag_rows.append([key, date, _text(c["name"]), _text(c["company"]),
                                f"{_day(date)}: {focus}" if focus and date else _text(focus),
                                f"{job} ({_day(date)})" if job and date else _text(job),
                                c.get("job_link") or "", c.get("role") or ""])
        if key not in keys:
            keys.add(key)
            static = {"Navn": _text(c["name"]), "Titel": _text(c.get("title")),
                      "Virksomhed": _text(c["company"]), "Afdeling": _text(c.get("department")),
                      "Rolle": c.get("role") if c.get("role") in ROLES else "Andet",
                      "Hvorfor relevant": _text(c.get("why")), "E-mail": _text(c.get("email")),
                      "Telefon": _text(c.get("phone")),
                      "LinkedIn-søgning": "https://www.linkedin.com/search/results/people/?keywords="
                                          + quote_plus(f"{c['name']} {c['company']}"),
                      "Status": "", "Noter": "", "Nøgle": key}
            kontakter_rows.append([FORMULAS.get(col, static.get(col, "")) for col in KONTAKTER])
    return {"opslag_rows": opslag_rows, "kontakter_rows": kontakter_rows}


def mark(history: dict) -> int:
    keys = set(history.get("contact_keys", []))
    n = 0
    for r in history.get("runs", []):
        for c in r.get("contacts", []):
            if not c.get("synced") and _valid(c):
                c["synced"] = True
                keys.add(person_key(c["name"], c["company"]))
                n += 1
    history["contact_keys"] = sorted(keys)
    return n


def _rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def setup(kontakter_sheet_id: int = 0, opslag_sheet_id: int = 1) -> dict:
    """update_spreadsheet-requests og overskrifter til et tomt ark."""
    k, o = kontakter_sheet_id, opslag_sheet_id
    widths = [170, 170, 160, 160, 130, 340, 300, 260, 120, 80, 95, 95, 170, 110, 120, 120, 220, 60]
    header_fmt = {"textFormat": {"bold": True, "foregroundColorStyle": {"rgbColor": _rgb("#ffffff")}},
                  "backgroundColorStyle": {"rgbColor": _rgb("#323b4c")}, "verticalAlignment": "MIDDLE"}
    date_fmt = {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}
    req = [
        {"updateSheetProperties": {"properties": {"sheetId": k, "title": "Kontakter",
                                                  "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1}},
                                   "fields": "title,gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        {"addSheet": {"properties": {"sheetId": o, "title": "Opslag", "gridProperties": {"frozenRowCount": 1}}}},
        {"repeatCell": {"range": {"sheetId": k, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": header_fmt},
                        "fields": "userEnteredFormat.textFormat,userEnteredFormat.backgroundColorStyle,"
                                  "userEnteredFormat.verticalAlignment"}},
        {"repeatCell": {"range": {"sheetId": o, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": header_fmt},
                        "fields": "userEnteredFormat.textFormat,userEnteredFormat.backgroundColorStyle,"
                                  "userEnteredFormat.verticalAlignment"}},
        {"repeatCell": {"range": {"sheetId": k, "startRowIndex": 1},
                        "cell": {"userEnteredFormat": {"verticalAlignment": "TOP", "wrapStrategy": "CLIP"}},
                        "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy"}},
        {"repeatCell": {"range": {"sheetId": k, "startRowIndex": 1, "startColumnIndex": 10, "endColumnIndex": 12},
                        "cell": {"userEnteredFormat": date_fmt}, "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": o, "startRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 2},
                        "cell": {"userEnteredFormat": date_fmt}, "fields": "userEnteredFormat.numberFormat"}},
        {"updateDimensionProperties": {"range": {"sheetId": k, "dimension": "COLUMNS", "startIndex": 17, "endIndex": 18},
                                       "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
        {"setDataValidation": {"range": {"sheetId": k, "startRowIndex": 1, "startColumnIndex": 15, "endColumnIndex": 16},
                               "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                                   {"userEnteredValue": v} for v in ("Følger på LinkedIn", "Kontaktet", "Har svaret",
                                                                     "Møde aftalt", "Ikke relevant")]},
                                        "showCustomUi": True, "strict": False}}},
    ]
    for col, px in enumerate(widths):
        req.append({"updateDimensionProperties": {"range": {"sheetId": k, "dimension": "COLUMNS",
                                                            "startIndex": col, "endIndex": col + 1},
                                                  "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    return {"requests": req, "headers": {"'Kontakter'!A1": [KONTAKTER], "'Opslag'!A1": [OPSLAG]}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["plan", "mark", "setup"])
    ap.add_argument("--jobs", help="site/jobs.json fra dagens kørsel")
    ap.add_argument("--out", help="plan/setup: hvor resultatet gemmes")
    args = ap.parse_args()

    if args.action == "setup":
        out = args.out or "setup.json"
        Path(out).write_text(json.dumps(setup(), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Opsætning skrevet til {out}")
        return
    path = Path(args.jobs)
    history = json.loads(path.read_text(encoding="utf-8"))
    if args.action == "plan":
        result = plan(history)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Kontaktark: {len(result['kontakter_rows'])} nye personer, "
              f"{len(result['opslag_rows'])} nye linjer i Opslag. Skrevet til {args.out}")
    else:
        n = mark(history)
        path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        from render_page import render_html
        (path.parent / "index.html").write_text(render_html(history), encoding="utf-8")
        print(f"{n} kontakter markeret som skrevet; {len(history['contact_keys'])} personer i arket.")


if __name__ == "__main__":
    main()
