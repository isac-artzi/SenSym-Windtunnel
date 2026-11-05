// WindLab page: shape -> conditions -> run -> results. No build step, no external code.
import { LineChart, XYChart, redrawAllCharts } from "./chart.js";
import { DIVERGING, VIRIDIS, cssGradient, fmtArea, fmtLength, legendTicks, slicePositionText } from "./plot.js";
import { SliceViewer, SurfaceViewer, cpRange, decodeSlice, decodeSurface } from "./viewer.js";
import { Preview } from "./preview.js";
import { builtinOutline, nacaOutline } from "./section.js";
import { currentLocale, hasMessage, loadMessages, nf, nfInt, t } from "./i18n.js";

const $ = (id) => document.getElementById(id);
const state = { config: null, geometry: null, file: null, runId: null, poll: null };
const UNIT_NAMES = { mm: "millimetres", cm: "centimetres", m: "metres", in: "inches" };

// ------------------------------------------------------------------ helpers

// Every message a student can see is plain language with a next step - never a stack
// trace or a bare HTTP code (the server sends {"detail": "..."} for every error). These two come from the
// catalog like everything else, but keep an English fallback: when the server is down the catalog itself
// may not have loaded, and "the server is not answering" must still be readable.
const FALLBACK_NO_SERVER = "WindLab's server is not answering. Check that the Ubuntu window where you typed " +
  "windlab start is still open (if not, start it again with  windlab start ), then reload this page.";
const FALLBACK_UNEXPECTED = "WindLab hit an unexpected problem. Please try again; if it keeps happening, restart " +
  "WindLab (Ctrl+C in its Ubuntu window, then  windlab start ) and reload this page.";
const msgNoServer = () => (hasMessage("msg.noServer") ? t("msg.noServer") : FALLBACK_NO_SERVER);
const msgUnexpected = () => (hasMessage("msg.unexpected") ? t("msg.unexpected") : FALLBACK_UNEXPECTED);

class ServerUnreachable extends Error {}

// Demo mode (the GitHub Pages build, scripts/build_static_demo.py): the page is the same, but every
// API call is answered from bundled files by demo.js, uploads and the solver are switched off.
const DEMO = document.documentElement.dataset.demo === "1";
let demoApi = null;

async function api(path, opts = {}) {
  if (DEMO) {
    demoApi ??= (await import("./demo.js")).demoApi;
    return demoApi(path, opts);
  }
  let r;
  try { r = await fetch(path, opts); } catch {
    throw new ServerUnreachable(msgNoServer());  // connection refused / network error
  }
  const isJson = (r.headers.get("content-type") || "").includes("json");
  let body;
  try { body = isJson ? await r.json() : await r.arrayBuffer(); } catch {
    throw new ServerUnreachable(msgNoServer());  // the connection dropped mid-answer
  }
  if (!r.ok) {
    const detail = isJson && body && typeof body.detail === "string" ? body.detail : "";
    throw new Error(detail || (r.status === 413 ? t("msg.tooLarge") : r.status === 404 ? t("msg.notFound") : msgUnexpected()));
  }
  return body;
}

// ---- the fixed text of the page: every element marked data-i18n / data-i18n-attr is filled from the catalog
// at start-up (the English in the markup is the fallback). Messages may carry a little inline markup
// (<b>, <strong>, <i>, <em>, <sub>, <sup>, <code>, and <span data-...> for a number the script fills in);
// richNodes() turns that into elements - never innerHTML.
const RICH_TAG = /<(\/?)(b|strong|i|em|sub|sup|code|span)((?:\s+data-[\w-]+(?:="[^"]*")?)*)\s*>/g;

function richNodes(text) {
  const root = document.createElement("span");
  const stack = [root];
  let last = 0, m;
  RICH_TAG.lastIndex = 0;
  while ((m = RICH_TAG.exec(text)) !== null) {
    if (m.index > last) stack.at(-1).append(document.createTextNode(text.slice(last, m.index)));
    if (m[1]) { if (stack.length > 1) stack.pop(); } else {
      const e = document.createElement(m[2]);
      for (const a of m[3].matchAll(/(data-[\w-]+)(?:="([^"]*)")?/g)) e.setAttribute(a[1], a[2] ?? "");
      stack.at(-1).append(e);
      stack.push(e);
    }
    last = RICH_TAG.lastIndex;
  }
  if (last < text.length) stack.at(-1).append(document.createTextNode(text.slice(last)));
  return [...root.childNodes];
}

function applyPageText() {
  document.querySelectorAll("[data-i18n]").forEach((e) => {
    const key = e.dataset.i18n;
    if (!hasMessage(key)) return;  // keep the English of the markup
    const text = t(key);
    if (text.includes("<")) e.replaceChildren(...richNodes(text)); else e.textContent = text;
  });
  document.querySelectorAll("[data-i18n-attr]").forEach((e) => {
    for (const pair of e.dataset.i18nAttr.split(";")) {
      const [attr, key] = pair.split(":").map((s) => s.trim());
      if (attr && key && hasMessage(key)) e.setAttribute(attr, t(key));
    }
  });
  document.documentElement.lang = currentLocale();
}

function show(el, on = true) { el.hidden = !on; }
function setText(id, text) { $(id).textContent = text; }

// Download links: the server's CSV routes, or in the demo the pre-rendered files beside the page.
function fileUrl(path) {
  if (!DEMO) return path;
  const m = path.match(/^\/api\/runs\/([\w-]+)\/(results|history)\.csv(?:\?point=(\d+))?$/);
  return m ? `./data/runs/${m[1]}/${m[2]}${m[3] != null ? `_${m[3]}` : ""}.csv` : "#";
}
// scrolling honours "reduce motion" (ACC-4): no smooth scrolling for people who turned animation off
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
function scrollTo(el) { el.scrollIntoView({ behavior: REDUCED_MOTION.matches ? "auto" : "smooth", block: "start" }); }
const fmtLen = (m) => fmtLength(m);
// the reference boxes take metres: show metres, plus the friendlier unit when it differs
function inMetres(m) { const f = fmtLen(m), s = `${Number(m.toPrecision(3))} m`; return f === s ? s : `${s} (${f})`; }
function areaInM2(a) {
  const f = fmtArea(a), s = `${Number(a.toPrecision(3))} m²`;
  return f === s ? s : `${s} (${f})`;
}
// numbers go through Intl.NumberFormat (i18n.js); messages through t() and the catalog i18n/en.json
function fmtNum(x, d = 3) { return nf(x, d); }
function fmtTime(s) {
  if (s == null) return "";
  if (s < 60) return t("run.timeLeft.lessThanMinute");
  const m = Math.round(s / 60);
  return m === 1 ? t("run.timeLeft.oneMinute") : t("run.timeLeft.minutes", { m });
}
function list(id, items) {
  const ul = $(id);
  ul.replaceChildren(...(items || []).map((t) => Object.assign(document.createElement("li"), { textContent: t })));
}
function markStep(step, how) {
  document.querySelectorAll(".steps li").forEach((li) => {
    if (li.dataset.step === step) li.classList.toggle(how, true);
    if (how === "current" && li.dataset.step !== step) li.classList.remove("current");
  });
}

// ------------------------------------------------------------------ step 1: shape

let shapeViewer = null, resultViewer = null;

function shapeKind() { return document.querySelector("input[name=shape-kind]:checked").value; }

document.querySelectorAll("input[name=shape-kind]").forEach((r) => r.addEventListener("change", () => {
  show($("form-naca"), shapeKind() === "naca");
  show($("form-builtin"), shapeKind() === "builtin");
  show($("form-upload"), shapeKind() === "upload");
  show($("shape-error"), false);
}));

function shapeBusy(text) {
  setText("shape-busy", text || "");
  show($("shape-busy"), !!text);
  document.querySelectorAll("#form-naca button[type=submit], #form-upload button[type=submit], " +
    "#form-builtin button[type=submit]").forEach((b) => { b.disabled = !!text; });
}

// ---- built-in bodies (sphere, cylinder, plate, cube, teardrop): the form follows the server's catalogue

function builtinSpec() {
  const shapes = (state.config || {}).builtin_shapes || [];
  return shapes.find((s) => s.id === $("builtin-shape").value) || shapes[0];
}

function renderBuiltinForm() {
  const shapes = (state.config || {}).builtin_shapes || [];
  const sel = $("builtin-shape");
  if (!sel.options.length) {
    sel.replaceChildren(...shapes.map((s) => el("option", { value: s.id, textContent: s.label })));
  }
  const spec = builtinSpec();
  if (!spec) return;
  setText("builtin-help", spec.description);
  $("builtin-params").replaceChildren(...spec.params.map((p) => {
    const id = `builtin-${p.id}`;
    const input = el("input", { id, type: "number", value: String(p.default), step: String(p.step), min: String(p.min),
      max: String(p.max), required: true });
    input.setAttribute("aria-describedby", `${id}-help`);
    return el("div", { className: "field" }, el("label", { htmlFor: id, textContent: p.label }), input,
      el("p", { id: `${id}-help`, className: "help", textContent: `${p.min} to ${p.max}` }));
  }));
}

$("builtin-shape").addEventListener("change", renderBuiltinForm);

$("form-builtin").addEventListener("submit", async (e) => {
  e.preventDefault();
  shapeError("");
  const spec = builtinSpec();
  if (!spec) return;
  const body = { shape: spec.id };
  for (const p of spec.params) {
    const v = Number($(`builtin-${p.id}`).value);
    if (!isFinite(v) || $(`builtin-${p.id}`).value.trim() === "") {
      return shapeError(t("shape.error.enterParam", { label: p.label.toLowerCase() }));
    }
    body[p.id] = v;
  }
  shapeBusy(t("shape.busy.makingShape"));
  try {
    useGeometry(await api("/api/geometry/builtin", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    }));
  } catch (err) { shapeError(err.message); } finally { shapeBusy(""); }
});

function shapeError(msg) {
  setText("shape-error", msg);
  show($("shape-error"), !!msg);
}

$("form-naca").addEventListener("submit", async (e) => {
  e.preventDefault();
  shapeError("");
  const body = {
    code: $("naca-code").value.trim(),
    chord: Number($("naca-chord").value),
    span: Number($("naca-span").value),
  };
  if (!/^[0-9]{4}$/.test(body.code)) return shapeError(t("shape.error.nacaCode"));
  shapeBusy(t("shape.busy.makingWing"));
  try {
    useGeometry(await api("/api/geometry/naca", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    }));
  } catch (err) { shapeError(err.message); } finally { shapeBusy(""); }
});

// A STEP file (SolidWorks: File > Save As > STEP AP214) says which units it uses, so the units choice gets
// an "as written in the file" entry, pre-selected; an STL file does not, so the student must choose.
function isStepName(name) { return /\.(step|stp)$/i.test(name || ""); }

function unitsChoicesForFile(file) {
  const fromFile = $("units-from-file"), sel = $("stl-units");
  const isStep = !!file && isStepName(file.name);
  fromFile.hidden = !isStep;
  if (isStep && (sel.value === "" || sel.value === "file")) sel.value = "file";
  if (!isStep && sel.value === "file") sel.value = "";
  setText("units-help", t(isStep ? "shape.unitsHelp.step" : "shape.unitsHelp.stl"));
  const stepInfo = state.config && state.config.step_import;
  show($("step-unavailable"), isStep && !!stepInfo && !stepInfo.available);
  if (isStep && stepInfo && !stepInfo.available) setText("step-unavailable", stepInfo.message);
}

$("stl-file").addEventListener("change", () => unitsChoicesForFile($("stl-file").files[0]));

async function uploadFile(file, units, bodies = "largest") {
  shapeError("");
  const maxMb = state.config ? state.config.max_upload_mb : 200;
  if (file.size > maxMb * 2 ** 20) {
    return shapeError(t("shape.error.fileTooLarge", { size: nf(file.size / 2 ** 20, 0), limit: maxMb }));
  }
  shapeBusy(t(isStepName(file.name) ? "shape.busy.uploadingStep" : "shape.busy.uploadingStl"));
  try {
    const q = new URLSearchParams({ name: file.name.slice(0, 200), bodies });
    if (units && units !== "file") q.set("units", units);
    state.bodies = bodies;
    useGeometry(await api(`/api/geometry?${q}`, {
      method: "POST", headers: { "content-type": "application/octet-stream" }, body: file,
    }));
  } catch (err) { shapeError(err.message); } finally { shapeBusy(""); }
}

$("form-upload").addEventListener("submit", (e) => {
  e.preventDefault();
  const file = $("stl-file").files[0], units = $("stl-units").value;
  if (!file) return shapeError(t("shape.error.chooseFile"));
  if (!units) { $("stl-units").focus(); return shapeError(t("shape.error.chooseUnits")); }
  state.file = file;
  uploadFile(file, units, state.bodies || "largest");
});

$("use-suggested").addEventListener("click", () => {
  const u = $("use-suggested").dataset.units;
  if (!state.file || !u) return;
  $("stl-units").value = u;
  uploadFile(state.file, u, state.bodies || "largest");
});

// a STEP file with several separate bodies: the largest by default, or all of them together
$("bodies-toggle").addEventListener("click", () => {
  const g = state.geometry;
  if (!state.file || !g || !g.source) return;
  const next = g.source.bodies_used === "all" ? "largest" : "all";
  uploadFile(state.file, $("stl-units").value, next);
});

// After a STEP upload: pre-select the unit the file declared, and say so (the student confirms the size).
function showStepFacts(g) {
  const src = g.source;
  const isStep = !!src && src.format === "step";
  show($("units-from-file-note"), isStep && !g.size_suspicious);
  show($("bodies-box"), isStep && src.bodies_available > 1);
  if (!isStep) return;
  if (src.units_from_file && g.units) $("stl-units").value = g.units;
  const [lx] = g.size_m;
  setText("units-from-file-text", src.converted_to_metres
    ? t("shape.stepFacts.converted", { unit: src.unit_name_in_file || t("shape.stepFacts.unusualUnit"), length: fmtLen(lx) })
    : src.unit_declared
      ? t("shape.stepFacts.declared", { units: UNIT_NAMES[src.unit_declared], length: fmtLen(lx) })
      : t("shape.stepFacts.assumed", { length: fmtLen(lx) }));
  if (src.bodies_available > 1) {
    const n = src.bodies_available;
    setText("bodies-text", t(src.bodies_used === "all" ? "shape.bodies.all" : "shape.bodies.largest", { n }));
    setText("bodies-toggle", t(src.bodies_used === "all" ? "shape.bodies.useLargest" : "shape.bodies.useAll"));
  }
}

$("units-ok").addEventListener("change", updateRunButton);

async function useGeometry(g, { keepUnits = false } = {}) {
  const prevOk = $("units-ok").checked;
  state.geometry = g;
  state.preview = null;
  refreshEstimates();
  show($("shape-report"));
  const [lx, ly, lz] = g.size_m;
  setText("size-headline", g.kind === "naca" ? t("shape.headline.wing", { chord: fmtLen(lx), span: fmtLen(ly) })
    : g.kind === "builtin" ? t("shape.headline.builtin", { name: g.name }) : t("shape.headline.part", { length: fmtLen(lx) }));
  setText("size-detail", t(g.kind === "upload" ? "shape.sizeDetail.upload" : "shape.sizeDetail",
    { x: fmtLen(lx), y: fmtLen(ly), z: fmtLen(lz), units: UNIT_NAMES[g.units] }));
  list("shape-problems", g.problems);
  list("shape-warnings", g.warnings);
  list("shape-messages", g.messages);
  showMeshNotes();
  // units check: prominent, and a run needs an explicit confirmation
  show($("units-confirm"), g.size_suspicious);
  $("units-ok").checked = keepUnits && prevOk;
  if (g.size_suspicious) {
    setText("units-confirm-text", t("shape.unitsConfirm", { units: UNIT_NAMES[g.units], length: fmtLen(g.longest_m) }));
    const b = $("use-suggested");
    show(b, !!g.suggested_units && !!state.file);
    if (g.suggested_units) {
      b.dataset.units = g.suggested_units;
      b.textContent = t("shape.useSuggested", { units: UNIT_NAMES[g.suggested_units] });
    }
  }
  showStepFacts(g);
  show($("shape-viewer").parentElement, g.usable && !DEMO);
  show($("orient-box"), g.usable && g.kind === "upload");
  if (!keepUnits) show($("orient-note"), false);
  show($("ref-box"), g.usable && !!g.reference);
  if (g.usable && g.reference) showReference(g);
  if (g.usable) {
    try {
      state.preview = decodeSurface(await api(`/api/geometry/${g.geometry_id}/preview`));
      if (state.geometry !== g) return;
      show($("shape-viewer").parentElement);  // the demo only has 3D views of the lessons' shapes
      showShape();
    } catch { /* preview is optional */ }
  }
  $("cond-fields").disabled = !g.usable;
  show($("cond-hint"), !g.usable);
  setText("cond-hint", g.usable ? "" : t("shape.condHint.fix"));
  if (g.usable) { markStep("shape", "done"); markStep("conditions", "current"); }
  updateReynolds();
  updateRunButton();
  updatePreviewButton();
  if (g.usable && !$("preview-fig").hidden) refreshPreview();
}

// ---- instant 2D preview (PRD Tier 0): the mid-span outline runs through a browser-side flow model

let preview = null;

function updatePreviewButton() {
  const g = state.geometry, ok = !!(g && g.usable);
  $("preview-btn").disabled = !ok;
  setText("preview-hint", t(ok ? "preview.hint.ready" : "preview.hint.noShape"));
}

// The outline to preview: NACA wings and built-in bodies from their formulas (also in the demo),
// uploads from a cut through the STL made by the server.
async function sectionForGeometry(g) {
  if (g.kind === "naca" && g.naca) {
    return { loops: [nacaOutline(g.naca.code, g.naca.chord)], referenceLength: g.reference.length, name: g.name };
  }
  if (g.kind === "builtin" && g.builtin) {
    const { shape, ...params } = g.builtin;
    return { loops: [builtinOutline(shape, params)], referenceLength: g.reference.length, name: g.name };
  }
  const s = await api(`/api/geometry/${g.geometry_id}/section`);
  return { loops: s.loops, referenceLength: s.reference_length, name: g.name };
}

async function refreshPreview() {
  const g = state.geometry;
  if (!g || !g.usable) return;
  show($("preview-fig"));
  $("preview-btn").setAttribute("aria-expanded", "true");
  $("preview-btn").textContent = t("preview.close");
  try {
    const section = await sectionForGeometry(g);
    if (state.geometry !== g) return;  // the student changed the shape meanwhile
    preview ??= new Preview();
    preview.setAngle(Number($("alpha").value) || 0);
    preview.setShape(section);
    updateReynolds();
  } catch (err) {
    setText("preview-note", t("preview.failed", { message: err.message }));
    show($("preview-note"));
  }
}

$("preview-btn").addEventListener("click", () => {
  if (!$("preview-fig").hidden) {
    preview?.pause();
    show($("preview-fig"), false);
    $("preview-btn").setAttribute("aria-expanded", "false");
    $("preview-btn").textContent = t("preview.open");
    return;
  }
  refreshPreview();
  scrollTo($("preview-fig"));
});

function showShape() {
  if (!state.preview) return;
  shapeViewer ??= new SurfaceViewer($("shape-viewer"));
  const a = Number($("alpha").value);
  shapeViewer.show(state.preview, { alphaDeg: isFinite(a) ? a : 0, colourByCp: false, annotate: true });
  const [lx, ly, lz] = state.geometry.size_m;
  $("shape-viewer").setAttribute("aria-label", t("shape.viewerLabel", { x: fmtLen(lx), y: fmtLen(ly), z: fmtLen(lz) }));
}

// ---- orientation (uploads only)

document.querySelectorAll("#orient-box [data-rotate]").forEach((b) => b.addEventListener("click", async () => {
  const g = state.geometry;
  if (!g) return;
  const buttons = document.querySelectorAll("#orient-box button");
  buttons.forEach((x) => { x.disabled = true; });
  try {
    const r = await api(`/api/geometry/${g.geometry_id}/orient`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ rotate: b.dataset.rotate }),
    });
    await useGeometry(r, { keepUnits: true });
    const [lx, ly, lz] = r.size_m;
    setText("orient-note", t("shape.orientNote", { note: r.note, x: fmtLen(lx), y: fmtLen(ly), z: fmtLen(lz) }) +
      (g.reference && g.reference.custom && g.reference.custom.length ? t("shape.orientReset") : ""));
    show($("orient-note"));
  } catch (err) {
    setText("orient-note", err.message); show($("orient-note"));
  } finally { buttons.forEach((x) => { x.disabled = false; }); b.focus(); }
}));

