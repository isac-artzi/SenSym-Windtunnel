# The instant preview: what it computes and what it cannot tell you

The **Preview the airflow** button in Step 2 opens an animated sketch of the air moving
around the part. It runs entirely in the browser, needs no server and no OpenFOAM, and
follows the angle of attack as you type it. It is labelled on the page as

> Preview — a 2D slice at a low Reynolds number; real wings differ at the tips. Use the
> full simulation for numbers.

This note says exactly what is behind it, so an instructor can say in class what the
picture shows and what it does not. Code: `windlab/app/static/preview.js` (the solver and
the page glue), `section.js` (the outlines), `windlab/geometry/section.py` (cutting an
uploaded STL at mid-span, served as `GET /api/geometry/{id}/section`), and
`tests/lbm_mirror.py`, a numpy copy of the same scheme used by the tests.

## What is simulated

**The geometry** is the outline of the part in the plane through the middle of its span,
y = centre of its bounding box: the NACA section from the formula for the built-in wings,
a circle for the sphere and the cylinder, a rectangle for the plate and the cube, the
revolved thickness profile for the teardrop, and for an uploaded part a real cut through
its STL file (made on the server, thinned to at most 400 points per loop). The outline is
turned by the angle of attack (nose up for a positive angle) and rasterised onto the
lattice: a cell is solid when its centre lies inside the outline. The part is placed with
its quarter-length point at 30 % of the width, scaled so that at every angle between −20°
and 20° its box fills at most 35 % of the width and 28 % of the height.

**The method** is a two-dimensional lattice-Boltzmann model, D2Q9: every cell holds nine
packets of air moving towards its neighbours (and one at rest). Each step, the packets hop
to the next cell (streaming) and are relaxed towards the local equilibrium of a gas at the
cell's density and velocity (BGK collision). The relaxation time sets the viscosity. A
Smagorinsky sub-grid model adds an eddy viscosity where the flow is strongly sheared
(C_s = 0.1), which is what keeps the scheme stable at a useful Reynolds number on a coarse
grid. The part is a halfway bounce-back wall (packets heading into it come straight back),
the left edge is a steady inflow, the right edge lets the flow leave (zero gradient), and
the top and bottom rows hold the free stream.

**Lattice settings** (lattice units: cell = 1, step = 1, density ≈ 1):

| Setting | Value | Meaning |
|---|---|---|
| Grid (graphics card) | 400 × 160 cells | the picture's own resolution |
| Grid (CPU fallback) | 200 × 80 cells | used when float render targets are unavailable |
| Inflow speed U | 0.08 | lattice Mach number ≈ 0.14: incompressible enough |
| Relaxation time τ₀ | 0.51 | molecular viscosity ν₀ = (τ₀ − ½)/3 = 0.0033 |
| Smagorinsky C_s | 0.1 | eddy viscosity ν_t = C_s² |S| on top of ν₀ |
| Reference length L | the part's reference length in cells (≈ 100 cells for a wing chord, ≈ 45 for a sphere's diameter) | |

**Preview Reynolds number** = U·L/ν₀ ≈ 2,400 for a wing chord of 100 cells, ≈ 1,100 for a
sphere or plate; the page shows this figure next to the Reynolds number of the student's
own case, which is typically 10⁵ to 10⁶. The eddy viscosity lowers the effective value
further where the flow is sheared, so "a few thousand at most" is the honest description.

**Forces** come from momentum exchange: every packet that bounces off the part transfers
twice its momentum to it. The sums over all wall links give a force per unit span, divided
by ½·U²·L to make 2D lift and drag coefficients. They are read back twice a second and
averaged over the last four seconds. The first ~4,000 steps (about one flow-through of the
domain) are labelled "still settling".

**Speed.** On a graphics card one lattice step is one full-screen fragment-shader pass over
64,000 cells (about 20 texture reads per cell) plus one small pass to colour the picture;
the page adapts the number of steps per frame between 1 and 24 to keep the frame time
under about 25 ms, so an integrated GPU runs it at 30–60 frames per second with 6–20 steps
per frame. The CPU fallback does at most 10 ms of work per frame (2–4 steps on the smaller
grid) so the page never freezes; it is visibly slower to develop. "Advance 200 steps" moves
the flow on in small chunks without animation, for people who have turned animation off
(`prefers-reduced-motion`, in which case the preview opens paused) and for a quick look.

