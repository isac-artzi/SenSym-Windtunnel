# Built-in bluff and streamlined bodies: one Quick run each against handbook values

Generated from the runs of 2026-11-03 (aarch64 Linux container, 4 MPI ranks, one run at a time) made for the
"Streamlining a bluff body" lesson; raw numbers in `bluff_bodies.json`, the runs themselves are the lesson's
pre-computed result pack (`windlab/lessons/streamlining/pack/`). **Uncalibrated prototype - not yet validated.**

Setup: each built-in body (`windlab.geometry.shapes`) at 20 m/s and 0 degrees on the app's Quick preset with the
`external_aero` template (blockMesh + snappyHexMesh with 5 boundary-layer cells, Spalding wall function, simpleFoam
k-omega SST, fully turbulent, Tu 0.1 %) and the everyday stop rule (iteration cap 1500). Reference area = frontal
area, reference length = diameter / side / height, so CD compares directly with handbook tables. Handbook values:
Hoerner, *Fluid-Dynamic Drag* (1965), chapters 3 and 6; Achenbach, J. Fluid Mech. 54 (1972) for the sphere;
Schlichting, *Boundary-Layer Theory*. These are approximate ranges read from the literature, not WindLab numbers.

## Results

| shape | size | Re | cells | CL (last 200) | CD | pressure / viscous | iterations | settled | solver time | handbook CD | WindLab vs handbook |
|:--|:--|---:|---:|---:|---:|:--|---:|:--|---:|:--|:--|
| flat plate facing the wind | 0.2 x 0.2 m (5 % thick) | 2.7e5 | 198,000 | -0.0001 | **1.212** | 1.212 / 0.000 | 360 | yes | 43 s | 1.17 (square plate; tables up to 1.28) | +4 % vs 1.17: **agrees** |
| cube | 0.2 m | 2.7e5 | 370,000 | -0.0001 (+/-0.003) | **1.027** | 1.030 / -0.003 | 454 | yes | 99 s | 1.05 | -2 %: **agrees** |
| cylinder across the wind, flat ends | d 0.15 m, L 0.45 m (L/d 3) | 2.1e5 | 548,000 | -0.008 (+/-0.25) | **0.574** | 0.564 / 0.010 | 1500 (cap) | **no** | 442 s | about 0.7 for L/d 3 with free ends (Hoerner: 0.63 at L/d 1, 0.68 at 2, 0.74 at 5); 1.0-1.2 for a very long cylinder | -18 % vs 0.7, -50 % vs 1.15: **low, and unsettled** |
| sphere | d 0.2 m | 2.7e5 | 170,000 | +0.083 (+/-0.026) | **0.252** | 0.234 / 0.018 | 472 | yes (lift swinging) | 49 s | 0.47 below the drag crisis (Re < 3e5); 0.1-0.2 above it | -46 % vs 0.47: **miss** |
| streamlined teardrop | d 0.2 m, fineness ratio 3 (L 0.6 m) | 2.7e5 | 83,000 | -0.0002 | **0.068** | 0.033 / 0.036 | 357 | yes | 16 s | 0.05-0.1 (fineness 3-4; 0.05-0.06 at Re_L about 1e6) | inside the range: **agrees** |

Every mesh passed checkMesh (0 failed checks); boundary layers formed to 95-100 % of the planned thickness. y+ on the
body: plate 1-22 (mean 12), cube 0.5-22 (mean 11), cylinder 0.7-117 (mean 17), sphere 2-41 (mean 25), teardrop
19-130 (mean 93). Solver time in total for the five runs: 11 minutes; the cylinder alone took 7.

## What agrees, what does not, and why

**Sharp-edged bodies agree.** The flat plate and the cube land within a few percent of the handbook values. Their
separation point is fixed by the sharp edge, so the steady solution does not depend on the boundary layer; the drag is
pure pressure drag (the viscous part is zero to three decimals) and the Quick mesh resolves the front stagnation
and the base pressure well enough. The cube's lift swings slightly (+/-0.003), a sign of a mildly unsteady wake, but
its drag is steady to 0.1 %.