// ---- reference length / area

function showReference(g) {
  const r = g.reference, d = g.reference_default || r;
  $("ref-length").value = Number(r.length.toPrecision(4));
  $("ref-area").value = Number(r.area.toPrecision(4));
  setText("ref-default", t("shape.refDefault", { how: d.how, length: inMetres(d.length), area: areaInM2(d.area) }));
  showPartType(g);
  show($("ref-error"), false); show($("ref-note"), false);
}

// Uploads: a body's coefficients sit on its frontal area, a wing's on its planform area. WindLab guesses from
// the proportions and the student can say otherwise; both areas are measured from the surface by the server.
function showPartType(g) {
  const pt = g.part_type, upload = g.kind === "upload" && !!pt;
  show($("part-type-field"), upload); show($("part-type-note"), upload);
  if (!upload) return;
  const radio = document.querySelector(`input[name=part-type][value="${pt.used}"]`);
  if (radio) radio.checked = true;
  const a = g.areas || {};
  const area = pt.used === "body" ? a.frontal : a.planform;
  const method = t(a.method === "silhouette" ? "shape.areaMethod.silhouette" : "shape.areaMethod.box");
  const guess = t(`shape.partType.guess.${pt.detected}`);
  setText("part-type-note", t(pt.used === "body" ? "shape.partType.body" : "shape.partType.wing",
    { area: areaInM2(area ?? (g.reference_default || g.reference).area), method }) + " " +
    (pt.chosen ? t("shape.partType.chosen") : guess));
}

document.querySelectorAll("input[name=part-type]").forEach((r) => r.addEventListener("change", () => {
  if (!r.checked || !state.geometry) return;
  sendReference({ part_type: r.value }, t("shape.partTypeSaved", { kind: t(`shape.kind.${r.value}`) }));
}));

