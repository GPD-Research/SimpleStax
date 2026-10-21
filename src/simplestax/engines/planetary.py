"""
Planetary engine: each video (or image folder) is stacked by PlanetarySystemStacker, then saved
the same way as a deep-sky stack so Finish can pick it up.

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

from simplestax.engines import pss
from simplestax.output import read_fits, write_fits, write_png, write_tiff16
from simplestax.stacking import StackJob

Emit = Callable[[dict], None]
PREVIEW_WIDTH = 720
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


def run(job: StackJob, emit: Emit, executable: str | None = None) -> dict:
    executable = executable or pss.find_executable()
    if executable is None:
        raise EngineError(
            "PlanetarySystemStacker isn't installed. Use Install PSS on the Stack step."
        )
    videos = [Path(p) for group in job.groups for p in group.lights]
    if not videos:
        raise EngineError("There are no videos to stack.")
    out = Path(job.output)
    out.mkdir(parents=True, exist_ok=True)
    flat = flat_for(job)
    stacks, skipped = [], []
    for i, video in enumerate(videos):

        def progress(fraction: float, message: str, i: int = i) -> None:
            emit({"progress": round((i + fraction) / len(videos), 4), "message": message})

        progress(0.0, f"{video.name}: starting PSS")
        made = pss.output_path(video, "fits")
        made.unlink(missing_ok=True)
        job_pss = pss.PssJob([video], stack_percent=pss.STACK_PERCENT, out_format="fits", flat=flat)
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
                progress(stage / len(pss.STAGES), f"{video.name}: {pss.STAGES[stage]}")
        if process.wait() != 0 or not made.exists():
            skipped.append([video.name, " / ".join(t for t in tail if t) or "PSS failed"])
            continue
        rgb = read_stack(made)
        name = "stack" if not stacks else f"stack_{len(stacks) + 1}"
        stacks.append({"video": video.name, **save(out, name, rgb, job.subject)})
        shutil.move(made, out / f"{video.stem}_pss.fits")
        progress(1.0, f"{video.name}: saved")
    if not stacks:
        raise EngineError(
            "PSS couldn't stack any of the videos: " + "; ".join(s[1] for s in skipped)
        )
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
