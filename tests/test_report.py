"""Report export (PRD RES-4): Markdown and printable HTML for single runs and sweeps, pictures, no external refs."""

from __future__ import annotations

import base64
import re
import zlib

import numpy as np
import pytest

from tests.asgi_client import make_client
from tests.test_app import CUBE_F, CUBE_V, FakeSolver, naca
from windlab import __version__
from windlab.app import Settings, create_app, raster, report, security
from windlab.geometry.stl import write_binary_stl


@pytest.fixture
def env(tmp_path):
    a = create_app(Settings(data_dir=tmp_path / "d", runner=FakeSolver()))
    c = make_client(a)
    yield a, c
    a.fastapi.state.runs.stop()


def _done_run(a, c, **body):
    gid = body.pop("geometry_id", None) or naca(c).json()["geometry_id"]
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, **body}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    return rid


def _external_refs(text: str) -> list[str]:
    """src/href/url() that would fetch anything from the network or the local server."""
    refs = re.findall(r"""(?:src|href)=["']([^"']+)["']""", text) + re.findall(r"url\(([^)]+)\)", text)
    return [r for r in refs if not r.startswith(("data:", "#"))]


# --------------------------------------------------------------------------- single run


def test_single_run_markdown_report(env):
    a, c = env
    rid = _done_run(a, c, alpha_deg=10)
    r = c.get(f"/api/runs/{rid}/report.md")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    assert re.fullmatch(r'attachment; filename="windlab-report-single-\d{4}-\d\d-\d\d-[0-9a-f]{8}\.md"',
                        r.headers["content-disposition"])
    md = r.text
    # banner, provenance
    assert md.startswith("# WindLab report: NACA 0012")
    assert "Uncalibrated prototype" in md and __version__ in md and "commit" in md and rid in md
    # inputs
    for needle in ("| Shape |", "NACA 0012 wing", "built-in NACA wing", "| Airspeed | 20 m/s",
                   "| Angle of attack | 10°", "| Reynolds number |", "| Reference area and length |", "0.18 m²",
                   "planform area", "| Moment reference point |", "sea level, 15 °C"):
        assert needle in md, needle
    # mesh
    for needle in ("## Mesh", "| Mesh preset | Quick (quick)", "| Cells | 371,000", "| Cell sizes |",
                   "boundary-layer cells", "| Quality checks (checkMesh) | all passed", "| Boundary layers |"):
        assert needle in md, needle
    # convergence
    assert "## Convergence" in md and "| Solver iterations | 300 of at most 1500" in md
    assert "stopped by WindLab's settling rule after 300 iterations" in md and "0.25 % (CL) and 0.5 % (CD)" in md
    # results with ranges
    assert "| Lift coefficient CL | 0.45 |" in md and "| Drag coefficient CD | 0.05 |" in md
    assert "likely" in md and "Why these ranges?" in md and "N of lift" in md
    # charts: two inline SVGs drawn in Python
    assert md.count("<svg") >= 2 and "CL against iteration" in md and "CD against iteration" in md
    assert "<polyline" not in md and "<path d=\"M" in md
    # pictures: two inline PNGs (surface with Cp, flow slice) with colour legends
    pngs = re.findall(r"!\[[^\]]*\]\(data:image/png;base64,([A-Za-z0-9+/=]+)\)", md)
    assert len(pngs) == 2
    for b in pngs:
        raw = base64.b64decode(b)
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert "pressure coefficient Cp" in md and "air speed ÷ wind speed" in md
    # validation note, third-party notices
    assert "## How much can these numbers be trusted?" in md and "turbulent from the leading edge" in md
    assert "Validation table version" in md
    assert "GNU General Public License v3" in md and "registered trade mark of OpenCFD Limited" in md
    # the trade-mark disclaimer OpenCFD asks for, and the GPL sentence, in the report body (not only the notice)
    assert ("This offering is not approved or endorsed by OpenCFD Limited, producer and distributor of the OpenFOAM "
            "software via www.openfoam.com, and owner of the OPENFOAM® and OpenCFD® trade marks.") in md
    assert "OpenFOAM is distributed under the GNU GPL v3." in md and "developer using" not in md
    assert "three.js" in md and "OpenCASCADE" in md
    # nothing points at the network
    assert not _external_refs(md), _external_refs(md)
    assert "http://" not in md.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in md.replace("https://www.openfoam.com", "").replace("https://develop.openfoam.com", "")


