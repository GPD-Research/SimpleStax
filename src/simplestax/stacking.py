"""
What the deep-sky engine is asked to do: which frames, which calibration, where to write.

Kept free of NumPy so the GUI can build jobs without loading the processing stack; the engine
itself runs in its own process (`simplestax --engine deepsky job.json`).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from simplestax.frames import FrameKind
from simplestax.library import Library, Session, slug

DEFAULT_KAPPA = 3.0
CALIBRATION_KINDS = (FrameKind.DARK, FrameKind.FLAT, FrameKind.BIAS)


@dataclass
class FrameGroup:
    """One night's lights and the calibration frames shot for them."""

    night: str
    lights: list[str]
    darks: list[str] = field(default_factory=list)
    flats: list[str] = field(default_factory=list)
    bias: list[str] = field(default_factory=list)


@dataclass
class StackJob:
    groups: list[FrameGroup]
    output: str
    kappa: float = DEFAULT_KAPPA
    subject: str = ""
    flatten: bool = True

    @property
    def light_count(self) -> int:
        return sum(len(group.lights) for group in self.groups)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_json(cls, text: str) -> StackJob:
        raw = json.loads(text)
        groups = [FrameGroup(**group) for group in raw.pop("groups")]
        return cls(groups=groups, **raw)


def output_folder(root: Path, subject: str, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return Path(root) / "stacks" / (slug(subject) or "stack") / stamp


def plan_job(
    library: Library,
    subject: str,
    sessions: list[Session],
    use: set[FrameKind],
    output: Path,
    kappa: float = DEFAULT_KAPPA,
    flatten: bool = True,
) -> StackJob:
    groups = []
    for session in sessions:

        def paths(kind: FrameKind, s: Session = session) -> list[str]:
            if kind is not FrameKind.LIGHT and kind not in use:
                return []
            return [str(p) for p in library.frames(kind, s.night, subject, s.profile)]

        groups.append(
            FrameGroup(
                night=session.night,
                lights=paths(FrameKind.LIGHT),
                darks=paths(FrameKind.DARK),
                flats=paths(FrameKind.FLAT),
                bias=paths(FrameKind.BIAS),
            )
        )
    return StackJob(
        groups=groups, output=str(output), kappa=kappa, subject=subject, flatten=flatten
    )


def calibration_advice(available: dict[FrameKind, int], use: set[FrameKind]) -> list[str]:
    """One line per calibration kind: what it fixes, or what happens without it."""
    lines = []
    darks, flats, bias = (available.get(kind, 0) for kind in CALIBRATION_KINDS)
    if FrameKind.DARK in use and darks:
        lines.append(f"Darks ({darks}): subtract hot pixels, amp glow and the sensor's zero level.")
    else:
        lines.append(
            "No darks: hot pixels are removed by outlier rejection instead. Untracked frames "
            "drift across the sensor, so a hot pixel lands on different stars in each frame."
        )
    if FrameKind.FLAT in use and flats:
        lines.append(f"Flats ({flats}): correct vignetting and dust shadows.")
    else:
        lines.append(
            "No flats: vignetting and dust spots stay. Vignetting is a smooth falloff that "
            "background removal mostly takes out later; dust spots remain."
        )
    if FrameKind.BIAS in use and bias:
        lines.append(f"Bias ({bias}): the sensor's zero level, for flats and dark-less lights.")
    else:
        lines.append("No bias: the black level the camera records in each file is used instead.")
    return lines


def engine_command(job_path: Path) -> list[str]:
    """How to start the deep-sky engine, from source or from the frozen .deb build."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--engine", "deepsky", str(job_path)]
    return [sys.executable, "-m", "simplestax", "--engine", "deepsky", str(job_path)]


def parse_engine_line(line: str) -> dict | None:
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None
