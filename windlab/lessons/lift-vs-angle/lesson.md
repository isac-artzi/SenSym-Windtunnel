# Lift vs angle of attack — background reading

A wing makes lift by turning the air that flows past it downwards. The more you tilt the
wing into the wind (the larger the **angle of attack**), the more air it turns and the more
lift it makes — up to a point.

**The lift coefficient.** Lift depends on the air's density, the square of the speed and the
wing's area, so engineers divide it out: C_L = L / (½ ρ V² S). What is left depends only on
the shape, its angle to the wind and the Reynolds number. For a symmetric section like the
NACA 0012, C_L is zero at zero angle and changes sign with the angle.

**The lift slope.** In the "linear range" C_L rises in proportion to the angle. Thin-airfoil
theory gives a two-dimensional section a slope of 2π per radian, about 0.11 per degree. A real
wing of finite span makes less: air leaks around the tips and rolls into vortices that push
air downwards over the wing (downwash), so the wing feels a smaller angle than the geometric
one. For aspect ratio 2 (WindLab's default wing) the slope is about 0.045 per degree.

**Stall.** Above about 12° the pressure on the upper surface has to rise so steeply towards the
trailing edge that the slowed air in the boundary layer cannot follow; it separates and the
lift stops rising while the drag climbs. WindLab's steady simulation shows where this begins
but is only rough past it: the real flow there is unsteady.

**The drag polar** is C_L plotted against C_D. The angle where a line from the origin just
touches the curve has the best lift-to-drag ratio: for the default wing about 8°, L/D ≈ 9.

**What WindLab's numbers are worth here.** Lift on the Quick mesh is within a few percent of a
much finer mesh; the lift slope and the angle where lift stops rising are trustworthy. Drag is
about 9 % too high against the same model on a fine mesh, and more against measurements, so read
L/D as a lower estimate (see the "Why this range?" notes).
