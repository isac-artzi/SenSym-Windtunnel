// Demo mode for the GitHub Pages build (scripts/build_static_demo.py): answers the page's API calls
// from files bundled beside it. No server, no uploads, no solver: lesson result packs, the glossary,
// the lessons, shapes made from their formulas, and the instant preview. Nothing leaves the browser;
// lesson answers and the list of opened runs are kept in this browser's localStorage only.
//
// Every function here returns what the real route returns (see windlab/app/server.py), or throws an
// Error whose message is plain language for the student.

const DATA = "./data";
const STORE_RUNS = "windlab-demo-runs";
const STORE_ANSWERS = "windlab-demo-answers";
const cache = new Map();

async function getJson(path) {
  if (!cache.has(path)) {
    cache.set(path, (async () => {
      const r = await fetch(path);
      if (!r.ok) throw new Error("That part of the demo is missing from this site.");
      return r.json();
    })());
  }
  return cache.get(path);
}

// A bundled binary (surface / slice blobs), gzip-packed in the repository; unpacked here with
// the browser's DecompressionStream (every current browser has it).
async function getBinary(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error("That picture is missing from this site.");
  const buf = await r.arrayBuffer();
  const b = new Uint8Array(buf);
  if (b.length < 2 || b[0] !== 0x1f || b[1] !== 0x8b) return buf;  // the server already unpacked it
  if (typeof DecompressionStream === "undefined") {
    throw new Error("This browser cannot unpack the pre-computed pictures. Please use a current Chrome, Edge, " +
      "Firefox or Safari.");
  }
  return new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
}

function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: not remembered */ }
}

// ---------------------------------------------------------------- shapes (made from their formulas)

const fmt = (x) => String(Number(x));

function nacaView(body, config) {
  const code = String(body.code || "").trim();
  if (!/^[0-9]{4}$/.test(code) || Number(code.slice(2)) < 1 || Number(code.slice(2)) > 40 ||
      (Number(code[0]) > 0 && Number(code[1]) === 0)) {
    throw new Error("The NACA code must be four digits, e.g. 0012 (symmetric, 12 % thick) or 2412 (cambered). " +
      "The last two digits (thickness) must be 01 to 40, and a cambered section (first digit > 0) needs a camber " +
      "position (second digit 1 to 9).");
  }
  const chord = Number(body.chord), span = Number(body.span);
  const [c0, c1] = config.naca.chord, [s0, s1] = config.naca.span;
  if (!(chord >= c0 && chord <= c1)) throw new Error(`Chord must be between ${c0} m and ${c1} m.`);
  if (!(span >= s0 && span <= s1)) throw new Error(`Span must be between ${s0} m and ${s1} m.`);
  if (span / chord > config.naca.max_aspect_ratio) {
    throw new Error(`The span can be at most ${config.naca.max_aspect_ratio} times the chord.`);
  }
  const t = Number(code.slice(2)) / 100, m = Number(code[0]) / 100;
  const key = `naca-${code}-${fmt(chord)}-${fmt(span)}`;
  return view(key, "naca", "streamlined-lifting", `NACA ${code} wing, chord ${fmt(chord)} m, span ${fmt(span)} m`,
    [chord, span, (t + m) * chord], { length: chord, area: chord * span, moment_point: [0.25 * chord, 0, 0],
      how: "mean aerodynamic chord and planform area (chord x span)" },
    { naca: { code, chord, span, taper: 1 } });
}

const BODY = {
  sphere: (p) => ({ name: `Sphere, diameter ${fmt(p.diameter)} m`, size: [p.diameter, p.diameter, p.diameter],
    area: Math.PI * (p.diameter / 2) ** 2, length: p.diameter, how: "frontal area and diameter" }),
  cylinder: (p) => ({ name: `Cylinder across the wind, diameter ${fmt(p.diameter)} m, length ${fmt(p.length)} m`,
    size: [p.diameter, p.length, p.diameter], area: p.diameter * p.length, length: p.diameter,
    how: "frontal area and diameter" }),
  plate: (p) => ({ name: `Flat plate facing the wind, ${fmt(p.width)} m wide, ${fmt(p.height)} m high`,
    size: [0.05 * p.height, p.width, p.height], area: p.width * p.height, length: p.height,
    how: "frontal area and height" }),
  cube: (p) => ({ name: `Cube, side ${fmt(p.side)} m`, size: [p.side, p.side, p.side], area: p.side * p.side,
    length: p.side, how: "frontal area and height" }),
  teardrop: (p) => ({ name: `Streamlined teardrop, diameter ${fmt(p.diameter)} m, fineness ratio ${fmt(p.fineness)}`,
    size: [p.fineness * p.diameter, p.diameter, p.diameter], area: Math.PI * (p.diameter / 2) ** 2,
    length: p.diameter, how: "frontal area and diameter" }),
};

