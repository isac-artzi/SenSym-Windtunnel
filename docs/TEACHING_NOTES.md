# WindLab teaching notes (for the instructor)

WindLab v0.1 is an **uncalibrated prototype**: every number it shows is labelled "not yet
validated", and every user must accept the educational-use notice
([`DISCLAIMER.md`](../DISCLAIMER.md)) before running it. These notes explain what the
numbers mean, what to expect for the built-in NACA wings, where the method is weak, and
give three short in-class exercises. All WindLab numbers below come from this repository's
own runs: the 2D validation in
[`validation/naca0012_tmr.md`](../validation/naca0012_tmr.md) and 3D Quick-mesh runs
made in the Docker container (4 cores) on 2026-11-03.

**Installing WindLab** on students' computers (Windows with WSL2, macOS with Docker, or
Linux): [`docs/INSTALL.md`](INSTALL.md). The one-page [student handout](STUDENT_HANDOUT.md)
has the short version.

**Five guided 50-minute lessons** (lift vs angle of attack, Reynolds number and scale,
streamlining a bluff body, aspect ratio and induced drag, why simulations disagree) live in
the app under *Lessons*, each with pre-computed results so a class can run without waiting
for the solver; their printable worksheets and minute-by-minute instructor guides are in
[`docs/lessons/`](lessons/README.md). Start a class with `windlab start --lesson <id>`
(teacher mode: the page opens in the lesson and hides the upload and reference-value
controls). The built-in bodies (sphere, cylinder, plate, cube, teardrop) and their one-run
check against handbook values are described in
[`validation/bluff_bodies.md`](../validation/bluff_bodies.md).

**Expected-error ranges.** Next to every C<sub>L</sub> and C<sub>D</sub> the app shows
"likely … to …" with a "Why this range?" note. The ranges are looked up in
`windlab/validation_table.json` (version 2026-11-03.2) by shape class × coefficient × mesh,
inside a checked envelope of Reynolds numbers and angles; they encode exactly the numbers in
section 2 below (lift ±4 %; drag on Quick from 50 % below to 2 % above the WindLab value at
low lift, 25 % below to 2 % above at 4–12°; bodies as in the bluff-body report). Outside the
envelope — uploads, angles beyond 12° for wings, Reynolds numbers outside 1.5·10⁵–1.2·10⁶,
runs that did not settle — the app says "Unvalidated — treat as a trend only". The CSV
files carry the same ranges in extra columns. Tell students the range is where the true
value most likely lies, not a guarantee, and that trends between runs on the same mesh are
far tighter than any absolute drag.

## 1. What the numbers mean

WindLab solves the steady, incompressible Reynolds-averaged Navier–Stokes equations
(OpenFOAM `simpleFoam`, k-ω SST turbulence model, fully turbulent) around the part in a
large virtual tunnel (20 reference lengths upstream and to the sides, 40 downstream, so
there is no wall blockage). Wind blows along +x; "up" is +z; the angle of attack α tilts
the wind, not the part.

| Shown as | Meaning |
|---|---|
| C<sub>L</sub>, C<sub>D</sub> | Lift (⊥ wind) and drag (∥ wind) force divided by q·S, with q = ½ρV² and S the **reference area**. For the NACA wings S is the planform area chord × span; for the built-in bodies the frontal area. For an uploaded part WindLab measures both the frontal silhouette and the planform outline from the surface, guesses wing or body from the proportions (thin → wing) and uses the matching area; the student can switch between "a wing" and "a body" or type values in Step 1. |
| C<sub>m</sub> | Pitching moment about the reference point (quarter chord for wings), divided by q·S·c. |
| L/D | C<sub>L</sub>/C<sub>D</sub>, the lift you get per unit of drag. |
| Lift, drag in N | C<sub>L</sub>·q·S and C<sub>D</sub>·q·S at the chosen speed (sea-level air, ρ = 1.225 kg/m³). |
| Reynolds number | Re = V·c/ν with ν = 1.46·10⁻⁵ m²/s and c the reference length (the chord). 20 m/s on a 0.3 m chord gives Re ≈ 4.1·10⁵. |
| "Wobble" ± | How much C<sub>L</sub>/C<sub>D</sub> still move over the last solver iterations: a measure of iterative convergence, **not** of accuracy. |
| Pressure picture, C<sub>p</sub> | C<sub>p</sub> = (p − p<sub>∞</sub>)/q on the surface and in the mid-span slice: 1 at a stagnation point, negative = suction. |
| Flow slice, streamlines | Air speed ÷ wind speed and C<sub>p</sub> in the plane through the middle of the part; the thin lines are streamlines traced through that plane (they ignore the spanwise flow, so near the tips they are only a sketch). |

