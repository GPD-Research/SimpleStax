# Notes for contributors and agents

- Python ≥ 3.10, PySide6 GUI (no HTML front end), GPL-3.0-or-later.
- Before a PR: `ruff check . && ruff format --check . && QT_QPA_PLATFORM=offscreen pytest -q`.
- One PR per change, with exactly one `semver:patch|minor|major|none` label (rules in README).
  Write release notes under README's `## Unreleased`; never edit `__version__` by hand: the
  Release workflow bumps it, tags and publishes the .deb on merge.
- Engines run as subprocesses (`src/simplestax/engines/`); never import an engine's GUI toolkit
  (PSS uses PyQt5) into the app process.
- Anything that can take over a second shows a real progress meter (`simplestax.progress`), not
  a spinner.
- Calibration works on the CFA grid before demosaicing; see the Hyper Browser wiki's
  Astro-test-frames page for the measurements behind the flat/vignetting checks.
