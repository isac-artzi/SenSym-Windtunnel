# Run-time profile and speed-ups

Measured in the Docker container on an Apple M-series desktop (Ubuntu 24.04 arm64, OpenFOAM
v2606) with **4 MPI ranks**, one solver run at a time, by `scripts/profile_run.py`. Raw
numbers: `validation/perf/*.json`. The case is the app's default wing (NACA 0012, chord
0.3 m, span 0.6 m, 20 m/s, Quick preset, about 372,000 cells). **Uncalibrated prototype -
not yet validated.** The Windows test workstation's numbers (14-core Xeon, WSL2 with 8
processors, 2026-11-03) for the same cold 10 deg run: 197 s on 4 ranks (meshing 34 s),
140 s on 7.

## 1. Where the time goes (before any change, 2026-11-03)

One cold Quick run, NACA 0012 at 10 deg, 4 ranks (`baseline-a10-np4.json`):

| stage | seconds | share | notes |
|:--|---:|---:|:--|
| blockMesh | 0.1 | 0.1 % | background box |
| surfaceFeatureExtract | 0.02 | - | |
| decomposePar | 0.07 | - | scotch, 4 ranks |
| snappyHexMesh | 11.4 | 14 % | refine 5.1 s, snap 3.3 s, layers 3.1 s (from its log) |
| checkMesh | 0.3 | 0.4 % | |
| potentialFoam | 0.4 | 0.5 % | 5 non-orthogonal correctors |
| simpleFoam | 66.6 | 84 % | 392 iterations, 169 ms per iteration (first 50: 174 ms, last 100: 168 ms); p needs 1.2 GAMG cycles per iteration, Ux 5.6 smoother sweeps |
| final write (fields, yPlus, wallShearStress, two sampled surfaces) | ~0.3 | - | inside the simpleFoam time |
| 3D surface extraction (WindLab, Python) | 0.03 | - | 32k faces to 1.3 MB |
| flow slice decode + encode | 0.005 | - | |
| streamlines (two windows) | 0.27 | 0.3 % | |
| **run_case wall** | **79.1** | | |
| **total wall incl. pictures** | **79.4** | | |

Result: CL 0.4515, CD 0.05581 (pressure 0.04679, viscous 0.00902), settled at iteration 390,
peak memory 1.0 GB. At 0 deg (`baseline-a0-np4.json`): 67.0 s, 330 iterations, CD 0.01711.

**Reading.** The solver is 84 % of a run and the meshing 14 %; everything WindLab does
in Python afterwards is under half a second. Within the solver, the recorded force
histories show CL and CD settled to 0.01 % by iteration ~200 (the live run above reads
0.4515 / 0.05581 from iteration 184 on), but the stop rule (200-iteration window plus a
100-iteration lag) could not fire before iteration 300 and fired at 390. So about half the
solver iterations of a Quick run were spent confirming an answer that was already there.
Per-iteration cost is dominated by the momentum and turbulence smoothers (p converges in
one or two GAMG cycles).

Baseline 6-angle sweep (-4, 0, 4, 8, 12, 16 deg; `baseline-sweep6-np4.json`): 659 s in all;
meshing once (11.7 s), then per angle 59 / 53 / 59 / 63 / 137 / 276 s (367, 322, 364, 377,
763 and 1500 iterations; 12 deg settles slowly and 16 deg is past stall - unsteady, it runs
to the 1500-iteration cap). Every angle started from scratch (uniform flow + potentialFoam).

## 2. Accuracy guard

Every solver or mesh change had to keep the NACA 0012 Quick results within 0.5 % (CL) and
1.5 % (CD) of the reference values: CL 0.4513 / CD 0.05578 at 10 deg, CD 0.01711 at 0 deg.
Note that every cold run meshes afresh and the cell count wanders by a few hundred
(371,8xx-372,6xx); that alone moves CL by up to 0.2 %.

| run (4 ranks) | CL at 10 deg | CD at 10 deg | CD at 0 deg | within guard |
|:--|---:|---:|---:|:--|
| reference (task text) | 0.4513 | 0.05578 | 0.01711 | - |
| baseline today (old stop rule) | 0.4515 | 0.05581 | 0.01711 | yes |
| new stop rule, cold | 0.4513 (+0.0 %) | 0.05578 (+0.0 %) | 0.01711 (-0.0 %) | yes |
| mesh reused + warm start from the 4 deg solution | 0.4521 (+0.2 %) | 0.05591 (+0.2 %) | 0.01711 (-0.0 %) | yes |
| snappyHexMesh settings (5 feature-snap passes, 30 % balance threshold), cold | 0.4516 (+0.1 %) | 0.05581 (+0.1 %) | 0.01713 (+0.1 %) | yes |
| rejected: looser inner tolerances (p relTol 0.1, U/k/omega 0.2) | 0.4512 | 0.05577 | - | yes, but slower |
| rejected: hierarchical decomposition (4 1 1) | 0.4513 | 0.05579 | - | yes, but no faster |

