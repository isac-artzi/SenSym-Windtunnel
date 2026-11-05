# Instructor guide: Reynolds number and scale

*WindLab lesson `reynolds-and-scale` · 50 minutes · uncalibrated prototype, not yet validated. Start the class with `windlab start --lesson reynolds-and-scale` (teacher mode: the page opens in the lesson and hides the upload and reference-value controls).*

Change the speed, then change the size: which one matters for the coefficients? Five single runs of the NACA 0012 wing at 8 degrees show why wind-tunnel models work.

## Learning objectives

1. Compute a Reynolds number from speed, length and viscosity and say what it compares.
2. Show that two flows with the same Reynolds number give the same coefficients, whatever the size and speed, and that forces then scale with dynamic pressure and area.
3. Describe how the drag coefficient of a wing changes with Reynolds number and why WindLab's drag is high at low Reynolds numbers.

## Minute by minute

| minutes | what happens |
|---:|:--|
| 0-5 | Objectives; prediction: what changes when the speed doubles, and when the size halves? |
| 5-20 | Three speed runs (10, 20, 40 m/s) - live or pre-computed; compute Re for each by hand. |
| 20-30 | Two size runs (chord 0.15 and 0.6 m at 20 m/s); Compare all five. |
| 30-40 | Explain: the Reynolds number, the forces in newtons, the laminar-turbulent caveat. |
| 40-50 | Comprehension questions, debrief, homework. |

Total 50 minutes. Quick runs take 1-5 minutes each on a student laptop; a sweep about that per angle. If the laptops are slow, use the pre-computed results for every run except one, so that students see one live run settle.

## Expected results and answers

**Predict before you run** — prompt: If the airspeed doubles from 20 to 40 m/s, by what factor do you expect (a) the lift force in newtons and (b) the lift coefficient to change? And if instead the wing is made half as big (chord 0.15 m, span 0.3 m) at the same 20 m/s: what happens to CL, CD and the lift force? Write your predictions.

Accept any prediction; the point is to commit to one before the run. Ask two or three students to read theirs out.

**Run 1: 10 m/s**

Pre-computed on 2026-11-03 (Quick mesh, 372,571 cells, 78 s of solver time on 4 cores). Reynolds number 205,381; reference area 0.18 m².

CL = 0.352, CD = 0.0430, L/D = 8.2; 375 iterations (settled).

**Run 2: 20 m/s**

Pre-computed on 2026-11-03 (Quick mesh, 372,578 cells, 75 s of solver time on 4 cores). Reynolds number 410,762; reference area 0.18 m².

CL = 0.360, CD = 0.0410, L/D = 8.8; 377 iterations (settled).

**Run 3: 40 m/s**

Pre-computed on 2026-11-03 (Quick mesh, 372,620 cells, 77 s of solver time on 4 cores). Reynolds number 821,524; reference area 0.18 m².

CL = 0.364, CD = 0.0394, L/D = 9.2; 381 iterations (settled).

**Observe: speed** — prompt: Write down CL, CD and the lift force (N) for 10, 20 and 40 m/s. By what factor did the lift force grow each time the speed doubled? By how much did CL and CD change?

Expected: the numbers in the result table(s) above.

**Run 4: half size at 20 m/s**

Pre-computed on 2026-11-03 (Quick mesh, 372,599 cells, 77 s of solver time on 4 cores). Reynolds number 205,381; reference area 0.045 m².

CL = 0.352, CD = 0.0430, L/D = 8.2; 377 iterations (settled).

**Run 5: double size at 20 m/s**

Pre-computed on 2026-11-03 (Quick mesh, 372,550 cells, 75 s of solver time on 4 cores). Reynolds number 821,524; reference area 0.72 m².

CL = 0.364, CD = 0.0394, L/D = 9.2; 379 iterations (settled).

**Observe: size** — prompt: Compare the half-size wing at 20 m/s with the full-size wing at 10 m/s, and the double-size wing with the full-size wing at 40 m/s. What do you notice about CL and CD? And about the lift forces in newtons?

