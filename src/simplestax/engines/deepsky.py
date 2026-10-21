"""
Deep-sky stacking: calibrate on the CFA grid, register on stars, reject outliers, average.

Runs as its own process (`simplestax --engine deepsky job.json`) and reports on stdout, one JSON
object per line: {"progress": 0.4, "message": "…"}, then {"result": {…}} or {"error": "…"}.

Memory stays at a few full-size frames whatever the number of lights. Pass one registers every
light and accumulates each pixel's mean and variance (Welford). Pass two reads the lights again
and averages only the samples within kappa sigma of the *other* frames' mean (leave-one-out, so a
single outlier can't hide by inflating the spread it is judged against). Untracked frames drift
across the sensor, so hot pixels land on different sky pixels in each frame and are rejected even
without darks.
"""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from simplestax.cfa import CfaFrame, UnsupportedSensor, demosaic, green_plane, read_cfa, tiled
from simplestax.output import write_fits, write_png, write_tiff16
from simplestax.stacking import FrameGroup, StackJob
from simplestax.stretch import auto_stretch

Reader = Callable[[Path], CfaFrame]
Emit = Callable[[dict], None]

MIN_STARS = 8
MAX_STARS = 150
ROWS_PER_CHUNK = 256
PREVIEW_WIDTH = 1600
FLAT_FLOOR = 0.05


class EngineError(RuntimeError):
    pass


@dataclass
class Masters:
    offset: np.ndarray | None  # master dark, else master bias, else None for the file's black level
    flat: np.ndarray | None  # normalised to 1 in the centre, per site


# Masters


