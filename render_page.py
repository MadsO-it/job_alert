"""
Bygger resultatsiden (index.html) og historikfilen (jobs.json) for sky-kørslen.

Brug:
  python render_page.py --new new.json --ratings ratings.json \
      [--history jobs.json] --out-dir site/

  new.json      output fra `python job_alert.py --export new.json`
  ratings.json  {"<id>": "green"|"yellow"|"red", ...} sat af Claude ud fra
                CANDIDATE_PROFILE og RATING_RULES i job_alert.py
  jobs.json     forrige kørsels historik (hentes fra resultatsiden), valgfri
  contacts.json dagens kontaktpersoner (se CLOUD_RUN.md), valgfri; gemmes i kørslen, så
                merge_contacts.py kan indhente dage hvor kontaktarket ikke blev opdateret

Siden viser de seneste kørsler med grøn/gul/rød-inddeling. Historikken
(alle tidligere sete links) gemmes i jobs.json, så næste kørsel kun tager nye opslag.
"""

import argparse
import html
import json
from datetime import datetime
from pathlib import Path

from job_alert import job_key

MAX_RUNS_ON_PAGE = 12
JOB_FIELDS = ("title", "company", "location", "source", "link", "date", "deadline", "rating")


def _clean(text) -> str:
    if not text:
        return ""
    return html.unescape(str(text)).replace("\\/", "/").strip()


def _norm_link(link: str) -> str:
    return (link or "").split("?")[0].rstrip("/")


def build_history(history: dict, new: dict, ratings: dict, contacts: list | None = None) -> dict:
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
    run_at = new.get("run_at") or datetime.now().isoformat(timespec="minutes")
    contacts = [dict(c, date=c.get("date") or run_at[:10]) for c in (contacts or [])]
    run = {"run_at": run_at, "jobs": jobs, "excluded": excluded, "sources": new.get("sources") or {},
           "contacts": contacts}

    seen = set(history.get("seen_links", []))
    seen |= {_norm_link(j["link"]) for j in jobs + excluded if j.get("link")}
    keys = {tuple(k) for k in history.get("seen_keys", [])}
    keys |= {job_key(j) for j in jobs + excluded}
    runs = [run] + history.get("runs", [])
    return {"updated": run["run_at"], "seen_links": sorted(seen),
            "seen_keys": sorted(list(k) for k in keys), "runs": runs[:MAX_RUNS_ON_PAGE]}


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
#src.warn summary { color: var(--red); font-weight: 700; }
#src li.bad { color: var(--red); }
/* Gemte opslag: ligger over kørselsvælgeren, fordi de går på tværs af kørsler. */
#saved { display: grid; gap: 8px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
#saved h2 { font-family: var(--font-display); font-size: 15px; font-weight: 600; margin: 0; display: flex; gap: 8px; align-items: baseline; }
#saved h2 .n { color: var(--muted); font-weight: 500; font-variant-numeric: tabular-nums; }
#saved details { border-top: 0; padding-top: 2px; }
#saved details > div { display: grid; gap: 8px; margin-top: 8px; }
.saved-empty { color: var(--muted); font-size: 14px; margin: 0; }
.acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 6px; }
.act { font-family: var(--font-display); font-weight: 600; font-size: 12.5px; min-height: 34px; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line); background: var(--bg); color: var(--ink); cursor: pointer; }
.act:hover { border-color: var(--muted); }
.act:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.act[aria-pressed="true"] { background: var(--brand); border-color: var(--brand); color: var(--brand-ink); }
.act.quiet { background: transparent; color: var(--muted); }
.act:disabled { opacity: .55; cursor: default; }
.state { font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; }
.state.warn { color: var(--yellow); }
.state.bad { color: var(--red); }
.state.ok { color: var(--green); }
.job.gone { opacity: .75; }
.job.gone a { text-decoration: line-through; text-decoration-thickness: 1px; }
.note { color: var(--muted); font-size: 13px; margin: 0; }
</style>

<header><div class="wrap">
  <div class="eyebrow">Storkøbenhavn · Jobindex, IT Jobbank, StepStone og 28 karrieresider</div>
  <h1>Job Alert KBH</h1>
  <p class="sub" id="sub"></p>
</div></header>

