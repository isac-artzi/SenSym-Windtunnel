"""Expected-error ranges for CL and CD (PRD RES-2), looked up in ``validation_table.json``.

The table is keyed by geometry class x coefficient x mesh preset and carries a validity
envelope (Reynolds number, angle of attack). Inside the envelope a coefficient gets a
range such as "likely 0.44-0.47" plus a plain-language "why"; outside it - uploads of
unknown class, angles near stall, Reynolds numbers we never checked, runs that did not
settle - the answer is "Unvalidated - treat as a trend only" with the reasons.

Range convention: ``low`` and ``high`` are fractions relative to the WindLab value (the
likely true value lies in ``value * (1 + low) .. value * (1 + high)``), with an absolute
``floor`` half-width for coefficients near zero. Drag ranges combine a *numerical* part
(what a mesh-converged, wall-resolved run of the same model would give) and a *model*
part (the fully turbulent assumption against low-turbulence measurements, applied below
a Reynolds number). Both multiply, so the result is asymmetric: the Quick mesh over-
predicts drag.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TABLE_PATH = Path(__file__).with_name("validation_table.json")
UNVALIDATED = "Unvalidated - treat as a trend only"
# uploads near 0 degrees: what a small lift on a symmetric part means (tasks 012 and 013; validation/robustness.md)
SYMMETRIC_LIFT_NOTE = ("A part that is symmetric top to bottom has zero lift at 0 degrees, so a small CL there comes "
                       "from the mesh, not the physics. Its size depends on the part: below 1 % of the drag for smooth "
                       "bodies. Thin fins or plates thinner than a mesh cell are not meshed symmetrically - a 42 cm "
                       "rocket with 2 mm fins gave a sideways force as large as its drag on the Quick mesh - so "
                       "WindLab now refines the mesh around such parts on its own (the 'Thin parts' note under the "
                       "mesh choice), which brought that rocket's lift down to under 1 % of its drag.")
CLASSES = ("streamlined-lifting", "streamlined-body", "bluff", "bluff-smooth", "mixed", "unknown")
MAX_OSCILLATION = 0.02  # same threshold as the run warning "the lift kept swinging"


@lru_cache(maxsize=1)
def load_table(path: Path = TABLE_PATH) -> dict:
    return json.loads(Path(path).read_text())


def table_version() -> str:
    return str(load_table().get("version", "?"))


def _entry(table: dict, geometry_class: str, preset: str) -> dict | None:
    cls = table.get("classes", {}).get(geometry_class or "unknown")
    if not cls:
        return None
    return cls.get("presets", {}).get(preset or "quick")


def _envelope_problems(entry: dict | None, geometry_class: str, reynolds, alpha_deg, converged,
                       oscillation, part_type: str | None = None) -> list[str]:
    out = []
    if entry is None:
        if geometry_class in (None, "", "unknown"):
            convention = (" (treated as a body: coefficients on its frontal area)" if part_type == "body"
                          else " (treated as a wing: coefficients on its planform area)" if part_type == "wing" else "")
            out.append(f"the shape is an upload of unknown class{convention}; WindLab's checks only cover its "
                       "built-in shapes")
        else:
            out.append(f"no validation evidence yet for this class of shape ({geometry_class}) on this mesh")
        return out
    env = entry.get("envelope", {})
    lo, hi = env.get("reynolds", [0, float("inf")])
    if reynolds is None or not lo <= float(reynolds) <= hi:
        out.append(f"the Reynolds number is outside the checked band ({_re_text(lo)} to {_re_text(hi)})")
    a_max = env.get("alpha_abs_max_deg")
    if a_max is not None and (alpha_deg is None or abs(float(alpha_deg)) > a_max):
        why = ("where this wing leaves its linear range and stall predictions are uncertain"
               if geometry_class == "streamlined-lifting" else "and bodies at an angle were not checked")
        out.append(f"the angle of attack is beyond {a_max:g} degrees, {why}")
    if converged is False:
        out.append("the numbers did not settle before the iteration limit")
    if oscillation is not None and oscillation > MAX_OSCILLATION and not env.get("unsteady_ok"):
        out.append("the lift kept swinging up and down (unsteady flow)")
    return out


def _re_text(re_: float) -> str:
    if re_ == float("inf"):
        return "any"
    return f"{re_ / 1e6:.2g} million" if re_ >= 1e6 else f"{re_:,.0f}"


def _band_for(cd: dict, alpha_deg) -> dict | None:
    a = abs(float(alpha_deg or 0.0))
    for band in cd.get("bands", []):
        if a <= band["alpha_abs_max_deg"]:
            return band
    return None


def _combine(numerical: dict, model: dict | None, reynolds) -> tuple[float, float]:
    low, high = float(numerical["low"]), float(numerical["high"])
    if model and (reynolds is None or float(reynolds) < model.get("reynolds_below", float("inf"))):
        low = (1 + low) * (1 + float(model["low"])) - 1
        high = (1 + high) * (1 + float(model["high"])) - 1
    return low, high


def _count_inside(evidence: list[dict], low: float, high: float, floor: float) -> tuple[int, int]:
    """How many recorded comparisons the range would have covered."""
    n = inside = 0
    for e in evidence:
        if "error" in e:
            n += 1
            # the evidence error is "WindLab minus reference" relative to the reference; the range is
            # relative to the WindLab value: reference = WindLab / (1 + error)
            ref_rel = 1 / (1 + e["error"]) - 1
            if low - 1e-9 <= ref_rel <= high + 1e-9:
                inside += 1
        elif "error_abs" in e:
            n += 1
            if abs(e["error_abs"]) <= floor + 1e-12:
                inside += 1
    return inside, n


def coefficient_range(coef: str, value, geometry_class: str | None, preset: str | None, reynolds, alpha_deg,
                      converged=True, oscillation=None, table: dict | None = None,
                      part_type: str | None = None) -> dict:
    """The expected-error range of one coefficient (``coef`` = "cl" or "cd").

    Returns a dict with ``status`` ("validated" | "unvalidated"), ``low``/``high`` (values, or None),
    ``text`` (e.g. "likely 0.44-0.47" or the unvalidated label), ``why`` (plain language) and
    ``reasons`` (why unvalidated, if so). ``part_type`` is an upload's convention ("wing" | "body"),
    named in the reason so the student knows which area the number sits on."""
    table = table or load_table()
    entry = _entry(table, geometry_class, preset)
    problems = _envelope_problems(entry, geometry_class, reynolds, alpha_deg, converged, oscillation, part_type)
    base = {"coefficient": coef, "value": value, "table_version": table.get("version"), "class": geometry_class,
            "preset": preset}
    spec = (entry or {}).get(coef)
    if problems or spec is None or value is None:
        if not problems and spec is None:
            problems.append(f"no validation evidence for {coef.upper()} on this mesh")
        why = (f"{UNVALIDATED}: " + "; ".join(problems) + ". The number still shows how this shape compares with "
               "other runs on the same mesh, but we have no measured or mesh-converged reference to say how far off "
               "it may be.")
        if coef == "cl" and geometry_class in (None, "", "unknown") and alpha_deg is not None and abs(alpha_deg) <= 2:
            why += " " + SYMMETRIC_LIFT_NOTE
        return {**base, "status": "unvalidated", "low": None, "high": None, "text": UNVALIDATED,
                "reasons": problems, "why": why}
    v = float(value)
    floor = float(spec.get("floor", 0.0))
    if coef == "cd" and "bands" in spec:
        band = _band_for(spec, alpha_deg)
        if band is None:
            return {**base, "status": "unvalidated", "low": None, "high": None, "text": UNVALIDATED,
                    "reasons": ["the angle of attack is beyond the checked range"], "why": UNVALIDATED}
        rel_lo, rel_hi = _combine(band["numerical"], band.get("model"), reynolds)
        basis = band["basis"]
        inside, n = _count_inside(spec.get("evidence", []), rel_lo, rel_hi, floor)
    else:
        rel_lo, rel_hi = float(spec["low"]), float(spec["high"])
        basis = spec["basis"]
        inside, n = _count_inside(spec.get("evidence", []), rel_lo, rel_hi, floor)
    lo, hi = v * (1 + rel_lo), v * (1 + rel_hi)
    if v < 0:  # a negative coefficient (e.g. CL at negative incidence): the band flips
        lo, hi = hi, lo
    # absolute floor for coefficients near zero
    if hi - lo < 2 * floor:
        mid = 0.5 * (lo + hi)
        lo, hi = mid - floor, mid + floor
    digits = 4 if coef == "cd" else 3
    text = f"likely {_fmt(lo, digits)} to {_fmt(hi, digits)}"
    tally = f" In our checks, {inside} of {n} comparisons landed inside this range." if n else ""
    pct = f"{-rel_lo * 100:.0f} % below to {abs(rel_hi) * 100:.0f} % {'above' if rel_hi >= 0 else 'below'}"
    why = (f"{basis}{tally} For this run that means the real value is most likely between {pct} the WindLab "
           f"number" + (f" (at least ±{floor:g} for values near zero)" if floor else "") +
           f". Validation table {table.get('version')}.")
    return {**base, "status": "validated", "low": lo, "high": hi, "rel_low": rel_lo, "rel_high": rel_hi,
            "text": text, "why": why, "reasons": [], "inside": inside, "checks": n}


def _fmt(x: float, digits: int) -> str:
    s = f"{x:.{digits}f}"
    return s.replace("-", "−")


def ratio_range(cl: dict, cd: dict) -> dict | None:
    """L/D range from the CL and CD ranges (None unless both are validated and keep their sign)."""
    if cl.get("status") != "validated" or cd.get("status") != "validated":
        return None
    if cl["low"] <= 0 <= cl["high"] or cd["low"] <= 0:
        return None
    lo, hi = sorted((cl["low"] / cd["high"], cl["high"] / cd["low"]))
    return {"status": "validated", "low": lo, "high": hi, "text": f"likely {_fmt(lo, 1)} to {_fmt(hi, 1)}"}


def expected_for_point(point: dict, params: dict, geometry_class: str | None) -> dict:
    """The expected-error block for one result (a single run's result or one sweep angle)."""
    alpha = point.get("alpha_deg", params.get("alpha_deg"))
    common = {"geometry_class": geometry_class, "preset": params.get("preset"), "reynolds": params.get("reynolds"),
              "alpha_deg": alpha, "converged": point.get("converged"), "oscillation": point.get("cl_oscillation"),
              "part_type": params.get("part_type")}
    cl = coefficient_range("cl", point.get("cl"), **common)
    cd = coefficient_range("cd", point.get("cd"), **common)
    out = {"cl": cl, "cd": cd, "table_version": table_version()}
    ld = ratio_range(cl, cd)
    if ld:
        out["l_over_d"] = ld
    return out


def geometry_class_of(params: dict) -> str:
    """The geometry class a run was made with (older runs: inferred from the shape name)."""
    cls = params.get("geometry_class")
    if cls in CLASSES:
        return cls
    name = str(params.get("geometry_name") or "")
    return "streamlined-lifting" if name.startswith("NACA ") else "unknown"


__all__ = ["CLASSES", "UNVALIDATED", "coefficient_range", "expected_for_point", "geometry_class_of", "load_table",
           "ratio_range", "table_version"]