def test_single_run_html_report_is_printable_and_self_contained(env):
    a, c = env
    rid = _done_run(a, c, alpha_deg=10)
    r = c.get(f"/api/runs/{rid}/report.html")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"].startswith("inline;")
    # its own policy: inline styles and data: images, no scripts, nothing from anywhere
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp and "img-src data:" in csp
    assert "script-src" not in csp.replace("default-src", "") or "'self'" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"  # the other security headers stay
    page = r.text
    assert page.startswith("<!doctype html>") and "<script" not in page and "<link" not in page
    assert "@media print" in page and "Save as PDF" in page
    assert not _external_refs(page), _external_refs(page)
    for needle in ("<h2>Inputs</h2>", "<h2>Mesh</h2>", "<h2>Convergence</h2>", "<h2>Results</h2>", "<h2>Charts</h2>",
                   "<h2>Pictures</h2>", "How much can these numbers be trusted?", "Third-party software",
                   "Uncalibrated prototype", "NACA 0012", "likely", "not approved or endorsed by OpenCFD Limited",
                   "OpenFOAM is distributed under the GNU GPL v3."):
        assert needle in page, needle
    assert "developer using" not in page
    assert page.count("<svg") >= 4  # two charts + two colour legends
    assert page.count('src="data:image/png;base64,') == 2
    assert 'alt="Pressure coefficient Cp on the part' in page


def test_report_of_an_upload_and_escaping_of_user_text(env):
    """An uploaded part's units and size are in the report; the display name (user text, already
    sanitised on upload) is escaped once more by the HTML renderer, belt and braces."""
    a, c = env
    p = a.fastapi.state.settings.data_dir / "cube.stl"
    write_binary_stl(p, CUBE_V * 300.0, CUBE_F)
    g = c.post("/api/geometry", params={"units": "mm", "name": "<b>evil</b> & co.stl"}, content=p.read_bytes()).json()
    assert g["usable"] and "<" not in g["name"]
    rid = _done_run(a, c, geometry_id=g["geometry_id"], alpha_deg=0)
    md = c.get(f"/api/runs/{rid}/report.md").text
    assert "uploaded part" in md and "| Units of the uploaded file | millimetres" in md
    assert "30 cm × 30 cm × 30 cm" in md and "| Surface triangles | 12 |" in md
    # bypass the upload sanitiser: a raw name in the run's stored parameters must still come out escaped
    st = a.fastapi.state.runs.get(rid)
    st.params["geometry_name"] = "<b>evil</b> & co"
    page = c.get(f"/api/runs/{rid}/report.html").text
    assert "<b>evil</b>" not in page and "&lt;b&gt;evil&lt;/b&gt; &amp; co" in page


# --------------------------------------------------------------------------- sweep


def test_sweep_report(env):
    a, c = env
    rid = _done_run(a, c, kind="sweep", angles=[-4, 4, 12, 16])
    md = c.get(f"/api/runs/{rid}/report.md").text
    assert "Angle sweep made" in md and "| Angles of attack | -4°, 4°, 12°, 16°" in md
    assert "| angle (°) | CL | CL likely | CD | CD likely | CM | L/D | iterations | settled | why it stopped |" in md
    assert md.count("stopped by WindLab's settling rule") == 3 and "reached the iteration cap" in md  # 16 deg
    assert "The best lift-to-drag ratio" in md
    assert "CL against angle of attack" in md and "Drag polar" in md and "L/D against angle of attack" in md
    assert "best L/D at" in md
    assert len(re.findall(r"data:image/png;base64,", md)) == 2  # the best-L/D angle's pictures
    page = c.get(f"/api/runs/{rid}/report.html").text
    assert page.count("<tr><td class=\"num\">") == 4 and not _external_refs(page)
    disposition = c.get(f"/api/runs/{rid}/report.md").headers["content-disposition"]
    assert re.search(r'filename="windlab-report-sweep-', disposition)


def test_report_needs_a_finished_run(env):
    a, c = env
    gid = naca(c).json()["geometry_id"]
    solver = a.fastapi.state.runs.runner
    solver.fail_stage = "snappyHexMesh"
    rid = c.post("/api/runs", json={"geometry_id": gid, "airspeed": 20, "alpha_deg": 0}).json()["run_id"]
    assert a.fastapi.state.runs.wait_idle(20)
    r = c.get(f"/api/runs/{rid}/report.md")
    assert r.status_code == 409 and "finished" in r.json()["detail"]
    assert c.get(f"/api/runs/{rid[:-1]}0/report.html").status_code in (400, 404)


