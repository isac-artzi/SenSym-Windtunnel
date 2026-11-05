# Worksheet: Aspect ratio and induced drag

*WindLab lesson `aspect-ratio` · about 50 minutes · uncalibrated prototype, not yet validated.*

Name: ____________________    Date: ____________

Three wings with the same section and chord but spans of 0.3, 0.6 and 1.2 m: why gliders have long, slender wings.

## Learning objectives

1. Define aspect ratio and show how the lift slope of a wing rises with it, towards the two-dimensional value.
2. Explain induced drag with the tip vortices and downwash, and check the estimate CDi = CL^2 / (pi e AR) against WindLab's numbers.
3. Judge when coefficients of different wings can be compared and when their reference areas get in the way.

## Steps

Work through the steps in order. **Write each prediction before you run.**

### 1. What you will do

Aspect ratio is span squared over area: span over chord for a rectangular wing. You will sweep three NACA 0012 wings of chord 0.3 m with spans 0.3, 0.6 and 1.2 m (aspect ratios 1, 2 and 4) through 0, 4 and 8 degrees at 20 m/s. Each wing's coefficients use its own planform area; Compare notes that the areas differ and that this is the standard way to compare wings - we compare shapes, not forces.

### 2. Predict before you run

The aspect-ratio-2 wing has a lift slope of about 0.045 per degree (lift lesson). What slope do you expect for the aspect-ratio-1 and the aspect-ratio-4 wing? Which wing will have the best lift-to-drag ratio at 8 degrees, and why?

> 
> 
> 
> 
> 

### 3. Run 1: aspect ratio 1 (span 0.3 m)

- Shape: NACA 0012 wing, chord 0.3 m, span 0.3 m
- Airspeed: 20 m/s
- Angles of attack: 0, 4, 8° (sweep)
- Mesh: Quick

A square wing. Three angles on one mesh: about 3 Quick runs.

Write down the numbers you read:

> 
> 
> 
> 

### 4. Run 2: aspect ratio 4 (span 1.2 m)

- Shape: NACA 0012 wing, chord 0.3 m, span 1.2 m
- Airspeed: 20 m/s
- Angles of attack: 0, 4, 8° (sweep)
- Mesh: Quick

Twice the default span. The mesh is bigger (about 670,000 cells), so each angle takes longer. For the aspect-ratio-2 wing use the sweep from the lift lesson (angles 0, 4, 8 are in it), then Compare the three sweeps.

Write down the numbers you read:

> 
> 
> 
> 

### 5. Observe

For each wing write down CL and CD at 0, 4 and 8 degrees and the L/D at 8 degrees. Compute the lift slope (CL at 8 degrees divided by 8) and the rise in drag from 0 to 8 degrees, CD(8) - CD(0).

> 
> 
> 
> 
> 

### 6. Explain

Compare your drag rises with the induced-drag estimate CDi = CL^2 / (pi e AR) with e = 0.9, using each wing's CL at 8 degrees. Why does the slender wing make more lift per degree and pay less drag for it?

> 
> 
> 
> 
> 

### 7. Question 1

Going from aspect ratio 1 to 4 (same chord, four times the span), the lift slope roughly ...

- [ ] halves
- [ ] stays the same
- [ ] doubles
- [ ] becomes zero

### 8. Question 2

Why does a longer, slenderer wing make more lift per degree?

- [ ] The tip vortices affect a smaller share of the wing, so the downwash and the loss of effective angle are smaller
- [ ] Longer wings are heavier
- [ ] The chord is larger
- [ ] The Reynolds number is higher

### 9. Question 3

Induced drag is about CL^2 / (pi e AR). At the same lift coefficient, doubling the aspect ratio ...

- [ ] doubles the induced drag
- [ ] halves the induced drag
- [ ] has no effect
- [ ] quarters the induced drag

### 10. Homework (optional)

Make a wing with span 2.4 m and chord 0.3 m (aspect ratio 8) and sweep 0, 4, 8 degrees. Predict its lift slope with Helmbold's formula first (about 0.082 per degree), then check. Note how the mesh grows with the span and how long the run takes. Add the forces in newtons to the comparison: which wing lifts most at 8 degrees, and is that the same wing that has the best L/D?

---

Rendered from `windlab/lessons/aspect-ratio/lesson.json` by `scripts/render_lessons.py`; the same lesson runs in the app under *Lessons*.