Expected: the numbers in the result table(s) above.

**Explain** — prompt: Why do two wings of different size and speed give exactly the same coefficients when V x L is the same? And why does CD fall a little as the Reynolds number rises?

Model answer: The flow pattern around a shape depends only on the balance between the air's momentum and its viscosity, which is the Reynolds number V L / nu: same Re, same pattern, same coefficients - WindLab gives CL 0.352 and CD 0.0430 for both the half-size wing at 20 m/s and the full-size wing at 10 m/s, to every digit. The forces then follow from the coefficients: lift = CL x (1/2 rho V^2) x S, so doubling the speed quadruples the force (3.9 N, 15.9 N, 64 N) while CL hardly moves (0.352, 0.360, 0.364). The small drop of CD from 0.043 to 0.039 as Re rises is skin friction: a thinner boundary layer rubs less. One caveat: WindLab assumes the boundary layer is turbulent everywhere; a real model at these Reynolds numbers keeps laminar flow over part of the chord and has lower drag still.

**Question 1.** Doubling the airspeed from 20 to 40 m/s multiplies the lift force (in newtons) by about ...

- 2
- **4 (correct)**
- 8
- it stays the same

  Lift = CL x (1/2 rho V^2) x S. CL changes by only about 1 %, so the force follows V^2: four times.

**Question 2.** The half-size wing at 20 m/s and the full-size wing at 10 m/s gave the same CL and CD to every digit. Why?

- **Same Reynolds number: V x L is the same, so the flow pattern is identical (correct)**
- WindLab reused the earlier result
- Coefficients never depend on anything
- It is a coincidence of these two numbers

  0.15 m x 20 m/s = 0.3 m x 10 m/s. With the same Reynolds number the two flows are scaled copies of each other, so every coefficient agrees. This is the principle behind wind-tunnel models.

**Question 3.** From Re 2 x 10^5 to 8 x 10^5 the drag coefficient fell from 0.043 to 0.039 while CL barely changed. The best explanation is ...

- **Skin friction falls as the Reynolds number rises, and it is a good share of the drag (correct)**
- The wing got smaller
- Lift and drag always fall together
- The mesh was coarser at high speed

  The friction coefficient of a turbulent boundary layer falls slowly with Reynolds number (roughly as Re to the power -1/5). Pressure and induced drag hardly change.

**Homework (optional)** — Run the full-size wing at 5 m/s and at 80 m/s (Re about 1 x 10^5 and 1.6 x 10^6) and add them to the comparison. Does the trend in CD continue? Then read the 'Why this range?' note next to CD: at low Reynolds numbers a real wing has a laminar boundary layer over part of the chord, so measured drag is 20-40 % lower than WindLab's fully turbulent result.

## Common misconceptions

- 'A smaller model gives smaller coefficients': coefficients already divide out size and speed; only the Reynolds number changes them, and only slightly.
- 'Doubling the speed doubles the lift': lift goes with the square of the speed; CL stays almost constant.
- 'Same Reynolds number means same forces': it means the same coefficients; the forces still scale with dynamic pressure times area (here the half-size wing at 20 m/s and the full-size wing at 10 m/s happen to give the same force because the factors cancel).

## Where the numbers come from

The pre-computed results were made with WindLab's own Quick preset in the dev container on 2026-11-03 (4 cores); the method's accuracy is documented in `docs/TEACHING_NOTES.md`, `validation/naca0012_tmr.md`, `validation/wing3d_mesh_study.md` and `validation/bluff_bodies.md`. Every coefficient in the app carries an expected-error range with a "Why this range?" note; the lesson *Why simulations disagree* teaches how to read it. Student answers are stored locally and exported with `windlab feedback export`.

Rendered from `windlab/lessons/reynolds-and-scale/lesson.json` by `scripts/render_lessons.py`.
