// Pure helpers for the page's pictures and numbers (no DOM, no three.js):
// colour maps, "nice" tick values, and lengths / areas in sensible units.
import { t } from "./i18n.js";

// Perceptually uniform sequential map (matplotlib's viridis, 9 samples; CC0).
export const VIRIDIS = ["#440154", "#472d7b", "#3b528b", "#2c728e", "#21918c", "#28ae80", "#5ec962", "#addc30",
  "#fde725"];
// Diverging map centred on 0 (ColorBrewer RdBu, reversed: blue = negative, red = positive).
export const DIVERGING = ["#2166ac", "#4393c3", "#92c5de", "#d1e5f0", "#f7f7f7", "#fddbc7", "#f4a582", "#d6604d",
  "#b2182b"];

// "#rrggbb" (with the #) -> [r, g, b] in 0..1. The only hex parser the page's canvases use
// (tests/test_preview.py mirrors it), so a slip in the offsets cannot hide in one script.
export function hexRgb(h) {
  return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
}
const RGB_CACHE = new Map();
function rgbOf(h) {
  if (!RGB_CACHE.has(h)) RGB_CACHE.set(h, hexRgb(h));
  return RGB_CACHE.get(h);
}

// Colour at t in [0, 1] (clamped) along a map of hex stops: [r, g, b] in 0..1.
export function mapColour(t, stops) {
  const x = Math.max(0, Math.min(1, isFinite(t) ? t : 0)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x)), f = x - i;
  const a = rgbOf(stops[i]), b = rgbOf(stops[i + 1]);
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

// CSS linear-gradient of a colour map (for the legend bar).
export function cssGradient(stops) {
  return `linear-gradient(to right, ${stops.map((s, i) => `${s} ${(100 * i) / (stops.length - 1)}%`).join(", ")})`;
}

// Ticks at 1, 2, 2.5 or 5 x 10^k covering [lo, hi] with about n intervals.
export function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1;
  const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : Number(v.toPrecision(12)));
  }
  return { ticks: out, step };
}

// Tick text with just enough decimals for the step (0.25 -> "0.25", 0.5 -> "0.5", 2 -> "2").
export function fmtTick(v, step) {
  let d = 0;
  while (d < 4 && Math.abs(step * 10 ** d - Math.round(step * 10 ** d)) > 1e-6 * 10 ** d) d++;
  const s = v.toFixed(d);
  return s.replace(/^-(0(\.0+)?)$/, "$1").replace("-", "−");
}

// Round x up to a "nice" number (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8 x 10^k).
export function niceCeil(x) {
  if (!(x > 0)) return 0;
  const mag = 10 ** Math.floor(Math.log10(x));
  const m = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((k) => k * mag >= x * (1 - 1e-9));
  return Number((m * mag).toPrecision(6));
}

function trim(x) { return String(Number(x.toPrecision(3))); }

// A length in metres as text with a sensible unit: "1.2 m", "30 cm", "4.5 mm".
// quantum (optional, metres): round to a multiple of it first, so 0.0000085 m on a
// 0.3 m part (quantum 0.3 mm) reads "0 m".
export function fmtLength(m, quantum = 0) {
  if (m == null || !isFinite(m)) return "–";
  const v = quantum > 0 ? Math.round(m / quantum) * quantum : m;
  const a = Math.abs(v);
  if (a === 0 || a < 1e-9) return "0 m";
  const sign = v < 0 ? "−" : "";
  if (a >= 1) return `${sign}${trim(a)} m`;
  if (a >= 0.01) return `${sign}${trim(a * 100)} cm`;
  return `${sign}${trim(a * 1000)} mm`;
}

// An area in square metres: "0.18 m²", "45 cm²", "120 mm²".
export function fmtArea(m2) {
  if (m2 == null || !isFinite(m2)) return "–";
  const a = Math.abs(m2);
  if (a >= 0.01) return `${trim(m2)} m²`;
  if (a >= 1e-4) return `${trim(m2 * 1e4)} cm²`;
  return `${trim(m2 * 1e6)} mm²`;
}