<main><div class="wrap">
  <section id="saved" aria-labelledby="saved-h">
    <h2 id="saved-h">Gemte opslag <span class="n" id="saved-n"></span></h2>
    <div id="saved-list" class="group"><p class="saved-empty">Indlæser gemte opslag …</p></div>
    <details id="saved-gone" hidden><summary id="saved-gone-sum"></summary><div id="saved-gone-list"></div></details>
    <details id="applied" hidden><summary id="applied-sum"></summary><div id="applied-list"></div></details>
    <p class="note" id="saved-msg" role="status" hidden></p>
  </section>
  <div class="controls">
    <div class="row">
      <label class="lbl" for="run">Kørsel</label>
      <select id="run"></select>
    </div>
    <div class="row" id="chips" role="group" aria-label="Vis farver"></div>
  </div>
  <div id="list"></div>
  <details id="excl"><summary id="excl-sum"></summary><ul id="excl-list"></ul></details>
  <details id="src"><summary id="src-sum"></summary><ul id="src-list"></ul></details>
  <footer>Farverne er Claudes forslag ud fra din profil. Grøn: klart match. Gul: delvist eller uklart. Rød: dårligt match. Gemte opslag tjekkes hver morgen og flyttes ned under "Ikke længere slået op", når annoncen er taget ned, eller fristen er udløbet.</footer>
</div></main>

<script type="application/json" id="data">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("data").textContent);
const LABEL = { green: "Grøn", yellow: "Gul", red: "Rød", unrated: "Ikke vurderet" };
const ORDER = ["green", "yellow", "red", "unrated"];
const FILTER_NAMES = { senior: "senior", test: "test", support: "support", engineer: "engineer",
  udvikler: "udvikler", tekniker: "tekniker", uopfordret: "uopfordret" };
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
    jobs.forEach(j => g.append(jobCard(j, k, run)));
    list.append(g);
  });
  if (!any) list.append(el("p", { class: "empty" }, run.jobs.length ? "Alle farver er slået fra. Tryk på en farve ovenfor." : "Ingen nye opslag i denne kørsel."));

  const ex = run.excluded || [];
  document.getElementById("excl").hidden = !ex.length;
  document.getElementById("excl-sum").textContent = `${ex.length} frasorteret af titelfilteret (senior, test, support, engineer mv.)`;
  const ul = document.getElementById("excl-list"); ul.replaceChildren();
  ex.forEach(j => { const li = el("li"); li.append(el("span", { class: "tag" }, (FILTER_NAMES[j.excluded_by] || j.excluded_by) + " · "));
    li.append(el("a", { href: j.link, target: "_blank", rel: "noopener" }, j.title)); if (j.company) li.append(` · ${j.company}`); ul.append(li); });

  renderSources(run);
  const nBad = sourceProblems(run.sources || {}).length;
  if (nBad) document.getElementById("sub").textContent += ` · ${nBad} ${nBad === 1 ? "kilde" : "kilder"} uden opslag`;
}

function sourceProblems(src) {
  const boards = (src.boards || []).filter(b => !b.count).map(b => ({ name: b.name, why: "0 opslag" }));
  const sites = (src.career_sites || []).filter(c => c.error || !c.count)
    .map(c => ({ name: c.name, why: c.error ? `fejl: ${c.error}` : "0 opslag" }));
  return boards.concat(sites);
}

function renderSources(run) {
  const src = run.sources || {};
  const box = document.getElementById("src");
  const boards = src.boards || [], sites = src.career_sites || [];
  box.hidden = !boards.length && !sites.length;
  if (box.hidden) return;
  const problems = sourceProblems(src);
  box.classList.toggle("warn", problems.length > 0);
  document.getElementById("src-sum").textContent = problems.length
    ? `Kildestatus: ${problems.length} ${problems.length === 1 ? "kilde" : "kilder"} uden opslag (${problems.map(p => p.name).join(", ")})`
    : `Kildestatus: alle ${boards.length} jobportaler og ${sites.length} karrieresider svarede`;
  const ul = document.getElementById("src-list"); ul.replaceChildren();
  boards.forEach(b => {
    const extra = b.searches > 1 ? ` (${b.searches} søgninger${b.empty_searches ? `, ${b.empty_searches} uden hits` : ""})` : "";
    ul.append(el("li", b.count ? {} : { class: "bad" }, `${b.name} · ${b.count} opslag${extra}`));
  });
  sites.forEach(c => ul.append(el("li", c.error || !c.count ? { class: "bad" } : {},
    `${c.name} · ${c.error ? "fejl: " + c.error : c.count + " opslag"}`)));
}

/* ── Gemte opslag ──────────────────────────────────────────────────────────
   Markeringer ligger i sidens database (samlingen "marks"), så de overlever de
   daglige genudgivelser. Den daglige kørsel skriver feltet "check" på hver gemt
   markering: state "live", "removed" eller "unknown". */
