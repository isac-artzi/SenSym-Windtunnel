# WindLab glossary

Rendered from `windlab/glossary.json` (version 2026-11-03.2); do not edit by hand. The same
definitions appear in the app behind every (?) button and in the Glossary panel, and in the lessons.

## Airspeed *(wind speed, freestream speed, V)*

The speed of the undisturbed air far from the part, in metres per second.

20 m/s is about 72 km/h. WindLab allows 1 to 100 m/s so the air can be treated as incompressible (Mach number below 0.3).

See also: Dynamic pressure, Reynolds number.

## Angle of attack *(alpha, α, AoA)*

The angle between the wind and the body's reference line (for a wing, its chord line).

In WindLab the part stays still and the wind is tilted: a positive angle means the wind comes from below, pushing the nose up. Lift grows roughly in proportion to the angle until the wing stalls.

See also: Lift, Stall, Lift slope.

## Angle sweep *(sweep, alpha sweep)*

A series of runs at several angles of attack on the same mesh: the classic wind-tunnel experiment.

WindLab makes the mesh once and solves the flow once per angle, then draws CL against angle, the drag polar and L/D against angle.

See also: Angle of attack, Drag polar.

## Aspect ratio *(AR)*

How slender a wing is: span squared divided by planform area (span ÷ chord for a rectangular wing).

Gliders have aspect ratios of 20 or more, fighter jets around 3. A higher aspect ratio gives a steeper lift slope and less induced drag; WindLab's default wing has aspect ratio 2.

See also: Span, Chord, Induced drag, Lift slope.

## Bluff body *(blunt body)*

A body whose flow separates early, leaving a wide wake: a sphere, a cylinder, a flat plate, a cube, a lorry.

Its drag is mostly pressure drag and its drag coefficient (on frontal area) is about 0.5 to 2, ten or more times that of a streamlined body of the same frontal area.

See also: Streamlined body, Flow separation, Wake, Frontal area.

## Boundary layer

The thin layer of air next to a surface where the air is slowed down by friction, from zero speed at the wall to the full speed a little way out.

It is a few millimetres thick on a model wing. Whether it is laminar (smooth) or turbulent (mixing) decides the skin friction and how easily the flow separates. WindLab assumes it is turbulent from the leading edge.

See also: Laminar and turbulent, Skin friction, y⁺ (y-plus).

## Camber

The curvature of a wing section: how much its centre line bows upwards.

A cambered section (NACA 2412) makes lift at zero angle of attack; a symmetric one (NACA 0012) makes none. Camber shifts the lift curve upwards without changing its slope much.

See also: NACA 4-digit code, Angle of attack.

## Chord *(c)*

The front-to-back length of a wing: the straight line from the leading edge to the trailing edge.

The chord is the reference length for a wing's Reynolds number and pitching moment. A tapered wing has a different chord at the root and the tip; WindLab then uses the mean aerodynamic chord.

See also: Span, Aspect ratio, Reference length.

## Convergence (settling) *(settled, iterations, residuals, wobble)*

The solver repeats its calculation (iterates) until lift and drag stop changing; then the result has settled, or converged.

WindLab stops when CL and CD have been steady for at least a hundred iterations and the equations are satisfied closely enough. The 'wobble' is how much they still moved at the end: a measure of settling, not of accuracy. A result that did not settle is a rough guide only.

See also: Solver (CFD, RANS), Expected-error range.

## Drag *(D)*

The part of the air force on a body that acts along the wind, holding the body back.

Drag has two sources: pressure differences between the front and the back (pressure or form drag) and friction of the air rubbing along the surface (skin-friction drag). A finite wing adds induced drag.

See also: Drag coefficient, Induced drag, Skin friction.

## Drag coefficient *(CD, C_D)*

Drag divided by the dynamic pressure of the wind and the reference area: CD = D / (½ ρ V² S).

Lower is more streamlined. Using the frontal area, a sphere has CD about 0.47 and a modern car about 0.25 to 0.3; using the planform area, a wing at low angle has CD about 0.01 to 0.02. Always check which reference area a CD uses before comparing numbers.

See also: Drag, Reference area, Bluff body, Streamlined body.

## Drag polar *(polar)*

A chart of lift coefficient against drag coefficient, one point per angle of attack.

It shows how much drag each amount of lift costs. The point where a straight line from the origin just touches the curve has the best lift-to-drag ratio.

See also: Lift coefficient, Drag coefficient, Lift-to-drag ratio, Angle sweep.

## Dynamic pressure *(q, ½ρV²)*

The pressure the moving air would exert if brought to a stop: q = ½ ρ V².

