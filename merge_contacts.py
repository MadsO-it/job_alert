"""
Fletter dagens kontaktpersoner ind i kontaktarket (Google Sheet i Mads' Drive).

Brug:
  python merge_contacts.py --new contacts.json [--existing values.json] \
      [--tab Kontakter] --out sheet_values.json [--csv kontakter.csv]

  contacts.json   (eller jobs.json, hvor kørslernes "contacts" samles)
                  liste skrevet af den daglige kørsel, ét objekt pr. kontaktperson pr. opslag:
                  {"name", "title", "company", "department", "role", "focus", "why",
                   "job_title", "job_link", "email", "phone", "date"}
                  role er en af ROLES. date er opslagets kørselsdato (YYYY-MM-DD).
  values.json     svaret fra Sheets get_values på '<tab>'!A:Q (eller kun dets "values"-liste).
                  Udelades første gang.
  sheet_values.json  {"range": "'<tab>'!A1", "values": [...]} klar til Sheets update_values.
  --csv           skriver også arket som CSV (til første oprettelse via Drive).

Samme person (navn + virksomhed) samles i én række. Rækkefølgen i arket bevares, nye
personer kommer nederst, og kolonnerne Status og Noter røres aldrig.
"""

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

HEADER = ["Navn", "Titel", "Virksomhed", "Afdeling", "Rolle", "Hvad de arbejder på",
          "Hvorfor relevant", "Opslag", "Seneste opslag", "Antal opslag", "Først set",
          "Sidst set", "E-mail", "Telefon", "LinkedIn-søgning", "Status", "Noter"]
COL = {h: i for i, h in enumerate(HEADER)}
ROLES = ("Leder", "HR og rekruttering", "Rekrutteringsbureau", "Andet")
MAX_FOCUS_ENTRIES = 3

_SUFFIX = re.compile(r"\b(a s|aps|as|a/s|i s|p s|amba|ab|asa|plc|ltd|gmbh|danmark|denmark|group|koncern)\b")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-zæøåäöüé ]+", " ", text)).strip()


def person_key(name: str, company: str) -> tuple[str, str]:
    return _norm(name), re.sub(r"\s+", " ", _SUFFIX.sub(" ", _norm(company))).strip()


def _day(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{int(m[3])}/{int(m[2])}" if m else ""


def _iso(value: str) -> str:
    """Dato som YYYY-MM-DD, uanset om arket viser 2026-10-29, 29.10.2026, 29-10-2026 eller 29/10/2026."""
    s = str(value or "").strip().lstrip("'")
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return s


def _text(value) -> str:
    """Tekst der ellers ville blive tolket som tal, dato eller formel får en apostrof foran."""
    s = str(value or "").strip()
    if s and (re.fullmatch(r"[+\d][\d\s\-./()]*", s) or s[0] in "=+-@"):
        return "'" + s
    return s


def linkedin_search(name: str, company: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{name} {company}")


def load_existing(path: str | None) -> list[list[str]]:
    if not path or not Path(path).exists():
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    values = data.get("values", []) if isinstance(data, dict) else data
    rows = [list(r) + [""] * (len(HEADER) - len(r)) for r in values]
    if rows and rows[0][:2] == HEADER[:2]:
        rows = rows[1:]
    rows = [[str(c) for c in r[:len(HEADER)]] for r in rows if any(str(c).strip() for c in r)]
    for r in rows:
        r[COL["Først set"]], r[COL["Sidst set"]] = _iso(r[COL["Først set"]]), _iso(r[COL["Sidst set"]])
    return rows


def merge(rows: list[list[str]], contacts: list[dict]) -> tuple[list[list[str]], int, int]:
    index = {person_key(r[COL["Navn"]], r[COL["Virksomhed"]]): r for r in rows}
    added = updated = 0
    for c in sorted(contacts, key=lambda c: c.get("date", "")):
        name, company = (c.get("name") or "").strip(), (c.get("company") or "").strip()
        if not name or not company:
            continue
        date = (c.get("date") or "")[:10]
        job = (c.get("job_title") or "").strip()
        job_entry = f"{job} ({_day(date)})" if job and date else job
        key = person_key(name, company)
        row = index.get(key)
        if row is None:
            row = [""] * len(HEADER)
            row[COL["Navn"]], row[COL["Virksomhed"]] = name, company
            row[COL["Først set"]] = date
            row[COL["LinkedIn-søgning"]] = linkedin_search(name, company)
            rows.append(row)
            index[key] = row
            added += 1
        else:
            updated += 1
        for field, col in (("title", "Titel"), ("department", "Afdeling"), ("why", "Hvorfor relevant"),
                           ("email", "E-mail"), ("phone", "Telefon")):
            if (c.get(field) or "").strip():
                row[COL[col]] = c[field].strip()
        role = (c.get("role") or "").strip()
        if role in ROLES and not (row[COL["Rolle"]] == "Leder" and role == "Andet"):
            row[COL["Rolle"]] = role
        focus = (c.get("focus") or "").strip()
        if focus and _norm(focus) not in _norm(row[COL["Hvad de arbejder på"]]):
            entries = [e for e in row[COL["Hvad de arbejder på"]].split("\n") if e.strip()]
            entries.insert(0, f"{_day(date)}: {focus}" if date else focus)
            row[COL["Hvad de arbejder på"]] = "\n".join(entries[:MAX_FOCUS_ENTRIES])
        jobs = [j for j in row[COL["Opslag"]].split("\n") if j.strip()]
        titles = {_norm(re.sub(r"\s*\(\d+/\d+\)$", "", j)) for j in jobs}
        if job and _norm(job) not in titles:
            jobs.insert(0, job_entry)
        row[COL["Opslag"]] = "\n".join(jobs)
        row[COL["Antal opslag"]] = str(len(jobs))
        if c.get("job_link") and date >= row[COL["Sidst set"]]:
            row[COL["Seneste opslag"]] = c["job_link"]
        if date and date > row[COL["Sidst set"]]:
            row[COL["Sidst set"]] = date
        if date and (not row[COL["Først set"]] or date < row[COL["Først set"]]):
            row[COL["Først set"]] = date
    return rows, added, updated


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", required=True)
    ap.add_argument("--existing")
    ap.add_argument("--tab", default="Kontakter")
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv")
    args = ap.parse_args()

    data = json.loads(Path(args.new).read_text(encoding="utf-8"))
    if isinstance(data, dict):   # jobs.json: alle kontakter fra de gemte kørsler
        contacts = [c for r in data.get("runs", []) for c in r.get("contacts", [])]
    else:
        contacts = data
    bad = [c for c in contacts if (c.get("role") or "") not in ROLES]
    if bad:
        raise SystemExit(f"Ukendt rolle i {len(bad)} kontakter (brug {', '.join(ROLES)}): "
                         f"{', '.join(c.get('name', '?') for c in bad[:5])}")
    rows, added, updated = merge(load_existing(args.existing), contacts)

    def cell(r, i):
        if HEADER[i] in ("Status", "Noter"):
            return r[i]
        if HEADER[i] == "Antal opslag":
            return int(r[i]) if str(r[i]).isdigit() else r[i]
        return _text(r[i])
    values = [HEADER] + [[cell(r, i) for i in range(len(HEADER))] for r in rows]
    tab = args.tab.replace("'", "''")
    Path(args.out).write_text(json.dumps({"range": f"'{tab}'!A1", "values": values},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([HEADER] + rows)
    print(f"Kontaktark: {len(rows)} personer, heraf {added} nye. Skrevet til {args.out}")


if __name__ == "__main__":
    main()
