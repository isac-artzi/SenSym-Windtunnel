# Worksheet: Lift vs angle of attack

*WindLab lesson `lift-vs-angle` · about 50 minutes · uncalibrated prototype, not yet validated.*

Name: ____________________    Date: ____________

Tilt the wind into a wing, step by step, and watch the lift rise - until it stops rising. One sweep of six angles on the default NACA 0012 wing.

## Learning objectives

1. Describe how the lift coefficient of a wing changes with angle of attack, and read the lift slope from a chart.
2. Explain why the curve bends over at high angles (stall) and why a symmetric wing gives zero lift at zero angle.
3. Use the drag polar to find the angle with the best lift-to-drag ratio.

## Steps

Work through the steps in order. **Write each prediction before you run.**

### 1. What you will do

A wing makes lift by deflecting air downwards. The angle between the wind and the wing is the angle of attack. In a real wind tunnel you would turn the model a few degrees at a time and read the force balance after each turn; here WindLab does the same with one mesh and six solver runs. The wing is the default NACA 0012: a symmetric section, chord 0.3 m, span 0.6 m (aspect ratio 2), in a 20 m/s wind.

### 2. Predict before you run

Sketch (on paper) and describe in words how you expect the lift coefficient CL to change as the angle of attack goes from -4 to 16 degrees. Will it be a straight line? What happens at 0 degrees for a symmetric wing? Does it keep rising all the way to 16 degrees? Write your prediction here before running.

> 
> 
> 
> 
> 

### 3. Run the angle sweep

- Shape: NACA 0012 wing, chord 0.3 m, span 0.6 m
- Airspeed: 20 m/s
- Angles of attack: -4, 0, 4, 8, 12, 16° (sweep)
- Mesh: Quick

Six angles on one mesh take about six Quick runs (6-10 minutes on a laptop). Watch the CL and CD traces settle for each angle. If time is short, use the pre-computed results: the WindLab team ran exactly this case.

Write down the numbers you read:

> 
> 
> 
> 

### 4. Observe

From the results table write down CL at -4, 4, 8, 12 and 16 degrees, and the angle with the best L/D. Work out the lift slope between -4 and 8 degrees (change in CL divided by change in angle) and between 12 and 16 degrees.

> 
> 
> 
> 
> 

### 5. Explain

Why is the lift slope so much smaller than the 0.11 per degree a two-dimensional wing section gives, and why does the curve flatten between 12 and 16 degrees? Compare with your prediction.

> 
> 
> 
> 
> 

### 6. Question 1

Between -4 and 8 degrees the lift coefficient rises by about the same amount per degree. What is this slope for this wing?

- [ ] about 0.011 per degree
- [ ] about 0.045 per degree
- [ ] about 0.11 per degree
- [ ] about 0.5 per degree

### 7. Question 2

Why does the lift curve bend over between 12 and 16 degrees?

- [ ] The wind speed drops at high angles
- [ ] The air separates from the upper surface: the beginning of stall
- [ ] The mesh runs out of cells
- [ ] Drag pushes the wing backwards

### 8. Question 3

At -4 degrees the drag coefficient equals that at +4 degrees and the lift is equal and opposite. Why?

- [ ] Because the solver reuses the 4-degree result
- [ ] Because the NACA 0012 is symmetric: turned upside down it is the same wing
- [ ] Because drag never depends on the angle
- [ ] Because a sweep uses one mesh for all angles

### 9. Homework (optional, 20 minutes)

Make a NACA 2412 wing with the same chord and span and run the same sweep. Then tick both sweeps under My runs and press Compare. Expect the 2412 curve to sit about 0.1 higher at every angle (its zero-lift angle is about -2.5 degrees) with the same slope. Where is the best L/D now?

---

Rendered from `windlab/lessons/lift-vs-angle/lesson.json` by `scripts/render_lessons.py`; the same lesson runs in the app under *Lessons*.