let db = null, dbState = "loading";            // loading | ready | off
const marks = new Map();
const pending = new Set();
let message = "";

const normLink = l => (l || "").split("?")[0].replace(/\/+$/, "");
function markId(link) {                         // cyrb53 af det normaliserede link
  const s = normLink(link); let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761); h2 = Math.imul(h2 ^ c, 1597334677); }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return "j" + (h2 >>> 0).toString(16).padStart(8, "0") + (h1 >>> 0).toString(16).padStart(8, "0");
}
const todayISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const fmtDay = iso => { const d = new Date(iso); return isNaN(d) ? "" : `${d.getDate()}/${d.getMonth() + 1}`; };
const isDate = s => /^\d{4}-\d{2}-\d{2}$/.test(s || "");
const daysUntil = s => Math.round((new Date(s + "T12:00") - new Date(todayISO() + "T12:00")) / 864e5);
const deadlinePassed = m => isDate(m.deadline) && daysUntil(m.deadline) < 0;

function jobCard(j, k, run) {
  const card = el("article", { class: "job", "data-r": k });
  card.append(el("a", { href: j.link, target: "_blank", rel: "noopener" }, j.title));
  card.append(el("div", { class: "meta" }, [j.company, j.location, j.source, fmtDate(j.date)].filter(Boolean).join(" · ")));
  if (dbState !== "ready") return card;
  const id = markId(j.link), m = marks.get(id), acts = el("div", { class: "acts" });
  if (m && m.status === "applied") {
    acts.append(el("span", { class: "state ok" }, `Søgt ${fmtDay(m.applied_at)}`));
  } else {
    const b = el("button", { class: "act", type: "button", "aria-pressed": String(!!m) }, m ? "Gemt" : "Gem");
    b.disabled = pending.has(id);
    b.onclick = () => m ? write(id, ref => ref.delete()) : saveJob(j, run);
    acts.append(b);
  }
  card.append(acts);
  return card;
}

function saveJob(j, run) {
  write(markId(j.link), ref => ref.set({
    status: "saved", link: j.link, title: j.title, company: j.company || "", location: j.location || "",
    source: j.source || "", rating: j.rating || "unrated", deadline: j.deadline || "",
    first_seen: run.run_at, saved_at: new Date().toISOString(),
  }));
}

async function write(id, fn) {
  if (!db || pending.has(id)) return;
  pending.add(id); message = ""; renderAll();
  try { await fn(db.collection("marks").doc(id)); }
  catch (e) {
    message = e && e.code === "quota_exceeded" ? "Der er ikke plads til flere markeringer. Fjern nogle gamle." :
      e && e.code === "invalid_argument" ? "Du har ikke adgang til at ændre markeringer på denne side." :
      "Ændringen blev ikke gemt. Prøv igen.";
  }
  finally { pending.delete(id); renderAll(); }
}

function savedCard(id, m, kind) {
  const r = ["green", "yellow", "red"].includes(m.rating) ? m.rating : "unrated";
  const card = el("article", { class: "job" + (kind === "gone" ? " gone" : ""), "data-r": r });
  card.append(el("a", { href: m.link, target: "_blank", rel: "noopener" }, m.title || m.link));
  card.append(el("div", { class: "meta" }, [m.company, m.location, m.source].filter(Boolean).join(" · ")));
  const acts = el("div", { class: "acts" });
  const state = (cls, text) => acts.append(el("span", { class: "state " + cls }, text));
  const btn = (label, fn, quiet) => { const b = el("button", { class: "act" + (quiet ? " quiet" : ""), type: "button" }, label);
    b.disabled = pending.has(id); b.onclick = () => write(id, fn); acts.append(b); };
  const c = m.check || {};
  if (kind === "applied") {
    state("ok", `Søgt ${fmtDay(m.applied_at)}`);
    btn("Fortryd", ref => ref.update({ status: "saved", applied_at: "" }), true);
  } else {
    if (kind === "gone") {
      state("bad", c.state === "removed" ? `Taget ned · opdaget ${fmtDay(c.removed_at || c.at)}` : `Fristen udløb ${fmtDate(m.deadline)}`);
    } else {
      if (m.deadline === "snarest") state("warn", "Frist: snarest");
      else if (isDate(m.deadline)) { const n = daysUntil(m.deadline);
        state(n <= 3 ? "warn" : "", n === 0 ? "Frist i dag" : `Frist ${fmtDate(m.deadline)} · ${n} ${n === 1 ? "dag" : "dage"}`); }
      if (c.state === "live") state("", `Stadig slået op · tjekket ${fmtDay(c.at)}`);
      else if (c.state === "unknown") state("warn", `Kunne ikke tjekkes ${fmtDay(c.at)}`);
      else state("", `Gemt ${fmtDay(m.saved_at)} · tjekkes ved næste kørsel`);
    }
    btn("Markér som søgt", ref => ref.update({ status: "applied", applied_at: new Date().toISOString() }));
    btn("Fjern", ref => ref.delete(), true);
  }
  card.append(acts);
  return card;
}

