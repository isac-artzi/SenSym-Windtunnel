# Streamlining a bluff body — background reading

**Two kinds of drag.** Skin friction is the air rubbing along the surface inside the boundary
layer. Pressure (form) drag is the difference between the high pressure on the front of a body
and the low pressure behind it. Which one dominates depends on the shape.

**Bluff bodies** — a flat plate, a cube, a cylinder, a sphere — have flow that separates from
the surface early and leaves a wide wake of slow, swirling air. The pressure in the wake stays
low, so the body is pushed from the front and not pushed back from behind: almost all its drag
is pressure drag. On frontal area, textbook drag coefficients are about 1.2 for a square plate,
1.05 for a cube, 1.0–1.2 for a long cylinder across the wind and 0.47 for a sphere (below its
drag crisis).

**Streamlined bodies** taper to a point so the air can close in smoothly behind them. The wake is
thin, the pressure recovers, and what is left is mostly friction: a teardrop with a fineness
ratio (length ÷ diameter) of 3–4 has a drag coefficient of 0.05–0.1 on frontal area — a tenth of
the sphere's with the same silhouette. Slimmer bodies have more surface and more friction;
fatter ones more pressure drag; the minimum lies in between.

**Reference area.** For bodies the drag coefficient uses the frontal area, the silhouette seen
from the front. A wing uses its planform area instead, so wing and body coefficients are not
directly comparable unless you convert.

**Where WindLab is honest about missing.** The plate and the cube agree with the textbook: their
separation is fixed by sharp edges. The sphere does not (WindLab 0.25 against 0.47). Its
separation depends on whether the boundary layer is laminar or turbulent, and WindLab assumes
turbulent everywhere — which is what a real sphere only does above its drag crisis (Re above
about 3 × 10⁵), where its drag coefficient really does fall to 0.1–0.2. The cylinder's wake is
unsteady in reality (vortex shedding), so a steady solver gives a rough average and the lift
trace keeps swinging.
