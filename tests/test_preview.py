"""The instant 2D preview (PRD Tier 0): static checks of the JavaScript and a numpy mirror of its physics."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from tests import lbm_mirror
from tests.jscheck import check_brackets, element_ids
from windlab.geometry.naca import _THICKNESS_COEFFS, naca4_section
from windlab.geometry.shapes import PLATE_THICKNESS

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "windlab" / "app" / "static"
PREVIEW = (STATIC / "preview.js").read_text()
SECTION = (STATIC / "section.js").read_text()
APP = (STATIC / "app.js").read_text()
HTML = (STATIC / "index.html").read_text()


def test_scripts_are_well_formed_and_use_existing_ids():
    check_brackets(PREVIEW)
    check_brackets(SECTION)
    ids = set(re.findall(r'\bid="([\w-]+)"', HTML))
    assert element_ids(PREVIEW) <= ids, element_ids(PREVIEW) - ids
    assert "http://" not in PREVIEW and "https://" not in PREVIEW and "fetch(" not in PREVIEW  # no server work


def test_outline_formulas_match_the_python_generators():
    coeffs = re.search(r"const THICKNESS = \[(.*?)\];", SECTION).group(1)
    assert [float(c) for c in coeffs.split(",")] == list(_THICKNESS_COEFFS)
    assert f"const PLATE_THICKNESS = {PLATE_THICKNESS};" in SECTION
    # the NACA outline runs trailing edge -> upper -> leading edge -> lower, as naca4_section does
    assert "upper.reverse(), ...lower.slice(1, -1)" in SECTION
    assert naca4_section("0012", 80)[0].tolist() == [1.0, 0.0]


def test_lattice_constants_match_the_mirror():
    for name, value in (("U_IN", lbm_mirror.U_IN), ("TAU0", lbm_mirror.TAU0), ("CS", lbm_mirror.CS)):
        assert re.search(rf"export const {name} = {value};", PREVIEW), name
    assert "const EX = [0, 1, 0, -1, 0, 1, -1, -1, 1];" in PREVIEW
    assert "const OPP = [0, 3, 4, 1, 2, 7, 8, 5, 6];" in PREVIEW
    assert "sqrt(tau0 * tau0 + cs2 * Q / rho)" in PREVIEW  # Smagorinsky relaxation time in the shader
    assert "18 * CS * CS" in PREVIEW


def test_gpu_path_is_guarded_and_falls_back_to_the_cpu_solver():
    assert 'getExtension("EXT_color_buffer_float")' in PREVIEW
    assert "FRAMEBUFFER_COMPLETE" in PREVIEW and "MAX_DRAW_BUFFERS" in PREVIEW
    assert "new CpuSolver(...CPU_GRID)" in PREVIEW and "webglcontextlost" in PREVIEW
    assert "export const GPU_GRID = [400, 160];" in PREVIEW and "export const CPU_GRID = [200, 80];" in PREVIEW
    # the CPU version never blocks the page for long: a time budget per frame and chunked "advance"
    assert "performance.now() - t0 < 10" in PREVIEW and "setTimeout(go, 0)" in PREVIEW


def test_gpu_mask_upload_agrees_with_the_shaders_solid_test():
    """Regression: the part mask goes into an R8 texture, a *normalised* format the shaders read as
    stored / 255. Uploading the 0/1 mask as it is made the solid test (> 0.5) never fire on the GPU:
    uniform flow through the part, CL = CD = 0 (found in a real browser). Every 8-bit normalised
    upload must scale to 255; the only one is the mask."""
    normalised = re.findall(r"_texture\(gl\.(R8|RG8|RGB8|RGBA8|LUMINANCE|ALPHA)\b", PREVIEW)
    assert normalised == ["R8"]  # the mask; the lattice textures are RGBA32F (read back as the float stored)
    assert re.findall(r"_texture\(gl\.(RGBA32F), gl\.RGBA, gl\.FLOAT", PREVIEW) == ["RGBA32F"]
    uploads = re.findall(r"texSubImage2D\([^;]*?gl\.UNSIGNED_BYTE,\s*(\w+)\);", PREVIEW)
    assert uploads == ["m8"] and "m8[c] = mask[c] ? 255 : 0;" in PREVIEW
    assert not re.search(r"gl\.UNSIGNED_BYTE,\s*mask\)", PREVIEW)  # never the raw 0/1 mask
    for data in re.findall(r"texSubImage2D\([^;]*?gl\.FLOAT,\s*(\w+)\);", PREVIEW):
        assert data == "data"  # the float lattice textures: no scaling needed
    # both shaders (step and display) test "r > 0.5", i.e. 255 / 255 = 1.0 is solid, 1 / 255 is not
    step, display = PREVIEW.split("const DISPLAY = `")
    assert "bool solid(ivec2 p) { return texelFetch(tM, p, 0).r > 0.5; }" in step
    assert re.search(r"bool solid\(ivec2 p\) \{ return texelFetch\(tM, [^;]*\)\.r > 0\.5; \}", display)
    assert "if (mask[c])" in PREVIEW and "mask[src] ?" in PREVIEW  # the CPU solver uses the 0/1 mask as it is


def hex_rgb_mirror(h: str) -> list[float]:
    """Python copy of plot.js hexRgb, built from the offsets written in the script."""
    plot = (STATIC / "plot.js").read_text()
    m = re.search(r"export function hexRgb\(h\) \{\n  return \[(\d), (\d), (\d)\]\.map\(\(i\) => "
                  r"parseInt\(h\.slice\(i, i \+ 2\), 16\) / 255\);", plot)
    assert m, "plot.js hexRgb changed shape"
    return [int(h[int(i):int(i) + 2], 16) / 255 for i in m.groups()]


def test_part_colour_comes_from_the_css_variable_not_the_colour_map():
    """Regression: cssRgb sliced the hex digits at the offsets meant for a string that still starts
    with '#', so '#3d3d3a' became 'd3 d3 a' = yellow: the part was drawn in the colour map's
    'fast air' colour (GPU and CPU alike). Now one parser, plot.js hexRgb, fed the whole '#rrggbb'."""
    assert hex_rgb_mirror("#3d3d3a") == pytest.approx([0x3D / 255, 0x3D / 255, 0x3A / 255])
    assert hex_rgb_mirror("#fde725") == pytest.approx([253 / 255, 231 / 255, 37 / 255])  # the viridis end
    assert "hexRgb" in re.search(r'import \{([^}]*)\} from "\./plot\.js";', PREVIEW).group(1)
    assert "parseInt(" not in PREVIEW  # no second hex parser
    assert re.search(r"return /\^#\[0-9a-f\]\{6\}\$/i\.test\(v\) \? hexRgb\(v\) : fallback;", PREVIEW)
    assert 'cssRgb("--body-fill", BODY_FALLBACK)' in PREVIEW and 'cssRgb("--body-edge", EDGE_FALLBACK)' in PREVIEW
    # both renderers draw the part in `body` and a one-cell outline in `edge`
    assert "if (this.mask[c]) rgb = body;" in PREVIEW and "else if (this.nextToSolid(i, j)) rgb = edge;" in PREVIEW
    display = PREVIEW.split("const DISPLAY = `")[1].split("`;")[0]
    assert "if (solid(p)) { colour = vec4(body, 1.0); return; }" in display
    assert "if (solid(p + ivec2(dx, dy))) { colour = vec4(edge, 1.0); return; }" in display
    assert 'gl.uniform3f(this._u(pr, "edge"), edge[0], edge[1], edge[2]);' in PREVIEW
    assert "the dark outlined shape is the cut" in (STATIC / "i18n" / "en.json").read_text()
    assert 't("preview.text", {' in PREVIEW


def test_pause_play_and_reduced_motion():
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in PREVIEW
    assert "REDUCED_MOTION.matches && !this.running" in PREVIEW  # starts paused when motion is reduced
    assert 'id="preview-play"' in HTML and 'id="preview-advance"' in HTML and 'id="preview-reset"' in HTML
    assert 'textContent = t("preview.play")' in PREVIEW and 'textContent = t("preview.pause")' in PREVIEW
    assert "cancelAnimationFrame" in PREVIEW and "visibilitychange" in PREVIEW


def test_page_labels_the_preview_honestly_and_follows_the_angle():
    assert ("Preview — a 2D slice at a low Reynolds number; real wings differ\n            at the tips. "
            "Use the full simulation for numbers.") in HTML
    catalog = (STATIC / "i18n" / "en.json").read_text()
    assert 'id="preview-re"' in HTML and "Preview Reynolds number" in catalog and "your case:" in catalog
    assert 't("preview.re", { re: fmtInt(this.previewRe), own })' in PREVIEW
    assert 'role="img"' in re.search(r'<div id="preview-view"[^>]*>', HTML).group(0)
    assert 'id="preview-readout"' in HTML  # the numbers as text, beside the colours
    assert "preview?.setAngle(a)" in APP and "preview?.setCase(re)" in APP and "preview?.applyTheme()" in APP
    assert 'api(`/api/geometry/${g.geometry_id}/section`)' in APP  # uploads: the server cuts the STL


# ---------------------------------------------------------------- physics (numpy mirror)


def _mask_from_loop(loop, nx, ny, ox, oz, scale):
    mask = np.zeros((ny, nx), np.uint8)
    pts = (np.asarray(loop) - [ox, oz]) * scale
    for j in range(ny):
        y = j + 0.5
        xs = []
        for k in range(len(pts)):
            (xa, ya), (xb, yb) = pts[k], pts[(k + 1) % len(pts)]
            if (ya <= y) == (yb <= y):
                continue
            xs.append(xa + (y - ya) / (yb - ya) * (xb - xa))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            mask[j, max(0, int(np.ceil(xs[k] - 0.5))):min(nx, int(np.floor(xs[k + 1] - 0.5)) + 1)] = 1
    return mask


def _rotate(pts, alpha, pivot):
    a, (px, pz) = -np.radians(alpha), pivot
    x, z = pts[:, 0] - px, pts[:, 1] - pz
    return np.column_stack([px + x * np.cos(a) - z * np.sin(a), pz + x * np.sin(a) + z * np.cos(a)])


def _run(loop, alpha, nx=120, ny=48, steps=600):
    scale = 0.3 * nx / np.ptp(loop[:, 0])
    pivot = (0.25 * np.ptp(loop[:, 0]), 0.0)
    mask = _mask_from_loop(_rotate(loop, alpha, pivot), nx, ny, pivot[0] - 0.3 * nx / scale, -0.5 * ny / scale, scale)
    lat = lbm_mirror.Lattice(nx, ny, mask)
    q = 0.5 * lbm_mirror.U_IN**2 * np.ptp(loop[:, 0]) * scale
    cl = cd = 0.0
    for n in range(steps):
        lat.step()
        if n >= steps - 100:
            cl += lat.fy / q / 100
            cd += lat.fx / q / 100
    return lat, cl, cd


def test_mirror_is_stable_and_the_force_signs_are_right():
    wing = naca4_section("0012", 60) * 0.3
    lat, cl_up, cd_up = _run(wing, 8)
    assert np.isfinite(lat.rho).all() and lat.rho[~lat.mask].min() > 0.8 and lat.rho[~lat.mask].max() < 1.2
    assert np.hypot(lat.ux, lat.uy).max() < 3 * lbm_mirror.U_IN
    assert cl_up > 0.1 and cd_up > 0  # nose up: lift upwards, drag downstream
    _, cl_down, cd_down = _run(wing, -8)
    # a symmetric section mirrored on the lattice: equal and opposite lift, the same drag (to roundoff)
    assert abs(cl_down + cl_up) < 1e-9 and abs(cd_down - cd_up) < 1e-9
    circle = np.array([[0.1 + 0.1 * np.cos(a), 0.1 * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 72, endpoint=False)])
    _, cl_c, cd_c = _run(circle, 0)
    assert abs(cl_c) < 1e-6 and cd_c > 0.5  # a cylinder: no lift, a bluff body's drag
