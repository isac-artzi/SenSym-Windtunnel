"""STL upload checks: parsing in a limited subprocess, topology checks, units."""

from __future__ import annotations

import numpy as np
import pytest

from windlab.geometry import upload
from windlab.geometry.naca import naca_wing
from windlab.geometry.stl import write_binary_stl
from windlab.geometry.upload import UploadError, check_stl

# Unit cube, outward-facing triangles (counter-clockwise seen from outside).
CUBE_V = np.array(
    [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float
)
CUBE_F = np.array(
    [
        [0, 2, 1], [0, 3, 2],  # bottom z=0
        [4, 5, 6], [4, 6, 7],  # top z=1
        [0, 1, 5], [0, 5, 4],  # y=0
        [2, 3, 7], [2, 7, 6],  # y=1
        [1, 2, 6], [1, 6, 5],  # x=1
        [3, 0, 4], [3, 4, 7],  # x=0
    ]
)  # fmt: skip


def write_ascii_stl(path, vertices, faces, normals=True):
    lines = ["solid test"]
    for f in faces:
        a, b, c = vertices[f]
        n = np.cross(b - a, c - a)
        n = n / (np.linalg.norm(n) or 1.0) if normals else np.zeros(3)
        lines.append(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}")
        lines.append("    outer loop")
        for p in (a, b, c):
            lines.append(f"      vertex {p[0]:.9e} {p[1]:.9e} {p[2]:.9e}")
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid test")
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def cube_mm(tmp_path):
    """A 300 mm cube, binary STL in millimetres."""
    p = tmp_path / "cube.stl"
    write_binary_stl(p, CUBE_V * 300.0, CUBE_F)
    return p


def test_good_cube_binary(cube_mm):
    r = check_stl(cube_mm, "mm")
    assert r.ok and r.watertight and r.manifold and r.consistent_normals and r.outward_normals
    assert r.format == "binary" and r.triangles == 12 and r.pieces == 1
    assert r.size_m == pytest.approx((0.3, 0.3, 0.3))
    assert r.volume_m3 == pytest.approx(0.027)
    assert not r.size_suspicious and not r.problems and not r.warnings
    assert "30 cm x 30 cm x 30 cm" in r.messages[0]


def test_good_cube_ascii(tmp_path):
    p = tmp_path / "cube_ascii.stl"
    write_ascii_stl(p, CUBE_V * 0.2, CUBE_F)
    r = check_stl(p, "m")
    assert r.ok and r.format == "ascii" and r.triangles == 12
    assert r.longest_m == pytest.approx(0.2)


def test_naca_wing_is_clean(tmp_path):
    w = naca_wing("2412", chord=0.3, span=0.6, taper=0.5)
    p = tmp_path / "wing.stl"
    w.write_stl(p)
    r = check_stl(p, "m")
    assert r.ok and r.outward_normals and r.pieces == 1, r.problems
    assert r.size_m[1] == pytest.approx(0.6, rel=1e-6)


def test_open_box_is_not_watertight(tmp_path):
    p = tmp_path / "open.stl"
    write_binary_stl(p, CUBE_V * 100.0, CUBE_F[2:])  # no bottom
    r = check_stl(p, "mm")
    assert not r.ok and not r.watertight
    assert r.counts["open_edges"] == 4
    assert any("holes" in m for m in r.problems)


def test_flipped_triangle_is_inconsistent(tmp_path):
    f = CUBE_F.copy()
    f[0] = f[0][::-1]
    p = tmp_path / "flip.stl"
    write_binary_stl(p, CUBE_V * 100.0, f)
    r = check_stl(p, "mm")
    assert r.watertight and not r.consistent_normals and not r.ok
    assert r.counts["inconsistent_edges"] == 3


def test_inside_out_is_a_warning(tmp_path):
    p = tmp_path / "inside_out.stl"
    write_binary_stl(p, CUBE_V * 100.0, CUBE_F[:, ::-1])
    r = check_stl(p, "mm")
    assert r.ok and not r.outward_normals
    assert any("inside-out" in w for w in r.warnings)


def test_two_cubes_sharing_an_edge_are_non_manifold(tmp_path):
    v = np.vstack([CUBE_V, CUBE_V + [1.0, 1.0, 0.0]])  # touch along the edge x=y=1
    f = np.vstack([CUBE_F, CUBE_F + 8])
    p = tmp_path / "touching.stl"
    write_binary_stl(p, v * 100.0, f)
    r = check_stl(p, "mm")
    assert not r.manifold and not r.ok
    assert r.counts["nonmanifold_edges"] == 1


def test_two_separate_cubes_are_two_pieces(tmp_path):
    v = np.vstack([CUBE_V, CUBE_V + [3.0, 0.0, 0.0]])
    p = tmp_path / "two.stl"
    write_binary_stl(p, v * 100.0, np.vstack([CUBE_F, CUBE_F + 8]))
    r = check_stl(p, "mm")
    assert r.ok and r.pieces == 2
    assert any("2 separate pieces" in w for w in r.warnings)


def test_overlapping_bodies_cross_each_other(tmp_path):
    """Two cubes exported overlapping (no boolean union): the surfaces cut through each other."""
    v = np.vstack([CUBE_V, CUBE_V + 0.5])
    p = tmp_path / "overlap.stl"
    write_binary_stl(p, v * 100.0, np.vstack([CUBE_F, CUBE_F + 8]))
    out = tmp_path / "body.stl"
    r = check_stl(p, "mm", output=out)
    assert r.watertight and r.manifold and r.consistent_normals  # each shell is fine on its own
    assert r.self_intersecting is True and r.self_intersection_check == "done"
    assert r.counts["self_intersections"] == 6 and not r.ok and not out.exists()
    msg = next(m for m in r.problems if "cross each other" in m)
    assert "one solid body" in msg and "Combine" in msg


def test_touching_and_separate_bodies_do_not_cross(tmp_path):
    for offset in ([1.0, 0.0, 0.0], [3.0, 0.0, 0.0]):
        v = np.vstack([CUBE_V, CUBE_V + offset])
        p = tmp_path / "pair.stl"
        p.unlink(missing_ok=True)
        write_binary_stl(p, v * 100.0, np.vstack([CUBE_F, CUBE_F + 8]))
        r = check_stl(p, "mm")
        assert r.self_intersecting is False and r.counts["self_intersections"] == 0
        assert not any("cross" in m for m in r.problems + r.warnings)


def test_spike_through_a_face_is_a_crossing(tmp_path):
    v = np.vstack([CUBE_V, [[0.5, 0.5, -0.5], [0.6, 0.5, -0.5], [0.5, 0.6, 1.5]]])
    p = tmp_path / "spike.stl"
    write_binary_stl(p, v, np.vstack([CUBE_F, [[8, 9, 10]]]))
    r = check_stl(p, "m")
    assert r.self_intersecting is True and r.counts["self_intersections"] == 3


def test_thin_wings_and_bodies_give_no_false_crossings(tmp_path):
    from windlab.geometry.shapes import make_body

    shapes = [naca_wing("0006", chord=0.3, span=0.6), naca_wing("2412", chord=0.3, span=0.6, taper=0.4)]
    shapes = [s.__dict__ for s in shapes] + [make_body(s).__dict__ for s in ("sphere", "cylinder", "teardrop", "plate")]
    for i, s in enumerate(shapes):
        p = tmp_path / f"shape{i}.stl"
        write_binary_stl(p, s["vertices"], s["faces"])
        r = check_stl(p, "m")
        assert r.ok and r.self_intersecting is False and r.self_intersection_check == "done", (i, r.problems)


def test_crossing_check_is_skipped_or_timed_out_with_a_warning(tmp_path):
    from windlab.geometry import _stl_worker as worker

    tris = CUBE_V[CUBE_F]
    stats, keep, faces, tol = worker.analyse(tris, np.zeros((12, 3)))
    assert worker.self_intersections(tris, faces, keep, tol, max_triangles=5) == ("skipped", 0)
    big = naca_wing("0012", chord=0.3, span=0.6)
    stats, keep, faces, tol = worker.analyse(big.vertices[big.faces], np.zeros((len(big.faces), 3)))
    status, _ = worker.self_intersections(big.vertices[big.faces], faces, keep, tol, seconds=0.0)
    assert status == "timed out"
    # the report turns both into a plain warning (not a problem): the part stays usable
    raw = {**stats, "format": "binary", "self_intersection_check": "skipped", "self_intersections": 0}
    r = upload.build_report(raw, "m")
    assert r.ok and r.self_intersecting is None
    assert any("too many triangles" in w and "cross" in w for w in r.warnings)
    raw["self_intersection_check"] = "timed out"
    r = upload.build_report(raw, "m")
    assert r.ok and r.self_intersecting is None and any("ran out of time" in w for w in r.warnings)


def test_wrong_units_metres_for_a_mm_part(cube_mm):
    r = check_stl(cube_mm, "m")  # 300 "metres"
    assert r.size_suspicious and r.suggested_units == "mm"
    assert r.longest_m == pytest.approx(300.0)
    assert "This part would be 300 m long - did you mean millimetres?" in r.warnings


def test_wrong_units_mm_for_a_metre_part(tmp_path):
    p = tmp_path / "small.stl"
    write_binary_stl(p, CUBE_V * 0.5, CUBE_F)  # 0.5 m part, but user says mm
    r = check_stl(p, "mm")
    assert r.size_suspicious and r.suggested_units == "m"
    assert any("0.5 mm long - did you mean metres?" in w for w in r.warnings)


def test_inches_are_converted(tmp_path):
    p = tmp_path / "inch.stl"
    write_binary_stl(p, CUBE_V * 10.0, CUBE_F)
    r = check_stl(p, "in")
    assert r.longest_m == pytest.approx(0.254) and not r.size_suspicious


def test_units_must_be_given(cube_mm):
    with pytest.raises(UploadError, match="units"):
        check_stl(cube_mm, "furlongs")


def test_file_size_limit(cube_mm):
    with pytest.raises(UploadError, match="limit"):
        check_stl(cube_mm, "mm", max_bytes=100)


def test_triangle_limit_checked_in_worker(cube_mm):
    with pytest.raises(UploadError, match="too many triangles"):
        check_stl(cube_mm, "mm", max_triangles=10)


def test_garbage_is_rejected(tmp_path):
    p = tmp_path / "junk.stl"
    p.write_bytes(b"\x00\x01PK not an stl" * 50)
    with pytest.raises(UploadError, match="not an STL"):
        check_stl(p, "mm")


def test_truncated_binary_is_rejected(cube_mm, tmp_path):
    p = tmp_path / "cut.stl"
    p.write_bytes(cube_mm.read_bytes()[:-30])
    with pytest.raises(UploadError):
        check_stl(p, "mm")


def test_ascii_with_bad_number(tmp_path):
    p = tmp_path / "bad.stl"
    write_ascii_stl(p, CUBE_V, CUBE_F)
    p.write_text(p.read_text().replace("vertex 0.000000000e+00", "vertex NaNx", 1))
    with pytest.raises(UploadError):
        check_stl(p, "m")


def test_empty_and_missing_files(tmp_path):
    empty = tmp_path / "empty.stl"
    empty.write_bytes(b"")
    with pytest.raises(UploadError, match="empty"):
        check_stl(empty, "mm")
    with pytest.raises(UploadError, match="not found"):
        check_stl(tmp_path / "nope.stl", "mm")


def test_symlink_is_refused(cube_mm, tmp_path):
    link = tmp_path / "link.stl"
    link.symlink_to(cube_mm)
    with pytest.raises(UploadError, match="not found"):
        check_stl(link, "mm")


def test_worker_wall_timeout(cube_mm, monkeypatch):
    monkeypatch.setattr(upload, "_WORKER", upload._WORKER.with_name("does_not_matter.py"))
    with pytest.raises(UploadError):
        check_stl(cube_mm, "mm")
    monkeypatch.undo()
    with pytest.raises(UploadError, match="too long"):
        check_stl(cube_mm, "mm", wall_seconds=0.001)


def test_worker_memory_limit_applies(tmp_path, monkeypatch):
    """A worker that tries to allocate far more than RLIMIT_AS is stopped, not the server."""
    hog = tmp_path / "hog.py"
    hog.write_text("x = bytearray(8 * 1024**3)\nprint('{}')\n")
    monkeypatch.setattr(upload, "_WORKER", hog)
    p = tmp_path / "any.stl"
    p.write_bytes(b"solid x\nendsolid x\n")
    with pytest.raises(UploadError, match="too big or too complex"):
        check_stl(p, "mm")


def _read_binary(path):
    data = path.read_bytes()
    n = int.from_bytes(data[80:84], "little")
    assert len(data) == 84 + 50 * n
    rec = np.frombuffer(data, dtype=upload_dtype(), count=n, offset=84)
    return rec["v"].astype(float)


def upload_dtype():
    return np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def test_scaled_copy_is_in_metres(cube_mm, tmp_path):
    out = tmp_path / "out" / "body.stl"
    out.parent.mkdir()
    r = check_stl(cube_mm, "mm", output=out)
    assert r.ok and out.is_file()
    v = _read_binary(out)
    assert v.shape == (12, 3, 3)
    assert v.max() == pytest.approx(0.3, rel=1e-6) and v.min() == pytest.approx(0.0)
    assert not out.read_bytes().startswith(b"solid")


def test_scaled_copy_turns_inside_out_surface_round(tmp_path):
    src = tmp_path / "inside_out.stl"
    write_binary_stl(src, CUBE_V * 10.0, CUBE_F[:, ::-1])
    out = tmp_path / "body.stl"
    r = check_stl(src, "in", output=out)
    assert r.ok and not r.outward_normals
    t = _read_binary(out)
    vol = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0
    assert vol == pytest.approx(0.254**3, rel=1e-5)


def test_scaled_copy_drops_zero_area_triangles(tmp_path):
    v = np.vstack([CUBE_V, [[0.5, 0.5, 0.0]]])
    f = np.vstack([CUBE_F, [[0, 1, 1]]])  # collapsed triangle on an existing edge
    src = tmp_path / "degen.stl"
    write_binary_stl(src, v * 100.0, f)
    out = tmp_path / "body.stl"
    r = check_stl(src, "mm", output=out)
    assert r.counts["degenerate_triangles"] == 1
    assert len(_read_binary(out)) == 12


def test_no_scaled_copy_for_a_bad_part(tmp_path):
    src = tmp_path / "open.stl"
    write_binary_stl(src, CUBE_V * 100.0, CUBE_F[2:])
    out = tmp_path / "body.stl"
    r = check_stl(src, "mm", output=out)
    assert not r.ok and not out.exists()


def test_existing_output_is_refused(cube_mm, tmp_path):
    out = tmp_path / "body.stl"
    out.write_bytes(b"x")
    with pytest.raises(UploadError):
        check_stl(cube_mm, "mm", output=out)


def test_worker_started_through_prlimit_without_preexec(cube_mm, monkeypatch):
    seen = {}
    real_run = upload.subprocess.run

    def spy(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return real_run(argv, **kw)

    monkeypatch.setattr(upload.subprocess, "run", spy)
    check_stl(cube_mm, "mm")
    assert seen["argv"][0].endswith("prlimit") and "--fsize=0" in seen["argv"]
    assert "preexec_fn" not in seen["kw"]


def test_checks_from_many_threads(cube_mm):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as ex:
        reports = list(ex.map(lambda _: check_stl(cube_mm, "mm"), range(16)))
    assert all(r.ok for r in reports)
