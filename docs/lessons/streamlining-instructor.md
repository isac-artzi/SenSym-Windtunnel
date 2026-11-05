# Instructor guide: Streamlining a bluff body

*WindLab lesson `streamlining` · 50 minutes · uncalibrated prototype, not yet validated. Start the class with `windlab start --lesson streamlining` (teacher mode: the page opens in the lesson and hides the upload and reference-value controls).*

Put a flat plate, a cube, a cylinder, a sphere and a streamlined teardrop in the same wind and see where the drag goes: from a drag coefficient above 1 to below 0.1.

## Learning objectives

1. Rank bluff and streamlined shapes by drag coefficient and explain the order with the size of the wake.
2. Distinguish pressure (form) drag from skin-friction drag and say which dominates for each kind of body.
3. Recognise a case where the simulation's assumptions (a fully turbulent boundary layer) make it miss the textbook value, and say why.

## Minute by minute

| minutes | what happens |
|---:|:--|
| 0-5 | Objectives; prediction: rank the five shapes by drag coefficient. |
| 5-20 | Run (or load) the plate, cube and sphere; look at the pressure pictures and the flow slices. |
| 20-30 | Run (or load) the cylinder and the teardrop; Compare all five. |
| 30-40 | Explain: wakes, pressure versus friction drag, the sphere miss (drag crisis). |
| 40-50 | Comprehension questions, debrief, homework (fineness ratio). |

Total 50 minutes. Quick runs take 1-5 minutes each on a student laptop; a sweep about that per angle. If the laptops are slow, use the pre-computed results for every run except one, so that students see one live run settle.

## Expected results and answers

**Predict before you run** — prompt: Rank the five shapes from highest to lowest drag coefficient (on frontal area) and guess a number for each. Which shape do you expect to have the widest wake? Which the thinnest?

Accept any prediction; the point is to commit to one before the run. Ask two or three students to read theirs out.

**Run 1: flat plate**

Pre-computed on 2026-11-03 (Quick mesh, 198,454 cells, 43 s of solver time on 4 cores). Reynolds number 273,841; reference area 0.04 m².

CL = -0.000, CD = 1.2123, L/D = -0.0; 360 iterations (settled).

**Run 2: cube**

Pre-computed on 2026-11-03 (Quick mesh, 370,029 cells, 99 s of solver time on 4 cores). Reynolds number 273,841; reference area 0.04 m².

CL = -0.000, CD = 1.0269, L/D = -0.0; 454 iterations (settled).

Notes shown with the result: The lift kept swinging up and down (the flow may be unsteady, e.g. stalled). Treat the numbers as a rough guide.

**Run 3: sphere**

Pre-computed on 2026-11-03 (Quick mesh, 169,536 cells, 49 s of solver time on 4 cores). Reynolds number 273,841; reference area 0.03142 m².

CL = 0.083, CD = 0.2523, L/D = 0.3; 472 iterations (settled).

Notes shown with the result: The lift kept swinging up and down (the flow may be unsteady, e.g. stalled). Treat the numbers as a rough guide.

**Observe: the three bluff bodies** — prompt: Write down CD for the plate, the cube and the sphere, and describe the wake of each in the flow slice (how wide, how long). Open 'More details': is the drag mostly pressure or friction? (WindLab splits CD into a pressure and a viscous part in the CSV of the mesh study; here judge it from the pictures: a wide dark wake means pressure drag.)

Expected: the numbers in the result table(s) above.

**Run 4: cylinder across the wind**

Pre-computed on 2026-11-03 (Quick mesh, 548,119 cells, 442 s of solver time on 4 cores). Reynolds number 205,381; reference area 0.0675 m².

CL = -0.008, CD = 0.5744, L/D = -0.0; 1500 iterations (did not settle).

Notes shown with the result: The numbers did not fully settle before the iteration limit. Treat them as a rough guide. The lift kept swinging up and down (the flow may be unsteady, e.g. stalled). Treat the numbers as a rough guide.

**Run 5: streamlined teardrop**

Pre-computed on 2026-11-03 (Quick mesh, 83,399 cells, 16 s of solver time on 4 cores). Reynolds number 273,841; reference area 0.03142 m².

CL = -0.000, CD = 0.0685, L/D = -0.0; 357 iterations (settled).

