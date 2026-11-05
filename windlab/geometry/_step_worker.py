"""Untrusted-STEP reader, run as a separate process by :mod:`windlab.geometry.step`.

Usage: python -I _step_worker.py <path> <out_stl> <max_triangles> <bodies>

``bodies`` is ``largest`` or ``all``: what to keep when the file holds several solids that
do not touch (touching solids are fused into one first). Self-contained on purpose
(stdlib + numpy + OCP): started with ``python -I`` under the CPU / memory / file-size
limits the parent sets, it never imports code from the current or script directory.

What it does, in order:

1. reads the length unit the file declares (``LENGTH_UNIT`` entity: ``SI_UNIT(.MILLI.,.METRE.)``,
   ``CONVERSION_BASED_UNIT('INCH', ...)`` ...),
2. loads the file with OpenCASCADE, converted to metres whatever the unit,
3. heals the shape (``ShapeFix_Shape``) and notes whether that changed anything,
4. fuses several solids into one when they touch; otherwise keeps the largest (or all),
5. tessellates with a chord error relative to the part size, coarsening until the triangle
   count fits the cap, and writes a binary STL **in the file's own units** (the parent then
   runs the usual STL checks with the units the student confirms).

It writes one JSON object to the *original* stdout (OpenCASCADE's own chatter is sent to
stderr) with the counts above. Exit codes: 0 ok, 2 unreadable (JSON has "error"), 3 out of
memory, 4 OpenCASCADE (OCP) could not be imported.
"""

from __future__ import annotations

import json
import os
import re
import sys

import numpy as np

# Chord error of the tessellation as a fraction of the part's bounding-box diagonal, and the
# angular deflection in radians. 1e-3 of the diagonal is far below the Quick mesh's surface
# cell (about 1/128 of the length), so the mesh sees the CAD surface, not the facets.
DEFLECTION_FRACTION = 1e-3
ANGULAR_DEFLECTION = 0.35
COARSEN_STEPS = 5  # each step doubles the deflection until the triangle count fits
MAX_ENTITIES = 3_000_000  # STEP entities ("#123 = ...") before we refuse (GEO-2: cap before tessellation)
FUSE_MAX_SOLIDS = 64  # pairwise booleans beyond this would take too long

# STEP unit names -> WindLab's units (metres per unit). Anything else is converted to metres.
_SI_PREFIX = {"": 1.0, "MILLI": 1e-3, "CENTI": 1e-2, "DECI": 1e-1, "KILO": 1e3, "MICRO": 1e-6, "NANO": 1e-9}
_SI_TO_UNIT = {1e-3: "mm", 1e-2: "cm", 1.0: "m"}
_NAMED = {"INCH": ("in", 0.0254), "INCHES": ("in", 0.0254), "FOOT": (None, 0.3048), "FEET": (None, 0.3048),
          "MIL": (None, 2.54e-5), "YARD": (None, 0.9144), "MILE": (None, 1609.344)}


class BadFile(ValueError):
    pass


def declared_unit(data: bytes) -> dict:
    """The length unit the file declares: ``{"unit": "mm"|"cm"|"m"|"in"|None, "name": str|None,
    "factor": metres per file unit or None}``. Looks at every entity that names a LENGTH_UNIT."""
    si_found: dict | None = None
    for m in re.finditer(rb"LENGTH_UNIT\s*\(\s*\)", data):
        s = data.rfind(b";", 0, m.start()) + 1
        e = data.find(b";", m.end())
        ent = data[s: e if e >= 0 else m.end() + 400].decode("ascii", errors="replace")
        named = re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']*)'", ent)
        if named:
            # an inch / foot file also lists the SI unit the conversion is based on: the named one wins
            key = named.group(1).strip().upper()
            unit, factor = _NAMED.get(key, (None, None))
            return {"unit": unit, "name": named.group(1).strip().lower(), "factor": factor}
        si = re.search(r"SI_UNIT\s*\(\s*(\$|\.(\w+)\.)\s*,\s*\.METRE\.\s*\)", ent)
        if si and si_found is None:
            prefix = (si.group(2) or "").upper()
            factor = _SI_PREFIX.get(prefix)
            if factor is not None:
                name = (prefix.lower() + "metre") if prefix else "metre"
                si_found = {"unit": _SI_TO_UNIT.get(factor), "name": name, "factor": factor}
    return si_found or {"unit": None, "name": None, "factor": None}