# --------------------------------------------------------------------------- the pieces


def test_svg_chart_and_legend_are_well_formed():
    svg = report.svg_chart([{"points": [(0, 0.1), (100, 0.45), (200, 0.451)]}], "iteration", "CL", title="CL")
    assert svg.startswith("<svg") and svg.endswith("</svg>") and svg.count("<svg") == svg.count("</svg>") == 1
    assert 'aria-label="CL against iteration"' in svg and "<path d=" in svg and "rotate(-90)" in svg
    assert ">0.4</text>" in svg and ">100</text>" in svg  # tick labels
    empty = report.svg_chart([{"points": []}], "x", "y")
    assert "no data" in empty
    hl = report.svg_chart([{"points": [(0, 1), (4, 9), (8, 5)], "markers": True}], "angle", "L/D",
                          highlight=(4, 9, "best"))
    assert "<circle" in hl and ">best</text>" in hl
    leg = report.svg_legend(-1.0, 1.0, raster.DIVERGING, "Cp")
    assert "<linearGradient" in leg and leg.count("<stop") == 9 and "−1" in leg
    assert report.nice_ticks(0, 1)[0] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def test_png_encoder_and_rasteriser():
    canvas = raster.new_canvas(20, 10)
    raster.draw_triangles(canvas, np.array([[[0, 0], [20, 0], [0, 10]]], float), np.array([[[1, 0, 0]] * 3], float))
    raster.draw_triangles(canvas, np.array([[[5.2, 5.2], [5.6, 5.4], [5.3, 5.7]]], float),
                          np.array([[[0, 0, 1]] * 3], float))  # tiny: one pixel
    assert canvas[0, 0].tolist() == [1, 0, 0] and canvas[5, 5].tolist() == [0, 0, 1]
    assert canvas[9, 19].tolist() == list(raster.BACKGROUND_RGB)
    raster.draw_polyline(canvas, np.array([[0, 9], [19, 9]], float), (0, 1, 0))
    assert canvas[9, 10].tolist() == [0, 1, 0]
    png = raster.png_bytes(canvas)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")
    # decode the IDAT back: height rows of (filter byte + 3 * width)
    i = png.index(b"IDAT")
    n = int.from_bytes(png[i - 4:i], "big")
    raw = zlib.decompress(png[i + 4:i + 4 + n])
    assert len(raw) == 10 * (1 + 20 * 3) and raw[1:4] == b"\xff\x00\x00"
    assert raster.colormap(np.array([0.0, 1.0]), raster.VIRIDIS).round(3).tolist() == [[0.267, 0.004, 0.329],
                                                                                          [0.992, 0.906, 0.145]]
    assert raster.speed_range(np.linspace(0, 1.3, 100)) == (0.0, 1.5)
    assert raster.cp_symmetric_range(np.array([-0.9, 0.3, 0.4])) == (-1.0, 1.0)


def test_render_surface_and_slice_from_small_blobs():
    from windlab.app import slice as flow_slice
    from windlab.app import surface

    verts = CUBE_V * 0.3
    cp = np.linspace(-1, 1, len(verts))
    pic = raster.render_surface(surface.decode(surface.encode(verts, CUBE_F, cp)), width=120)
    assert pic["png"].startswith(b"\x89PNG") and pic["width"] == 120 and 60 <= pic["height"] <= 200
    assert pic["lo"] == -1.0 and pic["hi"] == 1.0
    xz = np.array([[0, 0], [1, 0], [1, 0.5], [0, 0.5]], float)
    pts = np.column_stack([xz[:, 0], np.zeros(4), xz[:, 1]])
    blob = flow_slice.encode(pts, np.array([[0, 1, 2], [0, 2, 3]]), np.array([0.0, 1.0, 1.2, 0.5]), np.zeros(4))
    sl = raster.render_slice(flow_slice.decode(blob), [0, 1, 0, 0.5], width=100, streamlines=[[[0, 0.25], [1, 0.25]]])
    assert sl["png"].startswith(b"\x89PNG") and sl["height"] == 50 and sl["lo"] == 0.0


def test_middleware_keeps_a_routes_own_csp_only():
    assert "content-security-policy" in {k.decode() for k, _ in security.SECURITY_HEADERS}
    from windlab.app.server import REPORT_CSP

    assert "script-src" not in REPORT_CSP and "default-src 'none'" in REPORT_CSP
