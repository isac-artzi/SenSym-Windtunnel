"""Small STEP files made with OpenCASCADE for the tests (and the robustness corpus).

Everything here needs ``OCP`` (``pip install ".[step]"``); import it only inside tests that
skip when OCP is missing (``pytest.importorskip("OCP")``).

Shapes are built in **millimetres** (OpenCASCADE's working unit); :func:`write_step` converts
them into the unit the file should declare, so ``write_step(p, box(304.8, 25.4, 25.4), "in")``
is a 12 x 1 x 1 inch bar whose header says INCH.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

# OCCT's names for the unit a STEP file is written in (Interface_Static "write.step.unit").
WRITE_UNITS = {"mm": "MM", "cm": "CM", "m": "M", "in": "INCH", "ft": "FT"}


def write_step(path: Path, shape, unit: str = "mm") -> Path:
    """Write ``shape`` (built in millimetres) as a STEP file in ``unit`` (declared in its header)."""
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    w = STEPControl_Writer()  # the static parameters exist only once a writer has been made
    assert Interface_Static.SetCVal_s("write.step.unit", WRITE_UNITS[unit])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _quiet_stdout():  # OpenCASCADE prints transfer statistics straight to fd 1
        w.Transfer(shape, STEPControl_AsIs)
        w.Write(str(path))
    return path


@contextlib.contextmanager
def _quiet_stdout():
    import os
    import sys

    sys.stdout.flush()
    saved = os.dup(1)
    try:
        with open(os.devnull, "wb") as null:
            os.dup2(null.fileno(), 1)
        yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)


def write_stl(path: Path, shape, deflection: float = 0.1, angular: float = 0.3) -> Path:
    """Write ``shape`` as a binary STL in millimetres (what SolidWorks' STL export gives)."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.StlAPI import StlAPI_Writer

    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, float(deflection), False, float(angular), True)
    w = StlAPI_Writer()
    w.ASCIIMode = False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _quiet_stdout():
        w.Write(shape, str(path))
    return path


def box(dx: float, dy: float, dz: float, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*at), dx, dy, dz).Shape()


def cylinder(radius: float, height: float, axis: str = "z", at=(0.0, 0.0, 0.0)):
    """A cylinder along ``axis`` starting at ``at``."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    d = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(*d)), radius, height).Shape()


def compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    comp, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def fuse(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse

    return BRepAlgoAPI_Fuse(a, b).Shape()


def cut(a, b):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    return BRepAlgoAPI_Cut(a, b).Shape()


def fillet_all(shape, radius: float):
    """Round every edge of a solid (fails on some shapes: then the caller keeps the sharp one)."""
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    f = BRepFilletAPI_MakeFillet(shape)
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        f.Add(radius, TopoDS.Edge(ex.Current()))
        ex.Next()
    f.Build()
    return f.Shape() if f.IsDone() else shape


def faces_only(shape):
    """The faces of a solid as a shell-less compound (a 'surface body' export without a solid)."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    faces, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        faces.append(ex.Current())
        ex.Next()
    return compound(*faces[:-1])  # one face left out: an open surface


def translate(shape, dx: float, dy: float, dz: float):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec

    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def rotate(shape, axis: str, degrees: float, about=(0.0, 0.0, 0.0)):
    import math

    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf

    d = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(*about), gp_Dir(*d)), math.radians(degrees))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def extrude_polygon(points_xz, length_y: float, y0: float = 0.0):
    """A prism: a closed polygon in the x-z plane (list of (x, z)) extruded along y."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec

    wire = BRepBuilderAPI_MakeWire()
    pts = [gp_Pnt(x, y0, z) for x, z in points_xz]
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        wire.Add(BRepBuilderAPI_MakeEdge(a, b).Edge())
    face = BRepBuilderAPI_MakeFace(wire.Wire(), True).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, length_y, 0)).Shape()


def extrude_spline(points_xz, length_y: float, y0: float = 0.0):
    """A prism whose section is a smooth closed curve through the points (x, z)."""
    from OCP.Approx import Approx_ChordLength
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.collections import Array1_gp_Pnt
    from OCP.GeomAPI import GeomAPI_PointsToBSpline
    from OCP.gp import gp_Pnt, gp_Vec

    # Two spline edges (split at the point farthest from the first one, e.g. the leading edge of an
    # airfoil whose outline starts at the trailing edge) make a closed wire that bounds a planar face.
    pts = [(float(x), float(z)) for x, z in points_xz]
    x0, z0 = pts[0]
    k = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - z0) ** 2)
    halves = [pts[: k + 1], pts[k:] + pts[:1]]
    keep = []  # every builder stays alive until the prism exists (OCP handles are owned by their builders)
    wire_b = BRepBuilderAPI_MakeWire()
    for half in halves:
        arr = Array1_gp_Pnt(1, len(half))
        for i, (x, z) in enumerate(half, start=1):
            arr.SetValue(i, gp_Pnt(x, y0, z))
        approx = GeomAPI_PointsToBSpline(arr, Approx_ChordLength, 3, 8)
        if not approx.IsDone():
            raise ValueError("the section points could not be fitted with a B-spline")
        edge_b = BRepBuilderAPI_MakeEdge(approx.Curve())
        keep += [arr, approx, edge_b]
        wire_b.Add(edge_b.Edge())
    face_b = BRepBuilderAPI_MakeFace(wire_b.Wire(), True)
    if not face_b.IsDone():
        raise ValueError("the section curve does not bound a face")
    prism = BRepPrimAPI_MakePrism(face_b.Face(), gp_Vec(0, length_y, 0))
    return prism.Shape()


def revolve_polygon(points_xr):
    """A body of revolution about the x axis from a polygon of (x, r) points, r >= 0."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    wire = BRepBuilderAPI_MakeWire()
    pts = [gp_Pnt(x, 0.0, r) for x, r in points_xr]
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        if a.Distance(b) > 1e-9:
            wire.Add(BRepBuilderAPI_MakeEdge(a, b).Edge())
    face = BRepBuilderAPI_MakeFace(wire.Wire(), True).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0))).Shape()


def sphere(radius: float, at=(0.0, 0.0, 0.0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeSphere(gp_Pnt(*at), radius).Shape()
