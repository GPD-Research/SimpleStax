"""
Planetary engine: each video, and each night's RAW burst, is stacked by PlanetarySystemStacker,
then saved the same way as a deep-sky stack so Finish can pick it up. RAW bursts are calibrated
here on the CFA grid first (PSS can't read RAW), cropped around the object and passed as TIFFs.

PSS runs as its own process from its own virtual environment (see `pss.py`); this module only
reads and writes images. `simplestax --engine planetary job.json` runs a stack and
`simplestax --engine pss-install` sets PSS up.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

import numpy as np
import tifffile
from scipy import ndimage

from simplestax.cfa import UnsupportedSensor, demosaic, read_cfa
from simplestax.engines import pss
from simplestax.engines.deepsky import Reader, build_masters, calibrate
from simplestax.frames import RAW_SUFFIXES
from simplestax.output import read_fits, write_fits, write_png, write_tiff16
from simplestax.stacking import StackJob

Emit = Callable[[dict], None]
PREVIEW_WIDTH = 720
CROP_MARGIN = 1.6  # crop side as a multiple of the planet's or Moon's size
MIN_CROP = 128
PSS_DEPENDENCIES = (
    "numpy",
    "scipy",
    "astropy",
    "scikit-image",
    "opencv-python-headless",
    "matplotlib",
    "psutil",
    "PyQt5",
)


class EngineError(RuntimeError):
    pass


def flat_for(job: StackJob) -> Path | None:
    """PSS takes one flat, as a video or a single image; RAW flats can't be passed to it."""
    for group in job.groups:
        for flat in group.flats:
            if Path(flat).suffix.lower() in (
                ".avi",
                ".mov",
                ".mp4",
                ".ser",
                ".tif",
                ".tiff",
                ".png",
            ):
                return Path(flat)
    return None


