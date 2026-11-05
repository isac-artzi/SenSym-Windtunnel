# Worksheet: Streamlining a bluff body

*WindLab lesson `streamlining` · about 50 minutes · uncalibrated prototype, not yet validated.*

Name: ____________________    Date: ____________

Put a flat plate, a cube, a cylinder, a sphere and a streamlined teardrop in the same wind and see where the drag goes: from a drag coefficient above 1 to below 0.1.

## Learning objectives

1. Rank bluff and streamlined shapes by drag coefficient and explain the order with the size of the wake.
2. Distinguish pressure (form) drag from skin-friction drag and say which dominates for each kind of body.
3. Recognise a case where the simulation's assumptions (a fully turbulent boundary layer) make it miss the textbook value, and say why.

## Steps

Work through the steps in order. **Write each prediction before you run.**

### 1. What you will do

Five built-in bodies in a 20 m/s wind at zero angle: a flat plate facing the wind, a cube, a cylinder lying across the wind, a sphere and a streamlined teardrop. For bodies the drag coefficient uses the frontal area (the silhouette seen from the front), so the numbers compare directly with textbook tables: about 1.2 for a square plate, 1.05 for a cube, 1.0-1.2 for a long cylinder, 0.47 for a sphere below its drag crisis, and 0.05-0.1 for a good streamlined body.

### 2. Predict before you run

Rank the five shapes from highest to lowest drag coefficient (on frontal area) and guess a number for each. Which shape do you expect to have the widest wake? Which the thinnest?

> 
> 
> 
> 
> 

### 3. Run 1: flat plate

- Shape: built-in plate: height 0.2, width 0.2
- Airspeed: 20 m/s
- Angle of attack: 0°
- Mesh: Quick

A 0.2 x 0.2 m plate square to the wind. Look at the pressure picture: red (Cp near 1) over the whole front, blue behind. In the flow slice, how wide is the slow region behind it?

Write down the numbers you read:

> 
> 
> 
> 

### 4. Run 2: cube

- Shape: built-in cube: side 0.2
- Airspeed: 20 m/s
- Angle of attack: 0°
- Mesh: Quick

Same frontal area as the plate. Does the extra depth change the drag much?

Write down the numbers you read:

> 
> 
> 
> 

### 5. Run 3: sphere

- Shape: built-in sphere: diameter 0.2
- Airspeed: 20 m/s
- Angle of attack: 0°
- Mesh: Quick

Diameter 0.2 m, Reynolds number 274,000. Where does the flow leave the surface (where does the colour in the slice change from fast to slow)?

Write down the numbers you read:

> 
> 
> 
> 

### 6. Observe: the three bluff bodies

Write down CD for the plate, the cube and the sphere, and describe the wake of each in the flow slice (how wide, how long). Open 'More details': is the drag mostly pressure or friction? (WindLab splits CD into a pressure and a viscous part in the CSV of the mesh study; here judge it from the pictures: a wide dark wake means pressure drag.)

> 
> 
> 
> 
> 

### 7. Run 4: cylinder across the wind

- Shape: built-in cylinder: diameter 0.15, length 0.45
- Airspeed: 20 m/s
- Angle of attack: 0°
- Mesh: Quick

Diameter 0.15 m, three diameters long, with flat ends. This one is hard for a steady solver: the real wake sheds vortices left and right (the Karman street), so the lift trace keeps swinging and the run goes to its iteration limit. Treat its CD as a rough average.

Write down the numbers you read:

> 
> 
> 
> 

### 8. Run 5: streamlined teardrop

- Shape: built-in teardrop: diameter 0.2, fineness 3
- Airspeed: 20 m/s
- Angle of attack: 0°
- Mesh: Quick

Same frontal area as the sphere, three times as long as it is wide. Compare the wake with the sphere's, then Compare all five runs under My runs.

Write down the numbers you read:

> 
> 
> 
> 

### 9. Observe: all five

Rank the five shapes by CD as WindLab computed them and compare with your prediction and with the textbook values in the introduction. Which shapes agree with the textbook? Which do not?

> 
> 
> 
> 
> 

### 10. Explain

Why does the teardrop have roughly a tenth of the sphere's drag with the same frontal area? And why might WindLab's sphere be far below the textbook 0.47?

> 
> 
> 
> 
> 

### 11. Question 1

Which shape has the lowest drag coefficient on frontal area?

- [ ] Flat plate
- [ ] Cube
- [ ] Sphere
- [ ] Streamlined teardrop

### 12. Question 2

Where does most of a bluff body's drag come from?

- [ ] Skin friction on the front face
- [ ] The pressure difference between the high-pressure front and the low-pressure wake
- [ ] The weight of the body
- [ ] The reference area

### 13. Question 3

WindLab's sphere (CD 0.25) is well below the textbook 0.47. The most likely reason is ...

- [ ] The sphere is too small
- [ ] WindLab's fully turbulent boundary layer stays attached longer, as a real sphere's does only above its drag crisis; at this Reynolds number a real sphere has a laminar layer that separates early
- [ ] The frontal area is wrong
- [ ] Spheres have no drag

### 14. Homework (optional)

Make teardrops with fineness ratios 2, 4, 6 and 8 (diameter 0.2 m) and run each at 20 m/s. Plot CD against fineness ratio: expect a minimum around 3-4, because slim bodies have more surface (friction) and fat ones more pressure drag. Also work out the NACA 0012 wing's drag coefficient on its frontal area from the lift lesson (CD 0.0171 on 0.18 m^2 planform; frontal area 0.036 x 0.6 m) and place it in the ranking.

---

Rendered from `windlab/lessons/streamlining/lesson.json` by `scripts/render_lessons.py`; the same lesson runs in the app under *Lessons*.
