// Instant airflow preview (PRD Tier 0): a 2D lattice-Boltzmann simulation (D2Q9, BGK collision
// with a Smagorinsky eddy viscosity) of the mid-span outline of the part, running entirely in the
// browser. WebGL2 fragment shaders with ping-pong float textures when float render targets are
// available; otherwise a plain-JavaScript solver on a coarser grid. Nothing is sent to the server.
//
// Physics and limits: docs/PREVIEW.md. In short: the lattice Reynolds number is a few thousand
// (the full simulation runs at hundreds of thousands), the cut is 2D (no wing tips), and the
// forces come from momentum exchange with a staircase body - a sketch of the flow, not numbers.
//
// Lattice units: cell size 1, time step 1, density ~ 1. x runs with the wind (left to right),
// y up (WindLab's z). Boundaries: left column = freestream inflow, right column = outflow (zero
// gradient), top and bottom rows = freestream, the part = halfway bounce-back.
import { DIVERGING, VIRIDIS, hexRgb, mapColour } from "./plot.js";
import { bounds, rasterise, rotateLoops } from "./section.js";
import { nf, nfInt, t } from "./i18n.js";

export const GPU_GRID = [400, 160];
export const CPU_GRID = [200, 80];
export const U_IN = 0.08;            // inflow speed in lattice units (Mach ~ 0.14, incompressible enough)
export const TAU0 = 0.51;            // relaxation time: molecular viscosity nu0 = (tau0 - 0.5) / 3
export const CS = 0.1;               // Smagorinsky constant (PRD: C_s ~ 0.1)
export const NU0 = (TAU0 - 0.5) / 3;
const FILL_X = 0.35;                 // the part's box may fill this fraction of the domain's width ...
const FILL_Z = 0.28;                 // ... and this fraction of its height (blockage; the real tunnel has none)
const BODY_X = 0.3;                  // its pivot (quarter of its length) sits at this fraction of the width
const ANGLE_RANGE = [-20, -10, 0, 10, 20];  // the scale is fixed so every angle fits
const SAMPLE_MS = 500;               // how often the forces are read back and averaged
const SAMPLES_KEPT = 8;              // running mean over the last 4 s
const SETTLE_STEPS = 4000;           // about one flow-through; before that the numbers are "settling"

// D2Q9 directions, weights and opposites
const EX = [0, 1, 0, -1, 0, 1, -1, -1, 1];
const EY = [0, 0, 1, 0, -1, 1, 1, -1, -1];
const WT = [4 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 9, 1 / 36, 1 / 36, 1 / 36, 1 / 36];
const OPP = [0, 3, 4, 1, 2, 7, 8, 5, 6];

export function equilibrium(rho, ux, uy, out, off = 0) {
  const usq = 1.5 * (ux * ux + uy * uy);
  for (let i = 0; i < 9; i++) {
    const cu = 3 * (EX[i] * ux + EY[i] * uy);
    out[off + i] = WT[i] * rho * (1 + cu + 0.5 * cu * cu - usq);
  }
  return out;
}

// Where the part goes on an nx x ny lattice and how big it is: cells per metre, the origin
// (metres at cell 0,0), the pivot the angle turns about, and the reference length in cells.
export function placement(loops, referenceLength, nx, ny) {
  const [x0, x1, z0, z1] = bounds(loops);
  const pivot = [x0 + 0.25 * (x1 - x0), 0.5 * (z0 + z1)];
  let ex = 0, ez = 0;
  for (const a of ANGLE_RANGE) {
    const b = bounds(rotateLoops(loops, a, pivot));
    ex = Math.max(ex, b[1] - b[0]); ez = Math.max(ez, b[3] - b[2]);
  }
  const scale = Math.min((FILL_X * nx) / Math.max(ex, 1e-9), (FILL_Z * ny) / Math.max(ez, 1e-9));
  return { scale, pivot, originX: pivot[0] - (BODY_X * nx) / scale, originZ: pivot[1] - (0.5 * ny) / scale,
    lengthCells: referenceLength * scale };
}

// ---------------------------------------------------------------- CPU solver (reference logic)

export class CpuSolver {
  constructor(nx, ny) {
    this.nx = nx; this.ny = ny;
    const n = nx * ny;
    this.f = new Float32Array(9 * n);
    this.g = new Float32Array(9 * n);
    this.rho = new Float32Array(n);
    this.ux = new Float32Array(n);
    this.uy = new Float32Array(n);
    this.mask = new Uint8Array(n);
    this.fx = 0; this.fy = 0;  // force on the part over the last step
    this.reset();
  }

