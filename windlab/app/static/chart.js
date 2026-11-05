// Small canvas charts, no libraries. Every colour comes from a CSS custom property and is
// read at draw time, so the charts follow the light / dark theme. All charts register
// themselves; redrawAllCharts() draws every one again (the page calls it when the theme
// changes, either from the Colours selector or from the computer's own setting).
//   LineChart: one value against solver iteration (live CL / CD).
//   XYChart:   one or more series of points joined in order (CL vs angle, drag polar,
//              L/D vs angle, and the run comparison), each with its own colour and marker
//              shape, optional highlighted point with a direct label. With several series,
//              points that sit on top of each other are drawn as nested rings (the later
//              series hollow and larger) and the readout lists all of them (task 014).
// Both: crosshair + readout on hover; with keyboard focus, Left/Right step through points.
import { nf, t } from "./i18n.js";
import { coincidentGroups, coincidentMarker, fmtTick, niceTicks, stepGroups } from "./plot.js";

export function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

export const MARKERS = ["circle", "square", "triangle", "diamond", "star"];  // one per compared run (app.js MARKER_TEXT)

export function chartTheme() {
  return {
    ink: cssVar("--ink", "#1b1b1a"), text: cssVar("--chart-text", "#474744"), grid: cssVar("--chart-grid", "#e6e5e0"),
    axis: cssVar("--chart-axis", "#8b8a85"), surface: cssVar("--surface", "#fcfcfb"), tipBg: cssVar("--card", "#ffffff"),
    tipLine: cssVar("--line-strong", "#8b8a85"),
    series: [1, 2, 3, 4, 5].map((i) => cssVar(`--series-${i}`, ["#2a78d6", "#c2570c", "#2a8a4a", "#8e4fc9", "#a8225f"][i - 1])),
  };
}

const ALL_CHARTS = new Set();

export function redrawAllCharts() {
  ALL_CHARTS.forEach((c) => { if (c.c.isConnected) c.draw(); else ALL_CHARTS.delete(c); });
}

export function drawMarker(ctx, shape, x, y, r) {
  ctx.beginPath();
  if (shape === "square") ctx.rect(x - r * 0.88, y - r * 0.88, r * 1.76, r * 1.76);
  else if (shape === "triangle") {
    ctx.moveTo(x, y - r * 1.15); ctx.lineTo(x + r * 1.05, y + r * 0.75); ctx.lineTo(x - r * 1.05, y + r * 0.75);
    ctx.closePath();
  } else if (shape === "diamond") {
    ctx.moveTo(x, y - r * 1.25); ctx.lineTo(x + r * 1.05, y); ctx.lineTo(x, y + r * 1.25); ctx.lineTo(x - r * 1.05, y);
    ctx.closePath();
  } else if (shape === "star") {  // five points, the inner radius about half the outer
    for (let k = 0; k < 10; k++) {
      const a = -Math.PI / 2 + (k * Math.PI) / 5, rr = k % 2 ? r * 0.6 : r * 1.35;
      ctx.lineTo(x + rr * Math.cos(a), y + rr * Math.sin(a));
    }
    ctx.closePath();
  } else ctx.arc(x, y, r, 0, 2 * Math.PI);
  ctx.fill(); ctx.stroke();
}

class BaseChart {
  constructor(canvas) {
    this.c = canvas;
    this.points = [];
    this.hover = null;
    ALL_CHARTS.add(this);
    canvas.addEventListener("pointermove", (e) => this._pick(e));
    canvas.addEventListener("pointerleave", () => { this.hover = null; this.draw(); });
    canvas.addEventListener("keydown", (e) => {
      if (!this.points.length) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        this.hover = this._step(this.hover, e.key === "ArrowLeft" ? -1 : 1);
        e.preventDefault();
        this.draw();
      }
    });
    canvas.addEventListener("blur", () => { this.hover = null; this.draw(); });
    new ResizeObserver(() => this.draw()).observe(canvas);
  }

  _layout() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.c.clientWidth || 480, h = this.c.clientHeight || 200;
    if (this.c.width !== Math.round(w * dpr) || this.c.height !== Math.round(h * dpr)) {
      this.c.width = Math.round(w * dpr); this.c.height = Math.round(h * dpr);
    }
    const ctx = this.c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h, l: 58, r: 14, t: 14, b: 34 };
  }

  // Left/Right from point i (null: none yet): the next point, clamped at the ends
  _step(i, dir) {
    const from = i ?? (dir < 0 ? this.points.length : -1);
    return Math.max(0, Math.min(this.points.length - 1, from + dir));
  }

  // the readout box at the top: one line, or one line per coincident point
  _tooltip(ctx, L, T, x, text) {
    const lines = Array.isArray(text) ? text : [text];
    ctx.font = "13px system-ui, sans-serif";
    const tw = Math.max(...lines.map((s) => ctx.measureText(s).width)) + 12, th = 22 * lines.length;
    const bx = Math.min(Math.max(x - tw / 2, L.l), L.w - L.r - tw);
    ctx.fillStyle = T.tipBg; ctx.strokeStyle = T.tipLine; ctx.lineWidth = 1;
    ctx.fillRect(bx, L.t, tw, th); ctx.strokeRect(bx, L.t, tw, th);
    ctx.fillStyle = T.ink; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    lines.forEach((s, k) => ctx.fillText(s, bx + 6, L.t + 11 + 22 * k));
  }
}