The 2D validation case (`airfoil_2d`, `scripts/validate_naca0012.py`) keeps its own strict
stop rule and template; nothing in it changed (see section 4), and the 10 deg angle was
re-run after all changes (section 5).

## 3. Changes measured

All at 4 ranks in the Docker container, NACA 0012 wing, Quick preset. "Cold" = a fresh shape:
meshing + potential-flow start. "Second run" = the same shape at another angle after one
finished run. Raw numbers in `validation/perf/`.

| change | cold run, 10 deg | cold run, 0 deg | second run (10 deg after 4 deg) | kept? |
|:--|---:|---:|---:|:--|
| baseline (2026-11-03 morning) | 79.1 s, 392 it | 67.0 s, 330 it | 79.1 s (meshed and started from scratch again) | - |
| **3c. stop rule** 100/50 window, 0.25 % / 0.5 %, old rule as fallback | 55.1 s, 245 it (-30 %) | 45.6 s, 194 it (-32 %) | 55.1 s | yes |
| **3a. mesh reuse** (cache per shape/preset/orientation/ranks) | - | - | saves the 11.4 s of meshing | yes |
| **3b. warm start** from the nearest stored solution (changeDictionary 0.2 s instead of potentialFoam 0.4 s) | - | 29.6 s from the 4 deg solution, 165 it | 39.6 s, 214 it | yes |
| **3e. snappyHexMesh**: nFeatureSnapIter 10 -> 5, maxLoadUnbalance 0.10 -> 0.30 | 53.1 s (meshing 11.4 -> 9.3 s) | 43.5 s (meshing 9.7 s) | - | yes |
| 3c. p relTol 0.05 -> 0.1 and U/k/omega relTol 0.1 -> 0.2 | 56.8 s: 164 instead of 172 ms/it but 268 instead of 245 iterations | - | - | no |
| 3d. decomposition scotch -> hierarchical (4 1 1) | 55.8 s, 175 ms/it (scotch 172) | - | - | no (scotch stays) |
| 3e. nSolveIter 50 -> 30 | meshing unchanged (11.7 s) | | | no |
| 3e. nLayerIter 50 -> 30 | meshing unchanged (11.7 s) | | | no |
| 3e. no writeFlags | meshing unchanged (11.4 s) | | | no |

The mesh-only variants were timed with `--mesh-only`; cell counts stayed within 0.1 % of
the baseline (371,8xx), the mesh passed every check, the maximum non-orthogonality was
63.4-65.0 deg in all of them and the layer coverage 96 % (4.75 of 5 layers). The final
partition imbalance after snappyHexMesh is 0.6-0.7 % whatever the threshold during
refinement (the last balance is unconditional), so the solver's load balance is unchanged.

Not tried (noted for later): a coarser/first-order start then the final schemes (the 2D
case does this; the 3D wing converges in ~200 iterations from the potential-flow start
anyway, so there is little to gain), fewer function objects (forces, forceCoeffs and
solverInfo every iteration cost under 1 ms each), GAMG agglomeration settings (p takes 1-2
cycles per iteration already).

### Combined effect (4 ranks, Docker container)

| scenario | before | after | speed-up |
|:--|---:|---:|---:|
| one cold single run (NACA 0012, 10 deg) | 79.1 s | 53.1 s | 1.5x |
| one cold single run, 0 deg | 67.0 s | 43.5 s | 1.5x |
| a second run of the same shape at another angle (10 deg after 4 deg) | 79.1 s | 39.6 s | 2.0x |
| a second run at 0 deg after 4 deg | 67.0 s | 29.6 s | 2.3x |
| 6-angle sweep -4..16 deg, cold mesh, through the app | 659 s | 489 s | 1.35x (1.9x without the stalled 16 deg angle) |

**Sweep, per angle** (`baseline-sweep6-np4.json` -> `after-sweep6-np4.json`):

