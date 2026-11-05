"""The educational-use disclaimer: one source for the page, the command line, the reports and the exports.

WindLab is an educational simulation. Before anyone can run it, they must acknowledge that its
results may be inaccurate and that SenSym LLC is not responsible for anything built with it:

* the web page shows a modal on first load (and again whenever ``VERSION`` changes); the Run button
  stays disabled until it is accepted, and ``POST /api/runs`` refuses a request that does not carry
  the ``X-WindLab-Disclaimer: <VERSION>`` header the page adds after acceptance;
* ``windlab start`` and ``windlab benchmark`` print the text and ask for ``yes`` on first use, remember
  the answer in ``~/.windlab/accepted``, and accept ``--accept-disclaimer`` or
  ``WINDLAB_ACCEPT_DISCLAIMER=1`` for scripts and CI;
* every report, CSV and case export carries ``FOOTER``.

The full text is DISCLAIMER.md at the root of the repository.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

VERSION = "2026-11-05"
HEADER = "x-windlab-disclaimer"
ENV_ACCEPT = "WINDLAB_ACCEPT_DISCLAIMER"
ACCEPT_WORD = "yes"
DISCLAIMER_FILE = "DISCLAIMER.md"
DISCLAIMER_URL = "https://github.com/isac-artzi/SenSym-Windtunnel/blob/main/DISCLAIMER.md"

# The sentence the owner asked for, word for word.
RESPONSIBILITY = ("SenSym LLC assumes no responsibility for any damages or injury caused by objects built using this "
                  "simulator.")
# What the student ticks on the page (the catalog key page.disclaimer.ack carries the same words).
ACK_LABEL = ("I understand this is an educational simulation that may give inaccurate results, and SenSym LLC is "
             "not responsible for anything built with it")
# One line at the end of every report, CSV and export.
FOOTER = f"Educational simulation — results may be inaccurate. See {DISCLAIMER_FILE}."
FOOTER_ASCII = FOOTER.replace("—", "-")
# Short form for the About panel, the README and /api/config.
SHORT = ("WindLab is an educational simulation. Its results are approximate and may be inaccurate; it is not "
         "validated for engineering design, certification or safety-critical decisions. " + RESPONSIBILITY
         + " Verify independently (physical testing, a qualified engineer) before building or flying anything.")

CLI_TEXT = f"""\
============================================================================
  WindLab - please read before you run it
============================================================================
WindLab is an EDUCATIONAL simulation. Its results are approximate and
MAY BE INACCURATE. It is not validated for engineering design, certification
or safety-critical decisions.

{RESPONSIBILITY}

You are responsible for independent verification - physical testing and a
qualified engineer - before building or flying anything based on its numbers.

WindLab is provided "AS IS", without warranty of any kind (MIT License).
Full text: {DISCLAIMER_FILE} in the WindLab folder, or {DISCLAIMER_URL}
(disclaimer version {VERSION}).
"""

REJECTED = ("Please read and accept the notice about accuracy first: reload the page, tick the box and press "
            "Accept, then run again.")

# DISCLAIMER.md at the root of the repository holds exactly this text (tests/test_disclaimer.py keeps them equal);
# the app serves it at /disclaimer and the static demo ships it as disclaimer.txt.
FULL_TEXT = f"""\
# WindLab disclaimer

**Please read this before you use WindLab.** (notice version {VERSION})

## WindLab is an educational simulation

SenSym WindLab is a teaching tool for engineering students. It is an **uncalibrated
prototype**. The lift and drag coefficients, forces, moments, pressures, flow pictures and
every other number or picture it produces are **approximate and may be inaccurate**,
sometimes by a large margin. WindLab has **not been validated** for engineering design,
certification, or safety-critical decisions, and it must not be used as the basis for any
of them.

## No responsibility for what is built with it

**{RESPONSIBILITY}**

## You are responsible for independent verification

