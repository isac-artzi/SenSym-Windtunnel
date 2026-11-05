# Instructor guide: Aspect ratio and induced drag

*WindLab lesson `aspect-ratio` · 50 minutes · uncalibrated prototype, not yet validated. Start the class with `windlab start --lesson aspect-ratio` (teacher mode: the page opens in the lesson and hides the upload and reference-value controls).*

Three wings with the same section and chord but spans of 0.3, 0.6 and 1.2 m: why gliders have long, slender wings.

## Learning objectives

1. Define aspect ratio and show how the lift slope of a wing rises with it, towards the two-dimensional value.
2. Explain induced drag with the tip vortices and downwash, and check the estimate CDi = CL^2 / (pi e AR) against WindLab's numbers.
3. Judge when coefficients of different wings can be compared and when their reference areas get in the way.

## Minute by minute

| minutes | what happens |
|---:|:--|
| 0-5 | Objectives; prediction: how will the lift slope and the drag change with span? |
| 5-20 | Sweep (0, 4, 8 degrees) of the aspect-ratio-1 wing and of the aspect-ratio-4 wing - live or pre-computed; the aspect-ratio-2 points come from the lift lesson. |
| 20-30 | Compare the three sweeps; compute the lift slopes and the induced drag estimate. |
| 30-40 | Explain: tip vortices, downwash, Helmbold's formula, induced drag. |
| 40-50 | Comprehension questions, debrief, homework (aspect ratio 8). |

Total 50 minutes. Quick runs take 1-5 minutes each on a student laptop; a sweep about that per angle. If the laptops are slow, use the pre-computed results for every run except one, so that students see one live run settle.

## Expected results and answers

**Predict before you run** — prompt: The aspect-ratio-2 wing has a lift slope of about 0.045 per degree (lift lesson). What slope do you expect for the aspect-ratio-1 and the aspect-ratio-4 wing? Which wing will have the best lift-to-drag ratio at 8 degrees, and why?

Accept any prediction; the point is to commit to one before the run. Ask two or three students to read theirs out.

**Run 1: aspect ratio 1 (span 0.3 m)**

Pre-computed on 2026-11-03 (Quick mesh, 221,835 cells, 113 s of solver time on 4 cores). Reynolds number 410,762; reference area 0.09 m².

| angle (°) | CL | CD | L/D | iterations | settled |
|---:|---:|---:|---:|---:|:--|
| 0 | -0.000 | 0.0180 | -0.0 | 331 | yes |
| 4 | 0.115 | 0.0226 | 5.1 | 354 | yes |
| 8 | 0.239 | 0.0375 | 6.4 | 377 | yes |

Best L/D 6.4 at 8°.

**Run 2: aspect ratio 4 (span 1.2 m)**

Pre-computed on 2026-11-03 (Quick mesh, 673,749 cells, 337 s of solver time on 4 cores). Reynolds number 410,762; reference area 0.36 m².

| angle (°) | CL | CD | L/D | iterations | settled |
|---:|---:|---:|---:|---:|:--|
| 0 | 0.001 | 0.0167 | 0.1 | 328 | yes |
| 4 | 0.244 | 0.0226 | 10.8 | 371 | yes |
| 8 | 0.486 | 0.0421 | 11.5 | 377 | yes |

Best L/D 11.5 at 8°.

**Observe** — prompt: For each wing write down CL and CD at 0, 4 and 8 degrees and the L/D at 8 degrees. Compute the lift slope (CL at 8 degrees divided by 8) and the rise in drag from 0 to 8 degrees, CD(8) - CD(0).

Expected: the numbers in the result table(s) above.

**Explain** — prompt: Compare your drag rises with the induced-drag estimate CDi = CL^2 / (pi e AR) with e = 0.9, using each wing's CL at 8 degrees. Why does the slender wing make more lift per degree and pay less drag for it?

Model answer: Air leaks around the tips from the high-pressure lower surface to the low-pressure upper surface and rolls up into two vortices. They push air downwards over the whole span (downwash), which tilts the wind the wing feels: less effective angle (lower lift slope) and a lift force tilted backwards (induced drag). The shorter the wing, the larger the share of it near the tips. WindLab gives lift slopes of 0.030, 0.045 and 0.061 per degree for aspect ratios 1, 2 and 4 (Helmbold's formula: 0.026, 0.045 and 0.068; a two-dimensional section: 0.11). The drag rise from 0 to 8 degrees is 0.020, 0.024 and 0.025, against CL^2/(pi 0.9 AR) = 0.020, 0.023 and 0.021 - the estimate works, and the small excess at aspect ratio 4 is profile drag growing with lift. L/D at 8 degrees climbs from 6.4 to 8.8 to 11.5. A glider with aspect ratio 25 pushes the same idea to L/D above 40.

**Question 1.** Going from aspect ratio 1 to 4 (same chord, four times the span), the lift slope roughly ...

- halves
- stays the same
- **doubles (correct)**
- becomes zero

  0.030 to 0.061 per degree in WindLab. It cannot rise without limit: the two-dimensional section's 0.11 per degree is the ceiling as the aspect ratio grows.

**Question 2.** Why does a longer, slenderer wing make more lift per degree?

- **The tip vortices affect a smaller share of the wing, so the downwash and the loss of effective angle are smaller (correct)**
- Longer wings are heavier
- The chord is larger
- The Reynolds number is higher

  The chord and the Reynolds number are the same for all three wings here; only the span changes. Downwash from the tips reduces the angle the wing feels, and it matters less when the tips are a small part of the span.

**Question 3.** Induced drag is about CL^2 / (pi e AR). At the same lift coefficient, doubling the aspect ratio ...

- doubles the induced drag
- **halves the induced drag (correct)**
- has no effect
- quarters the induced drag

  AR is in the denominator. At the same CL, twice the aspect ratio means half the induced drag; this is why long wings are efficient at the high lift coefficients of slow flight.

**Homework (optional)** — Make a wing with span 2.4 m and chord 0.3 m (aspect ratio 8) and sweep 0, 4, 8 degrees. Predict its lift slope with Helmbold's formula first (about 0.082 per degree), then check. Note how the mesh grows with the span and how long the run takes. Add the forces in newtons to the comparison: which wing lifts most at 8 degrees, and is that the same wing that has the best L/D?

## Common misconceptions

- 'Four times the span, four times the lift coefficient': the coefficient already divides by the area; the slope rises because of less downwash, from 0.030 to 0.061 per degree, not fourfold.
- 'Induced drag comes from friction at the tips': it is the backward tilt of the lift force caused by the downwash, present even for a frictionless wing.
- 'WindLab warns that the areas differ, so the comparison is wrong': the warning is a reminder; comparing coefficients on each wing's own planform area is exactly how wings are compared.

## Where the numbers come from

The pre-computed results were made with WindLab's own Quick preset in the dev container on 2026-11-03 (4 cores); the method's accuracy is documented in `docs/TEACHING_NOTES.md`, `validation/naca0012_tmr.md`, `validation/wing3d_mesh_study.md` and `validation/bluff_bodies.md`. Every coefficient in the app carries an expected-error range with a "Why this range?" note; the lesson *Why simulations disagree* teaches how to read it. Student answers are stored locally and exported with `windlab feedback export`.

Rendered from `windlab/lessons/aspect-ratio/lesson.json` by `scripts/render_lessons.py`.
