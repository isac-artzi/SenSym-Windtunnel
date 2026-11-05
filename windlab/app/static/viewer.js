// 3D view of a WindLab surface blob (see windlab/app/surface.py for the format).
// Own small orbit controls: mouse/touch drag turns; the mouse wheel scrolls the PAGE unless
// Ctrl / Cmd is held (or a trackpad pinch, which browsers report as Ctrl + wheel) or the view
// has been clicked / focused - then it zooms; two-finger pinch zooms on touch screens. The
// keyboard works when the view has focus (arrows turn, + / - zoom, 0 resets, Esc leaves).
import * as THREE from "./vendor/three/three.module.js";
import { t } from "./i18n.js";
import { DIVERGING, VIRIDIS, cpSymmetricRange, fmtLength, mapColour, speedRange } from "./plot.js";

// Diverging Cp scale: blue (suction) - gray (freestream) - red (pressure). Matches app.css
// (--cp-neg / --cp-mid / --cp-pos; the same in both themes, drawn on its own surface).
const CP_NEG = new THREE.Color("#1c5cab");
const CP_MID = new THREE.Color("#d9d8d3");
const CP_POS = new THREE.Color("#b8322f");

export function decodeSurface(buf) {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== "WLS1") throw new Error("not a WindLab surface");
  const nv = dv.getUint32(4, true), nt = dv.getUint32(8, true), flags = dv.getUint32(12, true);
  const cpMin = dv.getFloat32(16, true), cpMax = dv.getFloat32(20, true);
  let off = 24;
  const positions = new Float32Array(buf, off, 3 * nv); off += 12 * nv;
  const index = new Uint32Array(buf, off, 3 * nt); off += 12 * nt;
  const cp = flags & 1 ? new Float32Array(buf, off, nv) : null;
  return { positions, index, cp, cpMin, cpMax };
}

// Colour range: each arm of the diverging scale is stretched separately (gray stays at
// Cp = 0) and clipped at the 2nd / 98th percentile, so one sharp suction peak at a
// leading edge doesn't wash out the rest of the surface.
// Returns [lo, hi, lowEndClipped, highEndClipped].
export function cpRange(cp, cpMin, cpMax) {
  if (!cp || !cp.length) return [Math.min(cpMin, -0.1), Math.max(cpMax, 0.1), false, false];
  const s = Float32Array.from(cp).sort();
  const q = (f) => s[Math.min(s.length - 1, Math.max(0, Math.round(f * (s.length - 1))))];
  const lo = Math.min(q(0.02), -0.1), hi = Math.max(q(0.98), 0.1);
  return [lo, hi, cpMin < lo, cpMax > hi];
}

function cpColor(v, lo, hi, out) {
  const t = Math.max(-1, Math.min(1, v < 0 ? v / -lo : v / hi));
  return t < 0 ? out.copy(CP_MID).lerp(CP_NEG, -t) : out.copy(CP_MID).lerp(CP_POS, t);
}

// ---------------------------------------------------------------- flow slice (2D)

export function decodeSlice(buf) {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== "WLP1") throw new Error("not a WindLab slice");
  const nv = dv.getUint32(4, true), nt = dv.getUint32(8, true);
  const f = [];
  for (let i = 0; i < 8; i++) f.push(dv.getFloat32(12 + 4 * i, true));
  let off = 44;
  const xz = new Float32Array(buf, off, 2 * nv); off += 8 * nv;
  const index = new Uint32Array(buf, off, 3 * nt); off += 12 * nt;
  const speed = new Float32Array(buf, off, nv); off += 4 * nv;
  const cp = new Float32Array(buf, off, nv);
  const [xMin, xMax, zMin, zMax, speedMax, cpMin, cpMax, y] = f;
  return { xz, index, speed, cp, xMin, xMax, zMin, zMax, speedMax, cpMin, cpMax, y };
}

// Speed: viridis (perceptually uniform, dark = slow, yellow = fast).
// Pressure coefficient: diverging map centred on Cp = 0 (blue = suction, red = pressure).
export const SPEED_MAP = VIRIDIS;
export const CP_MAP = DIVERGING;

