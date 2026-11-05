// Mid-span outlines for the instant 2D preview (pure helpers, no DOM, no three.js).
//
// Outlines are closed polygons in the (x, z) plane in metres: x along the wind, z up, as in
// the rest of WindLab. NACA wings come from the formula and the built-in bodies from their
// definitions (the same numbers as windlab/geometry/naca.py and shapes.py); uploads get their
// outline from the server (/api/geometry/{id}/section), which cuts the STL file.

// Closed trailing-edge variant of the NACA thickness polynomial (naca.py: _THICKNESS_COEFFS).
const THICKNESS = [0.2969, -0.1260, -0.3516, 0.2843, -0.1036];
const PLATE_THICKNESS = 0.05;  // of the height (shapes.py: PLATE_THICKNESS)

function thickness(x, t) {
  const [a0, a1, a2, a3, a4] = THICKNESS;
  return 5 * t * (a0 * Math.sqrt(x) + a1 * x + a2 * x * x + a3 * x ** 3 + a4 * x ** 4);
}

// NACA 4-digit section for a chord in metres: [[x, z], ...] counter-clockwise, trailing edge ->
// upper surface -> leading edge -> lower surface (the leading and trailing edge appear once).
export function nacaOutline(code, chord, nPerSide = 80) {
  const s = String(code);
  if (!/^[0-9]{4}$/.test(s)) throw new Error("NACA code must be four digits");
  const m = Number(s[0]) / 100, p = Number(s[1]) / 10, t = Number(s.slice(2)) / 100;
  const upper = [], lower = [];
  for (let k = 0; k <= nPerSide; k++) {
    const beta = (Math.PI * k) / nPerSide;
    const x = 0.5 * (1 - Math.cos(beta));
    let yt = thickness(x, t);
    if (k === 0 || k === nPerSide) yt = 0;
    let yc = 0, dyc = 0;
    if (m > 0 && p > 0) {
      if (x < p) { yc = (m / (p * p)) * (2 * p * x - x * x); dyc = (2 * m / (p * p)) * (p - x); }
      else { yc = (m / ((1 - p) ** 2)) * ((1 - 2 * p) + 2 * p * x - x * x); dyc = (2 * m / ((1 - p) ** 2)) * (p - x); }
    }
    const th = Math.atan(dyc);
    upper.push([x - yt * Math.sin(th), yc + yt * Math.cos(th)]);
    lower.push([x + yt * Math.sin(th), yc - yt * Math.cos(th)]);
  }
  const pts = [...upper.reverse(), ...lower.slice(1, -1)];
  return pts.map(([x, z]) => [x * chord, z * chord]);
}

function circle(cx, cz, r, n = 120) {
  const out = [];
  for (let k = 0; k < n; k++) {
    const a = (2 * Math.PI * k) / n;
    out.push([cx + r * Math.cos(a), cz + r * Math.sin(a)]);
  }
  return out;
}

function rect(x0, x1, z0, z1) {
  return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]];
}

// Mid-span outline of a built-in body (nose at x = 0, centred on z = 0, as shapes.py makes them).
export function builtinOutline(shape, params = {}) {
  const d = Number(params.diameter);
  switch (shape) {
    case "sphere": return circle(d / 2, 0, d / 2);
    case "cylinder": return circle(d / 2, 0, d / 2);  // axis across the wind: the cut is a circle
    case "plate": {
      const h = Number(params.height);
      return rect(0, PLATE_THICKNESS * h, -h / 2, h / 2);
    }
    case "cube": {
      const s = Number(params.side);
      return rect(0, s, -s / 2, s / 2);
    }
    case "teardrop": {
      // the NACA 00xx thickness distribution revolved: the cut is that profile, scaled so the
      // greatest half-thickness is d / 2 and the length is fineness x d
      const L = Number(params.fineness) * d, n = 100;
      const xs = [], ys = [];
      let ymax = 0;
      for (let k = 0; k <= n; k++) {
        const xi = 0.5 * (1 - Math.cos((Math.PI * k) / n));
        const y = k === 0 || k === n ? 0 : thickness(xi, 0.2);
        xs.push(xi * L); ys.push(y); ymax = Math.max(ymax, y);
      }
      const upper = xs.map((x, k) => [x, (d / 2) * ys[k] / ymax]).reverse();
      const lower = xs.map((x, k) => [x, -(d / 2) * ys[k] / ymax]).slice(1, -1);
      return [...upper, ...lower];
    }
    default: throw new Error(`unknown built-in shape ${shape}`);
  }
}

// Bounding box of a list of loops: [xMin, xMax, zMin, zMax].
export function bounds(loops) {
  let x0 = Infinity, x1 = -Infinity, z0 = Infinity, z1 = -Infinity;
  for (const lp of loops) for (const [x, z] of lp) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (z < z0) z0 = z; if (z > z1) z1 = z;
  }
  return [x0, x1, z0, z1];
}

// Turn the loops by the angle of attack: a positive angle puts the nose up relative to the wind,
// which (with the wind kept horizontal) is a clockwise turn in the x-right, z-up picture.
export function rotateLoops(loops, alphaDeg, pivot) {
  const a = (-alphaDeg * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  const [px, pz] = pivot;
  return loops.map((lp) => lp.map(([x, z]) => [px + (x - px) * c - (z - pz) * s, pz + (x - px) * s + (z - pz) * c]));
}

// Rasterise the loops onto an nx x ny lattice: cell (i, j) is solid when its centre lies inside
// an odd number of loops (even-odd rule). toLattice maps metres -> cell coordinates:
// cellX = (x - originX) * scale, cellY = (z - originZ) * scale. Returns a Uint8Array (row-major, j * nx + i).
export function rasterise(loops, nx, ny, originX, originZ, scale) {
  const mask = new Uint8Array(nx * ny);
  const edges = [];
  for (const lp of loops) {
    for (let k = 0; k < lp.length; k++) {
      const [xa, za] = lp[k], [xb, zb] = lp[(k + 1) % lp.length];
      edges.push([(xa - originX) * scale, (za - originZ) * scale, (xb - originX) * scale, (zb - originZ) * scale]);
    }
  }
  for (let j = 0; j < ny; j++) {
    const y = j + 0.5;  // cell centre
    const xs = [];
    for (const [xa, ya, xb, yb] of edges) {
      if ((ya <= y) === (yb <= y)) continue;  // the edge does not straddle this row
      xs.push(xa + ((y - ya) / (yb - ya)) * (xb - xa));
    }
    if (xs.length < 2) continue;
    xs.sort((p, q) => p - q);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const i0 = Math.max(0, Math.ceil(xs[k] - 0.5)), i1 = Math.min(nx - 1, Math.floor(xs[k + 1] - 0.5));
      for (let i = i0; i <= i1; i++) mask[j * nx + i] = 1;
    }
  }
  return mask;
}