function renderSaved() {
  const list = document.getElementById("saved-list"), n = document.getElementById("saved-n");
  const msg = document.getElementById("saved-msg");
  msg.hidden = !message; msg.textContent = message;
  if (dbState !== "ready") {
    n.textContent = "";
    list.replaceChildren(el("p", { class: "saved-empty" }, dbState === "loading" ? "Indlæser gemte opslag …" :
      "Gemte opslag kan kun vises, når siden er åbnet på claude.ai, og du er logget ind."));
    document.getElementById("saved-gone").hidden = document.getElementById("applied").hidden = true;
    return;
  }
  const active = [], gone = [], applied = [];
  marks.forEach((m, id) => {
    if (m.status === "applied") applied.push([id, m]);
    else if ((m.check || {}).state === "removed" || deadlinePassed(m)) gone.push([id, m]);
    else active.push([id, m]);
  });
  const dl = m => m.deadline === "snarest" ? "0000" : isDate(m.deadline) ? m.deadline : "9999";
  active.sort((a, b) => dl(a[1]).localeCompare(dl(b[1])) || String(a[1].saved_at).localeCompare(String(b[1].saved_at)));
  applied.sort((a, b) => String(b[1].applied_at).localeCompare(String(a[1].applied_at)));
  n.textContent = String(active.length);
  list.replaceChildren(...(active.length ? active.map(([id, m]) => savedCard(id, m, "active")) :
    [el("p", { class: "saved-empty" }, "Ingen gemte opslag. Tryk Gem på et opslag nedenfor, så bliver det stående her, indtil du har søgt det, eller det bliver taget ned.")]));
  const fill = (box, sum, listId, items, kind, label) => {
    document.getElementById(box).hidden = !items.length;
    document.getElementById(sum).textContent = `${label} (${items.length})`;
    document.getElementById(listId).replaceChildren(...items.map(([id, m]) => savedCard(id, m, kind)));
  };
  fill("saved-gone", "saved-gone-sum", "saved-gone-list", gone, "gone", "Ikke længere slået op");
  fill("applied", "applied-sum", "applied-list", applied, "applied", "Søgt");
}

function renderAll() { render(); renderSaved(); }

runSel.onchange = render;
renderAll();

(async () => {
  try { db = window.claude && window.claude.use ? await window.claude.use("db") : null; } catch (e) { db = null; }
  if (!db) { dbState = "off"; renderAll(); return; }
  db.collection("marks").onSnapshot(snap => {
    marks.clear();
    snap.docs.forEach(d => { if (d.exists) marks.set(d.id, d.data()); });
    dbState = "ready"; renderAll();
  }, () => { dbState = "off"; renderAll(); });
})();
</script>
"""


def render_html(history: dict) -> str:
    data = json.dumps(history, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__DATA__", data)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new")
    ap.add_argument("--ratings")
    ap.add_argument("--history")
    ap.add_argument("--contacts", help="contacts.json fra dagens kørsel; gemmes i kørslen i jobs.json")
    ap.add_argument("--rerender", metavar="JOBS_JSON",
                    help="Byg kun siden igen ud fra en eksisterende jobs.json (ingen ny kørsel)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    if args.rerender:
        result = json.loads(Path(args.rerender).read_text(encoding="utf-8"))
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "jobs.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "index.html").write_text(render_html(result), encoding="utf-8")
        print(f"Side genopbygget ud fra {args.rerender}. Ud: {out}")
        return
    if not (args.new and args.ratings):
        ap.error("--new og --ratings er påkrævet (eller brug --rerender)")

    new = json.loads(Path(args.new).read_text(encoding="utf-8"))
    ratings = json.loads(Path(args.ratings).read_text(encoding="utf-8"))
    history = {}
    if args.history and Path(args.history).exists():
        history = json.loads(Path(args.history).read_text(encoding="utf-8"))

    contacts = json.loads(Path(args.contacts).read_text(encoding="utf-8")) if args.contacts else []
    result = build_history(history, new, ratings, contacts)
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
