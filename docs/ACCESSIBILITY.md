# Accessibility checklist

WindLab's page is written to WCAG 2.2 AA. The dev container has no browser (Node runs the scripts in a tiny
DOM without layout or rendering, `tests/test_js.py`), so axe-core could be vendored but not run; this file
lists what was checked **in code** (static tests in `tests/test_accessibility.py`, `tests/test_contrast.py`,
`tests/test_page.py`, the headless smoke test in `tests/test_js.py`) and what still needs a manual pass in a
browser with a keyboard and a screen reader. Status 2026-11-05.

## Checked in code (automated, every `pytest`)

| What | Where | How it is checked |
|:--|:--|:--|
| Colour contrast ≥ 4.5:1 for text and ≥ 3:1 for UI graphics, in both themes | `app.css` custom properties | `tests/test_contrast.py` computes the WCAG ratio of every text/background pair used |
| Every form control has a label | `index.html` | `tests/test_accessibility.py`: every `<input>`/`<select>`/`<textarea>` has a `<label for>`, an `aria-label` or sits inside a `<label>` |
| Every button and link has a name | `index.html` | buttons/links are checked for visible text or `aria-label` |
| No positive `tabindex`; the 3D views and the flow slice are focusable (`tabindex="0"`) with keyboard instructions in their `aria-label` | `index.html` | test |
| Every `aria-controls` / `aria-labelledby` / `aria-describedby` points at an existing id; no duplicate ids | `index.html` | `tests/test_page.py` |
| Disclosures are buttons with `aria-expanded` (expected-error "Why this range?", glossary (?) notes, lesson explanations); no hover-only tooltips (`title` is never the only carrier of information) | `index.html`, `app.js` | test: no `title=` attributes on the page; every `.def` is a `<button>` |
| Minimum target size 24 × 24 px: (?) buttons, link-style buttons, compare checkboxes, radio/checkbox inputs, step chips | `app.css` | test reads the CSS rules |
| Visible focus ring on every focusable element (`:focus-visible`, 3 px, 3:1 contrast) | `app.css` | contrast test (`--focus` on `--card`) and a CSS presence test |
| Status is never colour alone: stage list items carry hidden text "(finished)/(now)/(waiting)" beside the ✓/▶ icon and colour; unvalidated ranges carry "⚠ unvalidated"; comparison notes carry ✓/⚠ with the text; problem/warning lists have ✕/⚠ markers; lesson verdicts say "Correct" / "Not quite" | `app.js`, `app.css` | test greps the sources |
| Live regions for progress, results, probe readouts and lesson notes (`aria-live`, `role="status"` / `role="alert"`) | `index.html` | test |
| `prefers-reduced-motion`: the progress bar's transition is off and smooth scrolling becomes instant (`scroll-behavior: auto`; `scrollTo()` in `app.js` checks the media query); nothing else animates; polling redraws charts without motion | `app.css`, `app.js` | test |
| Numeric probe readout for every coloured picture (ACC-2): a click or the keyboard (arrow keys / Home / Enter on the flow slice; arrow keys + Enter or P on the 3D view) reads air speed and Cp, or Cp on the surface, into a `role="status"` paragraph | `viewer.js`, `app.js`, `index.html` | test: readout elements and key handlers exist |
| Colour-blind-safe colour maps (viridis for speed; blue–white–red diverging centred on 0 for Cp) with numeric legend ticks | `plot.js` | `tests/test_page.py` |
| Charts have a text alternative: `aria-label` with the data, and the same numbers in a table (sweep, compare, solver history) | `app.js`, `index.html` | test |
| Compared runs whose points coincide stay visible (later series drawn as larger hollow rings) and the keyboard readout lists every run at that point, one ← → stop per spot | `chart.js`, `plot.js` | `tests/js/plot_helpers.mjs` (helpers); the drawing needs a browser |
| Glossary definitions reachable by keyboard: (?) is a button, Esc closes the note and returns focus | `app.js` | test (handlers present) |
| The instant preview (ACC-4, VIS-6): Pause/Play and "Advance 200 steps" buttons, opens **paused** under `prefers-reduced-motion`, the picture is `role="img"` with a description, and the lift/drag estimate is written as text in a readout paragraph beside the colours; air speed uses viridis, swirl a diverging map centred on zero | `preview.js`, `index.html` | `tests/test_preview.py` |
| Educational-use notice: a real modal (`role="dialog"`, `aria-modal="true"`, labelled heading that receives focus) that must be accepted once per notice version; the rest of the page is `inert` while it is open, Tab / Shift+Tab stay inside it, Esc does nothing, the Accept button is enabled by a labelled checkbox; remembered in `localStorage` inside try/catch | `index.html`, `app.js` | `tests/test_page.py`, the Node smoke test |
| First-visit tour and "Start here" card: a non-modal `role="dialog"` card with a labelled heading that receives focus on every step, Back / Next / Close buttons, Esc closes and returns focus to the trigger, ← → step; the section it describes is outlined (not colour alone: the step text names it) and scrolled into view honouring reduced motion; remembered in `localStorage` inside try/catch; never shown in teacher mode | `index.html`, `app.js` | `tests/test_page.py` |

## Keyboard-only paths (written against the code; to be confirmed in a browser)

**A whole lesson.** Tab to "Lessons" in the step bar → Enter; Tab to "Start this lesson" → Enter (focus moves to the
lesson heading). Tab through the objectives, the step chips (each a button), the step content: textarea → "Save my
answer"; "Set up this run for me" or "Use the pre-computed results" (focus moves to the Run button or the results
heading); question radios (arrow keys choose) → "Check my answer"; "Next step". Esc closes any open (?) note.

**A sweep.** Tab to the NACA code / chord / span fields → "Make this wing" (Enter). Tab to Airspeed; the "Angle
sweep" radio (arrow keys); the angles field; Tab to "Run the wind tunnel" → Enter. Progress is announced
(`aria-live`). When finished, focus moves to the Results heading; Tab reaches the sweep table (numbers), the charts
(focus a chart and use ← → to step through its points; the readout is drawn on the canvas and the chart's
`aria-label` lists every point), the 3D view (arrow keys turn, + − zoom, Enter reads Cp), the flow slice (arrow
keys move the probe, Enter reads), the CSV / case downloads, the "Why these ranges?" disclosure.

**Compare.** In "My runs" the checkboxes are reachable (Space ticks), then "Compare N runs".

## Still to do by hand (instructor's manual pass)

- Screen-reader pass (NVDA on Windows / VoiceOver): reading order of the results step, announcement of the
  progress bar (`aria-valuetext`), the probe readouts and the lesson step changes.
- Confirm the 24 px targets after browser rendering (zoom 100 % and 200 %) and that nothing is cut off at 320 px width
  or 400 % zoom (WCAG 1.4.10 reflow).
- Confirm the canvases' focus ring and the chart keyboard readout are visible in both themes and in forced-colours
  (Windows high contrast) mode.
- Run axe-core in the browser's developer tools (the axe DevTools extension) once a browser is available, and record
  the result here. An axe-core gate in CI is the goal; that needs a browser runner (Playwright) outside this
  container.
