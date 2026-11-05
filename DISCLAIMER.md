# WindLab disclaimer

**Please read this before you use WindLab.** (notice version 2026-11-05)

## WindLab is an educational simulation

SenSym WindLab is a teaching tool for engineering students. It is an **uncalibrated
prototype**. The lift and drag coefficients, forces, moments, pressures, flow pictures and
every other number or picture it produces are **approximate and may be inaccurate**,
sometimes by a large margin. WindLab has **not been validated** for engineering design,
certification, or safety-critical decisions, and it must not be used as the basis for any
of them.

## No responsibility for what is built with it

**SenSym LLC assumes no responsibility for any damages or injury caused by objects built using this simulator.**

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
  every report, CSV file and exported case ends with: *Educational simulation — results may be inaccurate. See DISCLAIMER.md.*

## Third-party software and trade marks

WindLab runs the open-source OpenFOAM® solver as separate programs. OPENFOAM® is a
registered trade mark of OpenCFD Limited; the full trade-mark notice is in the README, in
the About panel of the page and at the end of every report. The licences of the other
components are listed there too.

Copyright (c) 2026 SenSym LLC.