| angle | before: seconds (iterations) | after: seconds (iterations) | CL before -> after | CD before -> after |
|---:|---:|---:|:--|:--|
| -4 | 59 (367) | 41 (237, cold start) | -0.1760 -> -0.1768 | 0.02271 -> 0.02277 |
| 0 | 53 (322) | 35 (199, from -4) | -0.0001 -> -0.0009 | 0.01711 -> 0.01713 |
| 4 | 59 (364) | 29 (163, from 0) | 0.1759 -> 0.1751 | 0.02271 -> 0.02268 |
| 8 | 63 (377) | 37 (209, from 4) | 0.3590 -> 0.3594 | 0.04087 -> 0.04091 |
| 12 | 137 (763) | 47 (248, from 8) | 0.5351 -> 0.5354 | 0.07574 -> 0.07580 |
| 16 | 276 (1500, cap) | 288 (1500, cap) | 0.5455 -> 0.5539 | 0.1777 -> 0.1779 |

Every settled angle agrees with the cold baseline within 0.5 % (CL) and 0.3 % (CD); at 16 deg
the flow is past stall and unsteady, both runs hit the cap and report a rough average.
Meshing 11.5 -> 9.4 s. The 12 deg angle is the clearest warm-start win: from the 8 deg
solution it settles in 248 iterations where the cold start oscillated for 763.

An earlier warm-chain sweep without the fallback rule (`app-warm-sweep6-np4.json`, 616 s)
showed the risk the fallback removes: there 12 deg needed 985 iterations because a slowly
oscillating CL satisfies 200-iteration means before 100-iteration ones.

Where the remaining time goes: the stalled 16 deg angle alone is 59 % of the sweep. Past
stall the steady solver cannot settle; the 1500-iteration cap is the cost of offering those
angles at all.

## 4. What else changed around the runs

* The reported CL/CD of a settled run are the means over the stop rule's window (100
  iterations, or 200 when the fallback fired); a run that hits the cap keeps the
  200-iteration mean as before. On a settled steady case the two differ by < 0.01 %.
* `changeDictionary` (an OpenFOAM utility) sets the far-field freestream values of U, k
  and omega on the inherited fields; its dictionary is a WindLab template rendered with
  the same clamped numbers as everything else (`system/changeDictionaryDict`).
* The cache lives in `<data>/mesh-cache/`, keyed by a hash of the STL bytes and the
  rendered mesh dictionaries (so a turned upload, another preset, another reference
  length or rank count, or any template change gets a fresh mesh). Housekeeping keeps the
  three most recently used meshes and four solutions each, under 2 GB.
* A warm start is only taken from a solution at the same airspeed (within 1 %): a
  different speed changes k, omega and the Reynolds number, and scaling the fields was not
  worth the risk for the gain.

## 5. Final checks (after every change, 2026-11-03 evening)

* `pytest -m slow` (the NACA 0012 Quick run through the API, 4 ranks): passed in 57 s
  end to end, against 1-2 minutes before.
* `scripts/validate_naca0012.py --alpha 10 --np 4 --no-write` (the wall-resolved 2D case
  with its own strict stop rule): CL 1.0603, CD 0.01248 in 123 s - the stored validation
  row is CL 1.0604, CD 0.01248 (119 s). No regression.
* The 6-angle sweep and the single runs above were run through the app's own run manager
  (`scripts/profile_run.py --data`), so they include the mesh cache, the warm starts and
  the pictures exactly as a student gets them.

* Re-checked after task 011 (STEP import, robustness corpus, report export; no solver or mesh
  setting changed) on 2026-11-04: cold Quick run at 10 deg, 4 ranks: 371,830 cells, CL 0.4516
  (+0.07 % against 0.4513), CD 0.05581 (+0.05 % against 0.05578), settled at iteration 244 in 53 s;
  `pytest -m slow` passed in 58 s. The ten student-like parts of `validation/robustness.md` all
  meshed and solved with the same settings.

* Re-checked after task 013 (the refinement band around thin plates and fins of *uploads*; the
  snappyHexMesh template gained two placeholders that stay empty for built-in shapes) on
  2026-11-04 evening (`validation/perf/task013-guard-a10-np4.json`): cold Quick run at 10 deg,
  4 ranks: 371,956 cells, CL 0.4517 (+0.09 % against 0.4513), CD 0.05582 (+0.07 % against
  0.05578), settled at iteration 253 in 55.5 s; mesh checks all passed, 96 % layer coverage, as
  before. The band itself, on the 4-fin rocket upload: 86,600 -> 138,000 cells and 15 -> 24 s
  for a lift that went from 67-90 % of the drag to 0.7 % (`validation/robustness.md`).

What the Windows workstation should see (14-core Xeon under WSL2, 4 ranks, 197 s cold before): the
solver and meshing shares are the same, so roughly 130 s for a cold Quick run, 90-100 s
for a repeat run of the same shape, and on the default 7 ranks about two thirds of that.
To be confirmed with `windlab benchmark --np 4` on the laptop; the benchmark now also
records the laptop's speed for the time estimates on the page.