// Values of the slice points that lie inside a window [x0, x1, z0, z1].
function valuesInWindow(slice, values, win) {
  if (!win) return values;
  const [x0, x1, z0, z1] = win, out = [];
  for (let i = 0; i < values.length; i++) {
    const x = slice.xz[2 * i], z = slice.xz[2 * i + 1];
    if (x >= x0 && x <= x1 && z >= z0 && z <= z1) out.push(values[i]);
  }
  return out.length ? out : values;
}

export class SliceViewer {
  constructor(el) {
    this.el = el;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    el.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -10, 10);
    this.mesh = null;
    this.lines = null;
    this.marker = null;
    this.probe = null;     // the last probed point {x, z, speed, cp, onBody}
    this.onProbe = null;   // callback(probe) for the page's numeric readout (ACC-2)
    this._probeControls();
    new ResizeObserver(() => this._resize()).observe(el);
  }

  // Numeric probe: a click (not a drag) reads the nearest slice point; with keyboard focus the
  // arrow keys move the probe by 2 % of the window, Home returns to the centre, Enter reads again.
  _probeControls() {
    const c = this.renderer.domElement;
    let downAt = null;
    c.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; });
    c.addEventListener("pointerup", (e) => {
      if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 6) return;
      const r = c.getBoundingClientRect();
      this.probeAtPixel(e.clientX - r.left, e.clientY - r.top);
      this.el.focus({ preventScroll: true });
    });
    this.el.addEventListener("keydown", (e) => {
      if (!this.slice) return;
      const [x0, x1, z0, z1] = this.window;
      const dx = (x1 - x0) * 0.02, dz = (z1 - z0) * 0.02;
      let p = this.probe ? [this.probe.x, this.probe.z] : [(x0 + x1) / 2, (z0 + z1) / 2];
      if (e.key === "ArrowLeft") p[0] -= dx;
      else if (e.key === "ArrowRight") p[0] += dx;
      else if (e.key === "ArrowUp") p[1] += dz;
      else if (e.key === "ArrowDown") p[1] -= dz;
      else if (e.key === "Home") p = [(x0 + x1) / 2, (z0 + z1) / 2];
      else if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      this.probeAt(p[0], p[1]);
    });
  }

  probeAtPixel(px, py) {
    const w = this.el.clientWidth || 600, h = this.el.clientHeight || 260;
    const x = this.camera.left + (px / w) * (this.camera.right - this.camera.left);
    const z = this.camera.top - (py / h) * (this.camera.top - this.camera.bottom);
    return this.probeAt(x, z);
  }

  // Read the slice at (x, z): the nearest point; inside the part (the hole in the slice) there is
  // no point nearby, which the readout reports as "on the part".
  probeAt(x, z) {
    const s = this.slice;
    if (!s) return null;
    let best = -1, bd = Infinity;
    for (let i = 0; i < s.speed.length; i++) {
      const d = Math.hypot(s.xz[2 * i] - x, s.xz[2 * i + 1] - z);
      if (d < bd) { bd = d; best = i; }
    }
    if (best < 0) return null;
    const [x0, x1, z0, z1] = this.window;
    const near = bd < 0.02 * Math.max(x1 - x0, z1 - z0);
    this.probe = { x, z, i: best, px: s.xz[2 * best], pz: s.xz[2 * best + 1], speed: s.speed[best], cp: s.cp[best],
      onBody: !near, distance: bd };
    this._drawMarker();
    if (this.onProbe) this.onProbe(this.probe);
    return this.probe;
  }

  _drawMarker() {
    if (this.marker) { this.scene.remove(this.marker); this.marker.geometry.dispose(); this.marker = null; }
    if (!this.probe || !this.slice) return;
    const [x0, x1] = this.window;
    const r = 0.012 * (x1 - x0), pts = [];
    for (let k = 0; k < 32; k++) {
      const a = (2 * Math.PI * k) / 32;
      pts.push(this.probe.x + r * Math.cos(a), this.probe.z + r * Math.sin(a), 0.8);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pts), 3));
    this.marker = new THREE.LineLoop(g, new THREE.LineBasicMaterial({ color: new THREE.Color(cssColor("--focus", "#e8590c")),
      depthTest: false }));
    this.marker.renderOrder = 2;
    this.scene.add(this.marker);
    this.renderer.render(this.scene, this.camera);
  }

  // field: "speed" | "cp"; opts.window = [x0, x1, z0, z1] to show (default: the whole slice);
  // opts.lines = [[[x, z], ...], ...] streamlines to draw on top (or null).
  // Returns the colour range used ({lo, hi, clipLo, clipHi, max, min}), for the legend.
  show(slice, field = "speed", { window = null, lines = null } = {}) {
    this.slice = slice; this.field = field;
    this.window = window || [slice.xMin, slice.xMax, slice.zMin, slice.zMax];
    // the part is the hole in the slice: the background is the part's colour
    this.renderer.setClearColor(new THREE.Color(cssColor("--body-fill", "#3d3d3a")));
    if (this.mesh) { this.scene.remove(this.mesh); this.mesh.geometry.dispose(); this.mesh = null; }
    if (this.lines) { this.scene.remove(this.lines); this.lines.geometry.dispose(); this.lines = null; }
    const n = slice.speed.length;
    const pos = new Float32Array(3 * n);
    for (let i = 0; i < n; i++) { pos[3 * i] = slice.xz[2 * i]; pos[3 * i + 1] = slice.xz[2 * i + 1]; }
    const colors = new Float32Array(3 * n);
    const values = field === "speed" ? slice.speed : slice.cp;
    const inWin = valuesInWindow(slice, values, this.window);
    const range = field === "speed" ? speedRange(inWin) : cpSymmetricRange(inWin);
    range.min = Infinity; range.max = -Infinity;  // a loop: slices can have too many points to spread
    for (const v of inWin) { if (v < range.min) range.min = v; if (v > range.max) range.max = v; }
    const stops = field === "speed" ? SPEED_MAP : CP_MAP;
    const c = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const [r, gr, b] = mapColour((values[i] - range.lo) / (range.hi - range.lo), stops);
      c.setRGB(r, gr, b, THREE.SRGBColorSpace);  // map colours are sRGB; vertex colours are linear
      colors[3 * i] = c.r; colors[3 * i + 1] = c.g; colors[3 * i + 2] = c.b;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    g.setIndex(new THREE.BufferAttribute(slice.index, 1));
    this.mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }));
    this.scene.add(this.mesh);
    if (lines && lines.length) {
      const segs = [];
      for (const line of lines) {
        for (let k = 1; k < line.length; k++) {
          segs.push(line[k - 1][0], line[k - 1][1], 0.5, line[k][0], line[k][1], 0.5);
        }
      }
      const lg = new THREE.BufferGeometry();
      lg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(segs), 3));
      const mat = new THREE.LineBasicMaterial({ color: new THREE.Color(cssColor("--flowline", "#111110")),
        transparent: true, opacity: 0.55, depthTest: false });
      this.lines = new THREE.LineSegments(lg, mat);
      this.lines.renderOrder = 1;
      this.scene.add(this.lines);
    }
    this._fitHeight();
    this._resize();
    if (this.probe) this.probeAt(this.probe.x, this.probe.z);  // same place, the new field's value
    return range;
  }

  // The view's height follows the window's shape (metres stay square), within limits.
  _fitHeight() {
    const [x0, x1, z0, z1] = this.window;
    const w = this.el.clientWidth || 600;
    const h = Math.round(Math.max(220, Math.min(460, (w * (z1 - z0)) / Math.max(x1 - x0, 1e-9))));
    this.el.style.height = `${h}px`;
  }

  _resize() {
    const w = this.el.clientWidth || 600, h = this.el.clientHeight || 260;
    this.renderer.setSize(w, h, false);
    if (!this.slice) return;
    // fit the window, keeping metres square (the slice continues beyond it where it can)
    const [x0, x1, z0, z1] = this.window;
    const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
    const scale = Math.max((x1 - x0) / 2 / w, (z1 - z0) / 2 / h) * 1.01;
    Object.assign(this.camera, { left: cx - w * scale, right: cx + w * scale, top: cz + h * scale,
      bottom: cz - h * scale });
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
  }
}

