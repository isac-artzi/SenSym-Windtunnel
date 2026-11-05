# Instructor guide: Lift vs angle of attack

*WindLab lesson `lift-vs-angle` · 50 minutes · uncalibrated prototype, not yet validated. Start the class with `windlab start --lesson lift-vs-angle` (teacher mode: the page opens in the lesson and hides the upload and reference-value controls).*

Tilt the wind into a wing, step by step, and watch the lift rise - until it stops rising. One sweep of six angles on the default NACA 0012 wing.

## Learning objectives

1. Describe how the lift coefficient of a wing changes with angle of attack, and read the lift slope from a chart.
2. Explain why the curve bends over at high angles (stall) and why a symmetric wing gives zero lift at zero angle.
3. Use the drag polar to find the angle with the best lift-to-drag ratio.

## Minute by minute

| minutes | what happens |
|---:|:--|
| 0-5 | Open the lesson, read the objectives, write the prediction (sketch CL against angle). |
| 5-15 | Set up and start the sweep (or load the pre-computed results); while it runs, discuss what the solver is doing. |
| 15-30 | Read the table and the three charts; fill in the observation box; find the slope and the best L/D. |
| 30-40 | Explain: downwash and the finite wing, separation and stall; compare with the prediction. |
| 40-50 | Comprehension questions, debrief, homework (NACA 2412). |

Total 50 minutes. Quick runs take 1-5 minutes each on a student laptop; a sweep about that per angle. If the laptops are slow, use the pre-computed results for every run except one, so that students see one live run settle.

## Expected results and answers

**Predict before you run** — prompt: Sketch (on paper) and describe in words how you expect the lift coefficient CL to change as the angle of attack goes from -4 to 16 degrees. Will it be a straight line? What happens at 0 degrees for a symmetric wing? Does it keep rising all the way to 16 degrees? Write your prediction here before running.

Accept any prediction; the point is to commit to one before the run. Ask two or three students to read theirs out.

**Run the angle sweep**

Pre-computed on 2026-11-03 (Quick mesh, 372,571 cells, 526 s of solver time on 4 cores). Reynolds number 410,762; reference area 0.18 m².

| angle (°) | CL | CD | L/D | iterations | settled |
|---:|---:|---:|---:|---:|:--|
| -4 | -0.175 | 0.0227 | -7.7 | 371 | yes |
| 0 | 0.001 | 0.0171 | 0.0 | 338 | yes |
| 4 | 0.176 | 0.0227 | 7.8 | 374 | yes |
| 8 | 0.360 | 0.0410 | 8.8 | 373 | yes |
| 12 | 0.536 | 0.0759 | 7.1 | 489 | yes |
| 16 | 0.588 | 0.1796 | 3.3 | 956 | yes |

Best L/D 8.8 at 8°.

**Observe** — prompt: From the results table write down CL at -4, 4, 8, 12 and 16 degrees, and the angle with the best L/D. Work out the lift slope between -4 and 8 degrees (change in CL divided by change in angle) and between 12 and 16 degrees.

Expected: the numbers in the result table(s) above.

**Explain** — prompt: Why is the lift slope so much smaller than the 0.11 per degree a two-dimensional wing section gives, and why does the curve flatten between 12 and 16 degrees? Compare with your prediction.

Model answer: Two effects. First, this wing is short (aspect ratio 2): the air spilling around the tips rolls into vortices that push air down over the whole wing (downwash), so the wing feels a smaller angle than the geometric one; the slope drops from 0.11 to about 0.045 per degree. Second, above about 12 degrees the air can no longer follow the upper surface; it separates, the lift stops rising (0.536 at 12, only 0.588 at 16 degrees) and the drag more than doubles. That is the start of stall. WindLab's steady simulation shows where it begins but is only rough past it; the solver also needed many more iterations at 16 degrees.

**Question 1.** Between -4 and 8 degrees the lift coefficient rises by about the same amount per degree. What is this slope for this wing?

- about 0.011 per degree
- **about 0.045 per degree (correct)**
- about 0.11 per degree
- about 0.5 per degree

  (0.360 - (-0.175)) / 12 degrees is about 0.045 per degree. A two-dimensional section would give 0.11; the finite wing with aspect ratio 2 gives less than half of that.

**Question 2.** Why does the lift curve bend over between 12 and 16 degrees?

- The wind speed drops at high angles
- **The air separates from the upper surface: the beginning of stall (correct)**
- The mesh runs out of cells
- Drag pushes the wing backwards

  At high angles the pressure on the upper surface must rise steeply towards the trailing edge; the slowed air in the boundary layer cannot manage that and leaves the surface. Separated flow makes little extra lift and a lot of drag.

**Question 3.** At -4 degrees the drag coefficient equals that at +4 degrees and the lift is equal and opposite. Why?

- Because the solver reuses the 4-degree result
- **Because the NACA 0012 is symmetric: turned upside down it is the same wing (correct)**
- Because drag never depends on the angle
- Because a sweep uses one mesh for all angles

  A symmetric section at -4 degrees is the mirror image of the same section at +4 degrees, so the lift simply changes sign and the drag is the same. A cambered section (NACA 2412) would not show this symmetry.

**Homework (optional, 20 minutes)** — Make a NACA 2412 wing with the same chord and span and run the same sweep. Then tick both sweeps under My runs and press Compare. Expect the 2412 curve to sit about 0.1 higher at every angle (its zero-lift angle is about -2.5 degrees) with the same slope. Where is the best L/D now?

## Common misconceptions

- 'Lift keeps growing with angle': it does, until separation; past stall more angle gives less lift and much more drag.
- 'The wind tunnel turns the wind, so the wing is not really at an angle': tilting the wind by 8 degrees and tilting the wing by 8 degrees are the same experiment; WindLab measures lift perpendicular to the wind either way.
- 'The slope should be 0.11 per degree as in the textbook': that is the two-dimensional value; a finite wing of aspect ratio 2 feels a strong downwash and makes less than half of it.

## Where the numbers come from

The pre-computed results were made with WindLab's own Quick preset in the dev container on 2026-11-03 (4 cores); the method's accuracy is documented in `docs/TEACHING_NOTES.md`, `validation/naca0012_tmr.md`, `validation/wing3d_mesh_study.md` and `validation/bluff_bodies.md`. Every coefficient in the app carries an expected-error range with a "Why this range?" note; the lesson *Why simulations disagree* teaches how to read it. Student answers are stored locally and exported with `windlab feedback export`.

Rendered from `windlab/lessons/lift-vs-angle/lesson.json` by `scripts/render_lessons.py`.