def bright_object(rgb: np.ndarray) -> tuple[float, float, int] | None:
    """Centre (y, x) and size in pixels of the biggest bright object: the planet or the Moon."""
    step = max(1, max(rgb.shape[1:]) // 1000)
    luma = ndimage.uniform_filter(rgb[:, ::step, ::step].mean(axis=0), 3)
    sky, peak = float(np.median(luma)), float(np.percentile(luma, 99.99))
    if peak <= sky:
        return None
    labels, count = ndimage.label(luma > sky + 0.25 * (peak - sky))
    if count == 0:
        return None
    biggest = int(np.argmax(ndimage.sum_labels(np.ones_like(luma), labels, range(1, count + 1))))
    box = ndimage.find_objects(labels)[biggest]
    cy, cx = ndimage.center_of_mass(np.clip(luma - sky, 0, None), labels, biggest + 1)
    size = max(box[0].stop - box[0].start, box[1].stop - box[1].start)
    return cy * step, cx * step, size * step


def crop(rgb: np.ndarray, centre: tuple[float, float], size: tuple[int, int]) -> np.ndarray:
    """A window of [size] centred on the object, slid inside the frame at the edges."""
    _, h, w = rgb.shape
    top = int(np.clip(round(centre[0] - size[0] / 2), 0, h - size[0]))
    left = int(np.clip(round(centre[1] - size[1] / 2), 0, w - size[1]))
    return rgb[:, top : top + size[0], left : left + size[1]]


def crop_size(found_size: float, shape: tuple[int, int]) -> tuple[int, int]:
    """Square around the object, but never past the frame: a Moon bigger than the frame keeps
    the whole of that axis."""
    side = max(MIN_CROP, int(found_size * CROP_MARGIN) // 2 * 2)
    return min(side, shape[0] // 2 * 2), min(side, shape[1] // 2 * 2)


def stabilisation(job: StackJob) -> str:
    """PSS aligns planets on the disc; the Moon often fills the frame, so align on its detail."""
    return "Surface" if job.workflow == "lunar" else "Planet"


def prepare_burst(
    paths: list[Path],
    masters,
    reader: Reader,
    folder: Path,
    progress: Callable[[float, str], None],
) -> tuple[np.ndarray, list[list[str]]]:
    """Calibrate RAW frames on the CFA grid, crop around the object and write 16-bit TIFFs for
    PSS. Cropping on each frame's own centre also takes out drift between frames. Returns the
    camera-to-sRGB matrix, to apply to the stack, and the frames that were left out."""
    folder.mkdir(parents=True, exist_ok=True)
    side = scale = colour = None
    skipped: list[list[str]] = []
    for i, path in enumerate(paths):
        progress(i / len(paths), f"calibrating {path.name}")
        try:
            frame = reader(path)
            rgb = demosaic(calibrate(frame, masters), frame.pattern)
            found = bright_object(rgb)
            if found is None:
                raise ValueError("no planet or Moon found in it")
            if side is None:
                side = crop_size(found[2], rgb.shape[1:])
                scale = (frame.white - float(frame.black.mean())) * float(frame.wb.max())
                colour = frame.colour
                shape = rgb.shape
            elif rgb.shape != shape:
                raise ValueError(f"it is {rgb.shape[1:]}, the first frame is {shape[1:]}")
            window = crop(rgb, found[:2], side)
            data = np.clip(window / scale * 65535, 0, 65535).astype(np.uint16)
            tifffile.imwrite(folder / f"{i:05d}.tif", np.moveaxis(data, 0, -1), photometric="rgb")
        except (UnsupportedSensor, OSError, ValueError) as error:
            skipped.append([path.name, str(error)])
    if len(paths) - len(skipped) < 2:
        raise EngineError("a RAW burst needs at least two frames that show the planet or Moon")
    return colour, skipped


def read_stack(path: Path) -> np.ndarray:
    """A PSS 16-bit FITS as (3, H, W) float on 0..1."""
    return np.ascontiguousarray(read_fits(path), np.float32) / 65535.0


def save(out: Path, name: str, rgb: np.ndarray, subject: str) -> dict:
    fits, tiff, preview = out / f"{name}.fits", out / f"{name}.tif", out / f"{name}_preview.png"
    write_fits(
        fits, rgb, {"OBJECT": subject or "unknown", "CREATOR": "SimpleStax planetary engine"}
    )
    write_tiff16(tiff, rgb, 1.0)
    step = max(1, -(-rgb.shape[2] // PREVIEW_WIDTH))
    small = rgb[:, ::step, ::step]
    write_png(preview, np.clip(small / max(float(np.percentile(small, 99.9)), 1e-6), 0, 1))
    return {"fits": str(fits), "tiff": str(tiff), "preview": str(preview)}


def run(
    job: StackJob, emit: Emit, executable: str | None = None, reader: Reader = read_cfa
) -> dict:
    executable = executable or pss.find_executable()
    if executable is None:
        raise EngineError(
            "PlanetarySystemStacker isn't installed. Use Install PSS on the Stack step."
        )
    out = Path(job.output)
    out.mkdir(parents=True, exist_ok=True)
    flat = flat_for(job)
    # One PSS run per video, and one per night for RAW stills (a burst).
    units: list[tuple[str, object]] = []
    for group in job.groups:
        raws = [Path(p) for p in group.lights if Path(p).suffix.lower() in RAW_SUFFIXES]
        units += [(Path(p).name, Path(p)) for p in group.lights if Path(p) not in raws]
        if raws:
            units.append((f"RAW burst {group.night}", (group, raws)))
    if not units:
        raise EngineError("There are no videos or RAW frames to stack.")
    stacks, skipped = [], []
    for i, (label, unit) in enumerate(units):

        def progress(fraction: float, message: str, i: int = i) -> None:
            emit({"progress": round((i + fraction) / len(units), 4), "message": message})

        colour, work = None, None
        if isinstance(unit, Path):
            source, pss_flat, share = unit, flat, 0.0
        else:
            group, raws = unit
            work = out / f"pss-frames-{group.night}".replace(".", "-")
            share = 0.5

            def tick(message: str) -> None:
                progress(0.0, message)

            masters = build_masters(group, reader, out, tick)
            colour, left_out = prepare_burst(
                raws, masters, reader, work, lambda f, m: progress(0.5 * f, m)
            )
            skipped += left_out
            source, pss_flat = work, None  # calibrated already, on the CFA grid
        progress(share, f"{label}: starting PSS")
        made = pss.output_path(source, "fits")
        made.unlink(missing_ok=True)
        job_pss = pss.PssJob(
            [source],
            stabilisation=stabilisation(job),
            stack_percent=pss.STACK_PERCENT,
            out_format="fits",
            flat=pss_flat,
        )
        process = subprocess.Popen(
            pss.command(job_pss, executable),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        tail: list[str] = []
        for line in process.stdout:
            tail = (tail + [line.rstrip()])[-5:]
            stage = pss.stage_of(line)
            if stage is not None:
                fraction = share + (1 - share) * stage / len(pss.STAGES)
                progress(fraction, f"{label}: {pss.STAGES[stage]}")
        ok = process.wait() == 0 and made.exists()
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
        if not ok:
            skipped.append([label, " / ".join(t for t in tail if t) or "PSS failed"])
            continue
        rgb = read_stack(made)
        if colour is not None:
            rgb = np.einsum("ij,jhw->ihw", colour, rgb, dtype=np.float32)
        name = "stack" if not stacks else f"stack_{len(stacks) + 1}"
        stacks.append({"video": label, **save(out, name, rgb, job.subject)})
        shutil.move(made, out / f"{source.stem}_pss.fits")
        progress(1.0, f"{label}: saved")
    if not stacks:
        raise EngineError("PSS couldn't stack any of them: " + "; ".join(s[1] for s in skipped))
    return {
        **{k: stacks[0][k] for k in ("fits", "tiff", "preview")},
        "engine": "planetary",
        "stacked": len(stacks),
        "stacks": stacks,
        "skipped": skipped,
        "stack_percent": pss.STACK_PERCENT,
    }


def install_steps(python: str, venv: Path = pss.DEFAULT_VENV) -> list[tuple[str, list[str]]]:
    pip = [str(venv / "bin" / "python"), "-m", "pip", "install", "--quiet"]
    return [
        ("creating PSS's own Python environment", [python, "-m", "venv", "--clear", str(venv)]),
        # PSS 0.9.8 pins numpy<1.23, which has no wheels for current Python; it runs on NumPy 2.
        ("downloading PlanetarySystemStacker", [*pip, "--no-deps", "planetary-system-stacker"]),
        ("downloading the libraries PSS needs", [*pip, *PSS_DEPENDENCIES]),
    ]


def install(emit: Emit) -> dict:
    python = shutil.which("python3")
    if python is None:
        raise EngineError("Installing PSS needs python3 (sudo apt install python3-venv).")
    steps = install_steps(python)
    for i, (message, command) in enumerate(steps):
        emit({"progress": round(i / len(steps), 4), "message": message})
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode != 0:
            raise EngineError(f"{message} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    emit({"progress": 1.0, "message": "done"})
    found = pss.find_executable()
    if found is None:
        raise EngineError("PSS installed but its launcher wasn't found.")
    return {"pss": found}


def main(argv: list[str], installing: bool = False) -> int:
    def emit(message: dict) -> None:
        print(json.dumps(message), flush=True)

    if not installing and len(argv) != 1:
        print("usage: simplestax --engine planetary JOB.json", file=sys.stderr)
        return 2
    try:
        if installing:
            emit({"result": install(emit)})
        else:
            emit({"result": run(StackJob.from_json(Path(argv[0]).read_text()), emit)})
    except EngineError as error:
        emit({"error": str(error)})
        return 1
    except Exception as error:  # report anything else instead of dying silently
        traceback.print_exc()
        emit({"error": f"{type(error).__name__}: {error}"})
        return 1
    return 0