function cssColor(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const fmtMetres = (m) => fmtLength(m);

// Is this a wheel event the view should use for zooming (instead of scrolling the page)?
// Ctrl / Cmd + wheel, a trackpad pinch (browsers send it as Ctrl + wheel), or any wheel
// while the view has keyboard focus (after a click or Tab).
export function wheelZooms(e, focused) {
  return !!(e.ctrlKey || e.metaKey || focused);
}

export class SurfaceViewer {
  constructor(el) {
    this.el = el;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    el.appendChild(this.renderer.domElement);
    // the Ctrl + scroll hint (catalog: viewer.wheelHint), shown briefly when the wheel scrolls past the view
    this.hint = document.createElement("div");
    this.hint.className = "viewer-hint";
    this.hint.setAttribute("aria-hidden", "true");
    this.hint.textContent = t("viewer.wheelHint");
    this.hint.hidden = true;
    el.appendChild(this.hint);
    // HTML labels drawn over the 3D view (positions projected on every render)
    this.labelLayer = document.createElement("div");
    this.labelLayer.className = "viewer-labels";
    this.labelLayer.setAttribute("aria-hidden", "true");
    el.appendChild(this.labelLayer);
    this.labels = [];
    this.applyTheme();
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.001, 1000);
    this.camera.up.set(0, 0, 1); // WindLab frame: x = wind, z = up
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a85, 1.0));
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(-1, -2, 3);
    this.scene.add(sun);
    this.camLight = new THREE.DirectionalLight(0xffffff, 0.5);
    this.scene.add(this.camLight);
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.target = new THREE.Vector3();
    this.radius = 1;
    this.view = { az: -2.2, el: 0.45, dist: 3 };
    this.mesh = null;
    this.marker = null;
    this.probe = null;     // the last probed surface point {x, y, z, cp}
    this.onProbe = null;   // callback(probe) for the page's numeric readout (ACC-2)
    this.raycaster = new THREE.Raycaster();
    this._controls();
    new ResizeObserver(() => this._resize()).observe(el);
    this._resize();
  }

  applyTheme() {
    this.renderer.setClearColor(new THREE.Color(cssColor("--surface", "#fcfcfb")));
    if (this.last) this.show(this.last.surface, this.last.opts, true);
  }

  _label(text, pos, cls) {
    const d = document.createElement("span");
    d.className = `viewer-label ${cls || ""}`;
    d.textContent = text;
    this.labelLayer.appendChild(d);
    this.labels.push({ el: d, pos: pos.clone() });
  }

  show(surface, opts = {}, keepView = false) {
    const { alphaDeg = 0, colourByCp = true, annotate = false } = opts;
    this.last = { surface, opts };
    this.group.clear();
    this.labelLayer.replaceChildren();
    this.labels = [];
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(surface.positions, 3));
    g.setIndex(new THREE.BufferAttribute(surface.index, 1));
    g.computeVertexNormals();
    let mat;
    if (colourByCp && surface.cp) {
      const [lo, hi] = cpRange(surface.cp, surface.cpMin, surface.cpMax);
      const colors = new Float32Array(surface.positions.length);
      const c = new THREE.Color();
      for (let i = 0; i < surface.cp.length; i++) {
        cpColor(surface.cp[i], lo, hi, c);
        colors[3 * i] = c.r; colors[3 * i + 1] = c.g; colors[3 * i + 2] = c.b;
      }
      g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      mat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
    } else {
      mat = new THREE.MeshLambertMaterial({ color: 0x9db4cf, side: THREE.DoubleSide });
    }
    const mesh = new THREE.Mesh(g, mat);
    this.group.add(mesh);
    this.mesh = mesh;
    this.marker = null;
    this.probe = null;
    g.computeBoundingBox();
    const box = g.boundingBox;
    box.getCenter(this.target);
    const size = box.getSize(new THREE.Vector3());
    this.radius = Math.max(size.length() / 2, 1e-3);
    // wind arrow upstream of the part, tilted by the angle of attack (wind comes from below for alpha > 0)
    const windColor = new THREE.Color(cssColor("--wind", "#b8322f"));
    const upColor = new THREE.Color(cssColor("--up", "#2f7d32"));
    const a = (alphaDeg * Math.PI) / 180;
    const dir = new THREE.Vector3(Math.cos(a), 0, Math.sin(a));
    const len = this.radius * 0.9;
    const start = this.target.clone().addScaledVector(dir, -(this.radius * 1.15 + len));
    this.group.add(new THREE.ArrowHelper(dir, start, len, windColor, len * 0.25, len * 0.12));
    if (annotate) {
      this._label(alphaDeg ? t("viewer.label.windTilted", { alpha: alphaDeg }) : t("viewer.label.wind"), start.clone().addScaledVector(dir, len * 0.4),
        "wind");
      // "up" arrow above the part
      const upLen = this.radius * 0.6;
      const upStart = new THREE.Vector3(this.target.x, this.target.y, box.max.z + this.radius * 0.1);
      this.group.add(new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), upStart, upLen, upColor, upLen * 0.25,
        upLen * 0.14));
      this._label(t("viewer.label.up"), upStart.clone().add(new THREE.Vector3(0, 0, upLen * 1.1)), "up");
      // bounding box with its size along each axis
      const boxColor = new THREE.Color(cssColor("--ink-2", "#474744"));
      this.group.add(new THREE.Box3Helper(box.clone(), boxColor));
      const { min, max } = box;
      this._label(`x ${fmtMetres(size.x)}`, new THREE.Vector3((min.x + max.x) / 2, min.y, min.z), "dim");
      this._label(`y ${fmtMetres(size.y)}`, new THREE.Vector3(max.x, (min.y + max.y) / 2, min.z), "dim");
      this._label(`z ${fmtMetres(size.z)}`, new THREE.Vector3(max.x, min.y, (min.z + max.z) / 2), "dim");
    } else {
      // small axes at the lower front corner
      const axes = new THREE.AxesHelper(this.radius * 0.35);
      axes.position.copy(box.min);
      this.group.add(axes);
    }
    if (keepView) this.render(); else this.reset();
  }

  _placeLabels() {
    if (!this.labels.length) return;
    const w = this.el.clientWidth, h = this.el.clientHeight;
    for (const { el, pos } of this.labels) {
      const p = pos.clone().project(this.camera);
      const visible = p.z < 1 && Math.abs(p.x) <= 1.05 && Math.abs(p.y) <= 1.05;
      el.hidden = !visible;
      if (visible) {
        el.style.left = `${((p.x + 1) / 2) * w}px`;
        el.style.top = `${((1 - p.y) / 2) * h}px`;
      }
    }
  }

  reset() {
    this.view = { az: -2.2, el: 0.45, dist: this.radius * 3.6 };
    this.render();
  }

  render() {
    const { az, el, dist } = this.view;
    const p = new THREE.Vector3(Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el));
    this.camera.position.copy(this.target).addScaledVector(p, dist);
    this.camera.near = dist / 100;
    this.camera.far = dist * 100;
    this.camera.updateProjectionMatrix();
    this.camera.lookAt(this.target);
    this.camLight.position.copy(this.camera.position);
    this.renderer.render(this.scene, this.camera);
    this._placeLabels();
  }

  _resize() {
    const w = this.el.clientWidth || 600, h = this.el.clientHeight || 380;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.render();
  }

  _turn(dAz, dEl) {
    this.view.az += dAz;
    this.view.el = Math.max(-1.5, Math.min(1.5, this.view.el + dEl));
    this.render();
  }

  _zoom(f) {
    this.view.dist = Math.max(this.radius * 1.1, Math.min(this.radius * 30, this.view.dist * f));
    this.render();
  }

  _focused() {
    return document.activeElement === this.el || this.el.contains(document.activeElement);
  }

  // Numeric probe (ACC-2): read Cp at a clicked point of the surface, or at the centre of the view
  // with Enter / P when the view has keyboard focus (turn the part with the arrow keys first).
  probeAtPixel(px, py) {
    if (!this.mesh || !this.last) return null;
    const w = this.el.clientWidth || 600, h = this.el.clientHeight || 380;
    this.raycaster.setFromCamera(new THREE.Vector2((px / w) * 2 - 1, -(py / h) * 2 + 1), this.camera);
    const hit = this.raycaster.intersectObject(this.mesh, false)[0];
    if (!hit) { this.probe = null; this._drawMarker(); if (this.onProbe) this.onProbe(null); return null; }
    const cp = this.last.surface.cp, pos = this.last.surface.positions;
    let idx = null;
    if (cp && hit.face) {
      // the corner of the hit triangle nearest the hit point carries the value
      let bd = Infinity;
      for (const k of [hit.face.a, hit.face.b, hit.face.c]) {
        const d = Math.hypot(pos[3 * k] - hit.point.x, pos[3 * k + 1] - hit.point.y, pos[3 * k + 2] - hit.point.z);
        if (d < bd) { bd = d; idx = k; }
      }
    }
    this.probe = { x: hit.point.x, y: hit.point.y, z: hit.point.z, cp: idx === null ? null : cp[idx] };
    this._drawMarker();
    if (this.onProbe) this.onProbe(this.probe);
    return this.probe;
  }

  _drawMarker() {
    if (this.marker) { this.group.remove(this.marker); this.marker.geometry.dispose(); this.marker = null; }
    if (this.probe) {
      const g = new THREE.SphereGeometry(this.radius * 0.025, 12, 8);
      this.marker = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: new THREE.Color(cssColor("--focus", "#e8590c")) }));
      this.marker.position.set(this.probe.x, this.probe.y, this.probe.z);
      this.group.add(this.marker);
    }
    this.render();
  }

  _showHint() {
    this.hint.hidden = false;
    clearTimeout(this._hintTimer);
    this._hintTimer = setTimeout(() => { this.hint.hidden = true; }, 1600);
  }

  _controls() {
    const c = this.renderer.domElement;
    const down = new Map();  // pointerId -> [x, y] of every pointer on the view
    let last = null, pinch = null;
    const spread = () => { const [a, b] = [...down.values()]; return Math.hypot(a[0] - b[0], a[1] - b[1]); };
    c.addEventListener("pointerdown", (e) => {
      down.set(e.pointerId, [e.clientX, e.clientY]);
      c.setPointerCapture(e.pointerId);
      if (down.size === 2) { pinch = spread(); last = null; } else if (down.size === 1) last = [e.clientX, e.clientY];
    });
    c.addEventListener("pointermove", (e) => {
      if (!down.has(e.pointerId)) return;
      down.set(e.pointerId, [e.clientX, e.clientY]);
      if (down.size === 2 && pinch) {  // two fingers: pinch to zoom
        const d = spread();
        if (d > 0) { this._zoom(pinch / d); pinch = d; }
        return;
      }
      if (!last) return;
      this._turn(-(e.clientX - last[0]) * 0.008, (e.clientY - last[1]) * 0.008);
      last = [e.clientX, e.clientY];
    });
    let pressedAt = null;
    c.addEventListener("pointerdown", (e) => { pressedAt = [e.clientX, e.clientY]; });
    const up = (e) => {
      down.delete(e.pointerId);
      pinch = null;
      last = down.size === 1 ? [...down.values()][0] : null;
      // a click without a drag probes the surface
      if (pressedAt && e.type === "pointerup" && Math.hypot(e.clientX - pressedAt[0], e.clientY - pressedAt[1]) < 6) {
        const r = c.getBoundingClientRect();
        this.probeAtPixel(e.clientX - r.left, e.clientY - r.top);
      }
      pressedAt = null;
    };
    c.addEventListener("pointerup", up);
    c.addEventListener("pointercancel", up);
    // the wheel scrolls the page; it zooms only with Ctrl / Cmd (or a pinch) or once the view has focus
    c.addEventListener("wheel", (e) => {
      if (!wheelZooms(e, this._focused())) { this._showHint(); return; }
      e.preventDefault();
      this._zoom(e.deltaY > 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });
    this.el.addEventListener("focus", () => { this.hint.hidden = true; this.el.classList.add("active"); });
    this.el.addEventListener("blur", () => { this.el.classList.remove("active"); });
    this.el.addEventListener("keydown", (e) => {
      const k = e.key, step = 0.12;
      if (k === "Escape") { this.el.blur(); return; }
      if (k === "ArrowLeft") this._turn(step, 0);
      else if (k === "ArrowRight") this._turn(-step, 0);
      else if (k === "ArrowUp") this._turn(0, step);
      else if (k === "ArrowDown") this._turn(0, -step);
      else if (k === "+" || k === "=") this._zoom(1 / 1.15);
      else if (k === "-" || k === "_") this._zoom(1.15);
      else if (k === "0") this.reset();
      else if (k === "Enter" || k === "p" || k === "P") {
        this.probeAtPixel((this.el.clientWidth || 600) / 2, (this.el.clientHeight || 380) / 2);
      } else return;
      e.preventDefault();
    });
  }
}
