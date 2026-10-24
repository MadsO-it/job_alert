"""
Bygger resultatsiden (index.html) og historikfilen (jobs.json) for sky-kørslen.

Brug:
  python render_page.py --new new.json --ratings ratings.json \
      [--history jobs.json] --out-dir site/

  new.json      output fra `python job_alert.py --export new.json`
  ratings.json  {"<id>": "green"|"yellow"|"red", ...} sat af Claude ud fra
                CANDIDATE_PROFILE og RATING_RULES i job_alert.py
  jobs.json     forrige kørsels historik (hentes fra resultatsiden), valgfri

Siden viser de seneste kørsler med grøn/gul/rød-inddeling. Historikken
(alle tidligere sete links) gemmes i jobs.json, så næste kørsel kun tager nye opslag.
"""

import argparse
import html
import json
from datetime import datetime
from pathlib import Path

MAX_RUNS_ON_PAGE = 12
JOB_FIELDS = ("title", "company", "location", "source", "link", "date", "rating")


def _clean(text) -> str:
    if not text:
        return ""
    return html.unescape(str(text)).replace("\\/", "/").strip()


def _norm_link(link: str) -> str:
    return (link or "").split("?")[0].rstrip("/")


def build_history(history: dict, new: dict, ratings: dict) -> dict:
    jobs = []
    for j in new.get("jobs", []):
        rating = ratings.get(str(j.get("id")), "unrated")
        if rating not in ("green", "yellow", "red"):
            rating = "unrated"
        jobs.append({k: _clean(j.get(k)) for k in JOB_FIELDS if k != "rating"} | {"rating": rating})
    excluded = [
        {"title": _clean(j.get("title")), "company": _clean(j.get("company")),
         "link": _clean(j.get("link")), "excluded_by": j.get("excluded_by", "")}
        for j in new.get("excluded", [])
    ]
    run = {"run_at": new.get("run_at") or datetime.now().isoformat(timespec="minutes"),
           "jobs": jobs, "excluded": excluded}

    seen = set(history.get("seen_links", []))
    seen |= {_norm_link(j["link"]) for j in jobs + excluded if j.get("link")}
    runs = [run] + history.get("runs", [])
    return {"updated": run["run_at"], "seen_links": sorted(seen), "runs": runs[:MAX_RUNS_ON_PAGE]}


