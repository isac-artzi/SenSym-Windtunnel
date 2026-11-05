# Reynolds number and scale — background reading

**Why models work.** A wind-tunnel model is smaller than the real aircraft, and often tested at
a different speed. What makes the measurement useful is that the *pattern* of the flow — where
it speeds up, where it separates, how thick the boundary layer is — depends on one pure number,
the **Reynolds number** Re = V L / ν: speed times length divided by the air's kinematic
viscosity (1.46 × 10⁻⁵ m²/s at sea level). Two flows with the same Reynolds number around the
same shape are scaled copies of each other, so their coefficients agree.

**Speed versus size.** Doubling the speed doubles Re; so does doubling the size. In this lesson
the half-size wing at 20 m/s and the full-size wing at 10 m/s have the same Reynolds number
(about 205,000) — and WindLab gives them the same C_L and C_D to every digit.

**Forces are another matter.** Lift = C_L × ½ ρ V² × S. With C_L nearly constant, doubling the
speed quadruples the force; halving the size (a quarter of the area) at the same speed cuts it
to a quarter. The half-size wing at 20 m/s and the full-size wing at 10 m/s even produce the
same force in newtons (the factor 4 in dynamic pressure cancels the factor ¼ in area).

**What changes with Re.** The boundary layer gets relatively thinner as Re rises, so skin
friction falls slowly (roughly as Re⁻¹ᐟ⁵ for a turbulent layer): C_D drops from 0.043 to 0.039
between Re 2 × 10⁵ and 8 × 10⁵ here, while C_L hardly moves.

**A caveat about WindLab.** Its turbulence model assumes the boundary layer is turbulent from
the leading edge. A real model wing at Re below about a million keeps laminar flow over a good
part of the chord, with less friction and sometimes a laminar separation bubble. Measured drag
is therefore 20–40 % lower than WindLab's at these Reynolds numbers — which is what the
expected-error range next to C_D says.
