# SimpleStax

Guided calibration and stacking for astrophotography on Linux. SimpleStax walks a night's work
in order (**Plan → Shoot → Import → Calibrate → Stack → Export**) and picks a workflow for the
subject: planetary & lunar, nebulae, galaxies, star fields or the Milky Way. The result is a linear
TIFF/FITS to finish in a full editor; SimpleStax does not try to be one.

The guided steps (Plan, Shoot) can be skipped at any time and switched off per subject; the
subject's own processing pipeline still applies when they are.

## Engines

Stacking is not reimplemented where a proven engine exists. Each engine runs as its own process,
so its dependencies stay out of the app:

| Subject | Engine | Notes |
| --- | --- | --- |
| Planetary & lunar | [PlanetarySystemStacker](https://github.com/Rolf-Hempel/PlanetarySystemStacker) (GPL-3) | Run headless (`PlanetarySystemStacker <frames> …`) from its own venv; it pins PyQt5. |
| Deep sky | SimpleStax's own Python pipeline (rawpy, astropy/ccdproc, astroalign, sep) | Planned for 0.3. |
| Plate solving | [ASTAP](https://www.hnsky.org/astap.htm) CLI (MPL-2.0) | Planned. |

The PSS engine is found at `$SIMPLESTAX_PSS`, then `~/.local/share/simplestax/pss-venv/bin/`,
then `PATH`. To set it up by hand:

```bash
python3 -m venv ~/.local/share/simplestax/pss-venv
~/.local/share/simplestax/pss-venv/bin/pip install --no-deps planetary-system-stacker
~/.local/share/simplestax/pss-venv/bin/pip install numpy scipy astropy scikit-image \
  opencv-python-headless matplotlib psutil PyQt5
```

(PSS 0.9.8 declares `numpy<1.23`, which has no wheels for current Python; it runs on NumPy 2.)

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
QT_QPA_PLATFORM=offscreen pytest -q
python -m simplestax
```

Versioning is automatic. Each PR carries exactly one label, checked in CI:

| Label | When |
| --- | --- |
| `semver:patch` | Single-component fixes, syntax corrections, UI polish, localised bug fixes. |
| `semver:minor` | A finished multi-stage change, a new tool pipeline, or a clear step up in processing capability. |
| `semver:major` | A long-term goal reached, or a structural rewrite that changes how the app works. |
| `semver:none` | Docs, CI or tests only; no release. |

PRs add their notes under **Unreleased** below. On merge, the Release workflow bumps
`__version__` (`scripts/bump_version.py`), turns Unreleased into the new version's section (or
uses the PR title if it is empty), tags `vX.Y.Z` and attaches `simplestax_X.Y.Z_amd64.deb` and
`simplestax_latest_amd64.deb` to the Release.

Install: `sudo apt install ./simplestax_latest_amd64.deb`

## Unreleased

- Qt (PySide6) window: subject picker, the six steps down the side, and a per-subject
  "Guided planning" switch that hides Plan and Shoot; Plan has Skip to import.
- Plan estimates pixel pitch, crop factor and the longest untracked exposure (NPF rule, with the
  500 rule for comparison), and recommends dark, flat and bias counts for the number of lights,
  with the noise a master dark adds without dithering. Shoot turns that into a checklist.
- PSS adapter: command line, job validation and progress from its stage lines.
- Text progress meters (`[########------------]  40%`) for the processing steps to use.
- CI (ruff, pytest) and a tag-triggered `.deb` release.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