At 20 m/s in sea-level air q ≈ 245 Pa. All the coefficients divide forces by q times a reference area, and the pressure coefficient divides pressure differences by q.

See also: Lift coefficient, Pressure coefficient, Airspeed.

## Expected-error range *(likely range, validated, unvalidated)*

The band next to a coefficient saying where the true value most likely lies, judged from WindLab's own validation runs.

It comes from a small table built from comparisons with NASA's reference data and a mesh study. 'Unvalidated - treat as a trend only' means the shape, Reynolds number or angle is outside what was checked.

See also: Convergence (settling), Mesh.

## Fineness ratio

Length divided by maximum diameter of a streamlined body.

Around 3 to 4 gives the least drag for a teardrop: slimmer bodies have more surface (friction), fatter ones more pressure drag.

See also: Streamlined body.

## Flow separation *(separated flow)*

When the air stops hugging the surface and leaves it, forming a region of slow, swirling air (a wake).

Separation happens where the pressure rises too quickly along the surface. Bluff bodies always separate and have a wide wake; a streamlined body delays separation to its tail and has a thin wake.

See also: Stall, Wake, Boundary layer.

## Flow slice *(mid-span slice, cut)*

A cut through the air along the wind, through the middle of the part, coloured by air speed or pressure coefficient.

It shows the stagnation at the nose, the fast air over the top of a wing and the wake behind. It is one plane of a 3D flow: what happens nearer the tips is different.

See also: Streamline, Pressure coefficient.

## Frontal area *(projected area)*

The area of the body's silhouette seen from the front, straight into the wind.

The usual reference area for bluff bodies and vehicles: a sphere of diameter d has frontal area π d²/4.

See also: Reference area, Bluff body.

## Induced drag *(lift-induced drag)*

The extra drag a finite wing pays for making lift: the tip vortices tilt the air downwards and so tilt the lift force backwards.

It grows with the square of the lift coefficient and shrinks with aspect ratio: CDi ≈ CL² / (π e AR), with e about 0.9. This is why gliders have long, slender wings.

See also: Aspect ratio, Drag, Lift coefficient.

## Laminar and turbulent *(laminar, turbulent, transition)*

Laminar flow moves in smooth layers; turbulent flow is full of random swirls that mix fast and slow air.

A turbulent boundary layer has more skin friction but resists separation better. On a model at Re below about a million the boundary layer starts laminar and becomes turbulent part-way along (transition). WindLab assumes it is turbulent everywhere, which makes its drag at low Reynolds numbers higher than measured.

See also: Boundary layer, Reynolds number, Turbulence model (k-ω SST).

## Lattice-Boltzmann method (preview) *(LBM, instant preview, Tier 0)*

A fast way to compute a flow on a grid of cells by letting packets of air hop between neighbouring cells and collide; WindLab's instant preview uses it in your browser.

The preview is a 2D cut through the middle of the part at a Reynolds number of a few thousand (the full run is at hundreds of thousands), so it shows where the air speeds up, slows down and separates, but its lift and drag are only a rough sketch. Use the full simulation for numbers.

See also: Reynolds number, Flow slice, Flow separation.

## Lift *(L)*

The part of the air force on a body that acts at right angles to the wind.

For a wing flying level, lift points up and holds the aircraft in the air. WindLab measures lift perpendicular to the wind direction, whatever the angle of attack.

See also: Lift coefficient, Drag, Angle of attack.

## Lift coefficient *(CL, C_L)*

Lift divided by the dynamic pressure of the wind and the reference area: CL = L / (½ ρ V² S).

Dividing by ½ρV²S removes the effect of size and speed, so CL describes the shape and its angle to the wind alone. A small aircraft wing cruises around CL 0.3 to 0.5; a wing section can reach about 1.5 before it stalls.

See also: Lift, Dynamic pressure, Reference area, Drag coefficient.

## Lift slope *(lift-curve slope)*

How much the lift coefficient rises for each degree of angle of attack.

Thin-airfoil theory gives about 0.11 per degree for a 2D wing section. A real wing of finite span has a lower slope, the more so the smaller its aspect ratio: about 0.045 per degree for WindLab's default wing (aspect ratio 2).

See also: Angle of attack, Aspect ratio.

## Lift-to-drag ratio *(L/D)*

Lift divided by drag, or CL divided by CD: how much lift you get for each unit of drag.

Higher means more efficient. A glider reaches L/D of 40 or more; a small model wing in WindLab gives around 8 to 10. The angle with the best L/D is marked in a sweep.

See also: Lift coefficient, Drag coefficient, Drag polar.

