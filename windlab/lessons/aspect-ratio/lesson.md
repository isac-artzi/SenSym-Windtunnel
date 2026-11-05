# Aspect ratio and induced drag — background reading

**Aspect ratio** AR = b²/S is span squared over planform area: span over chord for a rectangular
wing. Gliders have aspect ratios above 20; fighters about 3; WindLab's default wing has 2.

**Tip vortices and downwash.** On a lifting wing the pressure is lower above than below. At the
tips the air leaks round from the high-pressure side to the low-pressure side and rolls into a
pair of vortices that trail behind the wing. They push the air over the whole span slightly
downwards (downwash). The wing therefore flies at a smaller *effective* angle of attack than
its geometric one, and its lift force, which stands perpendicular to the local air, is tilted
slightly backwards.

**Two consequences.** The lift slope of a finite wing is lower than the two-dimensional 0.11 per
degree — Helmbold's formula gives about 0.026, 0.045 and 0.068 per degree for aspect ratios
1, 2 and 4 — and the backward tilt of the lift is a drag: the **induced drag**
C_Di ≈ C_L² / (π e AR), with e ≈ 0.9 for a rectangular wing. It grows with the square of the
lift and shrinks with aspect ratio, which is why slow, high-lift aircraft (gliders, airliners
at take-off) want long slender wings.

**Checking it with WindLab.** Each wing's coefficients use its own planform area. The rise in
C_D from 0° to 8° is almost all induced drag (the profile drag changes little), so
C_D(8°) − C_D(0°) can be compared with C_L(8°)²/(π · 0.9 · AR): WindLab gives 0.020, 0.024 and
0.025 against 0.020, 0.023 and 0.021 for aspect ratios 1, 2 and 4.

**Comparing wings of different size.** WindLab warns that the reference areas differ; here that
is intended — we compare shapes. The forces in newtons do differ: the aspect-ratio-4 wing has
four times the area of the aspect-ratio-1 wing and lifts about eight times as much at 8°.
