"""
PlanetarySystemStacker (Rolf Hempel, GPL-3) as the planetary engine.

PSS runs headless when given arguments, so it is driven as a subprocess from its own virtual
environment: it pins PyQt5 and an old NumPy, neither of which belongs in SimpleStax's process.
Progress comes from the "+++ Start … +++" lines it prints between stages.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

EXECUTABLE = "PlanetarySystemStacker"
ENV_OVERRIDE = "SIMPLESTAX_PSS"
DEFAULT_VENV = Path.home() / ".local" / "share" / "simplestax" / "pss-venv"

# The stages PSS announces, in order, for a run without a protocol.
STAGES: tuple[str, ...] = (
    "reading frames",
    "ranking frames",
    "initializing frame alignment",
    "aligning all frames",
    "computing the reference frame",
    "creating alignment points",
    "ranking all frames at all alignment points",
    "stacking",
    "merging all alignment patches",
    "saving the stacked image",
)

_STAGE_LINE = re.compile(r"\+\+\+\s*(?:Start\s+)?(.+?)\s*\+\+\+")


@dataclass(frozen=True)
class PssJob:
    inputs: Sequence[Path]
    # "Planet" stabilises on the whole disc; "Surface" on a patch, for lunar and solar close-ups.
    stabilisation: str = "Planet"
    stack_percent: int = 10
    out_format: str = "tiff"
    dark: Path | None = None
    flat: Path | None = None
    drizzle: str = "Off"

    def __post_init__(self) -> None:
        if not self.inputs:
            raise ValueError("a PSS job needs at least one video or frame folder")
        if self.stabilisation not in ("Planet", "Surface"):
            raise ValueError(f"unknown stabilisation mode {self.stabilisation!r}")
        if not 1 <= self.stack_percent <= 100:
            raise ValueError("stack_percent must be between 1 and 100")
        if self.out_format not in ("png", "tiff", "fits"):
            raise ValueError(f"unknown output format {self.out_format!r}")


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_executable() -> str | None:
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return override if _runnable(Path(override)) else None
    bundled = DEFAULT_VENV / "bin" / EXECUTABLE
    if _runnable(bundled):
        return str(bundled)
    return shutil.which(EXECUTABLE)


def command(job: PssJob, executable: str) -> list[str]:
    args = [executable, *(str(p) for p in job.inputs)]
    args += ["--out_format", job.out_format, "-m", job.stabilisation]
    args += ["-s", str(job.stack_percent), "--drizzle", job.drizzle]
    if job.dark is not None:
        args += ["-d", str(job.dark)]
    if job.flat is not None:
        args += ["-f", str(job.flat)]
    return args


def output_path(input_path: Path, out_format: str) -> Path:
    """Where PSS writes the stack for one input: beside it, named after it with "_pss" added."""
    return input_path.with_name(f"{input_path.stem}_pss.{out_format}")


def stage_of(line: str) -> int | None:
    """Index into STAGES of the stage a PSS output line announces, or None."""
    match = _STAGE_LINE.search(line)
    if not match:
        return None
    text = match.group(1).lower()
    for index, stage in enumerate(STAGES):
        if text.startswith(stage):
            return index
    return None


def progress_of(line: str) -> float | None:
    index = stage_of(line)
    return None if index is None else index / len(STAGES)
