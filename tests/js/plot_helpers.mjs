// Unit tests of the pure plotting helpers in plot.js (run by tests/test_js.py with Node; no DOM needed):
// the coincident-marker helpers of task 014 and the tick helpers they sit next to.
//
// Environment: WINDLAB_SMOKE_STATIC (file: URL of a copy of the static folder with a package.json that
// says "type": "module"). Output: one JSON object on stdout; exit code 0 when every check passed.

import { strict as assert } from "node:assert";

const base = process.env.WINDLAB_SMOKE_STATIC;
if (!base) {
  console.error("set WINDLAB_SMOKE_STATIC");
  process.exit(2);
}
const { coincidentGroups, coincidentMarker, stepGroups, niceTicks, fmtTick } = await import(base + "plot.js");

const checks = [];
function test(name, fn) {
  try { fn(); checks.push({ name, ok: true }); } catch (e) { checks.push({ name, ok: false, detail: String(e.message) }); }
}

// ---------------------------------------------------------------- coincidentGroups

test("two runs at one angle with L/D 8.8 and 8.7 (a few pixels apart) share a group, in drawing order", () => {
  const g = coincidentGroups([{ x: 200, y: 120 }, { x: 200, y: 122.5 }]);
  assert.deepEqual(g, [{ group: 0, rank: 0, size: 2 }, { group: 0, rank: 1, size: 2 }]);
});

test("points further apart than the tolerance keep their own groups", () => {
  const g = coincidentGroups([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 0, y: 6.5 }]);
  assert.deepEqual(g, [{ group: 0, rank: 0, size: 1 }, { group: 1, rank: 0, size: 1 }, { group: 2, rank: 0, size: 1 }]);
  assert.deepEqual(coincidentGroups([{ x: 0, y: 0 }, { x: 0, y: 6.5 }], 7).map((q) => q.group), [0, 0]);
});

test("three series on one spot nest in drawing order; a fourth point elsewhere stands alone", () => {
  const g = coincidentGroups([{ x: 50, y: 50 }, { x: 300, y: 80 }, { x: 52, y: 49 }, { x: 49, y: 53 }]);
  assert.deepEqual(g.map((q) => q.group), [0, 1, 0, 0]);
  assert.deepEqual(g.map((q) => q.rank), [0, 0, 1, 2]);
  assert.deepEqual(g.map((q) => q.size), [3, 1, 3, 3]);
});

test("a point near any earlier member joins that member's group", () => {
  const g = coincidentGroups([{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }]);
  assert.deepEqual(g.map((q) => q.group), [0, 0, 0]);
  assert.deepEqual(g.map((q) => q.rank), [0, 1, 2]);
});

test("a point with non-finite coordinates stands alone", () => {
  const g = coincidentGroups([{ x: NaN, y: 0 }, { x: 0, y: 0 }, { x: NaN, y: 0 }, { x: 0, y: 1 }]);
  assert.deepEqual(g.map((q) => q.group), [0, 1, 2, 1]);
});

test("no points, no groups", () => {
  assert.deepEqual(coincidentGroups([]), []);
});

// ---------------------------------------------------------------- coincidentMarker

test("the first marker is filled at its base size, later ones hollow and larger by 2.5 px each", () => {
  assert.deepEqual(coincidentMarker(4.5, 0), { radius: 4.5, hollow: false });
  assert.deepEqual(coincidentMarker(4.5, 1), { radius: 7, hollow: true });
  assert.deepEqual(coincidentMarker(4.5, 2), { radius: 9.5, hollow: true });
  assert.deepEqual(coincidentMarker(4, -1), { radius: 4, hollow: false });
});

// ---------------------------------------------------------------- stepGroups

test("Left/Right stop once per group of coincident points and stay put at the ends", () => {
  const groups = [0, 0, 1, 2, 2];
  assert.equal(stepGroups(groups, null, +1), 0);
  assert.equal(stepGroups(groups, 0, +1), 2);
  assert.equal(stepGroups(groups, 1, +1), 2);
  assert.equal(stepGroups(groups, 2, +1), 3);
  assert.equal(stepGroups(groups, 3, +1), 3);  // the last group: nowhere further to go
  assert.equal(stepGroups(groups, 4, +1), 4);
  assert.equal(stepGroups(groups, null, -1), 4);
  assert.equal(stepGroups(groups, 4, -1), 2);
  assert.equal(stepGroups(groups, 2, -1), 1);
  assert.equal(stepGroups(groups, 1, -1), 1);  // the first group
  assert.equal(stepGroups(groups, 0, -1), 0);
});

test("stepGroups with every point in its own group steps one by one; an empty chart gives null", () => {
  assert.equal(stepGroups([0, 1, 2], 0, +1), 1);
  assert.equal(stepGroups([0, 1, 2], 2, +1), 2);
  assert.equal(stepGroups([0, 1, 2], 7, +1), 0);  // a stale index starts over
  assert.equal(stepGroups([], null, +1), null);
});

// ---------------------------------------------------------------- the tick helpers beside them

test("niceTicks and fmtTick still give round ticks with just enough decimals", () => {
  const { ticks, step } = niceTicks(-0.2, 0.6, 5);
  assert.equal(step, 0.2);
  assert.deepEqual(ticks, [-0.2, 0, 0.2, 0.4, 0.6]);
  assert.equal(fmtTick(0.2, 0.2), "0.2");
  assert.equal(fmtTick(-0.2, 0.2), "−0.2");
  assert.equal(fmtTick(2, 2), "2");
});

const ok = checks.every((c) => c.ok);
console.log(JSON.stringify({ ok, checks }));
process.exit(ok ? 0 : 1);
