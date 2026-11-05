// Headless smoke test of the WindLab page scripts (run by tests/test_js.py; needs Node 18+).
//
// What it proves: every module under windlab/app/static evaluates in a minimal DOM without a
// SyntaxError / ReferenceError / TypeError at load time; app.js runs its start-up (catalog,
// /api/config, glossary, lessons, runs, first-visit tour) and wires its main handlers; the
// educational-use notice blocks the page until its box is ticked and Accept pressed; the
// "Make this wing" flow reaches Step 2 and the Run button; the tour, the shape-kind radios, the
// glossary buttons and the theme selector react to events. With WINDLAB_SMOKE_CATALOG set to a
// pseudo-localised catalog it also checks that every message on the page came from the catalog.
//
// What it does not prove (see tests/js/dom_shim.mjs): layout, CSS, WebGL / three.js rendering,
// the canvas charts' drawing, the lattice-Boltzmann preview, real network behaviour, or anything a
// human sees. Those still need a browser.
//
// Environment: WINDLAB_SMOKE_STATIC (file: URL of a copy of the static folder with a package.json
// that says "type": "module"), WINDLAB_SMOKE_DATA (folder with dom.json and routes.json),
// optional WINDLAB_SMOKE_CATALOG (a catalog file to serve instead of i18n/en.json).
// Output: one JSON object on stdout; exit code 0 when every check passed.

import { readFileSync } from "node:fs";
import { installDom } from "./dom_shim.mjs";

const STATIC = process.env.WINDLAB_SMOKE_STATIC;
const DATA = process.env.WINDLAB_SMOKE_DATA;
if (!STATIC || !DATA) {
  console.error("set WINDLAB_SMOKE_STATIC and WINDLAB_SMOKE_DATA");
  process.exit(2);
}
const base = STATIC.endsWith("/") ? STATIC : STATIC + "/";
const tree = JSON.parse(readFileSync(`${DATA}/dom.json`, "utf8"));
const routes = JSON.parse(readFileSync(`${DATA}/routes.json`, "utf8"));
const pseudo = process.env.WINDLAB_SMOKE_CATALOG || null;
const env = installDom(tree, routes, { catalog: pseudo });
const doc = env.document;

const report = { modules: [], checks: [], errors: [], fetches: env.log, console: env.console, pseudo: !!pseudo };
process.on("uncaughtException", (e) => report.errors.push(`uncaughtException: ${e && e.stack || e}`));
process.on("unhandledRejection", (e) => report.errors.push(`unhandledRejection: ${e && e.stack || e}`));