async function sendReference(body, message) {
  const g = state.geometry;
  show($("ref-error"), false); show($("ref-note"), false);
  try {
    const r = await api(`/api/geometry/${g.geometry_id}/reference`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    state.geometry = { ...g, ...r };
    showReference(state.geometry);
    showMeshNotes();  // the thin-feature notes depend on the reference length (it sets the cell size)
    updateReynolds();
    setText("ref-note", message); show($("ref-note"));
  } catch (err) { setText("ref-error", err.message); show($("ref-error")); }
}

$("ref-box").addEventListener("submit", (e) => {
  e.preventDefault();
  const L = Number($("ref-length").value), A = Number($("ref-area").value);
  if (!($("ref-length").value.trim() && isFinite(L) && L > 0) || !($("ref-area").value.trim() && isFinite(A) && A > 0)) {
    setText("ref-error", t("shape.refError.positive")); show($("ref-error"));
    return;
  }
  const d = state.geometry.reference_default || state.geometry.reference;
  const same = (a, b) => Math.abs(a - b) <= 1e-4 * Math.abs(b);
  sendReference({ length: same(L, d.length) ? null : L, area: same(A, d.area) ? null : A }, t("shape.refSaved"));
});
$("ref-reset").addEventListener("click", () => sendReference({ length: null, area: null }, t("shape.refReset")));

// ------------------------------------------------------------------ step 2: conditions

function checkField(id, lo, hi, label, unit) {
  const el = $(id), err = $(`${id}-err`);
  const v = Number(el.value);
  let msg = "";
  if (el.value.trim() === "" || !isFinite(v)) msg = t("cond.error.enter", { label });
  else if (v < lo || v > hi) msg = t("cond.error.range", { label, lo, hi, unit });
  err.textContent = msg;
  show(err, !!msg);
  el.setAttribute("aria-invalid", msg ? "true" : "false");
  return msg ? null : v;
}

function runKind() { return document.querySelector("input[name=run-kind]:checked").value; }
function presetChoice() { return document.querySelector("input[name=preset]:checked")?.value || "quick"; }

// Mesh choices this computer offers (Detailed only on a big, fast one) and their time estimates.
function applyPresets(cfg) {
  const ids = new Set((cfg.presets || []).map((p) => p.id));
  show($("preset-detailed"), ids.has("detailed"));
  if (!ids.has(presetChoice())) document.querySelector("input[name=preset][value=quick]").checked = true;
}

let estimateTimer = null;
function refreshEstimates() {
  clearTimeout(estimateTimer);
  estimateTimer = setTimeout(async () => {
    const q = new URLSearchParams();
    if (state.geometry) q.set("geometry_id", state.geometry.geometry_id);
    const cond = state.config ? conditions() : null;
    q.set("n_angles", String(runKind() === "sweep" && cond?.angles ? cond.angles.length : 1));
    try {
      const est = await api(`/api/estimate?${q}`);
      for (const [id, e] of Object.entries(est.presets || {})) {
        const el = $(`est-${id}`);
        if (el) el.textContent = t("cond.est.suffix", { text: e.text });
      }
      setText("est-help", est.basis === "measured" ? t("cond.est.measured", { n: est.n_procs }) : t("cond.est.rough"));
      state.estimates = est;
    } catch { /* the demo has no estimates; the page works without them */ }
  }, 150);
}
document.querySelectorAll("input[name=preset]").forEach((r) => r.addEventListener("change", () => {
  updateRunButton();
  showMeshNotes();
}));

// Thin plates and fins (uploads): what the chosen mesh does about them, said before the run. The server
// measured the part's thickness at upload and writes the notes per mesh choice (windlab.geometry.thin).
function showMeshNotes() {
  const notes = state.geometry?.thin_features?.[presetChoice()]?.notes || [];
  list("mesh-notes", notes);
  show($("mesh-notes"), notes.length > 0);
}

// "-4, 0, 4" -> [-4, 0, 4] (sorted, no repeats), or an error message
function parseAngles(text, lo, hi, max) {
  const parts = text.split(/[,;\s]+/).filter((t) => t !== "");
  const vals = parts.map((t) => Number(t.replace("−", "-")));
  if (!parts.length) return { error: t("cond.angles.enter") };
  if (vals.some((v) => !isFinite(v))) return { error: t("cond.angles.notNumber") };
  if (vals.some((v) => v < lo || v > hi)) return { error: t("cond.angles.range", { lo, hi }) };
  const uniq = [...new Set(vals)].sort((a, b) => a - b);
  if (uniq.length < 2) return { error: t("cond.angles.atLeastTwo") };
  if (uniq.length > max) return { error: t("cond.angles.atMost", { max }) };
  return { angles: uniq };
}

function checkAngles() {
  const c = state.config, err = $("angles-err");
  const r = parseAngles($("angles").value, c.alpha_deg.min, c.alpha_deg.max, (c.sweep || {}).max_angles || 8);
  err.textContent = r.error || "";
  show(err, !!r.error);
  $("angles").setAttribute("aria-invalid", r.error ? "true" : "false");
  return r.angles || null;
}

function conditions() {
  const c = state.config;
  const v = checkField("airspeed", c.airspeed.min, c.airspeed.max, t("cond.label.airspeed"), t("cond.unit.ms"));
  if (runKind() === "sweep") {
    const angles = checkAngles();
    return v === null || angles === null ? null : { airspeed: v, kind: "sweep", angles };
  }
  const a = checkField("alpha", c.alpha_deg.min, c.alpha_deg.max, t("cond.label.alpha"), t("cond.unit.deg"));
  return v === null || a === null ? null : { airspeed: v, alpha_deg: a };
}

document.querySelectorAll("input[name=run-kind]").forEach((r) => r.addEventListener("change", () => {
  const sweep = runKind() === "sweep";
  show($("alpha-field"), !sweep);
  show($("angles-field"), sweep);
  if (state.config) conditions();
  updateRunButton();
  refreshEstimates();
}));
$("angles").addEventListener("input", () => { if (state.config) checkAngles(); updateRunButton(); refreshEstimates(); });

function updateReynolds() {
  const g = state.geometry;
  if (!g || !g.reference || !state.config) { setText("re-value", "–"); return; }
  const v = Number($("airspeed").value), L = g.reference.length;
  setText("re-length", t("cond.re.length", { length: fmtLen(L) }));
  if (!(v > 0)) { setText("re-value", "–"); return; }
  const re = (v * L) / state.config.air.nu;
  setText("re-value", re >= 1e6 ? t("cond.re.million", { value: nf(re / 1e6, 2) }) : nfInt(Math.round(re)));
  preview?.setCase(re);
}

["airspeed", "alpha"].forEach((id) => $(id).addEventListener("input", () => {
  if (state.config) conditions();
  updateReynolds();
  updateRunButton();
  if (id === "alpha" && state.geometry && state.geometry.usable) {
    showShape();
    const a = Number($("alpha").value);
    if (isFinite(a) && !$("preview-fig").hidden) preview?.setAngle(a);  // the preview follows the angle live
  }
}));

// ------------------------------------------------------------------ step 3: run

function updateRunButton() {
  const g = state.geometry;
  let why = "";
  if (DEMO) {
    $("run-btn").disabled = true;
    setText("run-hint", t("run.hint.demo"));
    return;
  }
  if (!disclaimerAccepted()) why = t("run.hint.disclaimer");
  else if (!g) why = t("run.hint.chooseShape");
  else if (!g.usable) why = t("run.hint.fixShape");
  else if (g.size_suspicious && !$("units-ok").checked) why = t("run.hint.confirmSize");
  else if (state.config && !conditions()) why = t("run.hint.fixConditions");
  else if (state.poll) why = t("run.hint.inProgress");
  $("run-btn").disabled = !!why;
  const n = !why && runKind() === "sweep" ? (conditions() || {}).angles?.length : 0;
  const est = state.estimates?.presets?.[presetChoice()];
  const label = t(`run.label.${["quick", "standard", "detailed"].includes(presetChoice()) ? presetChoice() : "detailed"}`);
  const when = est ? t("run.when.estimated", { text: est.text }) : t("run.when.unknown");
  setText("run-hint", why || (n ? t("run.hint.sweep", { n, when }) : t("run.hint.single", { label, when })));
}

const STAGE_ORDER = ["blockMesh", "surfaceFeatureExtract", "decomposePar", "snappyHexMesh", "meshReuse", "checkMesh",
  "potentialFoam", "changeDictionary", "simpleFoam", "surface"];
const PRESET_KEYS = { quick: "res.preset.quick", standard: "res.preset.standard", detailed: "res.preset.detailed" };
const charts = {};

$("run-btn").addEventListener("click", async () => {
  const cond = conditions();
  if (!cond || !state.geometry || !disclaimerAccepted()) return;
  show($("run-error"), false);
  $("run-btn").disabled = true;
  try {
    const run = await api("/api/runs", {
      // the server refuses a run without the acceptance header (windlab/disclaimer.py)
      method: "POST", headers: { "content-type": "application/json", [DISCLAIMER_HEADER]: notice.required || "" },
      body: JSON.stringify({
        geometry_id: state.geometry.geometry_id, ...cond, preset: presetChoice(),
        units_confirmed: $("units-ok").checked,
      }),
    });
    markStep("conditions", "done");
    watchRun(run);
    refreshRuns();
  } catch (err) {
    setText("run-error", err.message); show($("run-error"));
    updateRunButton();
  }
});

function ensureCharts() {
  charts.cl ??= new LineChart($("chart-cl"), { name: "CL", digits: 3 });
  charts.cd ??= new LineChart($("chart-cd"), { name: "CD", digits: 4 });
}

// Follow a queued or running run in Step 3 (also used to re-attach after a page reload).
function watchRun(run) {
  stopPolling();
  state.runId = run.run_id;
  markStep("run", "current");
  show($("run-progress")); show($("run-note"), false); show($("run-error"), false);
  show($("results"), false); show($("res-hint"));
  ensureCharts();
  charts.cl.set([]); charts.cd.set([]);
  closeCancelConfirm();
  renderRun(run);
  state.poll = setInterval(pollRun, 1500);
  updateRunButton();
}

function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

async function pollRun() {
  const id = state.runId;
  try {
    const s = await api(`/api/runs/${id}`);
    if (id !== state.runId) return;  // the student opened another run meanwhile
    if (state.pollFailures >= 3) { show($("run-error"), false); }  // back in contact
    state.pollFailures = 0;
    renderRun(s);
    if (!["queued", "running"].includes(s.status)) {
      stopPolling();
      updateRunButton();
      finishedRun(s);
      refreshRuns();
    }
  } catch (err) {
    // transient: try again on the next tick; after a few misses, say so (and keep trying)
    state.pollFailures = (state.pollFailures || 0) + 1;
    if (state.pollFailures === 3) {
      setText("run-error", err instanceof ServerUnreachable ? t("run.lostContact", { msg: msgNoServer() }) : err.message);
      show($("run-error"));
    }
  }
}

function finishedRun(s) {
  show($("cancel-btn"), false); closeCancelConfirm();
  if (s.status === "done") showResults(s);
  else if (s.status === "cancelled") {
    setText("run-note", t("run.note.cancelled"));
    show($("run-note"));
  } else { setText("run-error", s.error || t("run.error.generic")); show($("run-error")); }
}

// ---- cancel, with an in-page confirmation (no browser dialogs)

function closeCancelConfirm() {
  show($("cancel-confirm"), false);
  $("cancel-btn").setAttribute("aria-expanded", "false");
}

$("cancel-btn").addEventListener("click", () => {
  show($("cancel-confirm"));
  $("cancel-btn").setAttribute("aria-expanded", "true");
  $("cancel-no").focus();
});
$("cancel-no").addEventListener("click", () => { closeCancelConfirm(); $("cancel-btn").focus(); });
$("cancel-yes").addEventListener("click", async () => {
  $("cancel-yes").disabled = true;
  setText("stage-label", t("run.stopping"));
  try {
    renderRun(await api(`/api/runs/${state.runId}/cancel`, { method: "POST" }));
    closeCancelConfirm();
  } catch (err) {
    setText("run-error", err.message); show($("run-error"));
  } finally { $("cancel-yes").disabled = false; }
});
$("cancel-confirm").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeCancelConfirm(); $("cancel-btn").focus(); }
});

// The stages this run goes through: a reused mesh replaces the five meshing stages, a warm
// start ("changeDictionary") replaces the first guess; neither shows unless it happened.
function visibleStages(s) {
  const ran = (k) => s.stage === k || (s.stage_times && k in s.stage_times);
  const reused = ran("meshReuse"), warm = ran("changeDictionary");
  const meshing = ["blockMesh", "surfaceFeatureExtract", "decomposePar", "snappyHexMesh", "checkMesh"];
  return STAGE_ORDER.filter((k) => {
    if (k === "meshReuse") return reused;
    if (k === "changeDictionary") return warm;
    if (meshing.includes(k) && reused) return false;
    if (k === "potentialFoam" && warm && s.params.kind !== "sweep") return false;
    return k !== "decomposePar" || s.params.n_procs > 1;
  });
}

function renderRun(s) {
  const stopping = s.status === "running" && s.cancel_requested;
  setText("stage-label", stopping ? t("run.stopping") : s.status === "queued" && s.queue_position > 0
    ? t("run.queued", { stage: s.stage_label, position: s.queue_position }) : s.stage_label);
  show($("cancel-btn"), ["queued", "running"].includes(s.status) && !stopping);
  const pct = Math.round(s.percent || 0);
  $("bar-fill").style.width = `${pct}%`;
  $("bar").setAttribute("aria-valuenow", String(pct));
  $("bar").setAttribute("aria-valuetext", `${pct} percent, ${s.stage_label}`);
  setText("eta", s.status === "running" ? t("run.eta", { pct, left: s.time_left || fmtTime(s.seconds_left) })
    : (s.status === "done" ? t("run.done") : ""));
  const stages = state.config.stages;
  const now = STAGE_ORDER.indexOf(s.stage);
  $("stage-list").replaceChildren(...visibleStages(s).map((k) => {
    const li = document.createElement("li");
    const i = STAGE_ORDER.indexOf(k);
    // colour, an icon (CSS) and words: screen readers hear "finished" / "now" (WCAG 1.4.1)
    let state = "waiting";
    if (s.status === "done" || (now >= 0 && i < now)) { li.className = "done"; state = "finished"; }
    else if (i === now) { li.className = "now"; state = "now"; }
    li.append(document.createTextNode(stages[k] + " "),
      el("span", { className: "sr-only", textContent: t("run.stage.state", { state: t(`run.stage.${state}`) }) }));
    return li;
  }));
  state.history = s.history || [];
  if (!$("hist-wrap").hidden) renderHistoryTable();
  if (s.history && s.history.length) {
    charts.cl.set(s.history.map((h) => [h[0], h[1]]));
    charts.cd.set(s.history.map((h) => [h[0], h[2]]));
    const p = s.params;
    const angle = p.kind === "sweep" && s.status === "running" && s.points && s.points[s.point_index]
      ? t("run.iter.angle", { alpha: s.points[s.point_index].alpha_deg, i: s.point_index + 1, n: s.points.length }) : "";
    setText("iter-text", t("run.iter", { iteration: s.iteration, angle, max: p.max_iterations }));
  }
  if (s.params.kind === "sweep" && s.points && s.status === "running") {
    const done = s.points.filter((pt) => pt.status === "done");
    if (done.length) {
      setText("iter-text", t("run.iter.finished", { text: $("iter-text").textContent,
        list: done.map((pt) => t("run.iter.item", { alpha: pt.alpha_deg, cl: fmtNum(pt.cl, 3) })).join(", ") }));
    }
  }
}

// ---- CL / CD history as a table (the accessible view of the two charts)

function renderHistoryTable() {
  const h = state.history || [];
  // newest first; the server already thins long histories to at most 400 rows
  $("hist-body").replaceChildren(...h.slice().reverse().map((r) => el("tr", {},
    el("td", { className: "num", textContent: String(r[0]) }),
    el("td", { className: "num", textContent: nf(r[1], 4) }),
    el("td", { className: "num", textContent: nf(r[2], 5) }))));
  if (!h.length) $("hist-body").replaceChildren(el("tr", {}, el("td", { colSpan: 3, textContent: t("run.hist.none") })));
}

$("hist-toggle").addEventListener("click", () => {
  const open = $("hist-wrap").hidden;
  show($("hist-wrap"), open);
  $("hist-toggle").setAttribute("aria-expanded", String(open));
  setText("hist-toggle", t(open ? "run.hist.hide" : "run.hist.show"));
  if (open) renderHistoryTable();
});

// ------------------------------------------------------------------ glossary (PRD UX-1)

// Definitions come from /api/glossary (one JSON source, shared with the lessons and docs/GLOSSARY.md).
// Every <button class="def" data-term="..."> opens a note right after itself: keyboard reachable
// (it is a button), aria-expanded / aria-controls, Esc closes and returns focus. No hover tooltips.
const glossary = { terms: new Map(), seq: 0 };

function termNote(term) {
  const note = el("span", { className: "def-pop", role: "note" });
  note.append(el("b", { textContent: term.term }), document.createTextNode(`: ${term.short}`));
  if (term.more) note.append(document.createTextNode(` ${term.more}`));
  note.append(document.createTextNode(" "), el("a", { href: `#gl-${term.id}`, textContent: t("glossary.link") }));
  return note;
}