function builtinView(body, config) {
  const spec = (config.builtin_shapes || []).find((s) => s.id === body.shape);
  if (!spec || !BODY[spec.id]) throw new Error("Choose one of the built-in shapes.");
  const params = {};
  for (const p of spec.params) {
    const v = Number(body[p.id]);
    if (!(v >= p.min && v <= p.max)) throw new Error(`${p.label} must be between ${p.min} and ${p.max}.`);
    params[p.id] = v;
  }
  if (spec.id === "cylinder" && !(params.length >= 0.5 * params.diameter && params.length <= 20 * params.diameter)) {
    throw new Error("The cylinder's length must be between 0.5 and 20 times its diameter.");
  }
  const b = BODY[spec.id](params);
  const key = `${spec.id}-` + spec.params.map((p) => `${p.id}_${fmt(params[p.id])}`).join("-");
  return view(key, "builtin", spec.class, b.name, b.size,
    { length: b.length, area: b.area, moment_point: [0.5 * b.size[0], 0, 0], how: b.how },
    { builtin: { shape: spec.id, ...params }, description: spec.description });
}

function view(key, kind, cls, name, size, reference, extra) {
  return {
    geometry_id: `demo-${key}`, kind, class: cls, name, usable: true, units: "m", size_m: size,
    longest_m: Math.max(...size), size_suspicious: false, suggested_units: null, triangles: 0, pieces: 1,
    messages: ["Made in the demo page from its formula. The installed WindLab builds and checks the real surface."],
    problems: [], warnings: [], reference, reference_default: { ...reference },
    orientation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], naca: null, builtin: null, description: null, ...extra,
  };
}

const geometries = new Map();  // geometry_id -> view (this page session)

// ---------------------------------------------------------------- runs (the lessons' result packs)

async function runStatus(id) {
  if (!/^[a-z0-9-]+$/.test(id)) throw new Error("That run was not found.");
  const s = await getJson(`${DATA}/runs/${id}/status.json`);
  return structuredClone(s);
}

function openedRuns() { return stored(STORE_RUNS, []).filter((id) => /^[a-z0-9-]+$/.test(id)); }

async function listRuns() {
  const out = [];
  for (const id of openedRuns()) {
    try { out.push(await getJson(`${DATA}/runs/${id}/summary.json`)); } catch { /* skip a missing one */ }
  }
  return { runs: out, keep_runs: 20, disk_cap_gb: 10 };
}

// ---------------------------------------------------------------- compare (a copy of runs.py's logic)

function differ(values, rel = 0.01) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return false;
  const lo = Math.min(...v), hi = Math.max(...v);
  return hi - lo > rel * Math.max(Math.abs(hi), Math.abs(lo), 1e-12);
}

const fmtRe = (re) => (re >= 1e6 ? `${(re / 1e6).toFixed(2)} million` : Math.round(re).toLocaleString("en"));

function compareEntry(s, index) {
  const p = s.params, r = s.result || {};
  const pts = p.kind === "sweep" ? (r.points || []).filter((pt) => pt.status === "done") : [{ ...r, alpha_deg: p.alpha_deg }];
  const points = pts.map((pt) => ({ alpha_deg: pt.alpha_deg, cl: pt.cl, cd: pt.cd, cm: pt.cm, l_over_d: pt.l_over_d,
    converged: pt.converged, expected: pt.expected })).sort((a, b) => (a.alpha_deg ?? 0) - (b.alpha_deg ?? 0));
  return { run_id: s.run_id, letter: "ABCDE"[index] || String(index + 1), name: p.geometry_name || "run",
    kind: p.kind || "single", airspeed: p.airspeed, reynolds: p.reynolds, reference_area: p.reference_area,
    reference_length: p.reference_length, reference_how: p.reference_how, preset: p.preset,
    geometry_class: p.geometry_class, created: s.created, points };
}

// Mirrors windlab.app.runs.reference_convention.
function referenceConvention(how) {
  const h = (how || "").toLowerCase();
  if (h.includes("set by you")) return "set by you";
  if (h.includes("frontal area")) return "frontal area";
  if (h.includes("planform area")) return "planform area";
  if (h.includes("plan-view box")) return "plan-view box area";
  return "unknown";
}