def _median_master(
    paths: list[Path],
    reader: Reader,
    work: Path,
    name: str,
    offset: np.ndarray | None = None,
    normalise: bool = False,
    tick: Callable[[str], None] = lambda _m: None,
) -> np.ndarray:
    """Median of [paths] after subtracting [offset]; flats are scaled to a common level first."""
    first = reader(paths[0])
    h, w = first.shape
    stack = np.lib.format.open_memmap(
        work / f"{name}.npy", mode="w+", dtype=np.uint16, shape=(len(paths), h, w)
    )
    scales = np.ones((len(paths), 2, 2), np.float32)
    for i, path in enumerate(paths):
        frame = first if i == 0 else reader(path)
        if frame.shape != (h, w):
            raise EngineError(f"{path.name} is {frame.shape}, the other {name} are {(h, w)}")
        stack[i] = np.clip(np.rint(frame.data), 0, 65535)
        if normalise:
            signal = frame.data - (offset if offset is not None else tiled(frame.black, (h, w)))
            per_site = np.array(
                [[np.median(signal[i0::2, j0::2][::4, ::4]) for j0 in (0, 1)] for i0 in (0, 1)],
                np.float32,
            )
            if (per_site <= 0).any():
                raise EngineError(f"{path.name} is black; it can't be used as a flat")
            scales[i] = 1 / per_site
        tick(f"{name}: {path.name}")
    if offset is None and normalise:
        offset = tiled(first.black, (h, w))
    master = np.empty((h, w), np.float32)
    for r0 in range(0, h, 64):
        block = stack[:, r0 : r0 + 64].astype(np.float32)
        if offset is not None:
            block -= offset[r0 : r0 + 64]
        if normalise:
            rows = block.shape[1]
            block *= np.tile(scales, (1, rows // 2, w // 2))
        master[r0 : r0 + 64] = np.median(block, axis=0)
    del stack
    (work / f"{name}.npy").unlink(missing_ok=True)
    return master


def normalise_flat(flat: np.ndarray) -> np.ndarray:
    h, w = flat.shape
    out = flat.copy()
    view = out.reshape(h // 2, 2, w // 2, 2)
    for i in (0, 1):
        for j in (0, 1):
            plane = view[:, i, :, j]
            ph, pw = plane.shape
            centre = plane[ph // 4 : 3 * ph // 4, pw // 4 : 3 * pw // 4]
            plane /= np.median(centre)
    return np.maximum(out, FLAT_FLOOR)


def build_masters(
    group: FrameGroup, reader: Reader, work: Path, tick: Callable[[str], None]
) -> Masters:
    def master(kind: str, **kw) -> np.ndarray | None:
        paths = [Path(p) for p in getattr(group, kind)]
        return _median_master(paths, reader, work, kind, tick=tick, **kw) if paths else None

    bias = master("bias")
    dark = master("darks")
    flat = master("flats", offset=bias, normalise=True)
    return Masters(
        offset=dark if dark is not None else bias,
        flat=None if flat is None else normalise_flat(flat),
    )


def calibrate(frame: CfaFrame, masters: Masters) -> np.ndarray:
    h, w = frame.shape
    offset = masters.offset if masters.offset is not None else tiled(frame.black, (h, w))
    if offset.shape != (h, w):
        raise EngineError(f"a light is {(h, w)} but its calibration frames are {offset.shape}")
    cfa = frame.data.astype(np.float32) - offset
    if masters.flat is not None:
        cfa /= masters.flat
    cfa *= tiled(frame.wb[frame.pattern], (h, w))
    return cfa


# Registration


def _resize(small: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    zoomed = ndimage.zoom(small, (shape[0] / small.shape[0], shape[1] / small.shape[1]), order=1)
    out = np.empty(shape, np.float32)
    zh, zw = min(shape[0], zoomed.shape[0]), min(shape[1], zoomed.shape[1])
    out[:zh, :zw] = zoomed[:zh, :zw]
    out[zh:, :] = out[zh - 1 : zh, :]
    out[:, zw:] = out[:, zw - 1 : zw]
    return out


def find_stars(plane: np.ndarray, limit: int = MAX_STARS) -> np.ndarray:
    """Centroids (x, y) of the brightest point sources, brightest first; hot pixels skipped."""
    step = 8
    background = _resize(ndimage.median_filter(plane[::step, ::step], size=9), plane.shape)
    residual = plane - background
    smooth = ndimage.gaussian_filter(residual, 1.0)
    sample = smooth[::4, ::4]
    noise = 1.4826 * float(np.median(np.abs(sample - np.median(sample)))) or 1e-6
    peaks = (smooth == ndimage.maximum_filter(smooth, size=7)) & (smooth > 5 * noise)
    edge = 4
    peaks[:edge] = peaks[-edge:] = False
    peaks[:, :edge] = peaks[:, -edge:] = False
    ys, xs = np.nonzero(peaks)
    # A lone hot pixel loses most of its peak to a 1-pixel blur; a star, spread wider, doesn't.
    sharp = residual[ys, xs] / smooth[ys, xs]
    keep = sharp < 3
    ys, xs = ys[keep], xs[keep]
    order = np.argsort(smooth[ys, xs])[::-1][:limit]
    stars = []
    offsets = np.arange(-2, 3)
    for y, x in zip(ys[order], xs[order], strict=True):
        cut = np.clip(residual[y - 2 : y + 3, x - 2 : x + 3], 0, None)
        total = cut.sum()
        if total <= 0:
            continue
        dx = (cut.sum(0) * offsets).sum() / total
        dy = (cut.sum(1) * offsets).sum() / total
        stars.append((x + dx, y + dy))
    return np.array(stars, np.float64).reshape(-1, 2)


def match(stars: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """3×3 matrix taking (x, y, 1) in the frame to the reference, from matched star triangles."""
    import astroalign

    if len(stars) < MIN_STARS or len(reference) < MIN_STARS:
        raise EngineError(f"only {len(stars)} stars found")
    try:
        transform, _ = astroalign.find_transform(stars, reference)
    except (astroalign.MaxIterError, ValueError) as error:
        raise EngineError("its stars don't match the reference frame") from error
    return np.asarray(transform.params, np.float64)


# Half-size green pixel (u, v) covers full-size pixels 2u..2u+1, centred at 2u + 0.5.
_HALF_TO_FULL = np.array([[2, 0, 0.5], [0, 2, 0.5], [0, 0, 1]], np.float64)


def to_full_size(matrix: np.ndarray) -> np.ndarray:
    return _HALF_TO_FULL @ matrix @ np.linalg.inv(_HALF_TO_FULL)


_SWAP = np.array([[0, 1, 0], [1, 0, 0], [0, 0, 1]], np.float64)


def warp(rgb: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Resample (3, H, W) into the reference frame; pixels the frame doesn't cover become NaN."""
    if np.allclose(matrix, np.eye(3)):
        return rgb
    inverse = _SWAP @ np.linalg.inv(matrix) @ _SWAP  # reference (row, col) -> frame (row, col)
    out = np.empty_like(rgb)
    for c in range(rgb.shape[0]):
        ndimage.affine_transform(
            rgb[c], inverse[:2, :2], offset=inverse[:2, 2], output=out[c], order=1, cval=np.nan
        )
    return out


# Stacking


@dataclass
class Light:
    path: Path
    group: int
    matrix: np.ndarray | None = None
    offset: np.ndarray | None = None  # per-channel background shift onto the reference


def _background(rgb: np.ndarray) -> np.ndarray:
    sample = rgb[:, ::8, ::8].reshape(3, -1)
    return np.array([np.nanmedian(channel) for channel in sample], np.float32)


def _chunks(height: int):
    for r0 in range(0, height, ROWS_PER_CHUNK):
        yield slice(r0, r0 + ROWS_PER_CHUNK)


def accumulate(x: np.ndarray, count: np.ndarray, mean: np.ndarray, m2: np.ndarray) -> None:
    """Welford update with one frame; NaN samples are skipped."""
    for rows in _chunks(x.shape[1]):
        xs, n, mu, var = x[:, rows], count[:, rows], mean[:, rows], m2[:, rows]
        valid = np.isfinite(xs)
        xs = np.where(valid, xs, mu)
        n += valid
        delta = xs - mu
        mu += delta / np.maximum(n, 1)
        var += delta * (xs - mu)


def rejection_thresholds(kappa: float, frames: int) -> np.ndarray:
    """Allowed |x - mean of the others| / sd of the others, indexed by frame count.

    The others' spread is estimated from n - 1 samples, so the deviation follows Student's t with
    n - 2 degrees of freedom; matching kappa's normal tail keeps the rejection rate at that of
    kappa sigma (0.27% at kappa 3) instead of 25% at three frames.
    """
    from scipy import stats

    tail = stats.norm.sf(kappa)
    n = np.arange(frames + 1, dtype=np.float64)
    out = np.full(frames + 1, np.inf, np.float32)
    big = n >= 3
    out[big] = stats.t.isf(tail, n[big] - 2) * np.sqrt(1 + 1 / (n[big] - 1))
    return out


def accumulate_clipped(
    x: np.ndarray,
    count: np.ndarray,
    mean: np.ndarray,
    m2: np.ndarray,
    kappa: float,
    total: np.ndarray,
    kept: np.ndarray,
) -> int:
    """Add the samples of [x] within kappa sigma of the other frames; returns rejections."""
    rejected = 0
    for rows in _chunks(x.shape[1]):
        xs, n, mu, var = x[:, rows], count[:, rows].astype(np.float32), mean[:, rows], m2[:, rows]
        valid = np.isfinite(xs)
        xs = np.where(valid, xs, mu)
        dev = xs - mu
        with np.errstate(divide="ignore", invalid="ignore"):
            others_mean = mu - dev / (n - 1)
            others_var = (var - dev * dev * n / (n - 1)) / (n - 2)
            spread = np.sqrt(np.maximum(others_var, 0))
            limit = rejection_thresholds(kappa, int(count.max()))[count[:, rows]]
            ok = np.abs(xs - others_mean) <= limit * spread + 1e-6
        ok = valid & ok
        rejected += int(np.count_nonzero(valid & ~ok))
        total[:, rows] += np.where(ok, xs, 0)
        kept[:, rows] += ok
    return rejected


def run(job: StackJob, emit: Emit, reader: Reader = read_cfa) -> dict:
    out = Path(job.output)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        return _run(job, emit, reader, out, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(job: StackJob, emit: Emit, reader: Reader, out: Path, work: Path) -> dict:
    lights = [Light(Path(p), g) for g, group in enumerate(job.groups) for p in group.lights]
    if len(lights) < 2:
        raise EngineError("stacking needs at least two light frames")
    calibration = sum(len(g.darks) + len(g.flats) + len(g.bias) for g in job.groups)
    steps = calibration + 2 * len(lights) + 1
    done = 0

    def tick(message: str) -> None:
        nonlocal done
        done += 1
        emit({"progress": round(done / steps, 4), "message": message})

    masters = [build_masters(group, reader, work, tick) for group in job.groups]

    def prepared(light: Light) -> tuple[np.ndarray, CfaFrame, np.ndarray]:
        frame = reader(light.path)
        cfa = calibrate(frame, masters[light.group])
        return cfa, frame, demosaic(cfa, frame.pattern)

    # Middle frame as the reference: the least rotation to the first and last.
    order = [len(lights) // 2] + [i for i in range(len(lights)) if i != len(lights) // 2]
    reference_stars = None
    reference_background = None
    count = mean = m2 = None
    skipped: list[tuple[str, str]] = []
    header: dict[str, object] = {}
    for i in order:
        light = lights[i]
        try:
            cfa, frame, rgb = prepared(light)
            stars = find_stars(green_plane(cfa, frame.pattern))
            if reference_stars is None:
                if len(stars) < MIN_STARS:
                    raise EngineError(f"the reference frame shows only {len(stars)} stars")
                reference_stars, light.matrix = stars, np.eye(3)
                shape = rgb.shape
                count = np.zeros(shape, np.uint16)
                mean = np.zeros(shape, np.float32)
                m2 = np.zeros(shape, np.float32)
                header = {"WHITE": frame.white, "BLACK": float(frame.black.mean())}
                full_scale = (frame.white - float(frame.black.mean())) * float(frame.wb.max())
            else:
                if rgb.shape != count.shape:
                    raise EngineError(f"it is {rgb.shape[1:]}, the reference is {count.shape[1:]}")
                light.matrix = to_full_size(match(stars, reference_stars))
            aligned = warp(rgb, light.matrix)
            background = _background(aligned)
            if reference_background is None:
                reference_background = background
            light.offset = (reference_background - background)[:, None, None]
            accumulate(aligned + light.offset, count, mean, m2)
        except (EngineError, UnsupportedSensor, OSError) as error:
            if reference_stars is None:
                raise EngineError(f"{light.path.name}: {error}") from error
            skipped.append((light.path.name, str(error)))
        tick(f"registering {light.path.name}")

    used = [light for light in lights if light.offset is not None]
    if len(used) < 2:
        raise EngineError("fewer than two lights could be registered")
    total = np.zeros(count.shape, np.float32)
    kept = np.zeros(count.shape, np.uint16)
    rejected = 0
    for light in lights:
        if light.offset is not None:
            _, _, rgb = prepared(light)
            aligned = warp(rgb, light.matrix) + light.offset
            rejected += accumulate_clipped(aligned, count, mean, m2, job.kappa, total, kept)
        tick(f"stacking {light.path.name}")
    del m2
    with np.errstate(divide="ignore", invalid="ignore"):
        stacked = np.where(kept > 0, total / kept, np.where(count > 0, mean, np.nan))
    stacked = stacked.astype(np.float32)

    fits_path, tiff_path, preview_path = out / "stack.fits", out / "stack.tif", out / "preview.png"
    header.update(
        {
            "NCOMBINE": len(used),
            "KAPPA": float(job.kappa),
            "OBJECT": job.subject or "unknown",
            "CREATOR": "SimpleStax deep-sky engine",
        }
    )
    write_fits(fits_path, stacked, header)
    write_tiff16(tiff_path, stacked, full_scale)
    step = max(1, -(-stacked.shape[2] // PREVIEW_WIDTH))
    write_png(preview_path, auto_stretch(stacked[:, ::step, ::step]))
    tick("writing the stack")
    samples = int(np.count_nonzero(np.isfinite(stacked))) * len(used)
    return {
        "fits": str(fits_path),
        "tiff": str(tiff_path),
        "preview": str(preview_path),
        "stacked": len(used),
        "skipped": skipped,
        "rejected_percent": round(100 * rejected / max(samples, 1), 3),
    }


def main(argv: list[str]) -> int:
    def emit(message: dict) -> None:
        print(json.dumps(message), flush=True)

    if len(argv) != 1:
        print("usage: simplestax --engine deepsky JOB.json", file=sys.stderr)
        return 2
    try:
        job = StackJob.from_json(Path(argv[0]).read_text())
        emit({"result": run(job, emit)})
    except (EngineError, UnsupportedSensor) as error:
        emit({"error": str(error)})
        return 1
    except Exception as error:  # report anything else instead of dying silently
        traceback.print_exc()
        emit({"error": f"{type(error).__name__}: {error}"})
        return 1
    return 0