  reset() {
    const eq = equilibrium(1, U_IN, 0, new Float32Array(9));
    for (let c = 0; c < this.nx * this.ny; c++) { this.f.set(eq, 9 * c); this.rho[c] = 1; this.ux[c] = U_IN; this.uy[c] = 0; }
  }

  setMask(mask) { this.mask = mask; }

  step() {
    const { nx, ny, f, g, mask } = this;
    const eqIn = equilibrium(1, U_IN, 0, new Float32Array(9));
    const eq0 = equilibrium(1, 0, 0, new Float32Array(9));
    const fin = new Float32Array(9), feq = new Float32Array(9);
    const cs2 = 18 * CS * CS;
    let Fx = 0, Fy = 0;
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const c = j * nx + i, o = 9 * c;
        if (mask[c]) { g.set(eq0, o); this.rho[c] = 1; this.ux[c] = 0; this.uy[c] = 0; continue; }
        if (i === 0 || j === 0 || j === ny - 1) { g.set(eqIn, o); this.rho[c] = 1; this.ux[c] = U_IN; this.uy[c] = 0; continue; }
        if (i === nx - 1) {  // outflow: copy the neighbour (zero gradient)
          for (let k = 0; k < 9; k++) g[o + k] = f[o - 9 + k];
          this.rho[c] = this.rho[c - 1]; this.ux[c] = this.ux[c - 1]; this.uy[c] = this.uy[c - 1];
          continue;
        }
        // pull: f_k arrives from the cell behind, or bounces back off the part
        let rho = 0, mx = 0, my = 0;
        for (let k = 0; k < 9; k++) {
          const src = (j - EY[k]) * nx + (i - EX[k]);
          const v = mask[src] ? f[o + OPP[k]] : f[9 * src + k];
          fin[k] = v; rho += v; mx += EX[k] * v; my += EY[k] * v;
        }
        const ux = mx / rho, uy = my / rho;
        equilibrium(rho, ux, uy, feq);
        // Smagorinsky: eddy viscosity from the non-equilibrium momentum flux
        let pxx = 0, pyy = 0, pxy = 0;
        for (let k = 1; k < 9; k++) {
          const n = fin[k] - feq[k];
          pxx += EX[k] * EX[k] * n; pyy += EY[k] * EY[k] * n; pxy += EX[k] * EY[k] * n;
        }
        const Q = Math.sqrt(2 * (pxx * pxx + pyy * pyy + 2 * pxy * pxy));
        const om = 1 / (0.5 * (TAU0 + Math.sqrt(TAU0 * TAU0 + (cs2 * Q) / rho)));
        for (let k = 0; k < 9; k++) g[o + k] = fin[k] - om * (fin[k] - feq[k]);
        this.rho[c] = rho; this.ux[c] = ux; this.uy[c] = uy;
        // momentum exchange with the part: links from this cell into solid neighbours
        for (let k = 1; k < 9; k++) {
          if (mask[(j + EY[k]) * nx + (i + EX[k])]) { Fx += 2 * EX[k] * g[o + k]; Fy += 2 * EY[k] * g[o + k]; }
        }
      }
    }
    this.f = g; this.g = f;
    this.fx = Fx; this.fy = Fy;
  }

  forces() {
    let lo = Infinity, hi = -Infinity;
    for (let c = 0; c < this.rho.length; c++) {
      if (this.mask[c]) continue;
      const r = this.rho[c];
      if (r < lo) lo = r; if (r > hi) hi = r;
    }
    return { fx: this.fx, fy: this.fy, rhoMin: lo, rhoMax: hi, ok: isFinite(this.fx) && isFinite(this.fy) && isFinite(lo + hi) };
  }

  // Is any of the 8 neighbours of fluid cell (i, j) solid? (the one-cell outline around the part)
  nextToSolid(i, j) {
    const { nx, ny, mask } = this;
    for (let dj = -1; dj <= 1; dj++) {
      const jj = Math.min(Math.max(j + dj, 0), ny - 1);
      for (let di = -1; di <= 1; di++) {
        if (mask[jj * nx + Math.min(Math.max(i + di, 0), nx - 1)]) return true;
      }
    }
    return false;
  }

  // Paint the field into a 2D canvas (row 0 of the image is the top of the lattice): the part in
  // its colour with a one-cell outline in the edge colour, the air by the colour map.
  render(canvas, field, body, edge = body) {
    const { nx, ny } = this;
    const ctx = canvas.getContext("2d");
    this.image ??= ctx.createImageData(nx, ny);
    const d = this.image.data, stops = field === "speed" ? VIRIDIS : DIVERGING;
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const c = j * nx + i, p = 4 * ((ny - 1 - j) * nx + i);
        let rgb;
        if (this.mask[c]) rgb = body;
        else if (this.nextToSolid(i, j)) rgb = edge;
        else {
          let t;
          if (field === "speed") t = Math.hypot(this.ux[c], this.uy[c]) / (1.6 * U_IN);
          else {
            const l = Math.max(i - 1, 0), r = Math.min(i + 1, nx - 1), dn = Math.max(j - 1, 0), up = Math.min(j + 1, ny - 1);
            const w = 0.5 * (this.uy[j * nx + r] - this.uy[j * nx + l]) - 0.5 * (this.ux[up * nx + i] - this.ux[dn * nx + i]);
            t = 0.5 + w / (0.3 * U_IN);
          }
          rgb = mapColour(t, stops);
        }
        d[p] = 255 * rgb[0]; d[p + 1] = 255 * rgb[1]; d[p + 2] = 255 * rgb[2]; d[p + 3] = 255;
      }
    }
    ctx.putImageData(this.image, 0, 0);
  }
}