function compareWarnings(runs) {
  const out = [], by = (k) => runs.map((r) => r[k]);
  const letters = runs.map((r) => r.letter);
  const names = letters.length < 3 ? letters.join(" and ") : `${letters.slice(0, -1).join(", ")} and ${letters.at(-1)}`;
  if (differ(by("reference_area"))) {
    const areas = runs.map((r) => `${r.letter} ${Number(r.reference_area.toPrecision(3))} m²`).join(" / ");
    const conventions = new Set(runs.map((r) => referenceConvention(r.reference_how)));
    if (conventions.size === 1 && conventions.has("frontal area")) {
      out.push(`Each run's coefficients use its own frontal area (${areas}), the standard reference for bodies: ` +
        "that is exactly how handbook drag coefficients are defined, so the CD values can be compared directly. " +
        "The forces in newtons differ with the area, of course.");
    } else if (conventions.size === 1 && conventions.has("planform area")) {
      out.push(`Each wing's coefficients use its own planform area (${areas}), the standard reference for wings, ` +
        "so CL and CD can be compared directly as shape-to-shape numbers. The lift and drag forces differ with " +
        "the area: a bigger wing makes more lift at the same CL.");
    } else {
      const how = runs.map((r) => `${r.letter} ${referenceConvention(r.reference_how)}`).join(" / ");
      out.push(`These runs used different reference areas (${areas}) following different conventions (${how}), ` +
        "so their coefficients are not directly comparable. Coefficients are forces divided by the reference " +
        "area: a bigger area gives smaller CL and CD for the same force. Compare the forces instead, or set the " +
        "same kind of reference area for every run in Step 1.");
    }
  }
  if (differ(by("airspeed"))) {
    const speeds = runs.map((r) => `${r.letter} ${r.airspeed} m/s`).join(" / ");
    out.push(`These runs used different airspeeds (${speeds}), so their Reynolds numbers differ. Part of the ` +
      "difference in CL and CD comes from the Reynolds number, not only from the shape.");
  } else if (differ(by("reynolds"))) {
    const res = runs.map((r) => `${r.letter} ${fmtRe(r.reynolds)}`).join(" / ");
    out.push(`These runs have different Reynolds numbers (${res}) because their reference lengths differ. Part ` +
      "of the difference in CL and CD comes from the Reynolds number, not only from the shape.");
  }
  if (new Set(by("preset")).size > 1) out.push("These runs used different mesh sizes; small differences may come from the mesh.");
  if (!out.length) {
    out.push(`Runs ${names} used the same airspeed and reference area and have the same Reynolds number: ` +
      "their coefficients can be compared directly.");
  }
  return out;
}

// ---------------------------------------------------------------- lessons (answers stay in this browser)

async function lesson(id) {
  if (!/^[a-z0-9-]+$/.test(id)) throw new Error("That lesson was not found.");
  const l = structuredClone(await getJson(`${DATA}/lessons/${id}.json`));
  l.answers = stored(STORE_ANSWERS, {})[id] || {};
  return l;
}

async function saveAnswer(id, body) {
  const l = await lesson(id);
  const step = l.steps.find((s) => s.id === body.step);
  if (!step) throw new Error("That lesson step was not found.");
  let correct = null;
  if (step.kind === "question") {
    if (!Number.isInteger(body.answer) || body.answer < 0 || body.answer >= step.choices.length) {
      throw new Error("Please choose one of the answers.");
    }
    correct = body.answer === step.answer;
  }
  const all = stored(STORE_ANSWERS, {});
  const time = Math.round(Date.now() / 100) / 10;
  (all[id] ??= {})[body.step] = { answer: body.answer, correct, time };
  store(STORE_ANSWERS, all);
  const out = { saved: true, step: body.step, correct, time };
  if (step.kind === "question") { out.explanation = step.explanation || ""; out.answer = step.answer; }
  return out;
}

// ---------------------------------------------------------------- the router

