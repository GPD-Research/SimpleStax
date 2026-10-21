"""Finish engine: the full-size linear stack through the auto-finish chain, in its own process.

The job is JSON: {"fits", "output", "settings", "measurements"}. Progress, errors and the result
go to stdout as one JSON object per line, like the deep-sky engine.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

import numpy as np

from simplestax.finish import FinishSettings, Measurements, finish
from simplestax.output import read_fits, write_png, write_tiff16

Emit = Callable[[dict], None]


def run(job: dict, emit: Emit) -> dict:
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    settings = FinishSettings.from_dict(job["settings"])
    measurements = Measurements.from_dict(job["measurements"])

    def progress(fraction: float, message: str) -> None:
        emit({"progress": round(0.15 + 0.55 * fraction, 4), "message": message})

    emit({"progress": 0.0, "message": "reading the stack"})
    rgb = np.array(read_fits(Path(job["fits"])), np.float32)
    display = finish(rgb, settings, measurements, progress=progress)
    del rgb
    tiff_path, png_path = out / "finished.tif", out / "finished.png"
    emit({"progress": 0.75, "message": "writing the 16-bit TIFF"})
    write_tiff16(tiff_path, display, 1.0)
    emit({"progress": 0.9, "message": "writing the PNG"})
    write_png(png_path, display)
    (out / "finished.json").write_text(json.dumps({"settings": settings.to_dict()}, indent=2))
    emit({"progress": 1.0, "message": "done"})
    return {"tiff": str(tiff_path), "png": str(png_path)}


def main(argv: list[str]) -> int:
    def emit(message: dict) -> None:
        print(json.dumps(message), flush=True)

    if len(argv) != 1:
        print("usage: simplestax --engine finish JOB.json", file=sys.stderr)
        return 2
    try:
        emit({"result": run(json.loads(Path(argv[0]).read_text()), emit)})
    except (OSError, ValueError, KeyError) as error:
        emit({"error": f"{type(error).__name__}: {error}"})
        return 1
    except Exception as error:  # report anything else instead of dying silently
        traceback.print_exc()
        emit({"error": f"{type(error).__name__}: {error}"})
        return 1
    return 0