## Why it differs from the full simulation

1. **Reynolds number.** The full run is at Re = 10⁵–10⁶ with a turbulence model; the
   preview is at Re ≈ 10³. At Re 10³ the boundary layers are thick, they separate early
   (a NACA 0012 at 10° is already separating over most of its upper surface), and the
   wake of a bluff body is wide and unsteady. So the preview **over-states drag and
   separation** and under-states lift at moderate angles. Compare: in the numpy mirror of
   the scheme, the NACA 0012 at 10° gives a 2D lift coefficient wandering between about
   1.0 and 1.7 with a drag coefficient around 0.3; the full 3D Quick run of the AR-2 wing
   gives 0.45 and 0.056, and a 2D section at Re 6·10⁶ gives 1.06 and 0.0125.
2. **Two dimensions.** The preview is one plane; there are no wing tips, no tip vortices,
   no downwash. A finite wing's lift slope is far below the 2D value (the AR-2 wing makes
   about 40 % of the 2D lift per degree, see the teaching notes), and its drag includes
   induced drag that the preview cannot contain. Bodies of revolution (sphere, teardrop)
   become infinitely long cylinders in a 2D cut, whose drag coefficients are quite
   different (a cylinder's is roughly 2–3 times a sphere's).
3. **Blockage.** The part fills up to 28 % of the height of the preview tunnel, with the
   free stream imposed on the top and bottom rows; the full simulation keeps the far field
   20 reference lengths away. Blockage raises the speed over the part and its drag.
4. **Resolution.** The part is a staircase of square cells (a wing chord of about 100
   cells, a thickness of 12); boundary layers are a few cells thick. Thin trailing edges
   and sharp corners are only roughly represented.
5. **Compressibility and time.** The lattice Mach number is 0.14 and the scheme is
   explicit: small pressure waves run around the domain at start-up and after each change
   of angle. The forces oscillate because the wake is unsteady at this Reynolds number;
   the page shows a running mean.

**The picture.** The part is painted in the page's `--body-fill` colour with a one-cell
outline in `--body-edge` (the fluid cells that touch it), so it reads clearly against the
dark "stopped air" colour in both themes; the air is coloured by speed (viridis) or by
vorticity (a diverging map centred on zero). Both renderers (graphics card and CPU) draw
it the same way.

What the preview **does** show well, and what to use it for in class: where the air
speeds up (over the top of a wing at a positive angle, around the shoulders of a body),
where it is brought to rest (the stagnation point at the nose), how the wake widens as a
body gets blunter or a wing's angle grows, the two boundary layers leaving the surface as
sheets of swirl (choose *Colour by: Swirl*), and the **direction** of lift and drag
changes with angle and shape. Predict with the preview, confirm with the full run
(`docs/TEACHING_NOTES.md`, section 4).

## Stability and failure modes

- The scheme stays stable for the whole range of built-in shapes and angles in the numpy
  mirror (density within 0.87–1.06 of the free-stream value over 8,000 steps for a wing
  at ±10°, a flat plate and a cylinder). If a density ever leaves the range 0.3–3 or
  becomes NaN (a very sharp uploaded outline at a large angle can do it), the page restarts
  the preview from still air and says so.
- Without float render targets (`EXT_color_buffer_float`, missing on a few older mobile
  GPUs) or with WebGL2 disabled, the page uses the JavaScript solver on the 200 × 80 grid
  and tells the student it is the slower version. Lost graphics contexts stop the preview
  with a note; Play starts it again.
- Nothing from the preview is sent to the server; the only network request is the
  mid-span cut of an uploaded part.

## Testing

The dev container has no browser or WebGL (Node only evaluates `preview.js` in a tiny DOM,
`tests/test_js.py`), so `tests/test_preview.py` checks the scripts
statically (balanced brackets, ids on the page, the NACA and plate constants equal to the
Python generators, the lattice constants equal to the mirror, the reduced-motion and
fallback paths present, the mask uploaded to the GPU's normalised R8 texture as 0/255 so
the shaders' `> 0.5` solid test agrees with it, and the part's colour parsed from the CSS
variable by the one shared hex parser) and runs the physics through `tests/lbm_mirror.py`: the scheme must
stay bounded, give positive lift and drag for a wing nose-up, exactly mirrored values
nose-down, and zero lift with positive drag for a cylinder.
`tests/test_section.py` checks the mid-span cuts against the analytic outlines.