def _count(shape, kind) -> int:
    from OCP.TopExp import TopExp_Explorer

    ex, n = TopExp_Explorer(shape, kind), 0
    while ex.More():
        n += 1
        ex.Next()
    return n


def _solids(shape) -> list:
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    out, ex = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(float(props.Mass()))


def _bounds(shape) -> tuple[np.ndarray, np.ndarray]:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb, False)
    lo, hi = bb.CornerMin().Coord(), bb.CornerMax().Coord()
    return np.array(lo, float), np.array(hi, float)


def _compound(shapes: list):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    comp, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def _fuse(solids: list):
    """One solid out of several, when the boolean union manages it (touching / overlapping bodies)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    acc = solids[0]
    for s in solids[1:]:
        op = BRepAlgoAPI_Fuse(acc, s)
        if not op.IsDone():
            return None
        acc = op.Shape()
    return acc


def heal(shape) -> tuple[object, bool, bool, bool]:
    """ShapeFix on the whole shape: (shape, changed, valid_before, valid_after)."""
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeFix import ShapeFix_Shape

    valid_before = bool(BRepCheck_Analyzer(shape).IsValid())
    fix = ShapeFix_Shape(shape)
    changed = bool(fix.Perform())
    fixed = fix.Shape()
    valid_after = bool(BRepCheck_Analyzer(fixed).IsValid())
    return fixed, changed, valid_before, valid_after


def tessellate(shape, deflection: float, angular: float = ANGULAR_DEFLECTION) -> np.ndarray:
    """Triangles (N, 3, 3) of the shape's faces, outward by face orientation, in the shape's units."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, float(deflection), False, float(angular), True)
    chunks = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        ex.Next()
        if tri is None or tri.NbTriangles() == 0:
            continue
        trsf = loc.Transformation()
        identity = loc.IsIdentity()
        nodes = np.empty((tri.NbNodes(), 3))
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i)
            if not identity:
                p = p.Transformed(trsf)
            nodes[i - 1] = p.Coord()
        idx = np.empty((tri.NbTriangles(), 3), np.int64)
        for i in range(1, tri.NbTriangles() + 1):
            idx[i - 1] = tri.Triangle(i).Get()
        idx -= 1
        if face.Orientation() == TopAbs_REVERSED:
            idx = idx[:, ::-1]
        chunks.append(nodes[idx])
    if not chunks:
        return np.zeros((0, 3, 3))
    return np.concatenate(chunks)


_OUT_DTYPE = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def write_stl(path: str, tris: np.ndarray) -> None:
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, norm, out=np.zeros_like(n), where=norm > 0)
    rec = np.zeros(len(tris), dtype=_OUT_DTYPE)
    rec["normal"], rec["v"] = n, tris
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"WindLab binary STL from STEP (file units)".ljust(80, b" "))
        fh.write(np.uint32(len(tris)).tobytes())
        fh.write(rec.tobytes())