If you intend to build, fly, drive, sail, launch or otherwise use a real object that you
designed, sized or checked with WindLab, you are responsible for verifying it
independently before doing so: physical testing (for example in a real wind tunnel or by
careful incremental trials) and review by a qualified engineer. Treat WindLab's numbers as
a way to learn, explore and compare ideas, never as a certificate that something is safe,
strong enough or will perform as predicted.

## No warranty

WindLab is open-source software released under the MIT License (see the LICENSE file).
As that licence states, the software is provided "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## Where WindLab reminds you of this

- The web page asks you to accept this notice before anything can be run, and again
  whenever the notice changes. The Run button stays disabled until then, and the server
  refuses a run that was not sent from an accepting page.
- `windlab start` and `windlab benchmark` print the notice on first use and wait for
  `yes` (remembered in `~/.windlab/accepted`; `--accept-disclaimer` or
  `WINDLAB_ACCEPT_DISCLAIMER=1` accept it for scripts and automated tests).
- Every result on the page is labelled "uncalibrated prototype — not yet validated", and
  every report, CSV file and exported case ends with: *{FOOTER}*

## Third-party software and trade marks

WindLab runs the open-source OpenFOAM® solver as separate programs. OPENFOAM® is a
registered trade mark of OpenCFD Limited; the full trade-mark notice is in the README, in
the About panel of the page and at the end of every report. The licences of the other
components are listed there too.

Copyright (c) 2026 SenSym LLC.
"""


def config_view() -> dict:
    """What the page needs: the version to remember and the texts it does not already carry."""
    return {"version": VERSION, "header": HEADER, "ack": ACK_LABEL, "short": SHORT, "footer": FOOTER,
            "file": DISCLAIMER_FILE, "url": DISCLAIMER_URL}


def request_accepted(headers) -> bool:
    """True when a request carries the acceptance header for the current version."""
    try:
        value = headers.get(HEADER)
    except AttributeError:
        return False
    return (value or "").strip() == VERSION


# ----------------------------------------------------------------------------- command line


def accepted_file() -> Path:
    """``~/.windlab/accepted``: holds the version that was accepted."""
    return Path.home() / ".windlab" / "accepted"


def accepted_version() -> str | None:
    p = accepted_file()
    try:
        if p.is_symlink() or not p.is_file():
            return None
        return p.read_text(encoding="utf-8").strip()[:40] or None
    except OSError:
        return None


def record_acceptance() -> bool:
    p = accepted_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(VERSION + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def accepted_by_environment(environ=None) -> bool:
    value = (environ if environ is not None else os.environ).get(ENV_ACCEPT, "")
    return value.strip().lower() in ("1", "true", "yes")


def require_cli_acceptance(flag: bool = False, *, ask: Callable[[str], str] | None = None,
                           out: Callable[[str], None] = print, environ=None, interactive: bool | None = None) -> bool:
    """Make sure the person running this command has accepted the disclaimer.

    Accepted when ``--accept-disclaimer`` was given (remembered), the environment variable is set
    (not remembered: scripts and CI), or ``~/.windlab/accepted`` holds the current version. Otherwise
    the text is printed and the user must type ``yes``; a non-interactive run (no terminal) is refused
    with the two ways to accept. Returns True when the command may go on.
    """
    if accepted_by_environment(environ):
        return True
    if flag:
        record_acceptance()
        return True
    if accepted_version() == VERSION:
        return True
    out(CLI_TEXT)
    if interactive is None:
        interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if not interactive and ask is None:
        out(f"No terminal to ask on. Run again with  --accept-disclaimer  or set  {ENV_ACCEPT}=1  to accept it.")
        return False
    ask = ask or input
    try:
        answer = ask(f"Type {ACCEPT_WORD} to accept and continue (anything else stops): ")
    except (EOFError, KeyboardInterrupt):
        out("")
        return False
    if answer.strip().lower() != ACCEPT_WORD:
        out("Not accepted; nothing was started.")
        return False
    where = accepted_file()
    out(f"Thank you. Remembered in {where}; you will be asked again only if the notice changes.\n"
        if record_acceptance() else "Thank you. (The answer could not be saved; you will be asked again next time.)\n")
    return True