export async function demoApi(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const [route, query = ""] = path.split("?");
  const q = new URLSearchParams(query);
  const body = opts.body && typeof opts.body === "string" ? JSON.parse(opts.body) : null;
  let m;
  if (route === "/api/config") {
    const c = structuredClone(await getJson(`${DATA}/config.json`));
    c.version = `${c.version} (online demo)`;
    return c;
  }
  if (route === "/api/estimate") return { presets: {}, basis: "demo", n_angles: 1, n_procs: 0 };
  if (route === "/api/glossary") return getJson(`${DATA}/glossary.json`);
  if (route === "/api/validation-table") return getJson(`${DATA}/validation_table.json`);
  if (route === "/api/lessons") return getJson(`${DATA}/lessons.json`);
  if ((m = route.match(/^\/api\/lessons\/([\w-]+)$/))) return lesson(m[1]);
  if ((m = route.match(/^\/api\/lessons\/([\w-]+)\/answers$/)) && method === "POST") return saveAnswer(m[1], body);
  if ((m = route.match(/^\/api\/lessons\/([\w-]+)\/import$/)) && method === "POST") {
    const l = await lesson(m[1]);
    const step = l.steps.find((s) => s.kind === "run" && s.pack === body.run);
    if (!step) throw new Error("That pre-computed result is not part of this lesson.");
    const id = `${m[1]}--${body.run}`;
    const s = await runStatus(id);
    const ids = openedRuns();
    if (!ids.includes(id)) store(STORE_RUNS, [id, ...ids]);
    return s;
  }
  if (route === "/api/geometry/naca" && method === "POST") {
    const v = nacaView(body, await getJson(`${DATA}/config.json`));
    geometries.set(v.geometry_id, v);
    return v;
  }
  if (route === "/api/geometry/builtin" && method === "POST") {
    const v = builtinView(body, await getJson(`${DATA}/config.json`));
    geometries.set(v.geometry_id, v);
    return v;
  }
  if ((m = route.match(/^\/api\/geometry\/(demo-[\w.-]+)\/preview$/))) {
    return getBinary(`${DATA}/geometry/${m[1].slice(5)}/preview.bin.gz`);  // only the lessons' shapes have one
  }
  if ((m = route.match(/^\/api\/geometry\/(demo-[\w.-]+)\/reference$/)) && method === "POST") {
    const g = geometries.get(m[1]);
    if (!g) throw new Error("That shape was not found. Please make it again.");
    const d = g.reference_default, ref = { ...g.reference };
    const changed = [];
    if (body.length != null) { ref.length = Number(body.length); changed.push("length"); } else ref.length = d.length;
    if (body.area != null) { ref.area = Number(body.area); changed.push("area"); } else ref.area = d.area;
    if (!(ref.length > 0 && ref.area > 0)) throw new Error("Please enter a positive reference length and area.");
    ref.how = !changed.length ? d.how : changed.length === 2 ? "set by you" : `${changed[0]} set by you; otherwise ${d.how}`;
    ref.custom = changed;
    g.reference = ref;
    return { ...g };
  }
  if ((m = route.match(/^\/api\/geometry\/(demo-[\w.-]+)$/))) {
    const g = geometries.get(m[1]);
    if (!g) throw new Error("That shape was not found. Please make it again.");
    return { ...g };
  }
  if (route === "/api/runs" && method === "GET") return listRuns();
  if (route === "/api/runs" && method === "POST") {
    throw new Error("The full simulation runs only in the installed WindLab. Use the instant preview here, or open " +
      "a lesson's pre-computed results.");
  }
  if (route === "/api/compare") {
    const ids = (q.get("runs") || "").split(",").filter(Boolean);
    const runs = [];
    for (const id of ids) runs.push(compareEntry(await runStatus(id), runs.length));
    return { runs, warnings: compareWarnings(runs), label: runs[0] ? runs[0].label : "" };
  }
  if ((m = route.match(/^\/api\/runs\/([\w-]+)$/)) && method === "GET") return runStatus(m[1]);
  if ((m = route.match(/^\/api\/runs\/([\w-]+)$/)) && method === "DELETE") {
    store(STORE_RUNS, openedRuns().filter((id) => id !== m[1]));
    return { deleted: m[1] };
  }
  if ((m = route.match(/^\/api\/runs\/([\w-]+)\/(surface|slice)$/))) {
    const point = q.get("point");
    return getBinary(`${DATA}/runs/${m[1]}/${m[2]}${point != null ? `_${point}` : ""}.bin.gz`);
  }
  if ((m = route.match(/^\/api\/runs\/([\w-]+)\/slice-info$/))) {
    const point = q.get("point");
    return getJson(`${DATA}/runs/${m[1]}/sliceinfo${point != null ? `_${point}` : ""}.json`);
  }
  if ((m = route.match(/^\/api\/runs\/([\w-]+)\/cancel$/))) throw new Error("Pre-computed results cannot be stopped.");
  if (route === "/api/feedback") throw new Error("Feedback is collected by the installed WindLab, not by the demo.");
  throw new Error("This part of WindLab is not available in the online demo. Install WindLab to use it.");
}