def read_step(path: str):
    """The file as one OpenCASCADE shape, in metres."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()  # the static parameters exist only once a reader has been made
    Interface_Static.SetCVal_s("xstep.cascade.unit", "M")
    if Interface_Static.CVal_s("xstep.cascade.unit") != "M":
        raise BadFile("OpenCASCADE would not switch its working unit to metres")
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise BadFile("this is not a STEP file that OpenCASCADE can read")
    if reader.TransferRoots() < 1 or reader.NbShapes() < 1:
        raise BadFile("the STEP file contains no geometry")
    return reader.OneShape()


def process(path: str, out_path: str, max_triangles: int, bodies: str) -> dict:
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL

    with open(path, "rb") as fh:
        data = fh.read()
    if not data.lstrip()[:12].startswith(b"ISO-10303-21"):
        raise BadFile("this is not a STEP file (it does not start with ISO-10303-21)")
    n_entities = data.count(b"\n#")
    if n_entities > MAX_ENTITIES:
        raise BadFile(f"the STEP file is too complex ({n_entities:,d} entities; the limit is {MAX_ENTITIES:,d})")
    unit = declared_unit(data)
    del data

    shape = read_step(path)
    solids_in = _solids(shape)
    faces_in = _count(shape, TopAbs_FACE)
    shells_in = _count(shape, TopAbs_SHELL)
    shape, healed, valid_before, valid_after = heal(shape)
    solids = _solids(shape)
    fused = False
    if 1 < len(solids) <= FUSE_MAX_SOLIDS:
        merged = _fuse(solids)
        if merged is not None:
            after = _solids(merged)
            if 0 < len(after) < len(solids):
                fused = True
                solids = after
    volumes = sorted(((_volume(s), s) for s in solids), key=lambda t: -t[0])
    used = "single"
    if len(volumes) > 1:
        if bodies == "all":
            used = "all"
            shape = _compound([s for _, s in volumes])
        else:
            used = "largest"
            shape = volumes[0][1]
    elif len(volumes) == 1:
        shape = volumes[0][1]
    # no solid at all: keep whatever faces there are so the STL check can say "holes"

    lo, hi = _bounds(shape)
    diag = float(np.linalg.norm(hi - lo))
    if not np.isfinite(diag) or diag <= 0:
        raise BadFile("the part has no size (its bounding box is empty)")
    deflection, angular = DEFLECTION_FRACTION * diag, ANGULAR_DEFLECTION
    tris = tessellate(shape, deflection, angular)
    for _ in range(COARSEN_STEPS):
        if len(tris) <= max_triangles:
            break
        # both knobs: on fillets and cylinders the angular deflection decides the count
        deflection, angular = deflection * 2.0, min(1.2, angular * 1.5)
        tris = tessellate(shape, deflection, angular)
    if len(tris) == 0:
        raise BadFile("the STEP file contains no surfaces to mesh")
    if len(tris) > max_triangles:
        raise BadFile(f"the part has too much detail ({len(tris):,d} triangles even at a coarse setting; the limit "
                      f"is {max_triangles:,d})")

    # Back to the file's own units (the parent scales with the units the student confirms).
    factor = unit["factor"]
    native = factor if (factor and unit["unit"]) else 1.0
    tris_native = tris / native
    write_stl(out_path, tris_native)
    lo_n, hi_n = lo / native, hi / native
    body_sizes = []
    for v, s in volumes:
        a, b = _bounds(s)
        body_sizes.append({"volume": float(v / native**3), "longest": float(np.max(b - a) / native)})
    return {
        "format": "step",
        "unit_declared": unit["unit"] if (factor and unit["unit"]) else ("m" if factor else None),
        "unit_name_in_file": unit["name"],
        "unit_factor": factor,
        "converted_to_metres": bool(factor and not unit["unit"]),
        "entities": n_entities,
        "solids": len(solids_in),
        "shells": shells_in,
        "faces": faces_in,
        "faces_used": _count(shape, TopAbs_FACE),
        "healed": healed,
        "valid_before": valid_before,
        "valid_after": valid_after,
        "fused": fused,
        "bodies_after_fuse": len(volumes),
        "bodies_used": used,
        "bodies": body_sizes,
        "triangles": int(len(tris)),
        "deflection": float(deflection / native),
        "bounds_min": lo_n.tolist(),
        "bounds_max": hi_n.tolist(),
    }


def main(argv: list[str]) -> int:
    path, out_path, max_triangles, bodies = argv[1], argv[2], int(argv[3]), argv[4]
    # OpenCASCADE writes progress and statistics to stdout; keep the original stdout for our JSON only
    real_out = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def emit(obj) -> None:
        os.write(real_out, (json.dumps(obj) + "\n").encode())

    try:
        import OCP  # noqa: F401
    except ImportError as e:
        emit({"error": f"OpenCASCADE (OCP) could not be loaded: {e}", "missing": True})
        return 4
    try:
        emit(process(path, out_path, max_triangles, bodies))
    except BadFile as e:
        emit({"error": str(e)})
        return 2
    except MemoryError:
        emit({"error": "the file needs more memory to read than allowed"})
        return 3
    except Exception as e:  # noqa: BLE001 - OpenCASCADE raises plain RuntimeError / Standard_Failure
        emit({"error": f"OpenCASCADE could not process this file ({type(e).__name__}: {str(e)[:200]})"})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