function enhanceDefinitions(root = document) {
  root.querySelectorAll("button.def[data-term]").forEach((b) => {
    if (b.dataset.ready) return;
    const term = glossary.terms.get(b.dataset.term);
    if (!term) { b.hidden = true; return; }  // unknown id: better no button than a broken one
    b.dataset.ready = "1";
    const id = `def-pop-${++glossary.seq}`;
    b.setAttribute("aria-expanded", "false");
    b.setAttribute("aria-controls", id);
    if (!b.getAttribute("aria-label")) b.setAttribute("aria-label", t("glossary.whatIs", { term: term.term }));
    let note = null;
    const close = () => { if (note) { note.remove(); note = null; } b.setAttribute("aria-expanded", "false"); };
    b.addEventListener("click", () => {
      if (note) { close(); return; }
      note = termNote(term);
      note.id = id;
      note.tabIndex = -1;
      note.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); b.focus(); } });
      b.after(note);
      b.setAttribute("aria-expanded", "true");
    });
    b.addEventListener("keydown", (e) => { if (e.key === "Escape" && note) close(); });
  });
}

const t_ = t;  // renderGlossary names its loop variable t (a term); t_ is the catalog's t()

function renderGlossary(filter = "") {
  const q = filter.trim().toLowerCase();
  const all = [...glossary.terms.values()].sort((a, b) => a.term.localeCompare(b.term));
  const hit = (t) => !q || [t.term, t.short, t.more || "", ...(t.aliases || [])].join(" ").toLowerCase().includes(q);
  const shown = all.filter(hit);
  $("glossary-list").replaceChildren(...shown.flatMap((t) => {
    const dt = el("dt", { id: `gl-${t.id}` }, document.createTextNode(t.term));
    if (t.aliases && t.aliases.length) dt.append(" ", el("span", { className: "alias", textContent: `(${t.aliases.join(", ")})` }));
    const dd = el("dd", {}, document.createTextNode(t.short));
    if (t.more) dd.append(el("span", { className: "more", textContent: t.more }));
    if (t.see && t.see.length) {
      const see = el("span", { className: "see" }, document.createTextNode(t_("glossary.seeAlso")));
      t.see.forEach((s, i) => {
        const ref = glossary.terms.get(s);
        if (!ref) return;
        if (i) see.append(", ");
        see.append(el("a", { href: `#gl-${s}`, textContent: ref.term }));
      });
      dd.append(see);
    }
    return [dt, dd];
  }));
  setText("glossary-count", q ? t_("glossary.count.match", { shown: shown.length, all: all.length, filter: filter.trim() })
    : t_("glossary.count.all", { all: all.length }));
}

async function loadGlossary() {
  try {
    const g = await api("/api/glossary");
    g.terms.forEach((t) => glossary.terms.set(t.id, t));
  } catch { /* the page works without definitions */ }
  enhanceDefinitions();
  renderGlossary();
}
$("glossary-filter").addEventListener("input", () => renderGlossary($("glossary-filter").value));

// ------------------------------------------------------------------ expected-error ranges (PRD RES-2)

// Every "Why ...?" button toggles the panel it controls (aria-expanded, no hover needed).
function disclosure(btnId) {
  const b = $(btnId), panel = $(b.getAttribute("aria-controls"));
  b.addEventListener("click", () => {
    const open = panel.hidden;
    show(panel, open);
    b.setAttribute("aria-expanded", String(open));
  });
}
["why-cl-btn", "why-cd-btn", "why-ld-btn", "why-sweep-btn", "why-compare-btn"].forEach(disclosure);

// Text of a range: "likely 0.433 to 0.469" or the unvalidated label.
function rangeText(exp) { return exp ? exp.text : ""; }
function rangeEl(exp, tag = "span") {
  // colour never carries the meaning alone: an icon and the word "unvalidated" go with the tint
  return el(tag, { className: "range" + (exp && exp.status === "unvalidated" ? " unvalidated" : ""),
    textContent: exp ? (exp.status === "unvalidated" ? t("res.range.unvalidated") : exp.text.replace("likely ", "")) : "–" });
}

function setRange(id, exp) {
  const p = $(id), key = id.replace("res-", "").replace("-range", "");
  const why = $(`why-${key}`), btn = $(`why-${key}-btn`);
  if (!exp) {
    setText(id, ""); p.className = "tile-range"; show(btn, false); show(why, false);
    btn.setAttribute("aria-expanded", "false");
    return;
  }
  setText(id, (exp.status === "unvalidated" ? "⚠ " : "") + exp.text);
  p.className = "tile-range" + (exp.status === "unvalidated" ? " unvalidated" : "");
  show(btn, true);
  whyPanel(why, [exp]);
}

// The explanation(s) behind one or several ranges, as plain text with a list of reasons.
function whyPanel(panel, exps) {
  const items = [];
  for (const exp of exps.filter(Boolean)) {
    if (exp.status === "unvalidated") {
      const li = el("li", {}, document.createTextNode(`${exp.label || ""}${exp.text}: `));
      li.append(el("ul", {}, ...(exp.reasons || []).map((r) => el("li", { textContent: r }))));
      li.append(document.createTextNode(t("res.why.unvalidatedTail")));
      items.push(li);
    } else {
      items.push(el("li", { textContent: `${exp.label || ""}${exp.why || exp.text}` }));
    }
  }
  panel.replaceChildren(el("p", { textContent: t("res.why.intro") }), el("ul", {}, ...items));
}

// ------------------------------------------------------------------ step 4: results

const sweepCharts = {};

function detailRows(s) {
  const r = s.result, p = s.params;
  const preset = PRESET_KEYS[p.preset] ? t(PRESET_KEYS[p.preset]) : (p.preset || t("res.preset.quick"));
  const rows = [
    [t("res.details.reynolds"), nfInt(Math.round(p.reynolds))],
    [t("res.details.reference"), t("res.details.referenceValue", { area: fmtArea(p.reference_area),
      length: fmtLen(p.reference_length), how: p.reference_how })],
    [t("res.details.mesh"), t("res.details.meshValue", { preset, cells: nfInt(r.cells || 0),
      sweep: p.kind === "sweep" ? t("res.details.meshSweep") : "", reused: r.mesh_reused ? t("res.details.meshReused") : "" })],
  ];
  if (p.kind !== "sweep") {
    rows.splice(1, 0, [t("res.details.cm"), fmtNum(r.cm, 4)],
      [t("res.details.wobble"), t("res.details.wobbleValue", { cl: fmtNum(r.cl_pm, 4), cd: fmtNum(r.cd_pm, 5) })]);
    rows.push([t("res.details.iterations"), t("res.details.iterationsValue", { n: r.iterations,
      settled: t(r.converged ? "res.details.settled" : "res.details.notSettled"),
      warm: r.warm_start ? t("res.details.warm", { alpha: r.warm_start.alpha_deg }) : "" })]);
  } else {
    rows.push([t("res.details.iterations"), t("res.details.iterationsSweep", { n: r.iterations })]);
  }
  rows.push([t("res.details.runTime"), t("res.details.runTimeValue", { s: Math.round(r.solver_seconds || 0), n: p.n_procs })],
    [t("res.details.peakMemory"), r.peak_memory_mb ? t("res.details.peakMemoryValue", { mb: Math.round(r.peak_memory_mb) }) : "–"],
    [t("res.details.label"), r.label]);
  return rows;
}

async function showResults(s) {
  const r = s.result, p = s.params, sweep = p.kind === "sweep";
  state.result = s;
  refreshEstimates();  // this run has just taught the server how fast this computer is
  markStep("run", "done"); markStep("results", "current");
  show($("res-hint"), false); show($("results"));
  show($("single-tiles"), !sweep); show($("res-forces"), !sweep);
  show($("sweep-results"), sweep);
  list("res-warnings", s.warnings);
  $("dl-results").href = fileUrl(`/api/runs/${s.run_id}/results.csv`);
  $("dl-history").href = fileUrl(`/api/runs/${s.run_id}/history.csv`);
  $("dl-case").href = `/api/runs/${s.run_id}/case.zip`;
  $("dl-report").href = `/api/runs/${s.run_id}/report.md`;
  $("dl-report-html").href = `/api/runs/${s.run_id}/report.html`;
  if (sweep) {
    setText("res-title", t("res.title.sweep", { name: p.geometry_name, speed: p.airspeed,
      angles: p.angles.map((a) => `${a}°`).join(", ") }));
    renderSweep(s);
  } else {
    setText("res-title", t("res.title.single", { name: p.geometry_name, speed: p.airspeed, alpha: p.alpha_deg }));
    setText("res-cl", fmtNum(r.cl, 3));
    setText("res-cd", fmtNum(r.cd, 4));
    setText("res-ld", fmtNum(r.l_over_d, 1));
    const e = r.expected || {};
    setRange("res-cl-range", e.cl);
    setRange("res-cd-range", e.cd);
    setRange("res-ld-range", e.l_over_d ? { ...e.l_over_d, why: t("res.ld.why") } : null);
    const q = 0.5 * state.config.air.rho * p.airspeed ** 2 * p.reference_area;
    setText("res-forces", t("res.forces", { area: fmtArea(p.reference_area), lift: (q * r.cl).toPrecision(3),
      drag: (q * r.cd).toPrecision(3) }));
  }
  $("res-details").replaceChildren(...detailRows(s).flatMap(([k, v]) => [
    Object.assign(document.createElement("dt"), { textContent: k }),
    Object.assign(document.createElement("dd"), { textContent: v }),
  ]));
  const best = sweep ? r.points.findIndex((pt) => pt.alpha_deg === r.max_l_over_d_alpha) : null;
  await showSurface(s, sweep ? Math.max(0, best) : null);
  scrollTo($("step-results"));
  $("h-res").focus?.();
}

function renderSweep(s) {
  const r = s.result;
  const pts = r.points;
  const done = pts.filter((pt) => pt.status === "done");
  const bestAlpha = r.max_l_over_d_alpha;
  // table (also the accessible view of the three charts)
  $("sweep-body").replaceChildren(...pts.map((pt) => {
    const ok = pt.status === "done";
    const isBest = ok && pt.alpha_deg === bestAlpha;
    const status = ok ? t(pt.converged ? "sweep.status.settled" : "sweep.status.notSettled") : statusText(pt.status);
    const ex = ok ? pt.expected || {} : {};
    const tr = el("tr", { className: isBest ? "best" : "" },
      el("td", { className: "num", textContent: `${pt.alpha_deg}` }),
      el("td", { className: "num", textContent: ok ? fmtNum(pt.cl, 3) : "–" }),
      el("td", {}, rangeEl(ex.cl)),
      el("td", { className: "num", textContent: ok ? fmtNum(pt.cd, 4) : "–" }),
      el("td", {}, rangeEl(ex.cd)),
      el("td", { className: "num", textContent: ok ? fmtNum(pt.cm, 3) : "–" }),
      el("td", { className: "num", textContent: ok ? fmtNum(pt.l_over_d, 1) + (isBest ? t("sweep.bestMark") : "") : "–" }),
      el("td", { className: "num", textContent: ok ? `${pt.iterations}` : "–" }),
      el("td", { textContent: status }));
    return tr;
  }));
  whyPanel($("why-sweep"), sweepWhy(done));
  // stall / best-point note
  const notes = [];
  if (bestAlpha != null) notes.push(t("sweep.best", { ld: fmtNum(r.max_l_over_d, 1), alpha: bestAlpha }));
  if (r.stall_note) notes.push(r.stall_note.replace("C_L", "CL"));
  setText("sweep-note-text", notes.join(" "));
  show($("sweep-note"), notes.length > 0);
  $("sweep-note").className = r.stall_note ? "callout warn" : "callout info";
  // charts
  sweepCharts.cl ??= new XYChart($("chart-cl-alpha"), { xName: t("chart.alpha"), yName: t("chart.cl") });
  sweepCharts.polar ??= new XYChart($("chart-polar"), { xName: t("chart.cd"), yName: t("chart.cl") });
  sweepCharts.ld ??= new XYChart($("chart-ld"), { xName: t("chart.alpha"), yName: t("chart.ld") });
  const lab = (pt) => t("sweep.pointLabel", { alpha: pt.alpha_deg, cl: fmtNum(pt.cl, 3), cd: fmtNum(pt.cd, 4),
    ld: fmtNum(pt.l_over_d, 1) });
  const bi = done.findIndex((pt) => pt.alpha_deg === bestAlpha);
  const hl = bi >= 0 ? { highlight: bi } : {};
  sweepCharts.cl.set(done.map((pt) => ({ x: pt.alpha_deg, y: pt.cl, label: lab(pt) })),
    r.stall_alpha != null ? { highlight: done.findIndex((pt) => pt.alpha_deg === r.stall_alpha),
      highlightText: t("sweep.highlight.stall") } : {});
  sweepCharts.polar.set(done.map((pt) => ({ x: pt.cd, y: pt.cl, label: lab(pt) })),
    { ...hl, highlightText: bi >= 0 ? t("sweep.highlight.best", { alpha: bestAlpha }) : "" });
  sweepCharts.ld.set(done.map((pt) => ({ x: pt.alpha_deg, y: pt.l_over_d, label: lab(pt) })),
    { ...hl, highlightText: bi >= 0 ? t("sweep.highlight.bestLd", { ld: fmtNum(r.max_l_over_d, 1), alpha: bestAlpha }) : "" });
  const summary = done.map((pt) => t("sweep.aria.itemCl", { alpha: pt.alpha_deg, cl: fmtNum(pt.cl, 2) })).join("; ");
  $("chart-cl-alpha").setAttribute("aria-label", t("sweep.aria.cl", { summary,
    stall: r.stall_alpha != null ? t("sweep.aria.stall", { alpha: r.stall_alpha }) : "" }));
  $("chart-polar").setAttribute("aria-label", t("sweep.aria.polar", { list: done.map((pt) =>
    t("sweep.aria.itemPolar", { alpha: pt.alpha_deg, cd: fmtNum(pt.cd, 3), cl: fmtNum(pt.cl, 2) })).join("; ") }));
  $("chart-ld").setAttribute("aria-label", t("sweep.aria.ld", { list: done.map((pt) =>
    t("sweep.aria.itemLd", { alpha: pt.alpha_deg, ld: fmtNum(pt.l_over_d, 1) })).join("; "),
  best: bi >= 0 ? t("sweep.aria.best", { alpha: bestAlpha }) : "." }));
  // pressure-picture picker
  const avail = new Set(s.surface_points || []);
  $("sweep-point").replaceChildren(...pts.map((pt, i) => {
    const o = el("option", { value: String(i), textContent: `${pt.alpha_deg}°` + (pt.alpha_deg === bestAlpha
      ? t("sweep.bestLd") : "") + (avail.has(i) ? "" : t("sweep.noPicture")) });
    o.disabled = !avail.has(i);
    return o;
  }));
}