PAGE = r"""<title>Job Alert KBH</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=Lato:wght@400;700&display=swap">
<style>
/* Layout: navy header band as on Mads' CV, then one reading column of job rows,
   each row carrying its traffic-light colour as a left stripe. */
:root {
  --bg: #efeae2;
  --surface: #f8f5f0;
  --ink: #232a37;
  --muted: #5d6573;
  --line: #d9d2c6;
  --brand: #323b4c;
  --brand-ink: #ffffff;
  --brand-muted: #c3cad8;
  --green: #1f7a4a;
  --green-bg: #dcefe2;
  --yellow: #8a6400;
  --yellow-bg: #f6e7bd;
  --red: #a8352b;
  --red-bg: #f4dcd8;
  --grey: #6b6f78;
  --grey-bg: #e4e1dc;
  --font-display: "Poppins", "Segoe UI", system-ui, sans-serif;
  --font-body: "Lato", "Helvetica Neue", Arial, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #1b1f27; --surface: #232833; --ink: #ece8e1; --muted: #a7adb9; --line: #343a47;
    --brand: #2a3242; --brand-ink: #f3f1ec; --brand-muted: #a9b2c4;
    --green: #6fd09a; --green-bg: #1f3a2c; --yellow: #e8c15a; --yellow-bg: #3d3318;
    --red: #f08b80; --red-bg: #45231f; --grey: #a5a9b2; --grey-bg: #2e323a;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #1b1f27; --surface: #232833; --ink: #ece8e1; --muted: #a7adb9; --line: #343a47;
  --brand: #2a3242; --brand-ink: #f3f1ec; --brand-muted: #a9b2c4;
  --green: #6fd09a; --green-bg: #1f3a2c; --yellow: #e8c15a; --yellow-bg: #3d3318;
  --red: #f08b80; --red-bg: #45231f; --grey: #a5a9b2; --grey-bg: #2e323a;
  color-scheme: dark;
}
body { background: var(--bg); color: var(--ink); font-family: var(--font-body); font-size: 15px; line-height: 1.45; }
.wrap { max-width: 760px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
header { background: var(--brand); color: var(--brand-ink); }
header .wrap { padding-block: 22px 20px; display: grid; gap: 6px; }
.eyebrow { font-family: var(--font-display); font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--brand-muted); }
h1 { font-family: var(--font-display); font-weight: 700; font-size: 24px; margin: 0; letter-spacing: .01em; text-wrap: balance; }
.sub { color: var(--brand-muted); font-size: 14px; margin: 0; }
main .wrap { padding-block: 18px 40px; display: grid; gap: 16px; }
.controls { display: grid; gap: 10px; }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
label.lbl { font-family: var(--font-display); font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
select { font: inherit; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); max-width: 100%; }
.chip { font-family: var(--font-display); font-weight: 600; font-size: 13px; border-radius: 999px; padding: 7px 13px; border: 1.5px solid transparent; cursor: pointer; display: inline-flex; gap: 6px; align-items: center; font-variant-numeric: tabular-nums; }
.chip[data-r="green"] { background: var(--green-bg); color: var(--green); }
.chip[data-r="yellow"] { background: var(--yellow-bg); color: var(--yellow); }
.chip[data-r="red"] { background: var(--red-bg); color: var(--red); }
.chip[data-r="unrated"] { background: var(--grey-bg); color: var(--grey); }
.chip[aria-pressed="false"] { background: transparent; border-color: var(--line); color: var(--muted); }
.chip:focus-visible, select:focus-visible, a:focus-visible, summary:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.group { display: grid; gap: 8px; }
.group h2 { font-family: var(--font-display); font-size: 15px; font-weight: 600; margin: 8px 0 0; display: flex; gap: 8px; align-items: baseline; }
.group h2 .n { color: var(--muted); font-weight: 500; font-variant-numeric: tabular-nums; }
.job { background: var(--surface); border: 1px solid var(--line); border-left: 5px solid var(--stripe); border-radius: 6px; padding: 11px 14px; display: grid; gap: 3px; min-width: 0; }
.job a { color: var(--ink); font-weight: 700; text-decoration: none; overflow-wrap: anywhere; }
.job a:hover { text-decoration: underline; }
.meta { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.job[data-r="green"] { --stripe: var(--green); }
.job[data-r="yellow"] { --stripe: var(--yellow); }
.job[data-r="red"] { --stripe: var(--red); }
.job[data-r="unrated"] { --stripe: var(--grey); }
.empty { color: var(--muted); padding: 24px 0; text-align: center; }
details { border-top: 1px solid var(--line); padding-top: 12px; }
summary { cursor: pointer; color: var(--muted); font-size: 14px; }
details ul { margin: 10px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px; display: grid; gap: 4px; }
details a { color: var(--muted); }
.tag { font-family: var(--font-display); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
footer { color: var(--muted); font-size: 12px; }
</style>

<header><div class="wrap">
  <div class="eyebrow">Storkøbenhavn · IT Jobbank, Jobindex, StepStone</div>
  <h1>Job Alert KBH</h1>
  <p class="sub" id="sub"></p>
</div></header>

<main><div class="wrap">
  <div class="controls">
    <div class="row">
      <label class="lbl" for="run">Kørsel</label>
      <select id="run"></select>
    </div>
    <div class="row" id="chips" role="group" aria-label="Vis farver"></div>
  </div>
  <div id="list"></div>
  <details id="excl"><summary id="excl-sum"></summary><ul id="excl-list"></ul></details>
  <footer>Farverne er Claudes forslag ud fra din profil. Grøn: klart match. Gul: delvist eller uklart. Rød: dårligt match.</footer>
</div></main>

<script type="application/json" id="data">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const LABEL = { green: "Grøn", yellow: "Gul", red: "Rød", unrated: "Ikke vurderet" };
const ORDER = ["green", "yellow", "red", "unrated"];
const FILTER_NAMES = { senior: "senior", test: "test", support: "support", engineer: "engineer",
  udvikler: "udvikler", tekniker: "tekniker" };
let show = { green: true, yellow: true, red: false, unrated: true };
try { const s = JSON.parse(localStorage.getItem("show") || "null"); if (s) show = { ...show, ...s }; } catch (e) {}

const fmtRun = iso => { const d = new Date(iso); return isNaN(d) ? iso :
  d.toLocaleDateString("da-DK", { day: "numeric", month: "long", year: "numeric" }); };
const fmtDate = s => s && /^\d{4}-\d{2}-\d{2}/.test(s) ? `${s.slice(8,10)}/${s.slice(5,7)}` : "";
const el = (tag, attrs = {}, text) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };

const runSel = document.getElementById("run");
DATA.runs.forEach((r, i) => runSel.append(el("option", { value: i }, `${fmtRun(r.run_at)} · ${r.jobs.length} nye`)));

function render() {
  const run = DATA.runs[+runSel.value || 0];
  const counts = Object.fromEntries(ORDER.map(k => [k, run.jobs.filter(j => j.rating === k).length]));
  document.getElementById("sub").textContent =
    `${run.jobs.length} nye opslag ${fmtRun(run.run_at)} · ${counts.green} grønne, ${counts.yellow} gule, ${counts.red} røde`;

  const chips = document.getElementById("chips"); chips.replaceChildren();
  ORDER.filter(k => k !== "unrated" || counts.unrated).forEach(k => {
    const b = el("button", { class: "chip", "data-r": k, "aria-pressed": String(show[k]), type: "button" }, `${LABEL[k]} ${counts[k]}`);
    b.onclick = () => { show[k] = !show[k]; try { localStorage.setItem("show", JSON.stringify(show)); } catch (e) {} render(); };
    chips.append(b);
  });

  const list = document.getElementById("list"); list.replaceChildren();
  let any = false;
  ORDER.forEach(k => {
    const jobs = run.jobs.filter(j => j.rating === k);
    if (!show[k] || !jobs.length) return;
    any = true;
    const g = el("section", { class: "group" });
    const h = el("h2"); h.append(LABEL[k], el("span", { class: "n" }, String(jobs.length))); g.append(h);
    jobs.forEach(j => {
      const card = el("article", { class: "job", "data-r": k });
      card.append(el("a", { href: j.link, target: "_blank", rel: "noopener" }, j.title));
      card.append(el("div", { class: "meta" }, [j.company, j.location, j.source, fmtDate(j.date)].filter(Boolean).join(" · ")));
      g.append(card);
    });
    list.append(g);
  });
  if (!any) list.append(el("p", { class: "empty" }, run.jobs.length ? "Alle farver er slået fra. Tryk på en farve ovenfor." : "Ingen nye opslag i denne kørsel."));

  const ex = run.excluded || [];
  document.getElementById("excl").hidden = !ex.length;
  document.getElementById("excl-sum").textContent = `${ex.length} frasorteret af titelfilteret (senior, test, support, engineer mv.)`;
  const ul = document.getElementById("excl-list"); ul.replaceChildren();
  ex.forEach(j => { const li = el("li"); li.append(el("span", { class: "tag" }, (FILTER_NAMES[j.excluded_by] || j.excluded_by) + " · "));
    li.append(el("a", { href: j.link, target: "_blank", rel: "noopener" }, j.title)); if (j.company) li.append(` · ${j.company}`); ul.append(li); });
}
runSel.onchange = render;
render();
</script>
"""


def render_html(history: dict) -> str:
    data = json.dumps(history, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__DATA__", data)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", required=True)
    ap.add_argument("--ratings", required=True)
    ap.add_argument("--history")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    new = json.loads(Path(args.new).read_text(encoding="utf-8"))
    ratings = json.loads(Path(args.ratings).read_text(encoding="utf-8"))
    history = {}
    if args.history and Path(args.history).exists():
        history = json.loads(Path(args.history).read_text(encoding="utf-8"))

    result = build_history(history, new, ratings)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "jobs.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "index.html").write_text(render_html(result), encoding="utf-8")
    run = result["runs"][0]
    c = {k: sum(1 for j in run["jobs"] if j["rating"] == k) for k in ("green", "yellow", "red", "unrated")}
    print(f"Side bygget: {len(run['jobs'])} nye ({c['green']} grøn, {c['yellow']} gul, {c['red']} rød, "
          f"{c['unrated']} ikke vurderet), {len(run['excluded'])} frasorteret. Ud: {out}")


if __name__ == "__main__":
    main()