**Observe: all five** — prompt: Rank the five shapes by CD as WindLab computed them and compare with your prediction and with the textbook values in the introduction. Which shapes agree with the textbook? Which do not?

Expected: the numbers in the result table(s) above.

**Explain** — prompt: Why does the teardrop have roughly a tenth of the sphere's drag with the same frontal area? And why might WindLab's sphere be far below the textbook 0.47?

Model answer: A bluff body's drag is almost all pressure drag: high pressure on the front, low pressure in the wide wake behind where the air could not follow the surface. The plate and cube (CD 1.21 and 1.03 here, textbook 1.2 and 1.05) show it plainly - their skin friction is nearly zero. The teardrop lets the air close in smoothly behind it, so the wake is thin and the pressure recovers; what is left is mostly friction (CD 0.068, half pressure and half friction). The sphere is WindLab's honest miss: it gives 0.25 against the textbook 0.47 at this Reynolds number. WindLab assumes the boundary layer is turbulent everywhere; a turbulent layer clings to the sphere further round before separating, which is exactly what happens to a real sphere only above its drag crisis (Re above about 3 x 10^5), where measured CD does drop to 0.1-0.2. At Re 274,000 a real sphere still has a laminar layer that separates early and a wide wake. The cylinder (0.57) sits between the textbook values for a very long cylinder (1.0-1.2) and a short one with free ends (0.6-0.8), and its lift swings because the real wake is unsteady.

**Question 1.** Which shape has the lowest drag coefficient on frontal area?

- Flat plate
- Cube
- Sphere
- **Streamlined teardrop (correct)**

  The teardrop: about 0.07 against 0.25 for WindLab's sphere (0.47 textbook) and more than 1 for the plate and the cube. Streamlining means letting the air close in behind the body.

**Question 2.** Where does most of a bluff body's drag come from?

- Skin friction on the front face
- **The pressure difference between the high-pressure front and the low-pressure wake (correct)**
- The weight of the body
- The reference area

  On the cube WindLab's viscous (friction) part of CD is essentially zero and the pressure part is 1.03. The wide wake behind a bluff body keeps the rear pressure low; the front is at nearly stagnation pressure.

**Question 3.** WindLab's sphere (CD 0.25) is well below the textbook 0.47. The most likely reason is ...

- The sphere is too small
- **WindLab's fully turbulent boundary layer stays attached longer, as a real sphere's does only above its drag crisis; at this Reynolds number a real sphere has a laminar layer that separates early (correct)**
- The frontal area is wrong
- Spheres have no drag

  This is a model limitation, not a mesh problem: the plate and the cube, whose separation is fixed by their sharp edges, agree with the textbook, while the smooth sphere depends on where the boundary layer separates - and that depends on whether it is laminar or turbulent.

**Homework (optional)** — Make teardrops with fineness ratios 2, 4, 6 and 8 (diameter 0.2 m) and run each at 20 m/s. Plot CD against fineness ratio: expect a minimum around 3-4, because slim bodies have more surface (friction) and fat ones more pressure drag. Also work out the NACA 0012 wing's drag coefficient on its frontal area from the lift lesson (CD 0.0171 on 0.18 m^2 planform; frontal area 0.036 x 0.6 m) and place it in the ranking.

## Common misconceptions

- 'A pointed nose is what reduces drag': the tail matters more; a blunt-nosed, pointed-tailed teardrop is a low-drag shape, a pointed-nosed, blunt-tailed one is not.
- 'Drag is friction': for bluff bodies it is almost all pressure drag from the wake; friction only dominates once the body is streamlined.
- 'The sphere result is a bug': it is the fully turbulent assumption; the same assumption is why WindLab's drag on wings is high, and the sharp-edged shapes, whose separation is fixed, agree with the handbook.

## Where the numbers come from

The pre-computed results were made with WindLab's own Quick preset in the dev container on 2026-11-03 (4 cores); the method's accuracy is documented in `docs/TEACHING_NOTES.md`, `validation/naca0012_tmr.md`, `validation/wing3d_mesh_study.md` and `validation/bluff_bodies.md`. Every coefficient in the app carries an expected-error range with a "Why this range?" note; the lesson *Why simulations disagree* teaches how to read it. Student answers are stored locally and exported with `windlab feedback export`.

Rendered from `windlab/lessons/streamlining/lesson.json` by `scripts/render_lessons.py`.