// One explanation per distinct basis across the points of a sweep (or several runs), labelled by angle.
function sweepWhy(points, prefix = "") {
  const out = [], seen = new Set();
  for (const pt of points) {
    const e = pt.expected;
    if (!e) continue;
    for (const key of ["cl", "cd"]) {
      const x = e[key];
      if (!x) continue;
      const sig = x.status === "unvalidated" ? `u:${key}:${(x.reasons || []).join("|")}` : `v:${key}:${x.why}`;
      const label = `${prefix}${pt.alpha_deg}°, ${key.toUpperCase()}: `;
      if (seen.has(sig)) { const prev = out.find((o) => o.sig === sig); prev.angles.push(`${prefix}${pt.alpha_deg}°`); continue; }
      seen.add(sig);
      out.push({ ...x, sig, angles: [`${prefix}${pt.alpha_deg}°`], key, label });
    }
  }
  return out.map((o) => ({ ...o, label: `${o.key.toUpperCase()} at ${o.angles.join(", ")}: ` }));
}

// ---- mid-span flow slice

let sliceViewer = null;
const sliceState = { field: "speed", view: "closeup", lines: true, slice: null, info: null, alpha: 0 };

async function showSlice(s, point, alpha) {
  const sweep = s.params.kind === "sweep";
  const has = sweep ? (s.slice_points || []).includes(point) : s.slice_available;
  show($("slice-fig"), !!has);
  if (!has) return;
  const q = sweep ? `?point=${point}` : "";
  try {
    const [blob, info] = await Promise.all([
      api(`/api/runs/${s.run_id}/slice${q}`),
      api(`/api/runs/${s.run_id}/slice-info${q}`).catch(() => null),  // optional extras
    ]);
    sliceState.slice = decodeSlice(blob);
    sliceState.info = info;
    sliceState.alpha = alpha;
    drawSlice();
  } catch { show($("slice-fig"), false); }
}

// Is the close-up really different from the whole slice? (older runs only have the close-up)
function hasTunnelView(info) {
  if (!info || !info.windows) return false;
  const [a, b] = [info.windows.closeup, info.windows.tunnel];
  return (b[1] - b[0]) > 1.2 * (a[1] - a[0]) || (b[3] - b[2]) > 1.2 * (a[3] - a[2]);
}

function renderScale(range, stops, title) {
  setText("slice-scale-title", title);
  $("slice-bar").style.background = cssGradient(stops);
  const ticks = legendTicks(range.lo, range.hi, 5);
  $("slice-ticks").replaceChildren(...ticks.map((t, i) => {
    const sp = el("span", { textContent: (i === 0 && range.clipLo ? "≤ " : "") +
      (i === ticks.length - 1 && range.clipHi && Math.abs(t.value - range.hi) < 1e-9 ? "≥ " : "") +
      (range.lo < 0 && t.value > 0 ? "+" : "") + t.text });
    sp.style.left = `${(100 * t.at).toFixed(2)}%`;
    return sp;
  }));
}

function drawSlice() {
  const sl = sliceState.slice, info = sliceState.info;
  if (!sl) return;
  sliceViewer ??= new SliceViewer($("slice-viewer"));
  sliceViewer.onProbe = (p) => {
    if (!p) { setText("slice-probe", t("slice.probe.noPoint")); return; }
    const where = t("slice.probe.where", { x: fmtLength(p.px, 1e-3), z: fmtLength(p.pz, 1e-3) });
    setText("slice-probe", t(p.onBody ? "slice.probe.onBody" : "slice.probe.value",
      { where, speed: nf(p.speed, 2), cp: nf(p.cp, 2) }));
  };
  const tunnelOk = hasTunnelView(info);
  show($("slice-window-group"), tunnelOk);
  if (!tunnelOk) sliceState.view = "closeup";
  document.querySelectorAll("#slice-fig [data-window]").forEach((x) => {
    x.setAttribute("aria-pressed", String(x.dataset.window === sliceState.view));
  });
  const lines = info && info.streamlines ? info.streamlines[sliceState.view] : null;
  show($("slice-lines-label"), !!lines);
  const drawLines = !!lines && sliceState.lines;
  show($("slice-lines-text"), drawLines);
  const win = info && info.windows ? info.windows[sliceState.view] : null;
  const r = sliceViewer.show(sl, sliceState.field, { window: win, lines: drawLines ? lines : null });
  setText("slice-y", info ? slicePositionText(info.y, info.y_centre, info.body_size)
    : slicePositionText(sl.y, null, Math.max(sl.xMax - sl.xMin, sl.zMax - sl.zMin) / 3));
  const where = t(sliceState.view === "tunnel" ? "slice.where.tunnel" : "slice.where.closeup");
  const linesText = drawLines ? t("slice.withLines") : "";
  if (sliceState.field === "speed") {
    renderScale(r, VIRIDIS, t("slice.scale.speed"));
    setText("slice-text", t("slice.text.speed", { where, max: nf(r.max, 2) }));
    $("slice-viewer").setAttribute("aria-label", t("slice.label.speed", { alpha: sliceState.alpha,
      view: t(sliceState.view === "tunnel" ? "slice.view.tunnel" : "slice.view.closeup"), max: nf(r.max, 2), lines: linesText }));
  } else {
    renderScale(r, DIVERGING, t("slice.scale.cp"));
    setText("slice-text", t("slice.text.cp", { where, min: nf(r.min, 2), max: nf(r.max, 2),
      clipped: r.clipLo || r.clipHi ? t("res.cp.clipped") : "" }));
    $("slice-viewer").setAttribute("aria-label", t("slice.label.cp", { alpha: sliceState.alpha, min: nf(r.min, 2),
      max: nf(r.max, 2), lines: linesText }));
  }
}

document.querySelectorAll("#slice-fig [data-field]").forEach((b) => b.addEventListener("click", () => {
  sliceState.field = b.dataset.field;
  document.querySelectorAll("#slice-fig [data-field]").forEach((x) => {
    x.setAttribute("aria-pressed", String(x === b));
  });
  drawSlice();
}));

document.querySelectorAll("#slice-fig [data-window]").forEach((b) => b.addEventListener("click", () => {
  sliceState.view = b.dataset.window;
  drawSlice();
}));

$("slice-lines").addEventListener("change", () => {
  sliceState.lines = $("slice-lines").checked;
  drawSlice();
});

$("sweep-point").addEventListener("change", () => {
  const s = state.result;
  if (s) showSurface(s, Number($("sweep-point").value));
});

async function showSurface(s, point) {
  const p = s.params;
  const sweep = p.kind === "sweep";
  if (sweep) {
    $("sweep-point").value = String(point);
    $("dl-history").href = fileUrl(`/api/runs/${s.run_id}/history.csv?point=${point}`);
  }
  const has = sweep ? (s.surface_points || []).includes(point) : s.surface_available;
  show($("result-viewer").parentElement, !!has);
  const alpha = sweep ? p.angles[point] : p.alpha_deg;
  showSlice(s, point, alpha);
  if (!has) return;
  try {
    const q = sweep ? `?point=${point}` : "";
    const surf = decodeSurface(await api(`/api/runs/${s.run_id}/surface${q}`));
    resultViewer ??= new SurfaceViewer($("result-viewer"));
    resultViewer.onProbe = (p) => {
      setText("surface-probe", !p ? t("res.probe.surface.noPart")
        : p.cp === null ? t("res.probe.surface.noValue")
        : t("res.probe.surface.value", { cp: nf(p.cp, 2), x: fmtLength(p.x, 1e-3), y: fmtLength(p.y, 1e-3),
          z: fmtLength(p.z, 1e-3), note: p.cp > 0.7 ? t("res.probe.surface.nearStagnation")
            : p.cp < -0.5 ? t("res.probe.surface.suction") : "" }));
    };
    resultViewer.show(surf, { alphaDeg: alpha, colourByCp: true });
    setText("surface-probe", t("res.probe.surface.prompt"));
    const [lo, hi, clipLo, clipHi] = cpRange(surf.cp, surf.cpMin, surf.cpMax);
    setText("cp-min", `${clipLo ? "≤ " : ""}${nf(lo, 2)}`);
    setText("cp-max", `${clipHi ? "≥ " : ""}+${nf(hi, 2)}`);
    setText("cp-range", t("res.cp.range", { where: sweep ? t("res.cp.atAngle", { alpha }) : t("res.cp.thisRun"),
      min: nf(surf.cpMin, 2), max: nf(surf.cpMax, 2), clipped: clipLo || clipHi ? t("res.cp.clipped") : "" }));
    $("result-viewer").setAttribute("aria-label", t("res.viewerLabel", { alpha, min: nf(surf.cpMin, 2),
      max: nf(surf.cpMax, 2) }));
  } catch { list("res-warnings", [...s.warnings, t("res.picture.failed")]); }
}

// ------------------------------------------------------------------ my runs

const STATUS_KEYS = { queued: "runs.status.queued", running: "runs.status.running", done: "runs.status.done",
  failed: "runs.status.failed", cancelled: "runs.status.cancelled" };
const statusText = (status) => (STATUS_KEYS[status] ? t(STATUS_KEYS[status]) : status);

function fmtDate(t) {
  if (!t) return "–";
  return new Date(t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit",
    minute: "2-digit" });
}

function el(tag, props = {}, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids);
  return e;
}

async function refreshRuns() {
  let data;
  try { data = await api("/api/runs"); } catch (err) {
    setText("runs-error", t("runs.loadFailed", { message: err.message })); show($("runs-error"));
    return null;
  }
  show($("runs-error"), false);
  if (!DEMO) setText("runs-help", t("runs.help", { keep: data.keep_runs, gb: data.disk_cap_gb }));
  const runs = data.runs;
  show($("runs-empty"), !runs.length);
  show($("runs-table"), runs.length > 0);
  // keep the ticks of runs that are still there and finished
  const done = new Set(runs.filter((r) => r.status === "done").map((r) => r.run_id));
  state.compareSel = new Set([...(state.compareSel || [])].filter((id) => done.has(id)));
  show($("compare-bar"), done.size >= 2);
  $("runs-body").replaceChildren(...runs.map(runRow));
  updateCompareBar();
  return runs;
}

// ---- compare 2-5 finished runs (5: a lesson's five bodies at once; the server's limit is the same)

const MAX_COMPARE = 5;
const MARKER_TEXT = ["●", "■", "▲", "◆", "★"];  // the same shapes as the chart markers (chart.js MARKERS)
const MARKER_KEYS = ["compare.marker.circle", "compare.marker.square", "compare.marker.triangle",
  "compare.marker.diamond", "compare.marker.star"];
const compareCharts = {};

function updateCompareBar() {
  const n = (state.compareSel || new Set()).size;
  $("compare-btn").disabled = n < 2 || n > MAX_COMPARE;
  $("compare-btn").textContent = n >= 2 ? t("compare.button", { n }) : t("compare.buttonPlain");
  setText("compare-hint", n === 0 ? t("compare.hint.none", { max: MAX_COMPARE })
    : n === 1 ? t("compare.hint.one") : n >= MAX_COMPARE ? t("compare.hint.max", { n })
      : t("compare.hint.some", { n, more: MAX_COMPARE - n }));
  // at the limit, the other boxes can't be ticked
  document.querySelectorAll("#runs-body input.pick").forEach((b) => {
    b.disabled = !b.checked && n >= MAX_COMPARE;
  });
}

$("compare-btn").addEventListener("click", () => showCompare([...state.compareSel]));
$("compare-close").addEventListener("click", () => {
  show($("compare"), false); show($("nav-compare"), false);
  $("h-runs").focus?.();
});

function runTitle(r) {
  const what = r.kind === "sweep" ? t("runs.title.sweep", { angles: r.points.map((q) => `${q.alpha_deg}°`).join(", ") })
    : `${r.points[0] ? r.points[0].alpha_deg : "–"}°`;
  return t("runs.title", { name: r.name, speed: r.airspeed, what });
}