// Where the flow slice is, in plain words. Rounds to 0.1 % of the part's size, so the
// tiny nudge off the mesh faces never shows as "y = 0.00000848 m".
export function slicePositionText(y, yCentre, bodySize) {
  const q = (bodySize > 0 ? bodySize : 1) * 1e-3;
  const centre = yCentre != null && Math.abs(y - yCentre) < q;
  return `y = ${fmtLength(y, q)}` + (centre ? t("plot.centrePlane") : "");
}

// Colour range for air speed (relative to the wind speed) in a window: 0 at the bottom
// (stopped air), a nice value just above the 99th percentile at the top.
export function speedRange(values) {
  const s = Float32Array.from(values).sort();
  const p99 = s.length ? s[Math.min(s.length - 1, Math.round(0.99 * (s.length - 1)))] : 1;
  const hi = Math.max(0.5, niceCeil(p99));
  return { lo: 0, hi, clipHi: s.length ? s[s.length - 1] > hi : false, clipLo: false };
}

// Symmetric colour range for Cp centred on 0: +/- a nice value covering the 2nd..98th percentiles.
export function cpSymmetricRange(values) {
  const s = Float32Array.from(values).sort();
  if (!s.length) return { lo: -1, hi: 1, clipLo: false, clipHi: false };
  const q = (f) => s[Math.min(s.length - 1, Math.round(f * (s.length - 1)))];
  const m = Math.max(0.2, niceCeil(Math.max(Math.abs(q(0.02)), Math.abs(q(0.98)))));
  return { lo: -m, hi: m, clipLo: s[0] < -m, clipHi: s[s.length - 1] > m };
}

// Points of a chart that sit on top of each other (task 014: two compared runs with L/D 8.7 and 8.8 at the
// same angle drew one marker over the other). For pixel positions [{x, y}], every point gets the id of its
// group (points within tol pixels of an earlier member share a group), its rank within the group in drawing
// order (0 for the first), and the group's size. A point with non-finite coordinates stands alone.
export function coincidentGroups(pts, tol = 6) {
  const groupOf = [], rank = [], count = [];
  for (let i = 0; i < pts.length; i++) {
    let g = -1;
    for (let j = 0; j < i && g < 0; j++) {
      if (Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y) <= tol) g = groupOf[j];
    }
    if (g < 0) { g = count.length; count.push(0); }
    groupOf[i] = g;
    rank[i] = count[g]++;
  }
  return groupOf.map((g, i) => ({ group: g, rank: rank[i], size: count[g] }));
}

// How to draw the marker of a point with the given rank in its group: the first one as usual (filled), every
// later one as a hollow ring, larger by 2.5 px per rank, so that all of them stay visible.
export function coincidentMarker(base, rank) {
  return { radius: base + 2.5 * Math.max(0, rank), hollow: rank > 0 };
}

// Keyboard stepping through a chart's points when some of them coincide: from point i (null: none yet) in
// direction dir (+1 / -1), the next point in another group, or i itself at the ends. groups is the list of
// group ids per point (coincidentGroups(...).map((g) => g.group)); one stop per group, the readout lists all.
export function stepGroups(groups, i, dir) {
  const n = groups.length;
  if (!n) return null;
  if (i == null || i < 0 || i >= n) return dir > 0 ? 0 : n - 1;
  for (let k = i + dir; k >= 0 && k < n; k += dir) {
    if (groups[k] !== groups[i]) return k;
  }
  return i;
}

// Legend ticks for a range: [{value, text, at}] with "at" the fraction 0..1 along the bar.
export function legendTicks(lo, hi, n = 5) {
  const { ticks, step } = niceTicks(lo, hi, n);
  return ticks.filter((v) => v >= lo - 1e-9 && v <= hi + 1e-9)
    .map((v) => ({ value: v, text: fmtTick(v, step), at: (v - lo) / (hi - lo) }));
}
