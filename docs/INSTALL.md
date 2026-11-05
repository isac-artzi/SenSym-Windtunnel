# Installing WindLab

WindLab is a virtual wind tunnel that runs on your own computer: the simulation software (the
open-source OpenFOAM® solver) runs inside Ubuntu Linux, and you use WindLab in your normal web
browser at `http://localhost:8080/`. Nothing is sent over the internet while you use it.

> **Educational simulation — results may be inaccurate.** Read [DISCLAIMER.md](../DISCLAIMER.md).
> WindLab asks you to accept it the first time you start it (in the terminal and in the browser).

Pick your operating system:

- [Windows 10/11 (WSL2)](#windows-1011-wsl2) — the usual route for students, about 30–45 minutes
- [macOS (Apple Silicon and Intel)](#macos-apple-silicon-and-intel) — Docker Desktop, about 20–30 minutes
- [Linux](#linux) — Ubuntu 24.04 natively (22.04 with Python 3.12); other distributions through Docker
- then [Verify the installation and make a first run](#verify-the-installation-and-make-a-first-run),
  [Update or uninstall](#update-or-uninstall) and [Troubleshooting](#troubleshooting)

**You need** 16 GB of memory (8 GB works for the Quick mesh), about 10 GB of free disk space, an
internet connection for the installation, and administrator rights on the computer. No GitHub
account is needed: the repository is public.

**Getting the files.** Every route below starts with

```bash
git clone https://github.com/isac-artzi/SenSym-Windtunnel.git windlab
```

which puts WindLab into a folder called `windlab`. If you do not have git, open
<https://github.com/isac-artzi/SenSym-Windtunnel> in a browser, click **Code → Download ZIP**,
unpack it and rename the folder to `windlab`; the scripts work the same from an unpacked ZIP
(only `windlab update` then needs a new ZIP instead of `git pull`).

---

## Windows 10/11 (WSL2)

WindLab runs inside Ubuntu 24.04 on WSL2 (Windows Subsystem for Linux), which is built into Windows
10 (version 2004 or later) and Windows 11. Time: about 30–45 minutes, mostly downloads.

### 1. Turn on WSL2 and install Ubuntu 24.04 (5 minutes + a restart)

1. Right-click the **Start** button → **Terminal (Admin)** (or **Windows PowerShell (Admin)**) → **Yes**.
2. Type:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

   Expected output: a few lines about enabling features and downloading Ubuntu, ending with a request
   to restart. (Already have Ubuntu? `wsl -l -v` lists it; it must say `VERSION 2` and the distribution
   must be Ubuntu 24.04: open it and type `lsb_release -d`.)
3. **Restart** the computer.
4. After the restart an Ubuntu window opens by itself (if not: Start menu → **Ubuntu 24.04**). Wait
   for "Installing, this may take a few minutes...", then type a **Linux user name** (lower case, no
   spaces) and a **password** twice. Nothing appears while you type a password; that is normal. Remember
   it: Ubuntu asks for it when a command needs `sudo`.

Check: in the Ubuntu window, `lsb_release -d` prints `Description: Ubuntu 24.04.x LTS`.

### 2. Give Linux enough memory and cores (2 minutes, optional but recommended)

By default WSL2 gets half of the computer's memory. In Windows, open **Notepad**, paste

```ini
[wsl2]
memory=12GB
processors=8
```

and save it as `.wslconfig` (with the leading dot, **not** `.wslconfig.txt`: choose *All files* as
the type) in your user folder, `C:\Users\<your name>\`. Rule of thumb: `memory` = your memory minus
4 GB, `processors` = all your cores (Task Manager → Performance → CPU shows both). Then in PowerShell
run `wsl --shutdown` and open Ubuntu again. `windlab doctor` (below) prints the file it recommends for
your computer and says whether the current one matches.

### 3. Install WindLab (20–40 minutes, mostly downloads)

All the remaining commands are typed in the **Ubuntu** window. Paste these two lines:

```bash
git clone https://github.com/isac-artzi/SenSym-Windtunnel.git windlab
bash windlab/scripts/install_wsl.sh
```

The script asks for your Ubuntu password once (for `apt`), then prints what it does:

```
==> Checking this computer
    [ok] Ubuntu on WSL2 (Windows)
    [ok] Ubuntu 24.04
    [ok] internet connection
    [ok] Python 3.12 (/usr/bin/python3)

==> Step 1 of 5: Ubuntu packages (you may be asked for your password)
==> Step 2 of 5: OpenFOAM v2606 (a few hundred MB; this is the slow part)
==> Step 3 of 5: WindLab's files in /home/<you>/windlab
==> Step 4 of 5: Python environment in /home/<you>/windlab-venv
==> Step 5 of 5: windlab doctor (meshes and solves a tiny test case, about 30 s)
[PASS] OpenFOAM: v2606 ...
...
============================================================
WindLab is installed. Start it with:

    windlab start
```

It installs the Ubuntu packages and OpenFOAM, uses the `windlab` folder you cloned, creates a Python
environment in `~/windlab-venv` with WindLab (and STEP import) in it, puts `windlab` on your PATH for
new Ubuntu windows, and runs `windlab doctor`. It skips what is already done, never deletes anything,
and can be run again at any time. (The same script is `scripts/install_linux.sh`; it detects WSL2.)

### 4. Start WindLab

```bash
source ~/windlab-venv/bin/activate     # not needed in a new Ubuntu window
windlab start
```

The first time, WindLab prints the educational-use notice and asks you to type `yes`. Then:

```
WindLab 0.1.0 (uncalibrated prototype - not yet validated)
  Educational simulation - results may be inaccurate. See DISCLAIMER.md.
  shapes and runs: /home/<you>/.local/share/windlab
  cores per run:   7 (of 8; --procs N to change)
  keeps:           the last 20 finished runs, at most 10 GB

  Open this address in your browser:  http://localhost:8080/
  Press Ctrl+C here to stop WindLab.
```

Open **http://localhost:8080/** in Edge, Chrome or Firefox **on Windows** (WSL2 forwards
`localhost`). Accept the notice on the page, then continue with
[Verify the installation](#verify-the-installation-and-make-a-first-run). Keep the Ubuntu window open
while you use WindLab; **Ctrl+C** in it stops WindLab.

---

## macOS (Apple Silicon and Intel)

OpenFOAM does not run natively on macOS, so WindLab runs in a Linux container with Docker Desktop.
The image is built on your Mac from `dev/Dockerfile` (Ubuntu 24.04 + OpenFOAM v2606 + WindLab's
tools) for your processor, arm64 or amd64. Time: 5 minutes to install Docker, 15–25 minutes to build
the image the first time (about 3 GB of downloads), seconds afterwards.

### 1. Install Docker Desktop (5 minutes)

Download it from <https://www.docker.com/products/docker-desktop/> (choose *Apple silicon* or *Intel
chip*), open the `.dmg`, drag Docker to Applications, start it and finish its welcome screens. Docker
Desktop is free for personal and educational use. Then give it enough memory: **Docker menu →
Settings → Resources**: set **Memory** to at least **8 GB** (12 GB if you have 32 GB) and **CPUs** to
all your cores; click *Apply & restart*. Check in Terminal:

```bash
docker --version           # Docker version 27.x or newer
docker compose version     # Docker Compose version v2.x
```

### 2. Get WindLab and build the image (15–25 minutes the first time)

In **Terminal** (Applications → Utilities → Terminal):

```bash
git clone https://github.com/isac-artzi/SenSym-Windtunnel.git
cd SenSym-Windtunnel
scripts/run_docker.sh
```

(macOS comes with git; if it asks to install the command-line developer tools, say yes.) The script
shows the educational-use notice and asks you to type `yes` (once; remembered in
`~/.windlab/accepted`), then builds the image and starts WindLab:

```
==> Building the WindLab image (Ubuntu 24.04 + OpenFOAM v2606; 15-25 minutes and about 3 GB the first time)
[+] Building 1043.2s (12/12) FINISHED
==> Starting WindLab on http://localhost:8080/  (Ctrl+C stops it; runs are kept in the Docker volume 'runs')
windlab  | WindLab 0.1.0 (uncalibrated prototype - not yet validated)
windlab  |   Open this address in your browser:  http://localhost:8080/
```

Open **http://localhost:8080/** in Safari, Chrome or Firefox, accept the notice on the page, and
continue with [Verify the installation](#verify-the-installation-and-make-a-first-run).

What the script does: `docker compose -f dev/compose.yaml build windlab` and `... up windlab`, with
the repository mounted inside the container and simulation output in a Docker volume called `runs`.
`Ctrl+C` stops WindLab; `scripts/run_docker.sh` starts it again in seconds. Other commands:

```bash
scripts/run_docker.sh doctor     # the readiness check inside the container
scripts/run_docker.sh shell      # a Linux terminal inside the container (OpenFOAM loaded)
scripts/run_docker.sh --build    # rebuild the image (after a Docker Desktop or OpenFOAM update)
scripts/run_docker.sh down       # remove the container (your runs stay in the volume)
WINDLAB_PORT=8081 scripts/run_docker.sh    # if port 8080 is taken on your Mac
```

Without the script: `WINDLAB_ACCEPT_DISCLAIMER=1 docker compose -f dev/compose.yaml up --build windlab`
(setting that variable means you accept `DISCLAIMER.md`).

Speed: a Quick run takes about a minute on an Apple M-series Mac with 4–8 cores given to Docker.
Keep the WindLab folder on the Mac's own disk, not on a network drive.

---

## Linux

### Ubuntu 24.04 natively (recommended; Ubuntu 22.04 with Python 3.12)

The same installer as on Windows; it detects that it is not on WSL2.

```bash
git clone https://github.com/isac-artzi/SenSym-Windtunnel.git windlab
bash windlab/scripts/install_linux.sh
```

It installs `curl git python3-venv libgl1` and OpenFOAM v2606 from `dl.openfoam.com` with `apt`
(asking for your password once), creates `~/windlab-venv` with WindLab and STEP import, adds it to the
PATH of new terminals, and runs `windlab doctor`. Expected output and times: as in the Windows section
(20–40 minutes, mostly the OpenFOAM download). Then:

```bash
windlab start      # type yes to the notice the first time; open http://localhost:8080/
```

**Ubuntu 22.04**: WindLab needs Python 3.12. Install it first, then run the script:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install -y python3.12 python3.12-venv
```

### Other distributions (Debian, Fedora, Arch, …): Docker

Install Docker Engine and the Compose plugin from your distribution's packages (for example
`sudo apt install docker.io docker-compose-v2` on Debian/Ubuntu, `sudo dnf install docker-compose-plugin`
on Fedora) and add yourself to the `docker` group (`sudo usermod -aG docker $USER`, then log out and
in). Then exactly as on macOS:

```bash
git clone https://github.com/isac-artzi/SenSym-Windtunnel.git
cd SenSym-Windtunnel
scripts/run_docker.sh
```

---

## Verify the installation and make a first run

1. **`windlab doctor`** (in the Ubuntu / Linux terminal; on macOS `scripts/run_docker.sh doctor`),
   about 30 seconds. It checks OpenFOAM and its version, processor cores (and how many WindLab will
   use), memory, the `.wslconfig` on Windows, free disk, that port 8080 is free, whether STEP import is
   installed, and then meshes and solves a tiny test case. Every line says **PASS**, **WARN** or
   **FAIL** with a fix for anything that is not right:

   ```
   [PASS] OpenFOAM: v2606 at /usr/lib/openfoam/openfoam2606
   [PASS] Processor cores: 8 (WindLab will use 7 cores per run)
   [PASS] Memory: 12.0 GB for Linux, 11.2 GB free
   [PASS] WSL settings: C:\Users\you\.wslconfig gives Linux 12 GB and 8 processors.
   [PASS] Free disk: 41 GB
   [PASS] Port 8080: free
   [PASS] STEP import: available
   [PASS] Test run: a tiny wing meshed and solved in 18 s (CL 0.29, CD 0.04)
   All good.
   ```

   WARN lines are tips (for example a `.wslconfig` that could give Linux more memory); FAIL lines must
   be fixed first; see [Troubleshooting](#troubleshooting).
2. **Start WindLab** (`windlab start`, or `scripts/run_docker.sh` on macOS) and open
   **http://localhost:8080/**. Tick the box in the notice and press **Accept and continue**.
3. **A first run (2–4 minutes).** In **Step 1 · Shape** leave *A built-in wing (NACA 4-digit)* and
   press **Make this wing** (NACA 0012, chord 0.3 m, span 0.6 m): the 3D view shows the wing, the red
   arrow is the wind. In **Step 2 · Conditions** keep 20 m/s and set the angle of attack to **8°**
   (Reynolds number ≈ 410,000). In **Step 3 · Run** press **Run the wind tunnel** and watch the stages
   (mesh, then the solver, with C<sub>L</sub> and C<sub>D</sub> settling on the live charts). Expect
   about **20–60 s on a fast desktop with 6–8 cores, 1–3 minutes on a laptop**. In **Step 4 ·
   Results** you should see **C<sub>L</sub> ≈ 0.36** (likely 0.35 to 0.37) and **C<sub>D</sub> ≈ 0.04**,
   L/D ≈ 9, the pressure on the wing and the flow through the middle. A second run of the same wing is
   faster: the mesh is reused.
4. Then take the **tour** (top of the page), try an **Angle sweep**, **Compare** two runs under **My
   runs**, or open a **Lesson**. The [student handout](STUDENT_HANDOUT.md) has the short version;
   instructors: [TEACHING_NOTES.md](TEACHING_NOTES.md).

Your shapes and runs are kept in `~/.local/share/windlab` (Linux / WSL2) or the Docker volume
`runs` (macOS). WindLab keeps the last 20 finished runs (at most 10 GB) and deletes older ones;
`windlab start --keep-runs 50 --disk-cap-gb 20` keeps more. Trial feedback: `windlab feedback export`
writes one CSV to send to your instructor.

---

## Update or uninstall

**Windows (WSL2) and Linux**

```bash
windlab update                        # git pull in ~/windlab, then pip install into ~/windlab-venv
```

(By hand: `cd ~/windlab && git pull && pip install .` with the environment active. From a ZIP:
download the new ZIP, unpack it over `~/windlab`, and run `pip install ~/windlab`.) Uninstall:

```bash
rm -rf ~/windlab ~/windlab-venv ~/.local/share/windlab ~/.windlab   # WindLab, its environment, your runs, the accepted notice
sudo apt remove openfoam2606-default                                  # OpenFOAM, if nothing else uses it
```

then delete the line `export PATH="…/windlab-venv/bin:$PATH"` from `~/.bashrc`. To remove Ubuntu
itself on Windows: PowerShell → `wsl --unregister Ubuntu-24.04` (this deletes everything inside it).

**macOS and Docker on Linux**

```bash
cd SenSym-Windtunnel
git pull                              # new WindLab code is live at the next start (the folder is mounted)
scripts/run_docker.sh --build         # only when dev/Dockerfile changed (a new OpenFOAM version)
```

Uninstall: `scripts/run_docker.sh down`, then `docker volume rm sensym-windtunnel_runs
sensym-windtunnel_windlab-home` (your runs), `docker image rm windlab:2606`, delete the folder, and
remove `~/.windlab`. Docker Desktop itself: drag it to the Bin.

---

## Troubleshooting

Start with `windlab doctor` (macOS: `scripts/run_docker.sh doctor`): its FAIL and WARN lines name the
problem and the fix. Then look here. If it is not here, open an issue at
<https://github.com/isac-artzi/SenSym-Windtunnel/issues> (the **Bug report** template asks for your
operating system, the install route and the `windlab doctor` output).

### Windows

| What you see | What to do |
|---|---|
| `wsl : The term 'wsl' is not recognized` / WSL not enabled | Your Windows is too old or WSL is off. Run Windows Update (Windows 10 needs version 2004, build 19041, or later), then in an admin PowerShell: `dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart`, `dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart`, restart, and try `wsl --install -d Ubuntu-24.04` again. |
| `wsl --install` says virtualization is not enabled / "Please enable the Virtual Machine Platform" / error 0x80370102 | Virtualization is off in the firmware. Restart into the BIOS/UEFI setup (usually F2, F10, Del or Esc at power-on, or Windows: Settings → Recovery → Advanced start-up → UEFI Firmware Settings) and enable **Intel Virtualization Technology (VT-x)** or **SVM Mode** (AMD). `scripts/windows/preflight.ps1` (run in PowerShell: `powershell -ExecutionPolicy Bypass -File scripts\windows\preflight.ps1`) checks this and more, read-only. |
| `There is no distribution with the supplied name` | The distribution is called something else. `wsl -l -v` lists the names; use the exact one (often just `Ubuntu`), for example `wsl -d Ubuntu`. If `lsb_release -d` inside it does not say 24.04, install `Ubuntu-24.04` with `wsl --install -d Ubuntu-24.04`. |
| You forgot your Ubuntu password | In PowerShell: `wsl -u root` (opens Ubuntu as the administrator without a password; add `-d <name>` if you have several). Then `passwd <your-ubuntu-user-name>` and choose a new password; `exit`; open Ubuntu normally. |
| `mv: cannot stat '/var/run/motd.new': No such file or directory` when Ubuntu opens | Harmless: Ubuntu's message-of-the-day script complaining. Nothing is wrong. |
| The browser on Windows cannot reach `localhost:8080` | Make sure `windlab start` is still running in the Ubuntu window and said `Open this address`. Try **http://127.0.0.1:8080/**. If it still fails: close the Ubuntu window, in PowerShell run `wsl --shutdown`, open Ubuntu again and start WindLab again (WSL's localhost forwarding sometimes needs this; a VPN or a firewall product can block it too, try with it paused). |
| Runs are slow, or the laptop runs out of memory / Ubuntu says "Killed" | Give Linux more memory and all cores with `.wslconfig` (step 2 above; `windlab doctor` prints the recommended file), run `wsl --shutdown` and start again. Close SolidWorks and other large programs during a run. Use the Quick mesh; "More accurate, slower" needs about 2 GB per run. Keep the WindLab folder in the Linux home (`~`), not under `/mnt/c`, which is much slower. `windlab start --procs 4` uses fewer cores if the laptop becomes unresponsive. |
| `windlab: command not found` | `source ~/windlab-venv/bin/activate` first; the installer puts WindLab on the PATH of **new** Ubuntu windows only. |

### macOS and Docker

| What you see | What to do |
|---|---|
| `docker: command not found` or `Cannot connect to the Docker daemon` | Docker Desktop is not installed or not running: start it from Applications and wait for the whale icon in the menu bar to stop animating, then run `scripts/run_docker.sh` again. |
| The build stops with `killed`, `exit code 137`, or a run says the computer ran out of memory | Docker Desktop has too little memory. Docker menu → Settings → Resources → Memory: **8 GB** or more (12 GB for "More accurate, slower"), then *Apply & restart* and `scripts/run_docker.sh --build`. |
| `Bind for 127.0.0.1:8080 failed: port is already allocated` | Another program uses port 8080. `WINDLAB_PORT=8081 scripts/run_docker.sh` and open http://localhost:8081/. |
| The build fails while downloading (`apt-get` or `pip` errors, `TLS handshake timeout`) | A network hiccup or a campus network blocking downloads. Run `scripts/run_docker.sh --build` again, if needed on another network (a phone hotspot works). |
| `no matching manifest for linux/arm64` or a very slow build on Apple Silicon | Docker Desktop is set to emulate Intel. Settings → General: turn **off** "Use Rosetta for x86/amd64 emulation" is not needed, but make sure the default platform is not forced; `docker info | grep Architecture` should say `aarch64`. The image builds natively for arm64. |
| The page shows "WindLab's server is not answering" | The container stopped (Ctrl+C in the terminal, or Docker Desktop was quit). Run `scripts/run_docker.sh` again and reload the page. |

### Linux

| What you see | What to do |
|---|---|
| `This is not Ubuntu` / `This Ubuntu is version '22.04'` | The native installer supports Ubuntu 24.04 (22.04 after installing Python 3.12 from the deadsnakes PPA, see the Linux section). Everything else: use the Docker route. |
| `Python 3.12 or newer was not found` | Ubuntu 22.04: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install -y python3.12 python3.12-venv`, then run the installer again. |
| `E: Unable to locate package openfoam2606-default` | The OpenFOAM repository was not added (the `add-debian-repo.sh` step failed, often a network problem). Run `curl -s https://dl.openfoam.com/add-debian-repo.sh | sudo bash`, then the installer again. |
| `permission denied while trying to connect to the Docker daemon socket` | Add yourself to the `docker` group: `sudo usermod -aG docker $USER`, then log out and in. |

### Every system

| What you see | What to do |
|---|---|
| `windlab doctor` says **FAIL OpenFOAM: not found** | OpenFOAM is not installed or not where WindLab looks (`/usr/lib/openfoam/openfoam2606`). Run the installer again, or `sudo apt install openfoam2606-default` after adding the repository (`curl -s https://dl.openfoam.com/add-debian-repo.sh \| sudo bash`). |
| **FAIL OpenFOAM: found, but its settings could not be loaded** | A broken OpenFOAM install: `sudo apt reinstall openfoam2606-default`. |
| **FAIL Processor cores** / **Memory** | Fewer than 2 cores or less than 4 GB for Linux: on Windows fix `.wslconfig`, on macOS Docker's Resources; otherwise this computer is below WindLab's minimum. |
| **FAIL Free disk** | Under 2 GB free. Delete old runs under *My runs*, or free space (on Windows, WSL keeps its files on drive C:). |
| **FAIL Port 8080: in use** | Another program listens on 8080. `windlab start --port 8081` and open http://localhost:8081/ (macOS: `WINDLAB_PORT=8081`). |
| **WARN STEP import: not available**, or uploading a STEP file fails with `libGL.so.1: cannot open shared object file` | The STEP reader (OpenCASCADE) is missing or lacks a graphics library (nothing is drawn with it). In the terminal: `sudo apt install -y libgl1`, then `source ~/windlab-venv/bin/activate && pip install "~/windlab[step]"`, restart WindLab. Meanwhile, export the part as STL. |
| **FAIL Test run** | The tiny case did not mesh or solve: read the lines under it (often memory, or a half-installed OpenFOAM). Copy the whole `windlab doctor` output into an issue. |
| `address already in use` when starting | Same as port 8080 in use: `windlab start --port 8081`. |
| "The airflow calculation became unstable" | Try a lower airspeed or a smaller angle of attack, and check the part in Step 1 (one closed solid, right units, facing the wind). Past stall this is normal. |
| "The mesh could not be fitted around this part" | Export the part again as one solid body; check the units; avoid extremely thin details (WindLab refines around thin fins by itself, but a sliver thinner than 1/500 of the part is too thin for the Quick mesh). |
| "The computer's disk is full" / "almost full" | Delete old runs under *My runs* or free space. |
| The page says "WindLab's server is not answering" | The terminal running `windlab start` was closed or stopped. Start it again and reload the page. |
| The notice keeps coming back in the browser | Your browser blocks site storage (private window, strict privacy settings): WindLab cannot remember the acceptance. Allow storage for `localhost`, or accept it each time. |
| `git clone` fails: `Could not resolve host`, `Connection timed out`, or `pip` cannot download | A corporate or campus network blocking GitHub, dl.openfoam.com or PyPI. Retry on another network (home, a phone hotspot); the installer can be run again and continues where it stopped. Or download the ZIP on another computer and copy it over. |
| `windlab update` says git pull did not succeed | No connection, or you changed files in the WindLab folder (`git -C ~/windlab status` shows which; `git stash` puts them aside). From a ZIP: download the new ZIP and run `pip install .` in it. |