const $ = (id) => doc.getElementById(id);
function check(name, ok, detail = "") {
  report.checks.push({ name, ok: !!ok, detail: String(detail).slice(0, 300) });
  return !!ok;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// wait until the fetch log and the microtask queue have been quiet for a while (debounces are 150 ms)
async function settle(maxMs = 4000) {
  let n = -1;
  const end = Date.now() + maxMs;
  while (Date.now() < end) {
    await sleep(120);
    if (env.log.length === n) return;
    n = env.log.length;
    await sleep(200);
    if (env.log.length === n) return;
  }
}

// ---------------------------------------------------------------- 1. every module evaluates

const MODULES = ["plot.js", "i18n.js", "section.js", "chart.js", "vendor/three/three.module.js", "viewer.js",
  "preview.js", "demo.js"];
for (const name of MODULES) {
  try {
    const m = await import(base + name);
    report.modules.push({ name, ok: true, exports: Object.keys(m).length });
  } catch (e) {
    report.modules.push({ name, ok: false, error: `${e.name}: ${e.message}` });
  }
}

// ---------------------------------------------------------------- 2. app.js starts up

const errorsBefore = report.errors.length;
try {
  await import(base + "app.js");
  report.modules.push({ name: "app.js", ok: true });
} catch (e) {
  report.modules.push({ name: "app.js", ok: false, error: `${e.name}: ${e.message}\n${e.stack}` });
}
await settle();
check("no uncaught error during start-up", report.errors.length === errorsBefore, report.errors.slice(errorsBefore).join("\n"));

const fetched = (method, path) => env.log.some((f) => f.method === method && f.url.split("?")[0].endsWith(path));
check("loads the message catalog", env.log.some((f) => /\/i18n\/[a-z-]+\.json$/.test(f.url.split("?")[0]) && f.status === 200));
for (const path of ["/api/config", "/api/glossary", "/api/lessons", "/api/runs"]) {
  check(`start-up fetches ${path}`, fetched("GET", path));
}
check("no unrouted API call at start-up", !env.log.some((f) => f.unrouted), env.log.filter((f) => f.unrouted).map((f) => f.url).join(", "));
check("version is shown from /api/config", $("version").textContent === String(routes["GET /api/config"].json.version),
  $("version").textContent);
check("run hint is a real message (no ⟦key⟧)", /\S/.test($("run-hint").textContent) && !$("run-hint").textContent.includes("⟦") === !pseudo,
  $("run-hint").textContent);
check("Run button starts disabled", $("run-btn").disabled);
check("glossary buttons were enhanced", doc.querySelectorAll("button.def[data-term][data-ready]").length > 20,
  String(doc.querySelectorAll("button.def[data-term][data-ready]").length));
check("glossary list rendered", doc.querySelectorAll("#glossary-list dt").length > 20,
  String(doc.querySelectorAll("#glossary-list dt").length));
check("lesson cards rendered", doc.querySelectorAll("#lesson-list li h3").length >= 1);
check("first visit opens the start card and the tour", !$("start-card").hidden && !$("tour").hidden);
check("tour step 1 has a title from the catalog", /\S/.test($("tour-title").textContent));

// ---------------------------------------------------------------- 2b. the educational-use notice must be accepted first

const noticeVersion = String((routes["GET /api/config"].json.disclaimer || {}).version || "");
check("notice modal is open on first visit and the page behind it is inert",
  !$("disclaimer-modal").hidden && $("main").hasAttribute("inert") && doc.activeElement === $("h-disclaimer"));
check("Run button is disabled with the notice hint", $("run-btn").disabled && /\S/.test($("run-hint").textContent)
  && (pseudo ? $("run-hint").textContent.startsWith("⟦") : /notice/.test($("run-hint").textContent)), $("run-hint").textContent);
check("Accept is disabled until the box is ticked", $("disclaimer-accept").disabled);
$("disclaimer-modal").dispatchEvent(Object.assign(new Event("keydown", { bubbles: true, cancelable: true }), { key: "Escape" }));
check("Esc does not close the notice", !$("disclaimer-modal").hidden);
$("disclaimer-accept").click();
check("Accept does nothing while the box is unticked", !$("disclaimer-modal").hidden);
$("disclaimer-ack").click();
check("ticking the box enables Accept", !$("disclaimer-accept").disabled);
$("disclaimer-accept").click();
check("Accept closes the notice, frees the page and remembers the version", $("disclaimer-modal").hidden
  && !$("main").hasAttribute("inert") && localStorage.getItem("windlab-disclaimer-accepted") === noticeVersion,
  `${localStorage.getItem("windlab-disclaimer-accepted")} / ${noticeVersion}`);
check("Run hint moves on once the notice is accepted", $("run-btn").disabled && !/notice/.test($("run-hint").textContent),
  $("run-hint").textContent);

// ---------------------------------------------------------------- 3. the main handlers are wired

const WIRED = [["run-btn", "click"], ["form-naca", "submit"], ["form-upload", "submit"], ["form-builtin", "submit"],
  ["preview-btn", "click"], ["tour-open", "click"], ["tour-close", "click"], ["tour-next", "click"], ["tour-back", "click"],
  ["compare-btn", "click"], ["compare-close", "click"], ["cancel-btn", "click"], ["cancel-yes", "click"],
  ["cancel-no", "click"], ["hist-toggle", "click"], ["theme", "change"], ["glossary-filter", "input"],
  ["airspeed", "input"], ["alpha", "input"], ["angles", "input"], ["stl-file", "change"], ["ref-box", "submit"],
  ["ref-reset", "click"], ["use-suggested", "click"], ["bodies-toggle", "click"], ["units-ok", "change"],
  ["lesson-close", "click"], ["lesson-back", "click"], ["lesson-next", "click"], ["fb-form", "submit"],
  ["start-dismiss", "click"], ["start-tour", "click"], ["start-lesson", "click"], ["sweep-point", "change"],
  ["slice-lines", "change"], ["builtin-shape", "change"], ["about-open", "click"], ["about-close", "click"],
  ["disclaimer-ack", "change"], ["disclaimer-accept", "click"], ["disclaimer-modal", "keydown"]];
for (const [id, type] of WIRED) {
  const el = $(id);
  check(`#${id} listens for ${type}`, el && el.listenerCount(type) > 0, el ? "" : "element missing");
}
for (const sel of ["input[name=shape-kind]", "input[name=run-kind]", "input[name=preset]", "input[name=part-type]",
  "#orient-box [data-rotate]", "#slice-fig [data-field]", "#slice-fig [data-window]"]) {
  const els = doc.querySelectorAll(sel);
  const type = sel.startsWith("input") ? "change" : "click";
  check(`every ${sel} listens for ${type}`, els.length > 0 && els.every((e) => e.listenerCount(type) > 0), String(els.length));
}

// ---------------------------------------------------------------- 4. interactions

// 4a. choosing "My own part" shows the upload form
doc.querySelector('input[name=shape-kind][value="upload"]').click();
check("upload radio shows the upload form", !$("form-upload").hidden && $("form-naca").hidden);
doc.querySelector('input[name=shape-kind][value="naca"]').click();
check("NACA radio shows the wing form again", !$("form-naca").hidden && $("form-upload").hidden);

// 4b. Make this wing: the fake /api/geometry/naca answer reaches Step 2 and enables Run
$("naca-code").value = "0012";
doc.querySelector("#form-naca button[type=submit]").click();
await settle();
check("no uncaught error after Make this wing", report.errors.length === errorsBefore, report.errors.slice(errorsBefore).join("\n"));
check("Make this wing posts /api/geometry/naca", fetched("POST", "/api/geometry/naca"));
check("shape report is shown with a headline", !$("shape-report").hidden && /\S/.test($("size-headline").textContent),
  $("size-headline").textContent);
check("headline comes from the catalog", pseudo ? $("size-headline").textContent.startsWith("⟦")
  : /Your wing is .* long/.test($("size-headline").textContent), $("size-headline").textContent);
check("Step 2 is enabled", !$("cond-fields").disabled);
check("Reynolds number is shown", /\d/.test($("re-value").textContent), $("re-value").textContent);
check("reference box shows the default", !$("ref-box").hidden && /\S/.test($("ref-default").textContent), $("ref-default").textContent);
check("Run button is enabled", !$("run-btn").disabled, $("run-hint").textContent);
check("estimate was requested", fetched("GET", "/api/estimate"));

// 4c. conditions: a bad airspeed disables Run with a message, a good one re-enables it
$("airspeed").value = "500";
$("airspeed").dispatchEvent(new Event("input", { bubbles: true }));
check("airspeed out of range shows an error", !$("airspeed-err").hidden && /\S/.test($("airspeed-err").textContent) && $("run-btn").disabled,
  $("airspeed-err").textContent);
$("airspeed").value = "20";
$("airspeed").dispatchEvent(new Event("input", { bubbles: true }));
check("airspeed back in range clears the error", $("airspeed-err").hidden && !$("run-btn").disabled);
doc.querySelector('input[name=run-kind][value="sweep"]').click();
check("sweep radio shows the angles field", !$("angles-field").hidden && $("alpha-field").hidden && /sweep|⟦/.test($("run-hint").textContent),
  $("run-hint").textContent);
doc.querySelector('input[name=run-kind][value="single"]').click();

// 4d. the tour: Next to the end closes it and remembers that
const tourSteps = Number(($("tour-count").textContent.match(/(\d+)\D*$/) || [])[1]) || 6;
for (let i = 0; i < tourSteps + 1 && !$("tour").hidden; i++) $("tour-next").click();
check("tour closes after the last step", $("tour").hidden && localStorage.getItem("windlab-tour-done") === "1");
$("tour-open").click();
check("tour reopens from the header button", !$("tour").hidden);
$("tour").dispatchEvent(Object.assign(new Event("keydown", { bubbles: true, cancelable: true }), { key: "Escape" }));
check("Esc closes the tour", $("tour").hidden);

// 4e. a glossary (?) button opens a note after itself; Esc closes it
const defBtn = doc.querySelector("button.def[data-term][data-ready]");
if (defBtn) {
  defBtn.click();
  const note = defBtn.nextSibling;
  check("glossary button opens a definition note", note && note.classList && note.classList.contains("def-pop")
    && /\S/.test(note.textContent) && defBtn.getAttribute("aria-expanded") === "true", note ? note.textContent : "no note");
  note.dispatchEvent(Object.assign(new Event("keydown", { bubbles: true, cancelable: true }), { key: "Escape" }));
  check("Esc closes the definition note", defBtn.getAttribute("aria-expanded") === "false" && !defBtn.nextSibling?.classList?.contains("def-pop"));
}
$("glossary-filter").value = "lift";
$("glossary-filter").dispatchEvent(new Event("input", { bubbles: true }));
check("glossary filter narrows the list", doc.querySelectorAll("#glossary-list dt").length > 0
  && doc.querySelectorAll("#glossary-list dt").length < 20 && /\S/.test($("glossary-count").textContent), $("glossary-count").textContent);

// 4f. theme
$("theme").value = "dark";
$("theme").dispatchEvent(new Event("change", { bubbles: true }));
check("theme selector sets data-theme", doc.documentElement.dataset.theme === "dark" && localStorage.getItem("windlab-theme") === "dark");

// 4g. start card
$("start-dismiss").click();
check("start card can be hidden", $("start-card").hidden && localStorage.getItem("windlab-start-hidden") === "1");

// 4h. About WindLab: version, commit and licences from /api/config; Esc closes
$("about-open").click();
const cfg = routes["GET /api/config"].json;
check("About panel opens with version and commit", !$("about").hidden && $("about-version").textContent === String(cfg.version)
  && $("about-commit").textContent === String(cfg.commit), `${$("about-version").textContent} / ${$("about-commit").textContent}`);
check("About panel lists the third-party licences", doc.querySelectorAll("#about-licences li").length === (cfg.third_party || []).length
  && /GPL/.test($("about-licences").textContent), String(doc.querySelectorAll("#about-licences li").length));
check("About panel carries the OpenCFD disclaimer", pseudo ? $("about-trademark").textContent.startsWith("⟦")
  : /not approved or endorsed by OpenCFD Limited/.test($("about-trademark").textContent)
  && /GNU GPL v3/.test($("about-trademark").textContent));
$("about").dispatchEvent(Object.assign(new Event("keydown", { bubbles: true, cancelable: true }), { key: "Escape" }));
check("Esc closes the About panel", $("about").hidden && $("about-open").getAttribute("aria-expanded") === "false");

// 4i. My runs (task 014): every row's accessible names carry the speed and the angle(s), so two runs of one wing
// at 5° and 8° read differently; a sweep names its range; Compare's legend repeats speed and angles
const picks = doc.querySelectorAll("#runs-body input.pick");
const pickLabels = picks.map((b) => b.getAttribute("aria-label") || "");
check("runs table lists the three fixture runs with a Compare box each", picks.length === 3, String(picks.length));
check("compare checkbox labels are all different", new Set(pickLabels).size === pickLabels.length, pickLabels.join(" | "));
check("compare checkbox labels carry the speed and the angle", pickLabels.length > 0
  && pickLabels.every((l) => /\b20\b/.test(l) && /°/.test(l)), pickLabels.join(" | "));
check("the two single-angle labels name 5° and 8°", pickLabels.some((l) => /\b5°/.test(l)) && pickLabels.some((l) => /\b8°/.test(l)),
  pickLabels.join(" | "));
check("a sweep's label gives its angle range and count", pickLabels.some((l) => /-4°.*4°/.test(l) && /\b3\b/.test(l)),
  pickLabels.join(" | "));
const actionLabels = doc.querySelectorAll("#runs-body .actions-cell button").map((b) => b.getAttribute("aria-label") || "");
check("Open and Delete buttons name the run with speed and angle", actionLabels.length === 6
  && actionLabels.every((l) => /\b20\b/.test(l) && /°/.test(l)), actionLabels.join(" | "));
const [pickA, pickB] = picks.filter((b) => !/-4°/.test(b.getAttribute("aria-label")));  // the two single-angle runs
pickA.click();
check("one run ticked: Compare still disabled with a hint", $("compare-btn").disabled && /\S/.test($("compare-hint").textContent));
pickB.click();
check("two runs ticked: Compare enabled and counts them", !$("compare-btn").disabled && /2/.test($("compare-btn").textContent),
  $("compare-btn").textContent);
$("compare-btn").click();
await settle();
check("Compare fetches /api/compare", fetched("GET", "/api/compare"));
check("Compare section opens", !$("compare").hidden && !$("compare-body").hidden);
const legend = doc.querySelectorAll("#compare-legend li");
check("Compare legend names each run with speed and angle", legend.length === 2
  && legend.every((li) => /\b20\b/.test(li.textContent) && /°/.test(li.textContent)), legend.map((li) => li.textContent).join(" | "));
check("Compare legend entries differ", new Set(legend.map((li) => li.textContent)).size === legend.length);
check("Compare charts describe both runs", ["cmp-cl", "cmp-polar", "cmp-ld"].every((id) => {
  const l = $(id).getAttribute("aria-label") || "";
  return /\bA\b/.test(l) && /\bB\b/.test(l) && (id === "cmp-polar" || /°/.test(l));  // the polar has no angles
}), ["cmp-cl", "cmp-polar", "cmp-ld"].map((id) => $(id).getAttribute("aria-label")).join(" || "));
check("Compare table has a row per run point", doc.querySelectorAll("#compare-tbody tr").length === 2,
  String(doc.querySelectorAll("#compare-tbody tr").length));
$("compare-close").click();
check("Close hides the Compare section", $("compare").hidden);

// ---------------------------------------------------------------- 5. every text came from the catalog (pseudo mode)

if (pseudo) {
  const bad = [];
  for (const el of doc.querySelectorAll("[data-i18n]")) {
    const text = el.textContent.trim();
    if (text && !text.startsWith("⟦")) bad.push(`${el.outerHTML}: ${text.slice(0, 40)}`);
  }
  check("every data-i18n element shows the pseudo-localised text", bad.length === 0, bad.slice(0, 5).join(" | "));
  const attrBad = [];
  for (const el of doc.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of el.getAttribute("data-i18n-attr").split(";")) {
      const [attr] = pair.split(":").map((s) => s.trim());
      const v = el.getAttribute(attr) || "";
      if (!v.startsWith("⟦")) attrBad.push(`${el.outerHTML} ${attr}=${v.slice(0, 40)}`);
    }
  }
  check("every data-i18n-attr attribute is pseudo-localised", attrBad.length === 0, attrBad.slice(0, 5).join(" | "));
  const dyn = ["run-hint", "size-headline", "size-detail", "ref-default", "preview-hint", "runs-help", "compare-hint",
    "glossary-count", "tour-title", "tour-text", "cond-hint"];
  const dynBad = dyn.filter((id) => $(id) && $(id).textContent.trim() && !$(id).textContent.trim().startsWith("⟦"));
  check("messages app.js wrote are pseudo-localised", dynBad.length === 0, dynBad.map((id) => `${id}: ${$(id).textContent.slice(0, 40)}`).join(" | "));
}

check("no uncaught error at the end", report.errors.length === 0, report.errors.join("\n"));
check("no console.error from the page", !env.console.some((c) => c.level === "error"),
  env.console.filter((c) => c.level === "error").map((c) => c.text).join(" | "));

const ok = report.modules.every((m) => m.ok) && report.checks.every((c) => c.ok);
report.ok = ok;
console.log(JSON.stringify(report));
process.exit(ok ? 0 : 1);