**The sphere is a clear miss: 0.25 against 0.47.** This is a model limitation, not a mesh problem. WindLab's
turbulence model treats the boundary layer as turbulent from the stagnation point. A turbulent layer stays attached
further around the sphere (to about 120 degrees instead of 80) and leaves a narrower wake, which is exactly what a
real sphere does only above its drag crisis (Re above about 3-4e5, Achenbach 1972), where measured CD indeed falls to
0.1-0.2. At Re 2.7e5 a real sphere still has a laminar boundary layer that separates early. WindLab's value is
therefore close to the *supercritical* handbook value, not the subcritical one that applies at this Reynolds number.
A transition model (kOmegaSSTLM) is the only fix; mesh refinement would not help. The lift (+0.08,
swinging by +/-0.03) also shows the wake is not steady, so even the 0.25 is a rough average.

**The cylinder is low and never settled.** CD 0.57 sits below the handbook value for a short cylinder with free ends
(about 0.7 at L/d = 3) and well below the 1.0-1.2 of a very long cylinder. Two reasons: the same laminar-versus-
turbulent separation question as the sphere (the handbook 1.2 is subcritical), and the real wake is a Karman vortex
street, which a steady solver cannot represent: the lift swung by +/-0.25 throughout, the residuals plateaued at
1e-2 to 1e-4, and the run went to the 1500-iteration cap (7 minutes). Its CD is the mean of the last 200 iterations
of a solution that was still oscillating - a rough average at best. Note also that WindLab's cylinder is *finite*
(flat end caps, length 3 diameters): a spanning cylinder is not possible in the open-air tunnel template, so the
"1.0-1.2" textbook value for an infinite cylinder is not the right comparison; the finite-cylinder value (about 0.7)
is.

**The streamlined teardrop agrees, with a caveat.** CD 0.068 on frontal area lies in the handbook range 0.05-0.1 for
fineness ratios 3-4, and the split (half pressure, half friction) is what streamlining is about. But the mesh is
coarse for this slender body: the reference length is the diameter (0.2 m) while the body is 0.6 m long, so only
83,000 cells were used and y+ reached 130 (mean 93). The friction part carries the same fully turbulent bias as the
wing (too high at this Reynolds number), the pressure part depends on the mesh. One run is not a validation; it
shows the shape meshes and converges and that the number is plausible.

## Consequences for the expected-error table

`windlab/validation_table.json` (version 2026-11-03.2) now has three body classes, all for the Quick preset,
Re 1e5-5e5, |alpha| <= 2 degrees:

| class | shapes | CD band (true value relative to WindLab) | basis |
|:--|:--|:--|:--|
| bluff (sharp-edged) | flat plate, cube | -10 % to +10 % | 2 of 2 runs within 4 % of the handbook |
| bluff-smooth | sphere, cylinder | -20 % to +100 % | WindLab under-predicts by up to half (laminar-turbulent separation; unsteady wake) |
| streamlined-body | teardrop | -30 % to +50 % | one run inside the handbook range on a coarse mesh |

Outside these conditions (a body at an angle, other Reynolds numbers) the app says "Unvalidated - treat as a trend
only". The *trend* between shapes - plate and cube above 1, cylinder and sphere in between, teardrop a tenth of the
sphere - is right in every run and is what the lesson teaches.

## Open points

- Run the sphere and cylinder with a transition model, or at a Reynolds number clearly above the drag crisis
  (Re_d about 1e6, i.e. 75 m/s on the 0.2 m sphere), to show the supercritical agreement.
- A finer mesh family for slender bodies (reference length = body length for the mesh, diameter for Re and CD).
- The cylinder would need a transient solver (pisoFoam / pimpleFoam) for a time-averaged drag; out of scope for 1.0.