## Mesh *(grid, cells)*

The division of the air around the part into hundreds of thousands of small cells; the solver computes the flow in each cell.

Finer cells capture more detail but take longer. WindLab's Quick mesh has about 0.4 million cells, with thin layers along the surface for the boundary layer. Results depend a little on the mesh: see the expected-error ranges.

See also: y⁺ (y-plus), Convergence (settling), Solver (CFD, RANS).

## NACA 4-digit code *(NACA code, NACA)*

A four-digit name for a classic family of wing sections: the first digit is the camber in percent of chord, the second where the camber peaks (in tenths of chord), the last two the thickness in percent.

NACA 0012: symmetric, 12 % thick. NACA 2412: 2 % camber peaking at 40 % of the chord, 12 % thick. NACA was the US agency that became NASA.

See also: Camber, Chord.

## Pitching moment coefficient *(CM, Cm, C_M)*

The nose-up or nose-down turning moment on the body divided by ½ ρ V² S and the reference length.

WindLab takes the moment about the reference point, the quarter-chord of a built-in wing. Positive means nose up (the leading edge rises).

See also: Lift coefficient, Chord, Reference length.

## Pressure coefficient *(Cp, C_p)*

The local pressure relative to the surrounding air, divided by the dynamic pressure: Cp = (p − p∞) / (½ ρ V²).