// ---------------------------------------------------------------- WebGL2 solver

const VERT = `#version 300 es
const vec2 P[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
void main() { gl_Position = vec4(P[gl_VertexID], 0.0, 1.0); }
`;

// One lattice step per cell: stream (pull), bounce back, collide; also the force on the part.
// Textures: tA = f0..f3, tB = f4..f7, tC = (f8, rho, ux, uy), tM = part mask (r > 0.5 = solid).
const STEP = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
uniform sampler2D tA;
uniform sampler2D tB;
uniform sampler2D tC;
uniform sampler2D tM;
uniform ivec2 size;
uniform float tau0;
uniform float cs2;
uniform float uIn;
layout(location = 0) out vec4 oA;
layout(location = 1) out vec4 oB;
layout(location = 2) out vec4 oC;
layout(location = 3) out vec4 oF;
const float W0 = 4.0 / 9.0;
const float W1 = 1.0 / 9.0;
const float W2 = 1.0 / 36.0;
bool solid(ivec2 p) { return texelFetch(tM, p, 0).r > 0.5; }
void eq(float rho, vec2 u, out vec4 a, out vec4 b, out float f8) {
  float usq = 1.5 * dot(u, u);
  float cu;
  a.x = W0 * rho * (1.0 - usq);
  cu = 3.0 * u.x;           a.y = W1 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
  cu = 3.0 * u.y;           a.z = W1 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
  cu = -3.0 * u.x;          a.w = W1 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
  cu = -3.0 * u.y;          b.x = W1 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
  cu = 3.0 * (u.x + u.y);   b.y = W2 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
  cu = 3.0 * (-u.x + u.y);  b.z = W2 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
  cu = 3.0 * (-u.x - u.y);  b.w = W2 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
  cu = 3.0 * (u.x - u.y);   f8 = W2 * rho * (1.0 + cu + 0.5 * cu * cu - usq);
}
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  oF = vec4(0.0);
  vec4 ea, eb;
  float e8;
  if (solid(p)) {
    eq(1.0, vec2(0.0), ea, eb, e8);
    oA = ea; oB = eb; oC = vec4(e8, 1.0, 0.0, 0.0);
    return;
  }
  if (p.x == 0 || p.y == 0 || p.y == size.y - 1) {
    eq(1.0, vec2(uIn, 0.0), ea, eb, e8);
    oA = ea; oB = eb; oC = vec4(e8, 1.0, uIn, 0.0);
    return;
  }
  if (p.x == size.x - 1) {
    ivec2 q = p - ivec2(1, 0);
    oA = texelFetch(tA, q, 0); oB = texelFetch(tB, q, 0); oC = texelFetch(tC, q, 0);
    return;
  }
  vec4 ca = texelFetch(tA, p, 0);
  vec4 cb = texelFetch(tB, p, 0);
  float c8 = texelFetch(tC, p, 0).x;
  float f0 = ca.x;
  ivec2 q;
  q = p - ivec2(1, 0);  float f1 = solid(q) ? ca.w : texelFetch(tA, q, 0).y;
  q = p - ivec2(0, 1);  float f2 = solid(q) ? cb.x : texelFetch(tA, q, 0).z;
  q = p + ivec2(1, 0);  float f3 = solid(q) ? ca.y : texelFetch(tA, q, 0).w;
  q = p + ivec2(0, 1);  float f4 = solid(q) ? ca.z : texelFetch(tB, q, 0).x;
  q = p - ivec2(1, 1);  float f5 = solid(q) ? cb.w : texelFetch(tB, q, 0).y;
  q = p + ivec2(1, -1); float f6 = solid(q) ? c8 : texelFetch(tB, q, 0).z;
  q = p + ivec2(1, 1);  float f7 = solid(q) ? cb.y : texelFetch(tB, q, 0).w;
  q = p + ivec2(-1, 1); float f8 = solid(q) ? cb.z : texelFetch(tC, q, 0).x;
  float rho = f0 + f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8;
  vec2 u = vec2(f1 - f3 + f5 - f6 - f7 + f8, f2 - f4 + f5 + f6 - f7 - f8) / rho;
  eq(rho, u, ea, eb, e8);
  float n1 = f1 - ea.y, n2 = f2 - ea.z, n3 = f3 - ea.w, n4 = f4 - eb.x;
  float n5 = f5 - eb.y, n6 = f6 - eb.z, n7 = f7 - eb.w, n8 = f8 - e8;
  float pxx = n1 + n3 + n5 + n6 + n7 + n8;
  float pyy = n2 + n4 + n5 + n6 + n7 + n8;
  float pxy = n5 - n6 + n7 - n8;
  float Q = sqrt(2.0 * (pxx * pxx + pyy * pyy + 2.0 * pxy * pxy));
  float om = 1.0 / (0.5 * (tau0 + sqrt(tau0 * tau0 + cs2 * Q / rho)));
  f0 -= om * (f0 - ea.x);
  f1 -= om * n1; f2 -= om * n2; f3 -= om * n3; f4 -= om * n4;
  f5 -= om * n5; f6 -= om * n6; f7 -= om * n7; f8 -= om * n8;
  oA = vec4(f0, f1, f2, f3);
  oB = vec4(f4, f5, f6, f7);
  oC = vec4(f8, rho, u.x, u.y);
  vec2 F = vec2(0.0);
  if (solid(p + ivec2(1, 0)))   F += 2.0 * f1 * vec2(1.0, 0.0);
  if (solid(p + ivec2(0, 1)))   F += 2.0 * f2 * vec2(0.0, 1.0);
  if (solid(p + ivec2(-1, 0)))  F += 2.0 * f3 * vec2(-1.0, 0.0);
  if (solid(p + ivec2(0, -1)))  F += 2.0 * f4 * vec2(0.0, -1.0);
  if (solid(p + ivec2(1, 1)))   F += 2.0 * f5 * vec2(1.0, 1.0);
  if (solid(p + ivec2(-1, 1)))  F += 2.0 * f6 * vec2(-1.0, 1.0);
  if (solid(p + ivec2(-1, -1))) F += 2.0 * f7 * vec2(-1.0, -1.0);
  if (solid(p + ivec2(1, -1)))  F += 2.0 * f8 * vec2(1.0, -1.0);
  oF = vec4(F, rho, length(u));
}
`;

// Colour the field: air speed (viridis) or vorticity (diverging, centred on zero); the part in its
// colour (body) with a one-cell outline (edge) on the fluid cells that touch it.
const DISPLAY = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
uniform sampler2D tC;
uniform sampler2D tM;
uniform ivec2 size;
uniform float uIn;
uniform int field;
uniform vec3 stops[9];
uniform vec3 body;
uniform vec3 edge;
out vec4 colour;
bool solid(ivec2 p) { return texelFetch(tM, clamp(p, ivec2(0), size - 1), 0).r > 0.5; }
vec3 cmap(float t) {
  float x = clamp(t, 0.0, 1.0) * 8.0;
  int i = int(floor(min(x, 7.999)));
  return mix(stops[i], stops[i + 1], x - float(i));
}
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  if (solid(p)) { colour = vec4(body, 1.0); return; }
  for (int dy = -1; dy <= 1; dy++) {
    for (int dx = -1; dx <= 1; dx++) {
      if (solid(p + ivec2(dx, dy))) { colour = vec4(edge, 1.0); return; }
    }
  }
  vec4 c = texelFetch(tC, p, 0);
  float t;
  if (field == 0) {
    t = length(c.zw) / (1.6 * uIn);
  } else {
    ivec2 l = ivec2(max(p.x - 1, 0), p.y), r = ivec2(min(p.x + 1, size.x - 1), p.y);
    ivec2 d = ivec2(p.x, max(p.y - 1, 0)), u = ivec2(p.x, min(p.y + 1, size.y - 1));
    float w = 0.5 * (texelFetch(tC, r, 0).w - texelFetch(tC, l, 0).w) - 0.5 * (texelFetch(tC, u, 0).z - texelFetch(tC, d, 0).z);
    t = 0.5 + w / (0.3 * uIn);
  }
  colour = vec4(cmap(t), 1.0);
}
`;

function compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh);
    gl.deleteShader(sh);
    throw new Error(`shader: ${log}`);
  }
  return sh;
}

function program(gl, frag) {
  const pr = gl.createProgram();
  gl.attachShader(pr, compile(gl, gl.VERTEX_SHADER, VERT));
  gl.attachShader(pr, compile(gl, gl.FRAGMENT_SHADER, frag));
  gl.linkProgram(pr);
  if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) throw new Error(`program: ${gl.getProgramInfoLog(pr)}`);
  return pr;
}

export class GpuSolver {
  constructor(canvas, nx, ny) {
    this.nx = nx; this.ny = ny;
    const gl = canvas.getContext("webgl2", { antialias: false, alpha: false, depth: false, stencil: false,
      preserveDrawingBuffer: false, powerPreference: "low-power" });
    if (!gl) throw new Error("no WebGL2");
    if (!gl.getExtension("EXT_color_buffer_float")) throw new Error("no float render targets");
    if (gl.getParameter(gl.MAX_DRAW_BUFFERS) < 4 || gl.getParameter(gl.MAX_COLOR_ATTACHMENTS) < 4) {
      throw new Error("too few draw buffers");
    }
    this.gl = gl;
    this.stepProg = program(gl, STEP);
    this.showProg = program(gl, DISPLAY);
    this.mask = this._texture(gl.R8, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array(nx * ny));
    this.sets = [this._set(), this._set()];
    this.src = 0;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.sets[0].fbo);  // rendering to float textures must really work
    const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    if (status !== gl.FRAMEBUFFER_COMPLETE) throw new Error("float framebuffer incomplete");
    this.loc = new Map();  // uniform locations, looked up once
    this.readBuf = new Float32Array(4 * nx * ny);
    this.reset();
  }

  _u(prog, name) {
    const key = prog === this.stepProg ? `s:${name}` : `d:${name}`;
    if (!this.loc.has(key)) this.loc.set(key, this.gl.getUniformLocation(prog, name));
    return this.loc.get(key);
  }

  _texture(internal, format, type, data) {
    const gl = this.gl, t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, this.nx, this.ny, 0, format, type, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return t;
  }

  _set() {
    const gl = this.gl, n = this.nx * this.ny;
    const tex = [0, 1, 2, 3].map(() => this._texture(gl.RGBA32F, gl.RGBA, gl.FLOAT, new Float32Array(4 * n)));
    const fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    tex.forEach((t, k) => gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0 + k, gl.TEXTURE_2D, t, 0));
    gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1, gl.COLOR_ATTACHMENT2, gl.COLOR_ATTACHMENT3]);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    return { tex, fbo };
  }

  reset() {
    const gl = this.gl, n = this.nx * this.ny;
    const eq = equilibrium(1, U_IN, 0, new Float32Array(9));
    const A = new Float32Array(4 * n), B = new Float32Array(4 * n), C = new Float32Array(4 * n);
    for (let c = 0; c < n; c++) {
      A.set(eq.subarray(0, 4), 4 * c); B.set(eq.subarray(4, 8), 4 * c);
      C[4 * c] = eq[8]; C[4 * c + 1] = 1; C[4 * c + 2] = U_IN; C[4 * c + 3] = 0;
    }
    for (const s of this.sets) {
      [A, B, C].forEach((data, k) => {
        gl.bindTexture(gl.TEXTURE_2D, s.tex[k]);
        gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, this.nx, this.ny, gl.RGBA, gl.FLOAT, data);
      });
    }
  }

  setMask(mask) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.mask);
    // R8 is a normalised format: store solid cells as 255 so the shader reads 1.0 (a stored 1 would read
    // 1/255 and fail the "> 0.5" solid test, leaving the part out of the GPU simulation).
    const m8 = new Uint8Array(mask.length);
    for (let c = 0; c < mask.length; c++) m8[c] = mask[c] ? 255 : 0;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, this.nx, this.ny, gl.RED, gl.UNSIGNED_BYTE, m8);
  }

  _bind(prog, textures) {
    const gl = this.gl;
    gl.useProgram(prog);
    textures.forEach(([name, tex], unit) => {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniform1i(this._u(prog, name), unit);
    });
  }

  step(n = 1) {
    const gl = this.gl, pr = this.stepProg;
    gl.viewport(0, 0, this.nx, this.ny);
    for (let k = 0; k < n; k++) {
      const s = this.sets[this.src], d = this.sets[1 - this.src];
      gl.bindFramebuffer(gl.FRAMEBUFFER, d.fbo);
      this._bind(pr, [["tA", s.tex[0]], ["tB", s.tex[1]], ["tC", s.tex[2]], ["tM", this.mask]]);
      gl.uniform2i(this._u(pr, "size"), this.nx, this.ny);
      gl.uniform1f(this._u(pr, "tau0"), TAU0);
      gl.uniform1f(this._u(pr, "cs2"), 18 * CS * CS);
      gl.uniform1f(this._u(pr, "uIn"), U_IN);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      this.src = 1 - this.src;
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  render(canvas, field, body, edge = body) {
    const gl = this.gl, s = this.sets[this.src], pr = this.showProg;
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, canvas.width, canvas.height);
    this._bind(pr, [["tC", s.tex[2]], ["tM", this.mask]]);
    const stops = field === "speed" ? VIRIDIS : DIVERGING;
    const flat = new Float32Array(27);
    stops.forEach((_, k) => flat.set(mapColour(k / 8, stops), 3 * k));
    gl.uniform2i(this._u(pr, "size"), this.nx, this.ny);
    gl.uniform1f(this._u(pr, "uIn"), U_IN);
    gl.uniform1i(this._u(pr, "field"), field === "speed" ? 0 : 1);
    gl.uniform3fv(this._u(pr, "stops"), flat);
    gl.uniform3f(this._u(pr, "body"), body[0], body[1], body[2]);
    gl.uniform3f(this._u(pr, "edge"), edge[0], edge[1], edge[2]);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  // Read the force texture back and sum it (done a couple of times a second, not every frame).
  forces() {
    const gl = this.gl, s = this.sets[this.src], buf = this.readBuf;
    gl.bindFramebuffer(gl.FRAMEBUFFER, s.fbo);
    gl.readBuffer(gl.COLOR_ATTACHMENT3);
    gl.readPixels(0, 0, this.nx, this.ny, gl.RGBA, gl.FLOAT, buf);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    let fx = 0, fy = 0, lo = Infinity, hi = -Infinity;
    for (let c = 0; c < this.nx * this.ny; c++) {
      fx += buf[4 * c]; fy += buf[4 * c + 1];
      const r = buf[4 * c + 2];
      if (r > 0) { if (r < lo) lo = r; if (r > hi) hi = r; }  // solid cells carry rho = 1, borders too
    }
    return { fx, fy, rhoMin: lo, rhoMax: hi, ok: isFinite(fx) && isFinite(fy) && isFinite(lo + hi) };
  }
}

// ---------------------------------------------------------------- the page's preview

const $ = (id) => document.getElementById(id);
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");

// A CSS colour variable ("#rrggbb") as [r, g, b] in 0..1, or the fallback when it is not a plain hex colour.
function cssRgb(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return /^#[0-9a-f]{6}$/i.test(v) ? hexRgb(v) : fallback;
}
const BODY_FALLBACK = [0.24, 0.24, 0.23];  // --body-fill in the light theme
const EDGE_FALLBACK = [0, 0, 0];           // --body-edge in the light theme

const fmtInt = (x) => nfInt(Math.round(x));

// Drives the preview figure on the page: #preview-view (the picture), the Play / Advance / Start
// again buttons, the colour-by buttons, #preview-readout (the numbers), #preview-re, #preview-note.
export class Preview {
  constructor() {
    this.view = $("preview-view");
    this.shape = null; this.alpha = 0; this.caseRe = null;
    this.field = "speed";
    this.running = false;
    this.stepsPerFrame = 6;
    this.steps = 0;
    this.samples = [];
    this.lastSample = 0;
    this.lastFrame = 0;
    this.body = cssRgb("--body-fill", BODY_FALLBACK);
    this.edge = cssRgb("--body-edge", EDGE_FALLBACK);
    $("preview-play").addEventListener("click", () => (this.running ? this.pause() : this.play()));
    $("preview-advance").addEventListener("click", () => this.advance(200));
    $("preview-reset").addEventListener("click", () => this.reset(t("preview.note.restart")));
    document.querySelectorAll("[data-preview-field]").forEach((b) => b.addEventListener("click", () => {
      this.field = b.dataset.previewField;
      document.querySelectorAll("[data-preview-field]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      this._describe();
      this._draw();
    }));
    REDUCED_MOTION.addEventListener?.("change", () => { if (REDUCED_MOTION.matches) this.pause(); });
    document.addEventListener("visibilitychange", () => { if (document.hidden) this._stopLoop(); else if (this.running) this._loop(); });
  }

  // Build (or rebuild) the solver: WebGL2 if float render targets work, else the CPU version.
  _ensureSolver() {
    if (this.solver) return;
    this.view.replaceChildren();
    let canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    try {
      [canvas.width, canvas.height] = GPU_GRID;
      this.solver = new GpuSolver(canvas, ...GPU_GRID);
      this.mode = "gpu";
      canvas.addEventListener("webglcontextlost", (e) => {
        e.preventDefault();
        this._stopLoop(); this.running = false; this.solver = null;
        this._note(t("preview.note.contextLost"));
        $("preview-play").textContent = t("preview.play");
      });
    } catch (err) {
      console.warn("preview: using the CPU solver:", err.message);
      canvas = document.createElement("canvas");
      canvas.setAttribute("aria-hidden", "true");
      [canvas.width, canvas.height] = CPU_GRID;
      this.solver = new CpuSolver(...CPU_GRID);
      this.mode = "cpu";
      this.stepsPerFrame = 2;
    }
    this.canvas = canvas;
    this.view.appendChild(canvas);
    if (this.shape) this._place();
  }

  // shape = { loops: [[[x, z], ...]], referenceLength (m), name }
  setShape(shape) {
    this.shape = shape;
    this._ensureSolver();
    this._place();
    this.solver.reset();
    this.steps = 0; this.samples = [];
    this.setAngle(this.alpha);
    this._describe();
    if (!this.running && !REDUCED_MOTION.matches) this.play();
    else if (REDUCED_MOTION.matches && !this.running) {
      this._note(t("preview.note.reducedMotion"));
      this._draw();
    }
  }

  _place() {
    const { nx, ny } = this.solver;
    this.pl = placement(this.shape.loops, this.shape.referenceLength, nx, ny);
    this.previewRe = (U_IN * this.pl.lengthCells) / NU0;
    this._reText();
  }

  setAngle(alphaDeg) {
    this.alpha = isFinite(alphaDeg) ? alphaDeg : 0;
    if (!this.shape || !this.solver) return;
    const { nx, ny } = this.solver, pl = this.pl;
    const loops = rotateLoops(this.shape.loops, this.alpha, pl.pivot);
    this.solver.setMask(rasterise(loops, nx, ny, pl.originX, pl.originZ, pl.scale));
    this.samples = [];  // the averaged forces belong to the old angle
    this._describe();
    if (!this.running) this._draw();
  }

  // Reynolds number of the student's own case, for the "Preview Re - your case" line.
  setCase(reynolds) { this.caseRe = reynolds; this._reText(); }

  _reText() {
    if (!this.previewRe) return;
    const own = this.caseRe ? t("preview.re.yourCase", { re: fmtInt(this.caseRe) }) : t("preview.re.notSet");
    $("preview-re").textContent = t("preview.re", { re: fmtInt(this.previewRe), own });
  }

  applyTheme() {
    this.body = cssRgb("--body-fill", BODY_FALLBACK);
    this.edge = cssRgb("--body-edge", EDGE_FALLBACK);
    if (this.solver) this._draw();
  }

  play() {
    if (!this.shape) return;
    this._ensureSolver();
    this.running = true;
    $("preview-play").textContent = t("preview.pause");
    this._note("");
    this._loop();
  }

  pause() {
    this.running = false;
    this._stopLoop();
    $("preview-play").textContent = t("preview.play");
  }

  reset(note) {
    if (!this.solver) return;
    this.solver.reset();
    this.steps = 0; this.samples = [];
    this._note(note || "");
    this._readout();
    if (!this.running) this._draw();
  }

  // Move the flow on without animating (for people who turned animation off, and for a quick look).
  advance(n) {
    if (!this.shape) return;
    this._ensureSolver();
    const chunk = this.mode === "gpu" ? 50 : 5;
    let done = 0;
    const go = () => {
      const k = Math.min(chunk, n - done);
      this.solver.step(k); done += k; this.steps += k;
      if (done < n) { setTimeout(go, 0); return; }  // small chunks: the page stays responsive
      this._sample(); this._draw();
    };
    go();
  }

  _stopLoop() { if (this.raf) cancelAnimationFrame(this.raf); this.raf = null; }

  _loop() {
    this._stopLoop();
    const frame = (t) => {
      if (!this.running || !this.solver) return;
      if (this.view.closest("[hidden]")) { this.raf = requestAnimationFrame(frame); return; }  // figure closed
      const dt = this.lastFrame ? t - this.lastFrame : 16;
      this.lastFrame = t;
      if (this.mode === "gpu") {
        // keep the frame rate up: more steps when there is time to spare, fewer when the frame lags
        if (dt > 26) this.stepsPerFrame = Math.max(1, this.stepsPerFrame - 1);
        else if (dt < 15) this.stepsPerFrame = Math.min(24, this.stepsPerFrame + 1);
        this.solver.step(this.stepsPerFrame); this.steps += this.stepsPerFrame;
      } else {
        const t0 = performance.now();  // the CPU solver gets at most ~10 ms per frame
        let n = 0;
        do { this.solver.step(); n++; } while (n < 8 && performance.now() - t0 < 10);
        this.steps += n;
      }
      if (t - this.lastSample > SAMPLE_MS) { this.lastSample = t; this._sample(); }
      this._draw();
      this.raf = requestAnimationFrame(frame);
    };
    this.raf = requestAnimationFrame(frame);
  }

  _draw() { if (this.solver && this.canvas) this.solver.render(this.canvas, this.field, this.body, this.edge); }

  // Read the force on the part, keep a running mean, and restart if the flow has blown up.
  _sample() {
    const f = this.solver.forces();
    if (!f.ok || f.rhoMin < 0.3 || f.rhoMax > 3) {
      this.reset(t("preview.note.unstable"));
      return;
    }
    const q = 0.5 * U_IN * U_IN * this.pl.lengthCells;  // dynamic pressure x reference length (per unit span)
    this.samples.push([f.fy / q, f.fx / q]);
    if (this.samples.length > SAMPLES_KEPT) this.samples.shift();
    this._readout();
  }

  _readout() {
    const n = this.samples.length;
    if (!n) { $("preview-readout").textContent = t("preview.readout.waiting", { steps: this.steps }); return; }
    const cl = this.samples.reduce((s, v) => s + v[0], 0) / n, cd = this.samples.reduce((s, v) => s + v[1], 0) / n;
    $("preview-readout").textContent = t("preview.readout", { settling: this.steps < SETTLE_STEPS ? t("preview.readout.settling") : "",
      cl: nf(cl, 2), cd: nf(cd, 3), ld: nf(cd > 1e-6 ? cl / cd : 0, 1), steps: nfInt(this.steps),
      mode: t(this.mode === "gpu" ? "preview.mode.gpu" : "preview.mode.cpu") });
  }

  _describe() {
    if (!this.shape) return;
    const name = this.shape.name || t("preview.thePart");
    $("preview-text").textContent = t("preview.text", { what: t(this.field === "speed" ? "preview.text.speed" : "preview.text.swirl"),
      name, alpha: this.alpha });
    this.view.setAttribute("aria-label", t("preview.viewLabel", { name, alpha: this.alpha,
      field: t(this.field === "speed" ? "preview.field.speed" : "preview.field.swirl") }));
  }

  _note(text) {
    const el = $("preview-note");
    el.textContent = text;
    el.hidden = !text;
  }
}