A **sweep** reuses one mesh for all angles (2 to 8 of them) and gives C<sub>L</sub>(α), the
drag polar and L/D(α), marking the best L/D and the angle where C<sub>L</sub> stops rising.
**Compare** puts 2–5 finished runs side by side and warns when their speeds/Reynolds
numbers differ or their reference areas follow different conventions (a wing's planform
area against a body's frontal area, or a value typed in by hand); bodies each on their own
frontal area, or wings each on their own planform area, get an informational note instead,
because that is the standard comparison. In a lesson with several runs, "Open all N pre-computed
results and compare them" brings every run in and opens the comparison in one click.

## 2. Expected values for the NACA 0012 wing

The built-in default is a rectangular NACA 0012 wing, chord c = 0.3 m, span b = 0.6 m:
planform area S = 0.18 m², **aspect ratio AR = b²/S = 2**, two free tips.

### The 2D airfoil (validation)

The same solver chain was checked in 2D against NASA's Turbulence Modeling Resource
(TMR) NACA 0012 case: Re = 6·10⁶, SST, a wall-resolved C-grid (y⁺ < 1), see
[`validation/naca0012_tmr.md`](../validation/naca0012_tmr.md).

| α | C<sub>L</sub> WindLab 2D | C<sub>L</sub> TMR (CFL3D / FUN3D) | ΔC<sub>L</sub> vs mean | C<sub>D</sub> WindLab 2D | C<sub>D</sub> TMR (CFL3D / FUN3D) | ΔC<sub>D</sub> vs mean |
|---:|---:|---:|---:|---:|---:|---:|
| 0° | 0 (by symmetry) | ≈ 0 | – | 0.0080 | 0.00809 / 0.00808 | −0.8 % |
| 5° | 0.544 | – | – | 0.0090 | – | – |
| 10° | 1.060 | 1.0778 / 1.0840 | −1.9 % | 0.0125 | 0.01236 / 0.01253 | +0.3 % |
| 15° | 1.477 | 1.5068 / 1.5109 | −2.1 % | 0.0223 | 0.02219 / 0.02275 | −0.7 % |

The TMR values are the SST results of NASA's two codes on their finest (897 × 257) grid;
the errors are against the mean of the two codes, which is also how the project's acceptance
criterion (C<sub>L</sub> within 2 %, C<sub>D</sub> within 5 % at 10°) is judged. The 0° case is run
as the upper half of the section with a symmetry plane (the full model's wake oscillated
along the grid cut and diverged in half the attempts), so there only C<sub>D</sub> is a
result; an earlier full-model run gave C<sub>L</sub> = −0.001. So the **2D
lift-curve slope** is a₀ ≈ 0.106 per degree (TMR: 0.108/deg; thin-airfoil theory: 2π per
radian = 0.110/deg). WindLab's lift is consistently about 2 % low; roughly half of that is
expected, because TMR ran compressible at M = 0.15 (Prandtl–Glauert factor 1.011) and
WindLab is incompressible.

Reference: NASA Langley Research Center, *Turbulence Modeling Resource*, "2D NACA 0012
Airfoil Validation Case", SST results page
<https://tmbwg.github.io/turbmodels/naca0012_val_sst.html> (the TMR moved from
turbmodels.larc.nasa.gov to tmbwg.github.io/turbmodels in 2025; values read 2026-11-03;
experimental data: Ladson, NASA TM-4074, 1988; Gregory & O'Reilly, ARC R&M 3726, 1970).

### The 3D wing (Quick mesh, AR = 2)

A finite wing makes less lift per degree than its airfoil: the tip vortices induce a
downwash that lowers the effective angle of attack. For a low-aspect-ratio straight wing
the Helmbold formula is a good estimate:

  a = a₀ / ( √(1 + (a₀/πAR)²) + a₀/πAR ),  with a₀ = 2π/rad and AR = 2:  **a ≈ 2.6/rad ≈ 0.045/deg**

(Prandtl's lifting line, a = a₀/(1 + a₀/(π e AR)), gives 0.052–0.055/deg for e = 0.9–1;
it overestimates for AR this small.) So expect **about 40 % of the 2D slope**, and an
induced drag C<sub>Di</sub> ≈ C<sub>L</sub>²/(π e AR) on top of the profile drag.

WindLab, Quick mesh (≈ 0.37 million cells), 20 m/s, Re = 4.1·10⁵:

| Run | α | C<sub>L</sub> | C<sub>D</sub> | L/D |
|---|---:|---:|---:|---:|
| NACA 0012, sweep (−4/4/12) | −4° | −0.176 | 0.0227 | −7.7 |
| NACA 0012, sweep | 4° | 0.176 | 0.0227 | 7.8 |
| NACA 0012, sweep | 12° | 0.536 | 0.0759 | 7.1 |
| NACA 0012, single run | 8° | 0.360 | 0.0410 | 8.8 |
| NACA 2412, single run | 8° | 0.473 | 0.0547 | 8.7 |

- **Lift slope:** (0.176 + 0.176)/8° = **0.044/deg** from the sweep and 0.360/8° = 0.045/deg from the 8° run, against 0.045/deg from Helmbold
  and 0.106/deg for the 2D airfoil. This is a good first discussion point: same airfoil,
  less than half the lift slope, because of the tips.
- **Symmetry:** ±4° give equal and opposite C<sub>L</sub> and equal C<sub>D</sub>, as they
  must for a symmetric section.
- **Drag:** at 12° the induced part alone is about 0.536²/(π·0.9·2) ≈ 0.051, two thirds of
  the total. The profile part (C<sub>D</sub> at 0°, 0.017 on Quick) is **higher than
  handbook values** for a NACA 0012 at this Reynolds number (≈ 0.009–0.011 measured
  with natural transition): see the accuracy paragraph and the limitations below. Treat
  absolute C<sub>D</sub> with caution; differences between runs on the same mesh are more
  trustworthy.

### How accurate are the numbers? (honest summary)

The solver chain is good: in 2D, wall-resolved, against NASA's two codes it is within 2 %
on lift and 1 % on drag at Re 6·10⁶ (table above). The everyday **Quick 3D mesh** is the
weak link, and the mesh study (`validation/wing3d_mesh_study.md`) quantifies it for the
default wing at 20 m/s with three meshes (0.37, 0.81 and 3.4 million cells): **lift on
Quick is within 1–2 % of the finer meshes** (0.451 against 0.459 and 0.455 at 10°), so lift
curves, lift slopes and comparisons between shapes are trustworthy to a few percent.
**Drag on Quick is an over-estimate of about 9 %** against the mesh-converged value at 0°
(0.0171 against 0.0156) and 8 % at 10° (0.0558 against 0.0516), and about a third above the
validated 2D section run wall-resolved at the same Reynolds number (0.0125, plus about
0.0005 for the tip caps). Nearly all of the excess is pressure drag from the coarse surface
cells; the skin-friction part agrees with flat-plate theory (ITTC-57: 0.0122 for the wetted
area of this wing). On top of the mesh error, every WindLab run assumes a boundary layer
that is turbulent from the leading edge; a real wing at Re ≈ 4·10⁵ has a long laminar run,
and measured NACA 0012 drag at this Reynolds number is around 0.009–0.011. So a student
comparing Quick drag with a textbook polar should expect WindLab to be **high by roughly
60–90 % at zero lift**, of which about 60 % is mesh and wall treatment and 40 % the fully
turbulent assumption; at 10°, where induced drag dominates and is captured well, the gap
shrinks to about 10 %. Read C<sub>D</sub> as an upper estimate and L/D as a lower one.
- **Camber (2412 vs 0012):** thin-airfoil theory puts the zero-lift angle of the 2412 at
  about −2.1°, so at the same α its C<sub>L</sub> should be higher by roughly
  a·2.1° ≈ 0.09–0.10 on this AR-2 wing. WindLab at 8°: ΔC<sub>L</sub> = 0.473 − 0.360 = **0.113**, which with the measured slope of 0.045/deg puts the zero-lift angle at about −2.5° (2D theory −2.1°). Its C<sub>D</sub> is higher too (0.055 against 0.041), mostly induced drag of the extra lift, so L/D is about the same (8.7 against 8.8).
- **y⁺ on the Quick mesh:** for the NACA 0012 at 8° and 20 m/s the first cell next to the wall sits at y⁺ ≈ 7–160 (average 25); the 2412 is the same. That is the buffer/log layer where the wall function is meant to work, not the wall-resolved y⁺ < 1 of the 2D validation.
- **Run time:** these two single runs took 76 s and 70 s of solver time on 4 cores (≈ 0.36 million cells, about 380 iterations each).

## 3. Known limitations (tell the students)

1. **Stall is uncertain.** Steady RANS with SST predicts the linear range well but stall
   (C<sub>L,max</sub> and the angle where it happens) only roughly; past stall the real flow
   is unsteady and a steady solver gives a rough average at best. WindLab flags the angle
   where C<sub>L</sub> stops rising and warns when the lift keeps oscillating. On an
   AR-2 wing stall is also delayed and softened by the strong tip vortices — compare with
   2D data only in the linear range.
2. **Quick mesh resolution.** About 0.4 million cells for the whole tunnel. The mesh study
   in [`validation/wing3d_mesh_study.md`](../validation/wing3d_mesh_study.md) (Quick,
   Standard and a finer mesh of the same family, NACA 0012 wing at 0° and 10°) shows that
   **lift on Quick is within 1–2 % of the finer meshes** while **drag is too high: by
   about 9 % at 0° and 8 % at 10° against the mesh-converged (Richardson-extrapolated)
   value**, and by about a third against the validated 2D section run wall-resolved at the
   same Reynolds number. The excess is pressure drag from the coarse surface cells (the
   viscous part agrees with flat-plate theory); more boundary-layer cells did not help.
   Use Quick for **trends and comparisons on the same mesh**, not absolute drag numbers.
3. **Fully turbulent boundary layer at low Reynolds number.** The model assumes the
   boundary layer is turbulent from the leading edge. At Re ≈ 10⁵–10⁶ (student-size
   models at classroom speeds) a real wing has long laminar runs, often a laminar
   separation bubble, and therefore **lower skin-friction drag** and a different stall
   than WindLab predicts. Below Re ≈ 10⁵ the results become qualitative. This is the
   main reason the 3D C<sub>D</sub> above is higher than handbook polars (which are often
   measured with natural transition).
4. **y⁺ and wall functions.** The 2D validation is wall-resolved (y⁺ < 1). The 3D Quick
   mesh instead has 5 thin layers at the wall and uses Spalding's wall function, which
   is valid for any y⁺ but is an approximation of the near-wall flow; skin friction and
   separation from smooth surfaces are the most affected.
5. **Other simplifications:** steady and incompressible (keep Mach < 0.3: WindLab allows
   up to 100 m/s); free-stream turbulence 0.1 %; no wind-tunnel walls, so no blockage or
   wall corrections are needed when comparing with free-flight theory, but they **are**
   needed when comparing with closed-section tunnel measurements; the part is rigid and
   smooth.
6. **Uploads:** WindLab uses the part's own axes (x = wind, z = up) and, unless changed, the
   frontal area for a body or the planform area for a wing-like part (measured from the
   surface); check both before comparing with other data. A wing with a tall winglet may be
   guessed as a body: the radio in Step 1 puts it right. Thin fins or plates about one mesh
   cell thick (2 mm on a 40 cm part on Quick) are not meshed symmetrically: a symmetric rocket
   at 0° showed a sideways force as large as its drag that pointed between two fins and did
   not turn with the body (see `validation/robustness.md`); the page and the report say so.

## 4. The instant preview in class: predict with the preview, confirm with the full run

Step 2 has a **Preview the airflow** button. It opens an animated 2D sketch of the flow
through the middle of the part, computed in the browser (a lattice-Boltzmann model, see
[`PREVIEW.md`](PREVIEW.md)), and it follows the angle of attack as the student types it. It
is **not** a result: it runs at a Reynolds number of a few thousand (the page shows "Preview
Reynolds number ≈ … — your case: …"), in two dimensions, with the part filling a quarter of
the picture's height. It over-states separation and drag, knows nothing about wing tips, and
its lift and drag figures are rough 2D estimates. What it shows well is *where* the air speeds
up and slows down, where it leaves the surface, how the wake grows with angle and bluntness,
and the direction in which lift and drag change.

A pattern that works in a 50-minute class:

1. **Predict with the preview (5–10 min).** Make the shape, open the preview, and ask
   the class to drag the angle from 0° to 16° while watching the air-speed colours: where is
   the fastest air, where does the flow leave the upper surface, what happens to the wake?
   Switch *Colour by* to *Swirl* and point out the two boundary layers leaving as sheets.
   Have every student **write a prediction** for the full run: will C<sub>L</sub> keep rising
   to 16°? Will C<sub>D</sub> rise faster or slower than C<sub>L</sub>? (The lesson panel's
   *Predict* step is the place to write it.)
2. **Confirm with the full run (the solver runs while you talk).** Start the Quick run or
   the sweep, or open the pre-computed result. Compare the sweep's C<sub>L</sub>(α) chart and
   the flow slice with what the preview suggested.
3. **Explain the difference (10 min).** The preview separates earlier and shows a wider wake
   than the full run: its Reynolds number is a hundred times lower, so the boundary layers are
   thick and lazy. The full run's lift per degree is lower than any 2D picture suggests
   because the wing has tips (section 2). Ask: which of the two is closer to a real model in a
   classroom wind tunnel, and in what respect? (Neither in drag: see limitation 3.)

Practical notes: the preview needs no server and no wait, so it is the thing to use on a
laptop without WindLab installed (the online demo) or while a run queues. On a laptop
without a working graphics card it falls back to a smaller, slower version and says so.
Students who have turned animation off in their system settings see it paused with an
*Advance 200 steps* button. Every preview frame is a 2D cut at mid-span; for the
built-in bodies that means a sphere is previewed as a long cylinder — a good discussion
point in the streamlining lesson, not a bug.

## 5. Three in-class exercises (about 20–30 minutes each)

Run times: a Quick single run took 70–80 s of solver time on 4 cores in the Docker container on an
Apple M-series desktop (about 1½ minutes in all); on student laptops expect 1–5 minutes per run, and
roughly that per angle for a sweep. Runs queue,
so students can set up the next one while one is running. All results can be exported
as CSV (single runs, sweeps and comparisons) for a lab report.

### Exercise 1 — What camber does (single runs and Compare)

1. Make a NACA **0012** wing (default size). Run it at 20 m/s and **8°**.
2. Make a NACA **2412** wing (same chord and span) and run it at the same speed and angle.
3. Under **My runs**, tick both and press **Compare**. WindLab should say the runs can be
   compared directly (same speed, Reynolds number and reference area).

Questions: Which wing makes more lift at 8°, and by how much? Estimate the zero-lift
angle of the 2412 from ΔC<sub>L</sub> and the lift slope of exercise 2 (theory: about
−2°). Is the drag different? Look at the pressure pictures: where does the extra lift
come from? *Extension:* run both as a sweep (−4, 0, 4, 8, 12) and compare the C<sub>L</sub>
lines and drag polars; the 2412 line should be shifted up by a nearly constant amount.

### Exercise 2 — Aspect ratio via span (sweeps)

1. Keep chord 0.3 m and make three NACA 0012 wings with span **0.3, 0.6 and 1.2 m**
   (AR = 1, 2, 4).
2. Run each as a sweep at 20 m/s with angles **0, 4, 8**.
3. Compare the three sweeps.

Compare notes that the reference areas differ and that each wing's coefficients use its
own planform area — the usual choice when comparing wings of different size, so the
note is informational, not a warning. Questions: Measure the lift slope of each wing from the C<sub>L</sub>
line and plot it against AR together with the Helmbold estimate (AR 1: ≈ 0.026/deg,
AR 2: ≈ 0.045/deg, AR 4: ≈ 0.068/deg for a₀ = 2π/rad) and the 2D value (0.106/deg).
Which wing has the best L/D, and why (induced drag ∝ C<sub>L</sub>²/AR)? Look at the flow
slice near the tips of the short wing.

### Exercise 3 — Reynolds number via speed and chord (single runs and Compare)

1. NACA 0012, chord 0.3 m, span 0.6 m, **8°** at **10, 20 and 40 m/s**
   (Re ≈ 2·10⁵, 4·10⁵, 8·10⁵). Compare the three runs.
2. Then keep 20 m/s and AR 2 but change the size: chord **0.15 m / span 0.3 m** and
   chord **0.6 m / span 1.2 m**. Compare with the 0.3 m run at 20 m/s.

WindLab will warn that the Reynolds numbers differ (and, in part 2, note that each wing
uses its own planform area): that is the point of the exercise. Questions: How much do C<sub>L</sub> and C<sub>D</sub> change
when Re doubles? Do the lift and drag **forces** in newtons scale with V² and with S as
expected? Which matters for the coefficients: speed or size — or only their product?
Discuss why a real wind-tunnel model at the same Re would probably show *lower* drag
than WindLab (laminar flow; limitation 3).

## 6. Feedback from the trial

The Results step has a small **How was this?** panel. Answers are stored on each laptop
only. Ask students to run `windlab feedback export` at the end and e-mail you the CSV:
it contains their answers and an anonymous usage summary (number of runs, sweeps,
uploads, cancels, the problem messages they saw, median run time, OS, cores, RAM), with
no file or shape names. Open it in Excel, or in Python with
`pandas.read_csv(path, comment="#")` (the summary is in the `#` comment lines at the top).
The same file holds the students' **lesson answers**: one row per prediction, observation,
explanation and comprehension question (column *kind* = "lesson answer", with the lesson
and step ids and, for the questions, whether the answer was right), plus a summary line
with the number of questions answered correctly.
