# Notes for contributors and agents

- Python ≥ 3.10, PySide6 GUI (no HTML front end), GPL-3.0-or-later.
- Before a PR: `ruff check . && ruff format --check . && QT_QPA_PLATFORM=offscreen pytest -q`.
- One PR per change; version and README section bumped in the PR that ships a release.
- Engines run as subprocesses (`src/simplestax/engines/`); never import an engine's GUI toolkit
  (PSS uses PyQt5) into the app process.
- Anything that can take over a second shows a real progress meter (`simplestax.progress`), not
  a spinner.
- Calibration works on the CFA grid before demosaicing; see the Hyper Browser wiki's
  Astro-test-frames page for the measurements behind the flat/vignetting checks.