export class LineChart extends BaseChart {
  constructor(canvas, { name = "", digits = 3 } = {}) {
    super(canvas);
    this.name = name;
    this.digits = digits;
  }

  set(points) {
    this.points = points;
    if (this.hover !== null && this.hover >= points.length) this.hover = null;
    this.draw();
    const last = points[points.length - 1];
    if (last) this.c.setAttribute("aria-label",
      t("chart.liveLabel", { name: this.name, value: nf(last[1], this.digits), iteration: last[0] }));
  }

  _scales(L) {
    const p = this.points;
    const x0 = p.length ? p[0][0] : 0, x1 = p.length ? Math.max(p[p.length - 1][0], x0 + 1) : 100;
    // y range from the later 80 % of the run, so the start-up spike doesn't flatten the curve
    const tail = p.slice(Math.floor(p.length * 0.2));
    let y0 = Math.min(...tail.map((q) => q[1])), y1 = Math.max(...tail.map((q) => q[1]));
    if (!isFinite(y0)) { y0 = 0; y1 = 1; }
    const pad = Math.max((y1 - y0) * 0.15, Math.abs(y1) * 0.02, 1e-3);
    y0 -= pad; y1 += pad;
    const X = (x) => L.l + ((x - x0) / (x1 - x0)) * (L.w - L.l - L.r);
    const Y = (y) => L.t + (1 - (y - y0) / (y1 - y0)) * (L.h - L.t - L.b);
    return { X, Y, x0, x1, y0, y1 };
  }

  draw() {
    const L = this._layout(), { ctx, w, h } = L, T = chartTheme();
    ctx.fillStyle = T.surface; ctx.fillRect(0, 0, w, h);
    ctx.font = "12px system-ui, sans-serif";
    if (!this.points.length) {
      ctx.fillStyle = T.text; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText("Waiting for the solver…", L.l, h / 2);
      return;
    }
    const S = this._scales(L);
    ctx.strokeStyle = T.grid; ctx.lineWidth = 1; ctx.fillStyle = T.text;
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const v = S.y0 + ((S.y1 - S.y0) * i) / 4, y = S.Y(v);
      ctx.beginPath(); ctx.moveTo(L.l, y); ctx.lineTo(w - L.r, y); ctx.stroke();
      ctx.fillText(v.toFixed(this.digits), L.l - 6, y);
    }
    ctx.strokeStyle = T.axis;
    ctx.beginPath(); ctx.moveTo(L.l, L.t); ctx.lineTo(L.l, h - L.b); ctx.lineTo(w - L.r, h - L.b); ctx.stroke();
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for (let i = 0; i <= 4; i++) {
      const v = S.x0 + ((S.x1 - S.x0) * i) / 4;
      ctx.fillText(Math.round(v).toString(), S.X(v), h - L.b + 6);
    }
    ctx.textAlign = "right";
    ctx.fillText("iteration", w - L.r, h - 14);
    ctx.save();
    ctx.beginPath(); ctx.rect(L.l, L.t, w - L.l - L.r, h - L.t - L.b); ctx.clip();
    ctx.strokeStyle = T.series[0]; ctx.lineWidth = 2; ctx.lineJoin = "round";
    ctx.beginPath();
    this.points.forEach((q, i) => (i ? ctx.lineTo(S.X(q[0]), S.Y(q[1])) : ctx.moveTo(S.X(q[0]), S.Y(q[1]))));
    ctx.stroke();
    ctx.restore();
    const last = this.points[this.points.length - 1];
    const k = this.hover ?? null;
    if (k === null) {
      ctx.fillStyle = T.ink; ctx.textAlign = "right"; ctx.textBaseline = "bottom";
      ctx.font = "600 13px system-ui, sans-serif";
      ctx.fillText(`${this.name} ${last[1].toFixed(this.digits)}`, w - L.r - 4, L.t + 16);
      return;
    }
    const q = this.points[k], x = S.X(q[0]), y = S.Y(q[1]);
    ctx.strokeStyle = T.text; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, L.t); ctx.lineTo(x, h - L.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = T.series[0]; ctx.strokeStyle = T.surface; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    this._tooltip(ctx, L, T, x, `iteration ${q[0]}: ${this.name} ${q[1].toFixed(this.digits)}`);
  }

  _pick(e) {
    if (!this.points.length) return;
    const L = this._layout(), S = this._scales(L);
    const r = this.c.getBoundingClientRect(), mx = e.clientX - r.left;
    let best = 0, bd = Infinity;
    this.points.forEach((q, i) => { const d = Math.abs(S.X(q[0]) - mx); if (d < bd) { bd = d; best = i; } });
    this.hover = best;
    this.draw();
  }
}

