# Why simulations disagree — background reading

**Two kinds of error.** A simulation can be wrong because of *how* it solves its equations
(numerical error: cell size, iterations, rounding) and because of *what* its equations assume
(model error: here, steady flow and a boundary layer that is turbulent from the leading edge).
Refining the mesh reduces the first and does nothing for the second.

**A mesh study** runs the same case on a family of meshes and watches the answer settle. WindLab's
team did this for the default wing (validation/wing3d_mesh_study.md): Quick (0.37 million
cells), Standard (0.81 million) and fine (3.4 million). Lift at 10° changed by about 1 %; drag by
7–8 %. Richardson extrapolation estimates the mesh-converged drag at 0.0516 at 10° and 0.0156 at
0°, against Quick's 0.0558 and 0.0171: Quick drag is about 9 % too high, and always too high.

**Why lift converges and drag does not.** Lift is the pressure difference between two surfaces,
which coarse cells capture. Drag is a small difference between large pressure forces plus the
skin friction inside a boundary layer a few millimetres thick; the Quick mesh's five wall layers
cover only half of it, so the pressure part of the drag is too high.

**The model error.** Even the mesh-converged drag at 0° (0.0156) is above the wall-resolved 2D
section at the same Reynolds number (0.0125) and far above wind-tunnel measurements with
natural transition (0.009–0.011). The remaining gap is the fully turbulent assumption: a real
boundary layer at Re 4 × 10⁵ stays laminar over much of the chord and has less friction. Only a
different model (transition prediction) could close it. The 2D solver chain itself was checked
against NASA's reference data at Re 6 million: lift within 2 %, drag within 1 %.

**The expected-error range.** This is what the "likely … to …" band next to every WindLab
coefficient encodes: a numerical part from the mesh study and, for drag, a model part from the
comparison with measurements, both relative to the WindLab value, inside a validated envelope
of shapes, Reynolds numbers and angles. Outside the envelope the honest answer is
"Unvalidated — treat as a trend only".
