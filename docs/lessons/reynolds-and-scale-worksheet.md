# Worksheet: Reynolds number and scale

*WindLab lesson `reynolds-and-scale` · about 50 minutes · uncalibrated prototype, not yet validated.*

Name: ____________________    Date: ____________

Change the speed, then change the size: which one matters for the coefficients? Five single runs of the NACA 0012 wing at 8 degrees show why wind-tunnel models work.

## Learning objectives

1. Compute a Reynolds number from speed, length and viscosity and say what it compares.
2. Show that two flows with the same Reynolds number give the same coefficients, whatever the size and speed, and that forces then scale with dynamic pressure and area.
3. Describe how the drag coefficient of a wing changes with Reynolds number and why WindLab's drag is high at low Reynolds numbers.

## Steps

Work through the steps in order. **Write each prediction before you run.**

### 1. What you will do

The same wing at 8 degrees in a 10, 20 and 40 m/s wind, then a half-size and a double-size copy of it at 20 m/s. Before each run WindLab shows the Reynolds number Re = V L / nu, with L the chord and nu the viscosity of air (1.46 x 10^-5 m^2/s). You will see which of speed and size the coefficients really care about, and how the forces in newtons change.

### 2. Predict before you run

If the airspeed doubles from 20 to 40 m/s, by what factor do you expect (a) the lift force in newtons and (b) the lift coefficient to change? And if instead the wing is made half as big (chord 0.15 m, span 0.3 m) at the same 20 m/s: what happens to CL, CD and the lift force? Write your predictions.

> 
> 
> 
> 
> 

### 3. Run 1: 10 m/s

- Shape: NACA 0012 wing, chord 0.3 m, span 0.6 m
- Airspeed: 10 m/s
- Angle of attack: 8°
- Mesh: Quick

Reynolds number about 205,000. Note CL, CD and the forces in newtons.

Write down the numbers you read:

> 
> 
> 
> 

### 4. Run 2: 20 m/s

- Shape: NACA 0012 wing, chord 0.3 m, span 0.6 m
- Airspeed: 20 m/s
- Angle of attack: 8°
- Mesh: Quick

Reynolds number about 410,000.

Write down the numbers you read:

> 
> 
> 
> 

### 5. Run 3: 40 m/s

- Shape: NACA 0012 wing, chord 0.3 m, span 0.6 m
- Airspeed: 40 m/s
- Angle of attack: 8°
- Mesh: Quick

Reynolds number about 820,000. Compare the three runs under My runs: tick them and press Compare.

Write down the numbers you read:

> 
> 
> 
> 

### 6. Observe: speed

Write down CL, CD and the lift force (N) for 10, 20 and 40 m/s. By what factor did the lift force grow each time the speed doubled? By how much did CL and CD change?

> 
> 
> 
> 
> 

### 7. Run 4: half size at 20 m/s

- Shape: NACA 0012 wing, chord 0.15 m, span 0.3 m
- Airspeed: 20 m/s
- Angle of attack: 8°
- Mesh: Quick

Chord 0.15 m, span 0.3 m: the same shape at half scale. Its Reynolds number equals that of the full-size wing at 10 m/s.

Write down the numbers you read:

> 
> 
> 
> 

### 8. Run 5: double size at 20 m/s

- Shape: NACA 0012 wing, chord 0.6 m, span 1.2 m
- Airspeed: 20 m/s
- Angle of attack: 8°
- Mesh: Quick

Chord 0.6 m, span 1.2 m. Its Reynolds number equals that of the full-size wing at 40 m/s. Compare all five runs.

Write down the numbers you read:

> 
> 
> 
> 

### 9. Observe: size

Compare the half-size wing at 20 m/s with the full-size wing at 10 m/s, and the double-size wing with the full-size wing at 40 m/s. What do you notice about CL and CD? And about the lift forces in newtons?

> 
> 
> 
> 
> 

### 10. Explain

Why do two wings of different size and speed give exactly the same coefficients when V x L is the same? And why does CD fall a little as the Reynolds number rises?

> 
> 
> 
> 
> 

### 11. Question 1

Doubling the airspeed from 20 to 40 m/s multiplies the lift force (in newtons) by about ...

- [ ] 2
- [ ] 4
- [ ] 8
- [ ] it stays the same

### 12. Question 2

The half-size wing at 20 m/s and the full-size wing at 10 m/s gave the same CL and CD to every digit. Why?

- [ ] Same Reynolds number: V x L is the same, so the flow pattern is identical
- [ ] WindLab reused the earlier result
- [ ] Coefficients never depend on anything
- [ ] It is a coincidence of these two numbers

### 13. Question 3

From Re 2 x 10^5 to 8 x 10^5 the drag coefficient fell from 0.043 to 0.039 while CL barely changed. The best explanation is ...

- [ ] Skin friction falls as the Reynolds number rises, and it is a good share of the drag
- [ ] The wing got smaller
- [ ] Lift and drag always fall together
- [ ] The mesh was coarser at high speed

### 14. Homework (optional)

Run the full-size wing at 5 m/s and at 80 m/s (Re about 1 x 10^5 and 1.6 x 10^6) and add them to the comparison. Does the trend in CD continue? Then read the 'Why this range?' note next to CD: at low Reynolds numbers a real wing has a laminar boundary layer over part of the chord, so measured drag is 20-40 % lower than WindLab's fully turbulent result.

---

Rendered from `windlab/lessons/reynolds-and-scale/lesson.json` by `scripts/render_lessons.py`; the same lesson runs in the app under *Lessons*.