async function showCompare(ids) {
  show($("compare")); show($("nav-compare"));
  show($("compare-error"), false); show($("compare-body"), false);
  const q = new URLSearchParams({ runs: ids.join(",") });
  let d;
  try { d = await api(`/api/compare?${q}`); } catch (err) {
    setText("compare-error", err.message); show($("compare-error"));
    scrollTo($("compare"));
    return;
  }
  show($("compare-body"));
  $("dl-compare").href = `/api/compare.csv?${q}`;
  // a line that says the runs compare directly is good news; anything else is a caveat
  $("compare-warnings").replaceChildren(...d.warnings.map((w) => {
    const fine = /compared directly/.test(w);
    return el("li", { className: `callout ${fine ? "info" : "warn"}`, textContent: (fine ? "✓ " : "⚠ ") + w });
  }));
  $("compare-legend").replaceChildren(...d.runs.map((r, k) => {
    const li = el("li", {},
      el("span", { className: `mk mk-${k}`, textContent: MARKER_TEXT[k] }),
      el("b", { textContent: `${r.letter}: ` }), document.createTextNode(runTitle(r)));
    li.setAttribute("aria-label", t("compare.legendItem", { letter: r.letter, marker: t(MARKER_KEYS[k]), title: runTitle(r) }));
    return li;
  }));
  $("compare-tbody").replaceChildren(...d.runs.flatMap((r, k) => r.points.map((pt, i) => el("tr",
    { className: i === 0 ? "group-start" : "" },
    el("td", {}, el("span", { className: `mk mk-${k}`, textContent: MARKER_TEXT[k] }),
      document.createTextNode(` ${r.letter}`)),
    el("td", { className: "num", textContent: `${pt.alpha_deg}` }),
    el("td", { className: "num", textContent: fmtNum(pt.cl, 3) }),
    el("td", {}, rangeEl((pt.expected || {}).cl)),
    el("td", { className: "num", textContent: fmtNum(pt.cd, 4) }),
    el("td", {}, rangeEl((pt.expected || {}).cd)),
    el("td", { className: "num", textContent: fmtNum(pt.l_over_d, 1) }),
    el("td", { className: "num", textContent: `${r.airspeed}` }),
    el("td", { className: "num", textContent: Math.round(r.reynolds).toLocaleString("en") }),
    el("td", { className: "num", textContent: fmtArea(r.reference_area) })))));
  whyPanel($("why-compare"), d.runs.flatMap((r) => sweepWhy(r.points, `${r.letter} `)));
  compareCharts.cl ??= new XYChart($("cmp-cl"), { xName: t("chart.alpha"), yName: t("chart.cl") });
  compareCharts.polar ??= new XYChart($("cmp-polar"), { xName: t("chart.cd"), yName: t("chart.cl") });
  compareCharts.ld ??= new XYChart($("cmp-ld"), { xName: t("chart.alpha"), yName: t("chart.ld") });
  const lab = (pt) => t("sweep.pointLabel", { alpha: pt.alpha_deg, cl: fmtNum(pt.cl, 3), cd: fmtNum(pt.cd, 4),
    ld: fmtNum(pt.l_over_d, 1) });
  const ok = (pt) => pt.cl != null && pt.cd != null;
  const series = (fx, fy) => d.runs.map((r) => ({ name: r.letter,
    points: r.points.filter(ok).map((pt) => ({ x: fx(pt), y: fy(pt), label: lab(pt) })) }));
  compareCharts.cl.setSeries(series((pt) => pt.alpha_deg, (pt) => pt.cl));
  compareCharts.polar.setSeries(series((pt) => pt.cd, (pt) => pt.cl));
  compareCharts.ld.setSeries(series((pt) => pt.alpha_deg, (pt) => pt.l_over_d ?? pt.cl / pt.cd));
  const describe = (f) => d.runs.map((r, k) => t("compare.aria.run", { letter: r.letter, marker: t(MARKER_KEYS[k]),
    list: r.points.filter(ok).map(f).join("; ") })).join(". ");
  $("cmp-cl").setAttribute("aria-label", t("compare.aria.cl", { runs: describe((pt) =>
    t("compare.aria.itemCl", { alpha: pt.alpha_deg, cl: fmtNum(pt.cl, 2) })) }));
  $("cmp-polar").setAttribute("aria-label", t("compare.aria.polar", { runs: describe((pt) =>
    t("compare.aria.itemPolar", { cd: fmtNum(pt.cd, 3), cl: fmtNum(pt.cl, 2) })) }));
  $("cmp-ld").setAttribute("aria-label", t("compare.aria.ld", { runs: describe((pt) =>
    t("compare.aria.itemLd", { alpha: pt.alpha_deg, ld: fmtNum(pt.l_over_d, 1) })) }));
  scrollTo($("compare"));
  $("h-compare").focus?.({ preventScroll: true });
}

// The accessible name of a run in the list (the Compare checkbox, the Open / Delete buttons): shape, speed,
// angle(s) and date, so two runs of the same wing at 5° and 8° read differently (task 014).
function runLabel(r) {
  const sweep = r.kind === "sweep" && r.angles && r.angles.length;
  const angle = sweep ? t("runs.row.sweepLabel", { first: r.angles[0], last: r.angles[r.angles.length - 1],
    n: r.angles.length }) : t("runs.row.angleLabel", { alpha: r.alpha_deg ?? "–" });
  return t("runs.row.label", { name: r.geometry_name || t("runs.row.fallbackName"), speed: r.airspeed ?? "–", angle,
    date: fmtDate(r.created) });
}

function runRow(r) {
  const angle = r.kind === "sweep" && r.angles
    ? t("runs.row.sweepAngles", { first: r.angles[0], last: r.angles[r.angles.length - 1] }) : r.alpha_deg;
  const label = runLabel(r);
  const pick = el("td", { className: "pick" });
  if (r.status === "done") {
    const box = el("input", { type: "checkbox", className: "pick",
      checked: (state.compareSel || new Set()).has(r.run_id) });
    box.setAttribute("aria-label", t("runs.row.compareLabel", { label }));
    box.addEventListener("change", () => {
      state.compareSel ??= new Set();
      if (box.checked) state.compareSel.add(r.run_id); else state.compareSel.delete(r.run_id);
      updateCompareBar();
    });
    pick.append(box);
  }
  const tr = el("tr", {}, pick,
    el("td", { textContent: fmtDate(r.created) }),
    el("td", { textContent: r.geometry_name || "–" }),
    el("td", { className: "num", textContent: r.airspeed ?? "–" }),
    el("td", { className: "num", textContent: angle ?? "–" }),
    el("td", { className: "num", textContent: r.kind === "sweep" ? "–" : fmtNum(r.cl, 3) }),
    el("td", { className: "num", textContent: r.kind === "sweep" ? "–" : fmtNum(r.cd, 4) }),
    el("td", { textContent: statusText(r.status) }),
  );
  const actions = el("td", { className: "actions-cell" });
  const open = el("button", { type: "button", className: "secondary small",
    textContent: t(["queued", "running"].includes(r.status) ? "runs.row.showProgress" : "runs.row.open") });
  open.setAttribute("aria-label", t("runs.row.actionLabel", { action: open.textContent, label }));
  open.addEventListener("click", () => openRun(r.run_id));
  actions.append(open);
  if (!["queued", "running"].includes(r.status)) {
    const del = el("button", { type: "button", className: "secondary small danger", textContent: t("runs.row.delete") });
    del.setAttribute("aria-label", t("runs.row.actionLabel", { action: del.textContent, label }));
    del.addEventListener("click", () => confirmDelete(actions, r, label));
    actions.append(del);
  }
  tr.append(actions);
  return tr;
}

function confirmDelete(cell, r, label) {
  const keep = cell.replaceChildren.bind(cell, ...cell.childNodes);
  const yes = el("button", { type: "button", className: "small danger", textContent: t("runs.delete.yes") });
  const no = el("button", { type: "button", className: "secondary small", textContent: t("runs.delete.keep") });
  yes.setAttribute("aria-label", t("runs.delete.yesLabel", { label }));
  const q = el("span", { className: "confirm-q", textContent: t("runs.delete.question") });
  cell.replaceChildren(q, yes, no);
  no.focus();
  no.addEventListener("click", () => { keep(); cell.querySelector("button")?.focus(); });
  yes.addEventListener("click", async () => {
    yes.disabled = true;
    try {
      await api(`/api/runs/${r.run_id}`, { method: "DELETE" });
      if (state.runId === r.run_id) { show($("results"), false); show($("res-hint")); state.runId = null; }
      await refreshRuns();
      $("h-runs").focus?.();
    } catch (err) {
      setText("runs-error", err.message); show($("runs-error"));
      keep();
    }
  });
}

async function openRun(id) {
  let s;
  try { s = await api(`/api/runs/${id}`); } catch (err) {
    setText("runs-error", err.message); show($("runs-error")); return;
  }
  if (["queued", "running"].includes(s.status)) {
    watchRun(s);
    scrollTo($("step-run"));
    return;
  }
  stopPolling();
  state.runId = s.run_id;
  updateRunButton();
  ensureCharts();
  show($("run-progress"), (s.history || []).length > 0);
  show($("run-note"), false); show($("run-error"), false);
  renderRun(s);
  finishedRun(s);
  if (s.status !== "done") scrollTo($("step-run"));
}

// ------------------------------------------------------------------ trial feedback ("How was this?")

function ratingValue(name) {
  const r = document.querySelector(`input[name=${name}]:checked`);
  return r ? Number(r.value) : null;
}

$("fb-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  show($("fb-error"), false); show($("fb-note"), false);
  const body = {
    results_sensible: ratingValue("fb-sense"), easy_to_use: ratingValue("fb-easy"),
    comment: $("fb-comment").value.trim(), name: $("fb-name").value.trim(), run_id: state.runId || null,
  };
  if (body.results_sensible === null && body.easy_to_use === null && !body.comment) {
    setText("fb-error", t("fb.needAnswer")); show($("fb-error"));
    return;
  }
  $("fb-send").disabled = true;
  try {
    const r = await api("/api/feedback", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    setText("fb-note", r.message || t("fb.saved"));
    show($("fb-note"));
    $("fb-comment").value = "";
    document.querySelectorAll("#fb-form input[type=radio]").forEach((x) => { x.checked = false; });
    // the name stays filled in for the next answer
  } catch (err) {
    setText("fb-error", err.message); show($("fb-error"));
  } finally { $("fb-send").disabled = false; }
});

// ------------------------------------------------------------------ lessons (PRD EDU-1 / EDU-2)

// A lesson is a list of steps (intro, predict, run, observe, explain, question, homework) the panel walks
// through. Predictions and observations are typed before / after a run and saved locally; a run step can
// pre-fill the forms (the student presses Run) or import the pre-computed result into My runs.
const lessonState = { list: [], lesson: null, index: 0, answers: {}, packRuns: {} };
const KIND_KEYS = { intro: "lesson.kind.intro", predict: "lesson.kind.predict", run: "lesson.kind.run",
  observe: "lesson.kind.observe", explain: "lesson.kind.explain", question: "lesson.kind.question",
  homework: "lesson.kind.homework" };
const kindLabel = (kind) => (KIND_KEYS[kind] ? t(KIND_KEYS[kind]) : kind);

// A tiny Markdown renderer for the lesson's background reading: headings, paragraphs, **bold**.
function renderMarkdown(text) {
  const out = el("div", { className: "reading" });
  for (const block of text.split(/\n\s*\n/)) {
    const t = block.trim();
    if (!t) continue;
    const h = t.match(/^(#+)\s+(.*)$/);
    const node = h ? el(h[1].length === 1 ? "h4" : "h5") : el("p");
    const body = h ? h[2] : t.replace(/\n/g, " ");
    body.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
      const b = part.match(/^\*\*([^*]+)\*\*$/);
      node.append(b ? el("b", { textContent: b[1] }) : document.createTextNode(part));
    });
    out.append(node);
  }
  return out;
}

async function loadLessons() {
  let d;
  try { d = await api("/api/lessons"); } catch (err) {
    setText("lessons-error", err.message); show($("lessons-error")); return;
  }
  lessonState.list = d.lessons;
  $("lesson-list").replaceChildren(...d.lessons.map((l) => {
    const start = el("button", { type: "button", className: "primary", textContent: t("lesson.start") });
    start.setAttribute("aria-label", t("lesson.startLabel", { title: l.title }));
    start.addEventListener("click", () => openLesson(l.id));
    return el("li", {},
      el("h3", { textContent: l.title }),
      el("p", { className: "meta", textContent: t("lesson.meta", { minutes: l.minutes,
        runs: l.runs.length === 1 ? t("lesson.meta.oneRun") : t("lesson.meta.runs", { n: l.runs.length }),
        pack: t(l.pack_available ? "lesson.meta.packIncluded" : "lesson.meta.runYourself") }) }),
      el("p", { textContent: l.summary }),
      el("ol", {}, ...l.objectives.map((o) => el("li", { textContent: o }))),
      el("div", { className: "row" }, start));
  }));
  if (d.teacher_mode && d.lesson) {
    document.body.dataset.teacher = "1";
    openLesson(d.lesson);
  }
}

