"""Checked geometries, stored under ``<data>/geometry/<uuid4>/``.

Each folder holds ``body.stl`` (binary STL in metres, written by the upload worker) and
``geometry.json`` (the check report, reference values for the coefficients, and a
display name). Ids are server-generated UUID4s; every path is safe-joined.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from windlab.case_builder import InputError, safe_join
from windlab.geometry import orient, reference, step
from windlab.geometry.naca import NacaCode, naca_wing
from windlab.geometry.shapes import make_body
from windlab.geometry.upload import UploadError, _fmt_length, check_stl

# Built-in wing limits (plain-language messages come from these).
CHORD_RANGE = (0.05, 2.0)  # m
SPAN_RANGE = (0.1, 5.0)  # m
TAPER_RANGE = (0.2, 1.0)
MAX_ASPECT_RATIO = 20.0
# Student overrides of the reference values (the case builder's own limits).
REF_LENGTH_RANGE = (0.001, 100.0)  # m
REF_AREA_RANGE = (1e-5, 1e4)  # m^2


class GeometryError(ValueError):
    """Plain-language problem with a geometry request."""


def parse_uuid(value: str) -> str:
    """Return the canonical form of a UUID4 string, or raise ``GeometryError``."""
    try:
        u = uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise GeometryError("That id is not valid.") from None
    if u.version != 4 or str(u) != str(value).lower():
        raise GeometryError("That id is not valid.")
    return str(u)


def display_name(name: str | None, fallback: str) -> str:
    """A short, harmless label for the UI (never used in a path or a dictionary)."""
    s = re.sub(r"[^A-Za-z0-9 ._()+-]", "_", str(name or ""))[:80].strip()
    return s or fallback


@dataclass
class GeometryStore:
    root: Path

    def __post_init__(self):
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths

    def folder(self, geometry_id: str) -> Path:
        return safe_join(self.root, parse_uuid(geometry_id))

    def _new_folder(self) -> tuple[str, Path]:
        gid = str(uuid.uuid4())
        d = safe_join(self.root, gid)
        d.mkdir(mode=0o750)
        return gid, d

    # ------------------------------------------------------------------ create

    def add_upload(self, uploaded: Path, units: str | None, name: str | None, bodies: str = "largest") -> dict:
        """Check an uploaded file (already on disk, outside the store) and keep it if usable.

        STL files need ``units``; a STEP file (by content or name) may leave ``units`` None to
        take the unit the file declares. ``bodies`` ("largest" | "all") only matters for a
        STEP file holding several separate solids."""
        gid, d = self._new_folder()
        try:
            if step.is_step_file(uploaded, name):
                report = step.check_step(uploaded, units, output=d / "body.stl", bodies=bodies)
            else:
                if units is None:
                    raise UploadError("Please choose the units the part was drawn in: mm, cm, m or in "
                                      "(STL files do not store them).")
                report = check_stl(uploaded, units, output=d / "body.stl")
            areas = None
            if report.ok:  # both silhouette areas and WindLab's guess: wing or body
                areas = reference.measure(report.bounds_min_m, report.bounds_max_m, d / "body.stl", report.triangles)
            meta = {
                "id": gid,
                "kind": "upload",
                "class": "unknown",  # geometry class for the expected-error table (windlab.expected_error)
                "name": display_name(name, "Uploaded part"),
                "created": time.time(),
                "usable": report.ok,
                "report": report.to_dict(),
                "areas": areas,
                "part_type": {"detected": areas["detected"], "chosen": None} if areas else None,
                "reference": (reference.reference_values(report.bounds_min_m, report.bounds_max_m, areas)
                              if report.ok else None),
            }
            if not report.ok:
                # Nothing to run; keep only the report so the UI can show it again.
                (d / "body.stl").unlink(missing_ok=True)
            (d / "geometry.json").write_text(json.dumps(meta, indent=2))
            return meta
        except (UploadError, InputError) as e:
            shutil.rmtree(d, ignore_errors=True)
            raise GeometryError(str(e)) from None
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise

    def add_naca(self, code: str, chord: float, span: float, taper: float = 1.0) -> dict:
        try:
            NacaCode.parse(code)
        except ValueError:
            raise GeometryError(
                "The NACA code must be four digits, e.g. 0012 (symmetric, 12 % thick) or 2412 (cambered). "
                "The last two digits (thickness) must be 01 to 40, and a cambered section (first digit > 0) "
                "needs a camber position (second digit 1 to 9)."
            ) from None
        _check_range("Chord", chord, CHORD_RANGE, "m")
        _check_range("Span", span, SPAN_RANGE, "m")
        _check_range("Taper ratio", taper, TAPER_RANGE, "")
        if span / chord > MAX_ASPECT_RATIO:
            raise GeometryError(f"The span can be at most {MAX_ASPECT_RATIO:g} times the chord.")
        wing = naca_wing(code, chord=chord, span=span, taper=taper)
        gid, d = self._new_folder()
        try:
            raw = d / "generated.stl"
            wing.write_stl(raw)
            report = check_stl(raw, "m", output=d / "body.stl")
            raw.unlink()
            if not report.ok:  # would be a bug in the generator
                raise GeometryError("The wing generator produced an unusable shape: " + " ".join(report.problems))
            meta = {
                "id": gid,
                "kind": "naca",
                "class": "streamlined-lifting",
                "name": f"NACA {code} wing, chord {chord:g} m, span {span:g} m"
                + (f", taper {taper:g}" if taper != 1 else ""),
                "created": time.time(),
                "usable": True,
                "naca": {"code": code, "chord": chord, "span": span, "taper": taper},
                "report": report.to_dict(),
                "reference": {
                    "length": wing.mean_aero_chord,
                    "area": wing.reference_area,
                    "moment_point": list(wing.quarter_chord_point),
                    "how": "mean aerodynamic chord and planform area (chord x span)",
                },
            }
            (d / "geometry.json").write_text(json.dumps(meta, indent=2))
            return meta
        except UploadError as e:
            shutil.rmtree(d, ignore_errors=True)
            raise GeometryError(str(e)) from None
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise

    def add_builtin(self, shape: str, **params) -> dict:
        """A built-in bluff or streamlined body (windlab.geometry.shapes), checked like any STL."""
        try:
            body = make_body(shape, **params)
        except (ValueError, TypeError) as e:
            raise GeometryError(str(e) if isinstance(e, ValueError) else "Please give numbers for the size.") from None
        gid, d = self._new_folder()
        try:
            raw = d / "generated.stl"
            body.write_stl(raw)
            report = check_stl(raw, "m", output=d / "body.stl")
            raw.unlink()
            if not report.ok:  # would be a bug in the generator
                raise GeometryError("The shape generator produced an unusable shape: " + " ".join(report.problems))
            meta = {
                "id": gid,
                "kind": "builtin",
                "class": body.geometry_class,
                "name": body.name,
                "created": time.time(),
                "usable": True,
                "builtin": {"shape": body.shape, **body.params},
                "description": body.description,
                "report": report.to_dict(),
                "reference": {
                    "length": body.reference_length,
                    "area": body.reference_area,
                    "moment_point": list(body.moment_point),
                    "how": body.reference_how,
                },
            }
            (d / "geometry.json").write_text(json.dumps(meta, indent=2))
            return meta
        except UploadError as e:
            shutil.rmtree(d, ignore_errors=True)
            raise GeometryError(str(e)) from None
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise

    # ------------------------------------------------------------------ read

    def get(self, geometry_id: str) -> dict:
        d = self.folder(geometry_id)
        f = d / "geometry.json"
        if not f.is_file() or f.is_symlink():
            raise KeyError(geometry_id)
        return json.loads(f.read_text())

    # ------------------------------------------------------------------ orientation / reference

    def orient(self, geometry_id: str, action: str) -> tuple[dict, str]:
        """Turn an uploaded part by 90 degrees about x, y or z, or as suggested.

        Rewrites ``body.stl`` and the bounds in the report, and recomputes the default
        reference values (any values the student typed in are reset, because they
        belonged to the old orientation). Returns (meta, plain-language note)."""
        meta = self.get(geometry_id)
        if meta.get("kind") != "upload":
            raise GeometryError("Built-in shapes are already lined up with the wind; they can't be turned.")
        if not meta.get("usable"):
            raise GeometryError("Fix the problems with this shape before turning it.")
        report = meta["report"]
        size = report["size_m"]
        if action in ("x", "y", "z"):
            r = orient.quarter_turn(action)
            note = f"Turned the part 90° about the {action} axis."
        elif action == "suggest":
            r = orient.suggest_rotation(size)
            if np.array_equal(r, np.eye(3)):
                note = "The part already has its longest side along the wind and its thinnest side up."
            else:
                note = "Turned the part so its longest side is along the wind (x) and its thinnest side is up (z)."
            new_size = np.abs(r) @ np.asarray(size)
            if orient.looks_like_a_wing(new_size):
                note += (" If this is a wing, its span (tip to tip) should go across the wind: "
                         "turn it once about z.")
        else:
            raise GeometryError("Choose a turn about x, y or z, or the suggested orientation.")
        stl = self.stl_path(geometry_id)
        if not np.array_equal(r, np.eye(3)):
            lo, hi = orient.rotate_stl(stl, r)
            report["bounds_min_m"], report["bounds_max_m"] = list(lo), list(hi)
            report["size_m"] = [b - a for a, b in zip(lo, hi, strict=True)]
            report["messages"] = [_resize_message(m, report["size_m"]) for m in report.get("messages", [])]
            prev = np.asarray(meta.get("orientation") or np.eye(3, dtype=int).tolist())
            meta["orientation"] = (r @ prev).astype(int).tolist()
            sym = report.get("mirror_symmetric")
            if sym:  # a mirror plane turns with the part: new axis i was old axis j where |r|[i, j] = 1
                old = [bool(sym.get(a)) for a in "xyz"]
                report["mirror_symmetric"] = {a: old[int(np.argmax(np.abs(r[i])))] for i, a in enumerate("xyz")}
            # the silhouettes change with the orientation; the student's wing / body choice is kept
            areas = reference.measure(lo, hi, stl, report.get("triangles"))
            chosen = (meta.get("part_type") or {}).get("chosen")
            meta["areas"] = areas
            meta["part_type"] = {"detected": areas["detected"], "chosen": chosen}
            meta["reference"] = reference.reference_values(lo, hi, areas, chosen)
            meta.pop("reference_default", None)
        self.save(meta)
        return meta, note

    def _areas(self, meta: dict) -> dict:
        """The part's two reference areas (measured on demand for shapes stored before they were kept)."""
        if meta.get("areas"):
            return meta["areas"]
        report = meta["report"]
        try:
            stl = self.stl_path(meta["id"])
        except KeyError:
            stl = None
        areas = reference.measure(report["bounds_min_m"], report["bounds_max_m"], stl, report.get("triangles"))
        meta["areas"] = areas
        meta.setdefault("part_type", {"detected": areas["detected"], "chosen": None})
        return areas

    def set_reference(self, geometry_id: str, length: float | None, area: float | None,
                      part_type: str | None = None) -> dict:
        """Student-chosen reference length / area for the coefficients; None = the default.

        ``part_type`` ("wing" | "body" | "auto") says which convention an uploaded part's defaults
        follow (frontal area for a body, planform area for a wing); it replaces the defaults, so a
        typed-in length or area is reset unless it comes with the same request."""
        meta = self.get(geometry_id)
        if not meta.get("usable") or not meta.get("reference"):
            raise GeometryError("Fix the problems with this shape first.")
        if part_type is not None:
            if part_type not in (*reference.KINDS, "auto"):
                raise GeometryError("Choose whether the part is a wing or a body.")
            if meta.get("kind") != "upload":
                raise GeometryError("Built-in wings and bodies already use the standard reference area for their "
                                    "kind; only an uploaded part can be switched between wing and body.")
            areas = self._areas(meta)
            chosen = None if part_type == "auto" else part_type
            report = meta["report"]
            meta["part_type"] = {"detected": areas["detected"], "chosen": chosen}
            default = reference.reference_values(report["bounds_min_m"], report["bounds_max_m"], areas, chosen)
        else:
            default = meta.get("reference_default") or {k: v for k, v in meta["reference"].items() if k != "custom"}
        ref = dict(default)
        new_len = default["length"] if length is None else _checked(length, REF_LENGTH_RANGE, "reference length", "m")
        new_area = default["area"] if area is None else _checked(area, REF_AREA_RANGE, "reference area", "m²")
        ref["length"], ref["area"] = new_len, new_area
        changed = [n for n, v in (("length", length), ("area", area)) if v is not None]
        ref["how"] = default["how"] if not changed else (
            "set by you" if len(changed) == 2 else f"{changed[0]} set by you; otherwise {default['how']}")
        ref["custom"] = changed
        meta["reference"], meta["reference_default"] = ref, default
        self.save(meta)
        return meta

    def save(self, meta: dict) -> None:
        f = self.folder(meta["id"]) / "geometry.json"
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        tmp.replace(f)

    def touch(self, geometry_id: str) -> None:
        """Record that a run used this shape (housekeeping keeps recently used shapes)."""
        try:
            meta = self.get(geometry_id)
        except (KeyError, GeometryError, OSError, ValueError):
            return
        meta["last_used"] = time.time()
        self.save(meta)

    def stl_path(self, geometry_id: str) -> Path:
        p = self.folder(geometry_id) / "body.stl"
        if not p.is_file() or p.is_symlink():
            raise KeyError(geometry_id)
        return p


def _checked(value, rng: tuple[float, float], label: str, unit: str) -> float:
    lo, hi = rng
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if not lo <= v <= hi:  # also rejects NaN
        raise GeometryError(f"The {label} must be between {lo:g} and {hi:g} {unit}.")
    return v


_SIZE_MSG = re.compile(r"the part is .*? \(x, y, z\)\.")


def _resize_message(message: str, size) -> str:
    """Keep the "the part is A x B x C (x, y, z)." summary true after a turn."""
    text = "the part is " + " x ".join(_fmt_length(s) for s in size) + " (x, y, z)."
    return _SIZE_MSG.sub(lambda _m: text, message)


def _check_range(label: str, value: float, rng: tuple[float, float], unit: str) -> None:
    lo, hi = rng
    try:
        ok = lo <= float(value) <= hi
    except (TypeError, ValueError):
        ok = False
    if not ok:
        u = f" {unit}" if unit else ""
        raise GeometryError(f"{label} must be between {lo:g}{u} and {hi:g}{u}.")
