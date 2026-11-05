# Instructor guide: Why simulations disagree

*WindLab lesson `why-simulations-disagree` · 50 minutes · uncalibrated prototype, not yet validated. Start the class with `windlab start --lesson why-simulations-disagree` (teacher mode: the page opens in the lesson and hides the upload and reference-value controls).*

Three meshes, one wing, three different drag numbers - and a wind tunnel that disagrees with all of them. Mesh and model literacy from WindLab's own validation data.

## Learning objectives

1. Tell numerical error (mesh, iterations) from model error (the assumptions of steady, fully turbulent RANS) and name an example of each.
2. Read a mesh study: say which coefficient is mesh-converged and which is not, and estimate the mesh-converged value.
3. Use the expected-error range next to a WindLab result correctly: as a band, with its basis, and as 'unvalidated' when the case is outside what was checked.

## Minute by minute

| minutes | what happens |
|---:|:--|
| 0-5 | Objectives; prediction: how much would CL and CD change on a mesh nine times finer? |
| 5-15 | One Quick run of the default wing at 10 degrees (live or pre-computed); read its expected-error ranges and 'Why this range?'. |
| 15-30 | Read the mesh study table (Quick / Standard / fine / extrapolated, the 2D reference, the measurement) and fill in the observation box. |
| 30-40 | Explain: numerical versus model error; why lift converges and drag does not; the fully turbulent assumption. |
| 40-50 | Comprehension questions, debrief, homework. |

Total 50 minutes. Quick runs take 1-5 minutes each on a student laptop; a sweep about that per angle. If the laptops are slow, use the pre-computed results for every run except one, so that students see one live run settle.

## Expected results and answers

**Predict before you look** — prompt: The Quick mesh has about 370,000 cells. If the same case were run on a mesh with 3.4 million cells (nine times more), by how many percent do you expect CL and CD to change? Which of the two do you expect to change more, and why? Would a finer mesh bring the result closer to a wind-tunnel measurement?

Accept any prediction; the point is to commit to one before the run. Ask two or three students to read theirs out.

**Run: the default wing at 10 degrees on Quick**

Pre-computed on 2026-11-03 (Quick mesh, 372,592 cells, 79 s of solver time on 4 cores). Reynolds number 410,762; reference area 0.18 m².

CL = 0.452, CD = 0.0559, L/D = 8.1; 388 iterations (settled).

**Observe: the mesh study** — prompt: From the table in this lesson: by how many percent does CL change from Quick to fine? And CD at 10 degrees? And CD at 0 degrees? How far is the Quick drag at 0 degrees from the wall-resolved 2D value, and from the measurements?

Expected: the numbers in the result table(s) above.

**Explain** — prompt: Sort the differences in the table into numerical error and model error. Why does lift settle on a coarse mesh while drag does not? Why is even the finest mesh far from the measurement?

Model answer: Lift comes from the pressure difference between the two surfaces, which even coarse cells capture: Quick is within 1 % of the fine mesh. Drag is a small difference between large pressure forces plus the skin friction in a boundary layer a few millimetres thick; the Quick cells cover only half of that layer, so its pressure drag is too high by 30-50 % at zero lift and the total by 9 %. That is numerical error, and the three-mesh study lets you extrapolate it away (0.0156). The remaining gap to the wall-resolved 2D section (0.0125) is partly the mesh family and the tips. The gap from there to the measurement (0.009-0.011) is model error: WindLab assumes a turbulent boundary layer everywhere, while a real wing at Re 400,000 keeps laminar flow over much of the chord and has less friction. No mesh refinement can fix that; only a different model (transition) could. This is why WindLab's expected-error range for drag is wide and one-sided, and why it says 'unvalidated' for shapes and conditions nobody has checked.

**Question 1.** Refining the mesh from 0.37 to 3.4 million cells changed CL by about 1 % and CD by about 7 %. Which number should you trust more from a Quick run?

- CD
- **CL (correct)**
- Neither
- Both equally

  Lift is mesh-converged on Quick to about 1 %; drag is not. Read Quick drag as an upper estimate and compare runs on the same mesh with each other.

**Question 2.** WindLab's drag at zero lift (0.0171) is well above the measured 0.009-0.011 for this wing section at this Reynolds number. The two main reasons are ...

- **A coarse mesh (numerical) and the fully turbulent boundary-layer assumption (model) (correct)**
- A wrong reference area and a wrong air density
- Gravity and compressibility
- Stall and the tip vortices

  Roughly 60 % of the gap is mesh and wall treatment, 40 % the fully turbulent assumption (teaching notes and validation/wing3d_mesh_study.md). Reference area and density are inputs, not errors, and at 20 m/s the air is incompressible.

**Question 3.** 'A finer mesh always gives the right answer.' Is that true?

- True
- **False: it removes numerical error, but the model's assumptions (steady, fully turbulent RANS) remain (correct)**

  The mesh-converged drag (0.0156) is still 40-70 % above the measurement because of the model, not the mesh. Knowing which error you are reducing is the heart of simulation literacy.

**Homework (optional)** — Run the same case at 0 degrees and compare its drag with the table (Quick 0.0171). Then open the CSV of your run and find the 'CD likely low / high' columns: write one sentence on how you would report this drag in a lab report, with its range and its basis. Read validation/wing3d_mesh_study.md in the WindLab repository for the full study.

## Common misconceptions

- 'The computer's answer is exact': it is the exact answer to an approximate question; the mesh and the model both leave their mark.
- 'More cells always means closer to the measurement': refinement removes numerical error only; the fully turbulent model keeps WindLab's drag high however fine the mesh.
- 'A range next to a number means the number is useless': the range is what makes the number usable - it says how far to trust it and compares like with like (trends between runs on the same mesh are far tighter than the absolute drag).

## Where the numbers come from

The pre-computed results were made with WindLab's own Quick preset in the dev container on 2026-11-03 (4 cores); the method's accuracy is documented in `docs/TEACHING_NOTES.md`, `validation/naca0012_tmr.md`, `validation/wing3d_mesh_study.md` and `validation/bluff_bodies.md`. Every coefficient in the app carries an expected-error range with a "Why this range?" note; the lesson *Why simulations disagree* teaches how to read it. Student answers are stored locally and exported with `windlab feedback export`.

Rendered from `windlab/lessons/why-simulations-disagree/lesson.json` by `scripts/render_lessons.py`.