async function openLesson(id) {
  let lesson;
  try { lesson = await api(`/api/lessons/${id}`); } catch (err) {
    setText("lessons-error", err.message); show($("lessons-error")); return;
  }
  lessonState.lesson = lesson;
  lessonState.answers = lesson.answers || {};
  lessonState.index = 0;
  setText("h-lesson", t("lesson.title", { title: lesson.title }));
  setText("lesson-summary", lesson.summary);
  $("lesson-objectives").replaceChildren(...lesson.objectives.map((o) => el("li", { textContent: o })));
  const terms = $("lesson-terms");
  terms.replaceChildren(...(lesson.glossary || []).flatMap((tid, i) => {
    const term = glossary.terms.get(tid);
    if (!term) return [];
    const b = el("button", { type: "button", className: "def", textContent: "?" });
    b.dataset.term = tid;
    b.setAttribute("aria-label", t("glossary.whatIs", { term: term.term }));
    return [document.createTextNode((i ? ", " : "") + term.term + " "), b];
  }));
  enhanceDefinitions(terms);
  $("lesson-steps").replaceChildren(...lesson.steps.map((s, i) => {
    const b = el("button", { type: "button", textContent: t("lesson.stepChip", { i: i + 1, kind: kindLabel(s.kind) }) });
    b.addEventListener("click", () => { lessonState.index = i; renderLessonStep(); });
    return el("li", {}, b);
  }));
  show($("lesson-panel"));
  markStep("lessons", "current");
  renderLessonStep();
  scrollTo($("lesson-panel"));
  $("h-lesson").focus?.({ preventScroll: true });
}

$("lesson-close").addEventListener("click", () => {
  show($("lesson-panel"), false);
  lessonState.lesson = null;
  $("h-lessons").focus?.();
});
$("lesson-back").addEventListener("click", () => { lessonState.index = Math.max(0, lessonState.index - 1); renderLessonStep(); });
$("lesson-next").addEventListener("click", () => {
  const n = lessonState.lesson.steps.length;
  lessonState.index = Math.min(n - 1, lessonState.index + 1);
  renderLessonStep();
});

function lessonNote(msg, isError = false) {
  setText(isError ? "lesson-error" : "lesson-note", msg);
  show($("lesson-error"), isError && !!msg);
  show($("lesson-note"), !isError && !!msg);
}

async function saveAnswer(step, answer) {
  const lesson = lessonState.lesson;
  const r = await api(`/api/lessons/${lesson.id}/answers`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ step: step.id, answer }),
  });
  lessonState.answers[step.id] = { answer, correct: r.correct, time: r.time };
  return r;
}

function renderLessonStep() {
  const lesson = lessonState.lesson;
  if (!lesson) return;
  const i = lessonState.index, step = lesson.steps[i], n = lesson.steps.length;
  lessonNote("");
  $("lesson-steps").querySelectorAll("button").forEach((b, k) => {
    if (k === i) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current");
    const s = lesson.steps[k];
    b.classList.toggle("answered", !!lessonState.answers[s.id] || (s.kind === "run" && !!lessonState.packRuns[s.pack]));
  });
  setText("lesson-progress", t("lesson.progress", { i: i + 1, n }));
  $("lesson-back").disabled = i === 0;
  $("lesson-next").disabled = i === n - 1;
  const box = $("lesson-step");
  box.replaceChildren(el("span", { className: "kind", textContent: kindLabel(step.kind) }),
    el("h3", { textContent: step.title || (step.kind === "question" ? t("lesson.question", { n: questionNumber(lesson, step) }) : "") }));
  const saved = lessonState.answers[step.id];
  if (step.kind === "intro") {
    box.append(el("p", { textContent: step.text }));
    if (lesson.markdown) {
      const d = el("details", { className: "details" }, el("summary", { textContent: t("lesson.background") }),
        renderMarkdown(lesson.markdown));
      box.append(d);
    }
    if (lesson.table) box.append(renderLessonTable(lesson.table));
  } else if (["predict", "observe", "explain"].includes(step.kind)) {
    box.append(el("p", { textContent: step.prompt }));
    if (lesson.table && step.kind === "observe") box.append(renderLessonTable(lesson.table));
    const ta = el("textarea", { id: `lesson-answer-${step.id}`, rows: 4, maxLength: 2000, placeholder: step.placeholder || "" });
    ta.value = saved ? String(saved.answer) : "";
    const lab = el("label", { htmlFor: ta.id, textContent: t(step.kind === "predict" ? "lesson.answer.predict"
      : step.kind === "observe" ? "lesson.answer.observe" : "lesson.answer.explain") });
    const save = el("button", { type: "button", className: "secondary", textContent: t(saved ? "lesson.saveAgain" : "lesson.save") });
    const status = el("p", { className: "help", role: "status", textContent: saved ? t("lesson.savedAt", { date: fmtDate(saved.time) }) : "" });
    save.addEventListener("click", async () => {
      if (!ta.value.trim()) { lessonNote(t("lesson.writeFirst"), true); return; }
      save.disabled = true;
      try {
        await saveAnswer(step, ta.value.trim());
        status.textContent = t("lesson.savedHere");
        save.textContent = t("lesson.saveAgain");
        renderLessonStepChips();
      } catch (err) { lessonNote(err.message, true); } finally { save.disabled = false; }
    });
    box.append(el("div", { className: "field" }, lab, ta), el("div", { className: "row" }, save, status));
    if (step.kind === "explain" && step.text) {
      const btn = el("button", { type: "button", className: "link-btn", textContent: t("lesson.showExplanation") });
      const panel = el("div", { className: "why", id: `lesson-why-${step.id}`, hidden: true },
        el("p", { textContent: step.text }));
      btn.setAttribute("aria-expanded", "false");
      btn.setAttribute("aria-controls", panel.id);
      btn.addEventListener("click", () => {
        if (!lessonState.answers[step.id] && panel.hidden) {
          lessonNote(t("lesson.ownFirst"), true);
          return;
        }
        show(panel, panel.hidden);
        btn.setAttribute("aria-expanded", String(!panel.hidden));
      });
      box.append(btn, panel);
    }
  } else if (step.kind === "run") {
    renderRunStep(box, step);
  } else if (step.kind === "question") {
    renderQuestionStep(box, step, saved);
  } else if (step.kind === "homework") {
    box.append(el("p", { textContent: step.text }));
  }
  box.querySelector("h3")?.setAttribute("tabindex", "-1");
  box.querySelector("h3")?.focus?.({ preventScroll: true });
}

function renderLessonStepChips() { renderLessonStep.chips?.(); }

function questionNumber(lesson, step) {
  return lesson.steps.filter((s) => s.kind === "question").indexOf(step) + 1;
}

function renderLessonTable(t) {
  const table = el("table", { className: "runs" },
    el("caption", { textContent: t.title }),
    el("thead", {}, el("tr", {}, ...t.columns.map((c) => el("th", { scope: "col", textContent: c })))),
    el("tbody", {}, ...t.rows.map((r) => el("tr", {}, ...r.map((v, k) => el("td", { className: k ? "num" : "", textContent: v }))))));
  const wrap = el("div", { className: "table-wrap" }, table);
  if (t.note) wrap.append(el("p", { className: "help", textContent: t.note }));
  return wrap;
}

function describeShape(shape) {
  if (shape.naca) return t("lesson.shape.naca", { code: shape.naca.code, chord: shape.naca.chord, span: shape.naca.span });
  if (shape.builtin) {
    const { shape: kind, ...p } = shape.builtin;
    return `${kind}: ${Object.entries(p).map(([k, v]) => `${k} ${v}`).join(", ")}`;
  }
  return "-";
}

function renderRunStep(box, step) {
  const c = step.conditions;
  box.append(el("p", { textContent: step.text || "" }));
  box.append(el("dl", { className: "settings" },
    el("dt", { textContent: t("lesson.run.shape") }), el("dd", { textContent: describeShape(step.shape) }),
    el("dt", { textContent: t("lesson.run.airspeed") }), el("dd", { textContent: t("lesson.run.airspeedValue", { v: c.airspeed }) }),
    el("dt", { textContent: t(c.kind === "sweep" ? "lesson.run.angles" : "lesson.run.angle") }),
    el("dd", { textContent: c.kind === "sweep" ? t("lesson.run.anglesValue", { angles: c.angles.join(", ") }) : `${c.alpha_deg}°` }),
    el("dt", { textContent: t("lesson.run.mesh") }), el("dd", { textContent: t("res.preset.quick") })));
  const setup = el("button", { type: "button", className: "primary", textContent: t("lesson.run.setup") });
  setup.addEventListener("click", () => setupLessonRun(step).catch((err) => lessonNote(err.message, true)));
  const row = el("div", { className: "row" }, setup);
  const already = lessonState.packRuns[step.pack];
  // a lesson with several runs: one button brings every run in (and compares them, up to MAX_COMPARE)
  const runSteps = lessonState.lesson.steps.filter((s) => s.kind === "run" && s.pack_available && s.pack);
  const allText = runSteps.length > 1 ? t("lesson.run.openAll", { n: runSteps.length,
    compare: runSteps.length <= MAX_COMPARE ? t("lesson.run.andCompare") : "" }) : "";
  if (step.pack_available) {
    const use = el("button", { type: "button", className: "secondary",
      textContent: t(already ? "lesson.run.openPack" : "lesson.run.usePack") });
    use.setAttribute("aria-describedby", "lesson-pack-help");
    use.addEventListener("click", () => usePackRun(step, use).catch((err) => lessonNote(err.message, true)));
    row.append(use);
    if (allText) {
      const all = el("button", { type: "button", className: "secondary", textContent: allText });
      all.setAttribute("aria-describedby", "lesson-pack-help");
      all.addEventListener("click", () => useAllPackRuns(runSteps, step, all).catch((err) => lessonNote(err.message, true)));
      row.append(all);
    }
  }
  box.append(row, el("p", { className: "help", id: "lesson-pack-help", textContent: step.pack_available
    ? t("lesson.run.help.pack", { all: allText ? t("lesson.run.help.allText", { all: allText,
      compare: runSteps.length <= MAX_COMPARE ? t("lesson.run.help.compareTail") : "." }) : "" })
    : t("lesson.run.help.noPack") }));
}

async function makeLessonGeometry(shape) {
  if (shape.naca) {
    document.querySelector('input[name=shape-kind][value="naca"]').click();
    $("naca-code").value = shape.naca.code; $("naca-chord").value = shape.naca.chord; $("naca-span").value = shape.naca.span;
    return api("/api/geometry/naca", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ code: shape.naca.code, chord: shape.naca.chord, span: shape.naca.span }) });
  }
  const { shape: kind, ...params } = shape.builtin;
  document.querySelector('input[name=shape-kind][value="builtin"]').click();
  $("builtin-shape").value = kind;
  renderBuiltinForm();
  for (const [k, v] of Object.entries(params)) { const inp = $(`builtin-${k}`); if (inp) inp.value = String(v); }
  return api("/api/geometry/builtin", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ shape: kind, ...params }) });
}

async function setupLessonRun(step) {
  lessonNote(t("lesson.note.settingUp"));
  const c = step.conditions;
  await useGeometry(await makeLessonGeometry(step.shape));
  $("airspeed").value = String(c.airspeed);
  const kind = c.kind === "sweep" ? "sweep" : "single";
  document.querySelector(`input[name=run-kind][value="${kind}"]`).click();
  if (kind === "sweep") $("angles").value = c.angles.join(", "); else $("alpha").value = String(c.alpha_deg);
  ["airspeed", "alpha", "angles"].forEach((id) => $(id).dispatchEvent(new Event("input")));
  updateRunButton();
  lessonNote(t("lesson.note.filled"));
  scrollTo($("step-run"));
  $("run-btn").focus?.({ preventScroll: true });
}

// One run step's pre-computed result into My runs (the server builds a finished run from the pack).
async function importPackRun(step) {
  const s = await api(`/api/lessons/${lessonState.lesson.id}/import`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ run: step.pack }),
  });
  lessonState.packRuns[step.pack] = s.run_id;
  return s.run_id;
}

async function usePackRun(step, button) {
  button.disabled = true;
  lessonNote(t("lesson.note.loading"));
  try {
    const id = await importPackRun(step);
    await openRun(id);
    await refreshRuns();
    renderLessonStep();  // (clears the note; the chip and the button text now say the run is in)
    lessonNote(t("lesson.note.loaded"));
  } finally { button.disabled = false; }
}

// Every run step of the lesson, through the same import route one after another; this step's run opens
// in Step 4, all of them are ticked under My runs, and (2 to MAX_COMPARE runs) Compare opens with them all.
async function useAllPackRuns(runSteps, step, button) {
  button.disabled = true;
  const ids = [];
  try {
    for (const [k, s] of runSteps.entries()) {
      lessonNote(t("lesson.note.loadingN", { i: k + 1, n: runSteps.length, title: s.title || s.pack }));
      ids.push(await importPackRun(s));
    }
    state.compareSel = new Set(ids);
    await refreshRuns();
    await openRun(lessonState.packRuns[step.pack]);
    const compared = ids.length >= 2 && ids.length <= MAX_COMPARE;
    if (compared) await showCompare(ids);
    renderLessonStep();
    lessonNote(t("lesson.note.allLoaded", { n: ids.length, compared: compared ? t("lesson.note.allCompared") : "" }));
  } finally { button.disabled = false; }
}

