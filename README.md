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

## Frame library

Import files frames into a library folder (default `~/SimpleStax`), one folder per night, indexed
in `library.sqlite`. A night runs noon to noon, so frames shot after midnight stay with their
evening.

```
<night>/lights/<focal>_<f>_<exposure>_<ISO>/<subject>/<name>_<subject>_<night>.ARW
<night>/darks/<exposure>_<ISO>/     match lights by exposure, ISO and night (sensor temperature)
<night>/flats/<focal>_<f>/          match by lens, aperture and night (dust, focus)
<night>/bias/<ISO>/                 match by ISO from any night
```

Several nights of one subject can be stacked together, each calibrated with its own masters, so a
summer and a winter session never share darks.

## Unreleased

## Version 0.4.0 (October 2026)

- Stack removes the light-pollution gradient by default. It fits a smooth surface to each colour's
  sky background, leaving out stars and bright subjects, and subtracts it. The sky ends up flat and
  neutral grey. Untick "Remove light-pollution gradient" to keep the raw background.
- Stacks are converted from the camera's own colour to linear sRGB with the camera's colour
  matrix (from LibRaw), so colours match what a raw editor shows instead of looking green.

## Version 0.3.0 (October 2026)

- Calibrate and Stack: deep-sky stacking for nebulae, galaxies, star fields and the Milky Way.
  Calibrate lists the nights a subject has lights for. Tick one or more, and choose whether to use
  the darks, flats and bias found for them; each choice says what it fixes or what happens
  without it. Lights-only works: untracked frames drift across the sensor, so outlier rejection
  removes hot pixels. Stack runs in its own process with a progress meter. It calibrates on the
  raw Bayer grid (master darks, flats and bias are medians), demosaics, registers on stars
  (astroalign), rejects outliers per pixel (leave-one-out kappa-sigma, so memory doesn't grow
  with the frame count) and averages. It writes a linear 32-bit FITS, a 16-bit TIFF and a
  stretched preview to `<library>/stacks/<subject>/<date_time>/`.

## Version 0.2.1 (October 2026)

- Import: the Select buttons open SimpleStax's own frame picker. It lists a folder's RAWs with
  their exposure, ISO, focal length, aperture and date, read in the background with a progress
  meter, and dims frames whose EXIF doesn't match the shot profile (one checkbox per setting).
  Frames that don't record a value (manual or adapted lenses) aren't dimmed for it, every frame
  stays selectable, and Select matching picks the bright ones.

## Version 0.2.0 (October 2026)

- Import: a shot profile (focal length, aperture, exposure, ISO) and a subject, then Select
  lights / darks / flats / bias buttons that copy or move the picked frames into the frame
  library. Lights are renamed with the subject and night; the subject list is kept for later
  filtering. Date, exposure, ISO and lens are read from EXIF, and frames that don't match the
  profile are flagged before filing. Copying shows a progress meter.
- A red outline marks the next control that needs input (exposure, subject, or the next missing
  frame kind for the subject), and every control has a tooltip.

## Version 0.1.2 (October 2026)

- Plan rounds the NPF limit down to a shutter speed the camera can actually set (1/3-stop
  steps, e.g. 3.6 s → 3.2″).

## Version 0.1.1 (October 2026)

- PSS is only offered when its launcher is actually executable.
- Releases build from the merged PR's own commit and push the version tag only after the .deb
  builds; a failed upload can be republished from an existing tag without another bump.

## Version 0.1.0 (October 2026)

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
