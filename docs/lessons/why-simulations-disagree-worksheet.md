# Worksheet: Why simulations disagree

*WindLab lesson `why-simulations-disagree` · about 50 minutes · uncalibrated prototype, not yet validated.*

Name: ____________________    Date: ____________

Three meshes, one wing, three different drag numbers - and a wind tunnel that disagrees with all of them. Mesh and model literacy from WindLab's own validation data.

## Learning objectives

1. Tell numerical error (mesh, iterations) from model error (the assumptions of steady, fully turbulent RANS) and name an example of each.
2. Read a mesh study: say which coefficient is mesh-converged and which is not, and estimate the mesh-converged value.
3. Use the expected-error range next to a WindLab result correctly: as a band, with its basis, and as 'unvalidated' when the case is outside what was checked.

## Steps

Work through the steps in order. **Write each prediction before you run.**

### 1. What you will do

Every simulation answers a question with two kinds of error. Numerical error comes from how the equations are solved: cell size, the number of iterations, rounding. Model error comes from what the equations leave out: here, that the flow is steady and that the boundary layer is turbulent from the leading edge. WindLab's team ran the default wing on three meshes and against a wall-resolved 2D reference; in this lesson you read that study like an engineer and learn what the expected-error range next to every coefficient means.

### 2. Predict before you look

The Quick mesh has about 370,000 cells. If the same case were run on a mesh with 3.4 million cells (nine times more), by how many percent do you expect CL and CD to change? Which of the two do you expect to change more, and why? Would a finer mesh bring the result closer to a wind-tunnel measurement?

> 
> 
> 
> 
> 

### 3. Run: the default wing at 10 degrees on Quick

- Shape: NACA 0012 wing, chord 0.3 m, span 0.6 m
- Airspeed: 20 m/s
- Angle of attack: 10°
- Mesh: Quick

This is the case of the mesh study. After it finishes, open 'Why this range?' under CL and under CD and read how the ranges were made. If you run it live, your numbers should match the pre-computed ones to three digits: the same inputs give the same answer on any computer.

Write down the numbers you read:

> 
> 
> 
> 

### 4. Observe: the mesh study

From the table in this lesson: by how many percent does CL change from Quick to fine? And CD at 10 degrees? And CD at 0 degrees? How far is the Quick drag at 0 degrees from the wall-resolved 2D value, and from the measurements?

> 
> 
> 
> 
> 

### 5. Explain

Sort the differences in the table into numerical error and model error. Why does lift settle on a coarse mesh while drag does not? Why is even the finest mesh far from the measurement?

> 
> 
> 
> 
> 

### 6. Question 1

Refining the mesh from 0.37 to 3.4 million cells changed CL by about 1 % and CD by about 7 %. Which number should you trust more from a Quick run?

- [ ] CD
- [ ] CL
- [ ] Neither
- [ ] Both equally

### 7. Question 2

WindLab's drag at zero lift (0.0171) is well above the measured 0.009-0.011 for this wing section at this Reynolds number. The two main reasons are ...

- [ ] A coarse mesh (numerical) and the fully turbulent boundary-layer assumption (model)
- [ ] A wrong reference area and a wrong air density
- [ ] Gravity and compressibility
- [ ] Stall and the tip vortices

### 8. Question 3

'A finer mesh always gives the right answer.' Is that true?

- [ ] True
- [ ] False: it removes numerical error, but the model's assumptions (steady, fully turbulent RANS) remain

### 9. Homework (optional)

Run the same case at 0 degrees and compare its drag with the table (Quick 0.0171). Then open the CSV of your run and find the 'CD likely low / high' columns: write one sentence on how you would report this drag in a lab report, with its range and its basis. Read validation/wing3d_mesh_study.md in the WindLab repository for the full study.

---

Rendered from `windlab/lessons/why-simulations-disagree/lesson.json` by `scripts/render_lessons.py`; the same lesson runs in the app under *Lessons*.