function renderQuestionStep(box, step, saved) {
  box.append(el("p", { textContent: step.prompt }));
  const name = `lesson-q-${step.id}`;
  const fs = el("fieldset", { className: "choices" }, el("legend", { className: "sr-only", textContent: step.prompt }));
  step.choices.forEach((choice, k) => {
    const input = el("input", { type: "radio", name, value: String(k) });
    if (saved && Number(saved.answer) === k) input.checked = true;
    fs.append(el("label", {}, input, document.createTextNode(choice)));
  });
  const check = el("button", { type: "button", className: "secondary", textContent: t("lesson.check") });
  const verdict = el("p", { className: "verdict", role: "status" });
  const expl = el("p", { className: "help" });
  const showVerdict = (correct, explanation, answer) => {
    verdict.textContent = correct ? t("lesson.correct") : t("lesson.wrong", { answer: step.choices[answer] });
    verdict.className = `verdict ${correct ? "ok" : "bad"}`;
    expl.textContent = explanation || "";
  };
  if (saved && saved.correct !== null && saved.correct !== undefined) showVerdict(saved.correct, step.explanation, step.answer);
  check.addEventListener("click", async () => {
    const picked = fs.querySelector("input:checked");
    if (!picked) { lessonNote(t("lesson.chooseFirst"), true); return; }
    check.disabled = true;
    try {
      const r = await saveAnswer(step, Number(picked.value));
      showVerdict(r.correct, r.explanation, r.answer);
      renderLessonStep.chips?.();
    } catch (err) { lessonNote(err.message, true); } finally { check.disabled = false; }
  });
  box.append(fs, el("div", { className: "row" }, check), verdict, expl);
}

// the step chips re-render without rebuilding the whole step (keeps the textarea / radios as they are)
renderLessonStep.chips = () => {
  const lesson = lessonState.lesson;
  if (!lesson) return;
  $("lesson-steps").querySelectorAll("button").forEach((b, k) => {
    const s = lesson.steps[k];
    b.classList.toggle("answered", !!lessonState.answers[s.id] || (s.kind === "run" && !!lessonState.packRuns[s.pack]));
  });
};

// ------------------------------------------------------------------ first visit: start card and tour

// Six short steps that point at the parts of the page. The tour is a plain card (role="dialog", not modal):
// Back / Next / Close are buttons, Esc closes, focus moves to the step's heading, the section it talks about
// is outlined and scrolled into view. Whether it was seen is remembered in localStorage (try/catch: private
// windows and blocked storage simply show it again).
const TOUR_KEY = "windlab-tour-done", START_KEY = "windlab-start-hidden";
// (titles and texts live in the catalog as tour.<n>.title / tour.<n>.text)
const TOUR_STEPS = [
  { target: "step-shape" }, { target: "form-upload" }, { target: "step-conditions" }, { target: "preview-btn" },
  { target: "step-run" }, { target: "step-results" },
];
const tour = { index: 0, open: false, returnTo: null };

function storedFlag(key) { try { return localStorage.getItem(key) === "1"; } catch { return false; } }
function storeFlag(key) { try { localStorage.setItem(key, "1"); } catch { /* not remembered: shown again next time */ } }

function tourHighlight(id) {
  document.querySelectorAll(".tour-target").forEach((x) => x.classList.remove("tour-target"));
  const el = id ? $(id) : null;
  if (!el) return;
  (el.closest("section") || el).classList.add("tour-target");
  if (el.hidden || el.closest("[hidden]")) return;
  const rect = el.getBoundingClientRect();
  if (rect.top < 120 || rect.bottom > window.innerHeight - 40) {
    el.scrollIntoView({ behavior: REDUCED_MOTION.matches ? "auto" : "smooth", block: "center" });
  }
}

function renderTour() {
  const step = TOUR_STEPS[tour.index], n = TOUR_STEPS.length;
  setText("tour-count", t("tour.count", { i: tour.index + 1, n }));
  setText("tour-title", t(`tour.${tour.index + 1}.title`));
  setText("tour-text", t(`tour.${tour.index + 1}.text`));
  $("tour-back").disabled = tour.index === 0;
  setText("tour-next", t(tour.index === n - 1 ? "tour.done" : "tour.next"));
  if (step.target === "form-upload" && shapeKind() !== "upload") {
    // the units choice lives in the upload form: show it for this step, without changing the student's choice
    tourHighlight("step-shape");
  } else {
    tourHighlight(step.target);
  }
  $("tour-title").focus?.({ preventScroll: true });
}

function openTour(from) {
  tour.index = 0; tour.open = true; tour.returnTo = from || null;
  show($("tour"));
  $("tour-open").setAttribute("aria-expanded", "true");
  renderTour();
  $("tour").scrollIntoView({ behavior: REDUCED_MOTION.matches ? "auto" : "smooth", block: "start" });
}

function closeTour() {
  tour.open = false;
  show($("tour"), false);
  tourHighlight(null);
  $("tour-open").setAttribute("aria-expanded", "false");
  storeFlag(TOUR_KEY);
  (tour.returnTo || $("tour-open")).focus?.();
}

$("tour-open").addEventListener("click", () => (tour.open ? closeTour() : openTour($("tour-open"))));
$("tour-close").addEventListener("click", closeTour);
$("tour-back").addEventListener("click", () => { if (tour.index > 0) { tour.index--; renderTour(); } });
$("tour-next").addEventListener("click", () => {
  if (tour.index >= TOUR_STEPS.length - 1) { closeTour(); return; }
  tour.index++;
  renderTour();
});
$("tour").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.preventDefault(); closeTour(); }
  else if (e.key === "ArrowRight" && tour.index < TOUR_STEPS.length - 1) { tour.index++; renderTour(); }
  else if (e.key === "ArrowLeft" && tour.index > 0) { tour.index--; renderTour(); }
});

function hideStartCard() {
  show($("start-card"), false);
  storeFlag(START_KEY);
}
$("start-dismiss").addEventListener("click", () => { hideStartCard(); $("h-shape").scrollIntoView?.(); });
$("start-tour").addEventListener("click", () => { openTour($("tour-open")); });
$("start-lesson").addEventListener("click", () => { hideStartCard(); openLesson("lift-vs-angle"); });

// First visit: the start card, and the tour unless it was closed before. Teacher mode (a lesson opens by
// itself) shows neither; the demo page shows both.
function firstVisit() {
  if (document.body.dataset.teacher === "1" || (state.config && state.config.lesson)) return;
  if (!storedFlag(START_KEY)) show($("start-card"));
  if (!storedFlag(TOUR_KEY)) openTour(null);
}

// ------------------------------------------------------------------ educational-use notice (must be accepted first)

// A real modal (role="dialog", aria-modal): the header, main and footer are made inert while it is open, Tab and
// Shift+Tab cycle inside it, Esc does nothing, and the only way out is to tick the box and press Accept. The accepted
// version is remembered in localStorage (try/catch, like the tour), so the notice returns only when its version
// changes (windlab/disclaimer.py); the Run button and the /api/runs request both depend on it.
const DISCLAIMER_KEY = "windlab-disclaimer-accepted", DISCLAIMER_HEADER = "x-windlab-disclaimer";
const notice = { required: null, accepted: false, returnTo: null };
const INERT_WHILE_OPEN = () => [document.querySelector("a.skip"), document.querySelector("header"), $("main"),
  document.querySelector("footer")];

function storedDisclaimer() { try { return localStorage.getItem(DISCLAIMER_KEY); } catch { return null; } }
function storeDisclaimer(version) { try { localStorage.setItem(DISCLAIMER_KEY, version); } catch { /* asked again next time */ } }
function disclaimerAccepted() { return notice.accepted; }

function modalFocusables() {
  return [...$("disclaimer-modal").querySelectorAll("a[href], button, input")].filter((e) => !e.disabled && !e.hidden);
}

function openDisclaimer() {
  notice.accepted = false;
  notice.returnTo = document.activeElement;
  $("disclaimer-ack").checked = false;
  $("disclaimer-accept").disabled = true;
  INERT_WHILE_OPEN().forEach((e) => e?.setAttribute("inert", ""));
  show($("disclaimer-modal"));
  $("h-disclaimer").focus?.({ preventScroll: true });
  updateRunButton();
}

function acceptDisclaimer() {
  if (!$("disclaimer-ack").checked) return;
  notice.accepted = true;
  if (notice.required) storeDisclaimer(notice.required);
  show($("disclaimer-modal"), false);
  INERT_WHILE_OPEN().forEach((e) => e?.removeAttribute("inert"));
  ($("tour").hidden ? $("tour-open") : $("tour-title")).focus?.({ preventScroll: true });
  updateRunButton();
}

// Called once /api/config has answered (or failed): the notice is shown unless this browser accepted this version.
function checkDisclaimer() {
  notice.required = state.config?.disclaimer?.version || null;
  if (notice.required && storedDisclaimer() === notice.required) {
    notice.accepted = true;
    updateRunButton();
  } else {
    openDisclaimer();
  }
}

$("disclaimer-ack").addEventListener("change", () => { $("disclaimer-accept").disabled = !$("disclaimer-ack").checked; });
$("disclaimer-accept").addEventListener("click", acceptDisclaimer);
$("disclaimer-modal").addEventListener("keydown", (e) => {
  if (e.key === "Escape") { e.preventDefault(); return; }  // no way out but Accept
  if (e.key !== "Tab") return;
  const items = modalFocusables();
  if (!items.length) { e.preventDefault(); return; }
  const i = items.indexOf(document.activeElement);
  if (e.shiftKey && (i <= 0)) { e.preventDefault(); items.at(-1).focus(); }
  else if (!e.shiftKey && (i === -1 || i === items.length - 1)) { e.preventDefault(); items[0].focus(); }
});

// ------------------------------------------------------------------ about WindLab (version, licences, notices)

// A plain card like the tour (role="dialog", not modal): Close or Esc closes and returns focus to the button.
function fillAbout(cfg) {
  setText("about-version", cfg.version || "–");
  setText("about-commit", cfg.commit || "–");
  // (the trade-mark notice itself is fixed page text: index.html / the catalog, the same words as /api/config)
  $("about-licences").replaceChildren(...(cfg.third_party || []).map((x) =>
    el("li", {}, el("b", { textContent: x.name }), document.createTextNode(
      `${x.licence ? ` (${x.licence})` : ""}: ${x.what}.`))));
}

function openAbout() {
  show($("about"));
  $("about-open").setAttribute("aria-expanded", "true");
  scrollTo($("about"));
  $("h-about").focus?.({ preventScroll: true });
}

function closeAbout() {
  show($("about"), false);
  $("about-open").setAttribute("aria-expanded", "false");
  $("about-open").focus?.();
}

$("about-open").addEventListener("click", () => ($("about").hidden ? openAbout() : closeAbout()));
$("about-close").addEventListener("click", closeAbout);
$("about").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); closeAbout(); } });

// ------------------------------------------------------------------ colour theme

const THEME_KEY = "windlab-theme";

function applyTheme(choice) {
  const root = document.documentElement;
  if (choice === "light" || choice === "dark") root.dataset.theme = choice;
  else delete root.dataset.theme;
  // canvases and 3D views read the CSS colours when they draw: draw every one again, now and
  // on the next frame (by then the browser has certainly applied the new theme's styles)
  const redraw = () => {
    redrawAllCharts();
    shapeViewer?.applyTheme();
    resultViewer?.applyTheme();
    preview?.applyTheme();
    if (sliceState.slice) drawSlice();
  };
  redraw();
  requestAnimationFrame(redraw);
}

function savedTheme() {
  try { return localStorage.getItem(THEME_KEY) || "system"; } catch { return "system"; }
}

$("theme").value = savedTheme();
applyTheme($("theme").value);
$("theme").addEventListener("change", () => {
  try { localStorage.setItem(THEME_KEY, $("theme").value); } catch { /* private mode: not remembered */ }
  applyTheme($("theme").value);
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme($("theme").value));

// ------------------------------------------------------------------ start

// The demo page: no uploads, no solver, no trial feedback or case export; everything else works.
function setupDemo() {
  const upload = document.querySelector('input[name=shape-kind][value="upload"]');
  upload.disabled = true;
  upload.parentElement.append(t("demo.uploadOff"));
  ["fb-form", "dl-case", "dl-case-help", "dl-compare", "dl-report", "dl-report-html", "dl-report-help"]
    .forEach((id) => show($(id), false));
  setText("runs-help", t("runs.help.demo"));
}

(async function init() {
  await loadMessages();  // the catalog (i18n/en.json); every message goes through t()
  applyPageText();  // the fixed text of index.html, from the same catalog
  if (DEMO) setupDemo();
  try {
    state.config = await api("/api/config");
    setText("version", state.config.version);
    fillAbout(state.config);
    document.querySelectorAll("[data-max-upload]").forEach((e) => { e.textContent = String(state.config.max_upload_mb); });
    unitsChoicesForFile($("stl-file").files[0]);
    renderBuiltinForm();
    applyPresets(state.config);
    refreshEstimates();
  } catch (err) {
    shapeError(err instanceof ServerUnreachable ? msgNoServer() : err.message);
  }
  checkDisclaimer();  // the educational-use notice, before anything else can be used
  updateRunButton();
  updatePreviewButton();
  await loadGlossary();
  await loadLessons();
  const runs = await refreshRuns();
  // page reloaded while a run is going: show its progress again
  const active = (runs || []).find((r) => r.status === "running") || (runs || []).find((r) => r.status === "queued");
  if (active) openRun(active.run_id);
  else firstVisit();
})();
