# WindLab in one page

**WindLab is a virtual wind tunnel on your own computer.** You make a wing or upload a part from SolidWorks,
set the airspeed and the angle of attack, and WindLab meshes the part, runs a real CFD solver (OpenFOAM®)
and shows the lift and drag coefficients, the pressure on the part and the airflow around it. It runs
inside Ubuntu Linux (on Windows through WSL2; on a Mac through Docker); you use it in your normal browser.
Nothing is sent over the internet.

> **Uncalibrated prototype — an educational simulation whose results may be inaccurate.** Use the
> numbers to learn and compare, not to design, build or fly real hardware. You must accept the notice in
> [`DISCLAIMER.md`](../DISCLAIMER.md) (shown on first start) before you can run it. Next to every coefficient
> WindLab shows the range where the true value most likely lies.

## Install in three commands (Windows 11, admin rights, 16 GB, about 30–45 minutes of downloads)

1. **PowerShell as administrator** (right-click Start → *Terminal (Admin)*), then restart when asked:
   ```powershell
   wsl --install -d Ubuntu-24.04
   ```
   After the restart an Ubuntu window opens: choose a Linux user name and password.
2. **In the Ubuntu window**, two lines (they download WindLab, install OpenFOAM and STEP import, then check the laptop):
   ```bash
   git clone https://github.com/isac-artzi/SenSym-Windtunnel.git windlab
   bash windlab/scripts/install_wsl.sh
   ```
   It ends with `windlab doctor`: every line should say PASS (or WARN with a tip).
3. **Start WindLab** (in a new Ubuntu window, or after `export PATH="$HOME/windlab-venv/bin:$PATH"`):
   ```bash
   windlab start
   ```
   The first time it shows the notice and asks you to type `yes`. Open **http://localhost:8080/** in Edge,
   Chrome or Firefox and accept the notice there too. Keep the Ubuntu window open; Ctrl+C stops WindLab.

Mac or Linux, and the full guide with troubleshooting: `docs/INSTALL.md`. Later updates: `windlab update`.

## Your first run (about 5 minutes)

1. **Step 1 · Shape**: leave *A built-in wing (NACA 4-digit)*, press **Make this wing** (NACA 0012, chord 0.3 m,
   span 0.6 m). The 3D view shows the wing; the wind blows along the red arrow.
2. **Step 2 · Conditions**: airspeed 20 m/s, angle of attack 8°. Try **Preview the airflow** for an instant 2D sketch.
3. **Step 3 · Run**: press **Run the wind tunnel** and watch C<sub>L</sub> and C<sub>D</sub> settle (1–3 minutes).
4. **Step 4 · Results**: C<sub>L</sub> ≈ 0.36, C<sub>D</sub> ≈ 0.04 and L/D with their likely ranges, the pressure
   on the wing, the flow through the middle. **Open the report** gives a printable page (save as PDF); CSV files
   and the OpenFOAM case can be downloaded too. A second run of the same wing is faster (the mesh is reused).

Then try **Angle sweep** in Step 2 (several angles on one mesh: lift curve, drag polar, best L/D), tick two
finished runs under **My runs** and press **Compare**, or open a **Lesson** (five guided 50-minute lessons with
pre-computed results; start with *Lift vs angle of attack*). The **Take the tour** button at the top walks you
through the page.

## Uploading your own SolidWorks part

- In SolidWorks: **File → Save As → STEP AP214** (best: exact surfaces, units included) or **STL**. Export **one
  solid body** (not a surface body; combine several bodies with *Insert → Features → Combine* first). Fins,
  wings and brackets must be merged into the body; parts that merely touch along an edge are separate bodies.
- In WindLab, Step 1 → *My own part*, choose the file, **check the units**: a STEP file pre-selects its own
  units (confirm the size WindLab reports); for an STL file pick the units it was drawn in (SolidWorks parts
  are usually millimetres). "This part would be 300 m long" means the units are wrong.
- The wind blows along **x**, **z** is up. If the part faces the wrong way, use **Turn 90°** or **Suggest
  orientation**. WindLab guesses whether the part is **a wing** (coefficients on its planform area, the outline
  from above) or **a body** (frontal area, the silhouette from the front, as for cars and rockets); check the
  radio in Step 1 and change it if the guess is wrong. The length and area can also be typed in.
- Keep parts simple for the Quick mesh: a few thousand to a few hundred thousand triangles, no internal
  cavities, nothing much thinner than 1/500 of the part's length. Ten example parts (bracket, car body, rocket
  with fins, drone arm, wing with winglet, thin plate, …) are in `validation/robustness/parts/` of the WindLab
  folder (`~/windlab`); all of them mesh and solve in under a minute on a laptop.

## Please send us your feedback

The **How was this?** panel under the results asks two quick questions; your answers stay on your laptop.
At the end, in the Ubuntu window:

```bash
windlab feedback export
```

writes `windlab-feedback-<date>.csv` into the current folder (on Windows, type `explorer.exe .` to open that
folder in File Explorer and drag the file to your Desktop). E-mail the file to your instructor. It contains
your answers and an anonymous usage summary (number of runs, problems seen, run times, laptop size); no file
names, shape names or anything personal except the name or initials you typed. If anything did not work as
described, please write down the step and the exact message you saw, or open an issue on GitHub. Thank you!