Cp = 1 where the air is brought to a complete stop (the stagnation point at the nose), 0 where the pressure equals the surrounding air, and negative where the air speeds up and the pressure drops (suction, blue in WindLab's pictures). The suction on the upper surface of a wing is where most of its lift comes from.

See also: Dynamic pressure, Stagnation point, Suction.

## Quick mesh (preset) *(Quick, preset)*

WindLab's everyday mesh size: about 0.4 million cells, a run of one to five minutes on a laptop.

Good for lift, trends and comparisons; its drag is a few percent to a third too high (see the expected-error ranges). Finer presets exist for later versions.

See also: Mesh, Expected-error range.

## Reference area *(S, Aref)*

The area that CL and CD are divided by. For wings it is the planform area (chord × span); for cars and bluff bodies it is the frontal area.

The choice is a convention, not physics: the same force gives a smaller coefficient with a larger area. WindLab's built-in wings use the planform area, the built-in bluff bodies their frontal area, and for an uploaded part it measures both from the surface and uses the frontal area for a body or the planform area for a wing-like part, unless you change it.

See also: Lift coefficient, Drag coefficient, Frontal area, Reference length.

## Reference length *(L, lRef)*

The length used in the Reynolds number and the moment coefficient: the chord for a wing, the diameter or length for a body.

WindLab also scales its virtual wind tunnel and mesh with the reference length, so a wrong value (for example from a units mistake) gives a wrong tunnel.

See also: Reynolds number, Chord, Reference area.

## Reynolds number *(Re)*

A pure number that compares the air's momentum with its stickiness: Re = V L / ν (speed × length ÷ kinematic viscosity).

Flows with the same Reynolds number behave alike, whatever their size, which is why wind-tunnel models work. A 0.3 m chord at 20 m/s gives Re ≈ 410,000; an airliner wing flies at tens of millions. Below about a million the boundary layer stays laminar for a while, which lowers drag compared with WindLab's fully turbulent model.

See also: Viscosity, Boundary layer, Laminar and turbulent.

## Skin friction *(friction drag, viscous drag)*

The drag from the air rubbing along the surface inside the boundary layer.

It is most of the drag of a streamlined body at low angle, and roughly doubles when the boundary layer turns from laminar to turbulent.

See also: Drag, Boundary layer.

## Solver (CFD, RANS) *(CFD, RANS, simpleFoam)*

The program that computes the airflow by solving the equations of fluid motion on the mesh: computational fluid dynamics (CFD).

WindLab uses the open-source OpenFOAM solver in its steady, Reynolds-averaged form (RANS): it computes the average flow and models the effect of turbulence rather than following every swirl.

See also: Turbulence model (k-ω SST), Mesh, Convergence (settling).

## Span *(b)*

The tip-to-tip width of a wing, measured across the wind.

Span times chord is the planform area of a rectangular wing. A longer span for the same area means a higher aspect ratio and less induced drag.

See also: Chord, Aspect ratio, Induced drag.

## Stagnation point

The spot on the front of a body where the oncoming air is brought to a stop and splits to flow around it.

The pressure there is the highest on the body (Cp = 1). On a wing it sits just below the leading edge at positive angles of attack.

See also: Pressure coefficient.

## Stall

The loss of lift when the angle of attack is so large that the air can no longer follow the upper surface and separates from it.

Lift stops rising and then drops while drag climbs steeply. WindLab's steady simulation predicts the linear range well but the stall angle and the lift after stall only roughly: the real flow there is unsteady.

See also: Angle of attack, Flow separation, Lift.

## STEP file *(STEP, .step, .stp, AP214)*

The standard exchange format for exact CAD geometry (ISO 10303): SolidWorks exports it with File > Save As > STEP AP214.

A STEP file keeps the true curved surfaces and says which units it uses, so WindLab can pre-select them; it still asks you to check the size. WindLab turns the surfaces into triangles itself (an STL) before meshing. Several bodies that touch are joined into one; separate bodies are an assembly, and WindLab uses the largest unless you ask for all.

See also: STL file, Units (mm vs m), Watertight.

## STL file *(STL)*

A 3D file format that describes a surface as a list of triangles; every CAD program can export it.

STL files carry no units, so WindLab asks which units the part was drawn in. The surface must be closed (watertight) to be meshed: export one solid body.

See also: Watertight, Units (mm vs m), STEP file.

## Streamline *(streamlines)*

The path a particle of air follows through the flow; in a steady flow, the lines the air moves along.

Streamlines bunch together where the air speeds up and spread out where it slows down. WindLab traces them in the mid-span slice; near the wing tips, where air also moves sideways, they are only a sketch.

See also: Flow slice.

## Streamlined body *(streamlining)*

A body shaped so the air can follow it all the way to a pointed tail: a teardrop, an airship, a wing section.

Separation is delayed to the very back, the wake is thin, and the drag is mostly skin friction: a drag coefficient on frontal area of about 0.05 to 0.1, compared with 0.47 for a sphere.

See also: Bluff body, Fineness ratio, Skin friction.

## Suction

Pressure below that of the surrounding air (negative pressure coefficient).

Air that speeds up over a curved surface loses pressure, so it pulls on the surface rather than pushing. The suction peak near a wing's leading edge is where the pressure coefficient is most negative.

See also: Pressure coefficient.

## Taper ratio *(taper)*

Tip chord divided by root chord: 1 for a rectangular wing, smaller for a wing that narrows towards the tips.

Taper changes how lift is spread along the span and lowers the weight of a real wing.

See also: Chord, Span.

## Turbulence model (k-ω SST) *(k-omega SST, SST)*

The set of equations that stands in for the swirls the solver does not compute directly.

k-ω SST is the usual choice for wings and vehicles. WindLab runs it fully turbulent: no laminar flow anywhere.

See also: Solver (CFD, RANS), Laminar and turbulent.

## Units (mm vs m)

WindLab works in metres; CAD parts are usually drawn in millimetres, so a part exported as '300' may be 300 mm or 300 m.

A units mistake changes the Reynolds number by a factor of a thousand and the forces by a million. Check the size WindLab reports before running.

See also: STL file, Reynolds number.

## Viscosity *(kinematic viscosity, nu, ν)*

How sticky a fluid is: its resistance to being sheared.

WindLab uses sea-level air at 15 °C with kinematic viscosity ν = 1.46 × 10⁻⁵ m²/s and density ρ = 1.225 kg/m³. Viscosity is what makes a boundary layer form and what causes skin-friction drag.

See also: Reynolds number, Boundary layer.

## Wake

The region of slowed, often swirling air behind a body.

A wide wake means a lot of pressure drag: the pressure behind the body is low because the air cannot close in smoothly. In the flow slice it is the dark (slow) region downstream of the part.

See also: Flow separation, Bluff body.

## Watertight *(closed surface)*

A surface with no holes: every triangle edge joins exactly one neighbour, so inside and outside are well defined.

Only a watertight surface can be meshed. Surfaces exported as sheets, or several bodies touching along an edge, are not.

See also: STL file.

## Wind tunnel *(virtual wind tunnel)*

A duct through which air is blown over a fixed model to measure the forces on it; WindLab is a virtual one computed on your laptop.

WindLab's tunnel has no walls close to the part (20 reference lengths to the sides), so there is no blockage correction; its results compare with free-flight theory.

See also: Solver (CFD, RANS).

## y⁺ (y-plus) *(y+, yPlus)*

The height of the first mesh cell at the wall, measured in the boundary layer's own units.

y⁺ below 1 means the cell sits inside the thin laminar sublayer and the boundary layer is resolved; y⁺ of 30 to 300 means a wall function bridges the gap. WindLab's Quick mesh has y⁺ around 10 to 150 and uses a wall function; the 2D validation was wall-resolved.

See also: Boundary layer, Mesh.