export class XYChart extends BaseChart {
  // One series: set([{x, y, label}], {highlight, highlightText}).
  // Several: setSeries([{name, points: [{x, y, label}]}]) - series k gets colour --series-(k+1)
  // and marker MARKERS[k]; this.points is then the flat list of every point (for hover / keys).
  constructor(canvas, { xName = "x", yName = "y", zeroLine = true } = {}) {
    super(canvas);
    this.xName = xName; this.yName = yName; this.zeroLine = zeroLine;
    this.highlight = null;
    this.series = [];
  }

  set(points, { highlight = null, highlightText = "" } = {}) {
    this.series = [{ name: "", points }];
    this.points = points.map((p) => ({ ...p, s: 0 }));
    this.highlight = highlight;
    this.highlightText = highlightText;
    this.hover = null;
    this.draw();
  }

  setSeries(series) {
    this.series = series;
    this.points = series.flatMap((s, k) => s.points.map((p) => ({ ...p, s: k })));
    this.highlight = null;
    this.highlightText = "";
    this.hover = null;
    this.draw();
  }

  _scales(L) {
    const xs = this.points.map((p) => p.x), ys = this.points.map((p) => p.y);
    let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (!isFinite(x0)) { x0 = 0; x1 = 1; y0 = 0; y1 = 1; }
    if (this.zeroLine) { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
    const px = (x1 - x0) * 0.08 || Math.abs(x0) * 0.1 || 1, py = (y1 - y0) * 0.12 || Math.abs(y0) * 0.1 || 1;
    x0 -= px; x1 += px; y0 -= py; y1 += py;
    const X = (x) => L.l + ((x - x0) / (x1 - x0)) * (L.w - L.l - L.r);
    const Y = (y) => L.t + (1 - (y - y0) / (y1 - y0)) * (L.h - L.t - L.b);
    return { X, Y, x0, x1, y0, y1 };
  }

  draw() {
    const L = this._layout(), { ctx, w, h } = L, T = chartTheme();
    ctx.fillStyle = T.surface; ctx.fillRect(0, 0, w, h);
    ctx.font = "12px system-ui, sans-serif";
    if (!this.points.length) {
      ctx.fillStyle = T.text; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      ctx.fillText("No finished angles yet", L.l, h / 2);
      return;
    }
    const S = this._scales(L);
    const yt = niceTicks(S.y0, S.y1, 5), xt = niceTicks(S.x0, S.x1, 6);
    ctx.lineWidth = 1; ctx.fillStyle = T.text;
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const v of yt.ticks) {
      const y = S.Y(v);
      ctx.strokeStyle = v === 0 && this.zeroLine ? T.axis : T.grid;
      ctx.beginPath(); ctx.moveTo(L.l, y); ctx.lineTo(w - L.r, y); ctx.stroke();
      ctx.fillText(fmtTick(v, yt.step), L.l - 6, y);
    }
    ctx.strokeStyle = T.axis;
    ctx.beginPath(); ctx.moveTo(L.l, L.t); ctx.lineTo(L.l, h - L.b); ctx.lineTo(w - L.r, h - L.b); ctx.stroke();
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for (const v of xt.ticks) ctx.fillText(fmtTick(v, xt.step), S.X(v), h - L.b + 6);
    ctx.textAlign = "right"; ctx.textBaseline = "bottom";
    ctx.fillText(this.xName, w - L.r, h - 2);
    ctx.save();
    ctx.translate(12, L.t); ctx.rotate(-Math.PI / 2); ctx.textAlign = "right"; ctx.textBaseline = "top";
    ctx.fillText(this.yName, 0, 0);
    ctx.restore();
    // with several series, points on top of each other are found once per draw (pixel positions)
    const many = this.series.length > 1;
    this._groups = many ? coincidentGroups(this.points.map((p) => ({ x: S.X(p.x), y: S.Y(p.y) }))) : null;
    // each series: 2px line through its points in order, markers with a surface ring; a marker that sits on
    // an earlier series' marker is drawn as a larger hollow ring in its own colour, so both stay visible
    let flat = 0;
    this.series.forEach((s, k) => {
      const col = T.series[k % T.series.length], shape = MARKERS[k % MARKERS.length];
      ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.lineJoin = "round";
      ctx.beginPath();
      s.points.forEach((p, i) => (i ? ctx.lineTo(S.X(p.x), S.Y(p.y)) : ctx.moveTo(S.X(p.x), S.Y(p.y))));
      ctx.stroke();
      s.points.forEach((p) => {
        const m = coincidentMarker(many ? 4.5 : 4, this._groups ? this._groups[flat++].rank : 0);
        if (m.hollow) { ctx.fillStyle = "rgba(0, 0, 0, 0)"; ctx.strokeStyle = col; ctx.lineWidth = 2; }  // unfilled: the marker underneath must show through
        else { ctx.fillStyle = col; ctx.strokeStyle = T.surface; ctx.lineWidth = 1.5; }
        drawMarker(ctx, shape, S.X(p.x), S.Y(p.y), m.radius);
      });
    });
    if (this.highlight !== null && this.points[this.highlight]) {
      const p = this.points[this.highlight], x = S.X(p.x), y = S.Y(p.y);
      ctx.strokeStyle = T.ink; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 8, 0, 2 * Math.PI); ctx.stroke();
      if (this.highlightText) {
        ctx.font = "600 12px system-ui, sans-serif"; ctx.fillStyle = T.ink; ctx.textBaseline = "bottom";
        const tw = ctx.measureText(this.highlightText).width;
        const right = x + 12 + tw < w - L.r;
        ctx.textAlign = right ? "left" : "right";
        ctx.fillText(this.highlightText, right ? x + 12 : x - 12, Math.max(L.t + 14, y - 8));
      }
    }
    if (this.hover !== null && this.points[this.hover]) {
      const p = this.points[this.hover], x = S.X(p.x), y = S.Y(p.y);
      ctx.strokeStyle = T.text; ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, L.t); ctx.lineTo(x, h - L.b); ctx.stroke();
      ctx.setLineDash([]);
      // the readout lists every point at this spot (the hovered one first), each with its run's letter
      const here = this.coincident(this.hover);
      here.forEach((i, k) => {
        const q = this.points[i];
        ctx.fillStyle = k ? T.surface : T.series[q.s % T.series.length];
        ctx.strokeStyle = k ? T.series[q.s % T.series.length] : T.surface; ctx.lineWidth = 2;
        drawMarker(ctx, MARKERS[q.s % MARKERS.length], x, y, 6 + 2.5 * k);
      });
      this._tooltip(ctx, L, T, x, here.map((i) => this._pointText(this.points[i])));
    }
  }

  _pointText(p) {
    const name = this.series.length > 1 && this.series[p.s].name ? `${this.series[p.s].name} – ` : "";
    return name + p.label;
  }

  // the indices of the points drawn on top of point i (i first, then the others in drawing order)
  coincident(i) {
    if (!this._groups || !this._groups[i]) return [i];
    const g = this._groups[i].group;
    return [i, ...this._groups.map((x, k) => (x.group === g && k !== i ? k : -1)).filter((k) => k >= 0)];
  }

  // Left/Right: one stop per group of coincident points (the readout lists them all)
  _step(i, dir) {
    if (!this._groups) return super._step(i, dir);
    return stepGroups(this._groups.map((g) => g.group), i, dir);
  }

  _pick(e) {
    if (!this.points.length) return;
    const L = this._layout(), S = this._scales(L);
    const r = this.c.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    let best = 0, bd = Infinity;
    this.points.forEach((p, i) => {
      const d = Math.hypot(S.X(p.x) - mx, S.Y(p.y) - my);
      if (d < bd) { bd = d; best = i; }
    });
    this.hover = bd < 40 ? best : null;
    this.draw();
  }
}
