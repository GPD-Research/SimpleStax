"""The subjects SimpleStax has a workflow for, and what each one needs from a night out."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Engine(str, Enum):
    """Which stacking engine a subject's pipeline runs on."""

    # Lucky imaging: thousands of short frames, ranked, the sharpest few aligned patch by patch.
    PLANETARY = "planetary"
    # Calibrate, register on stars, reject outliers, average.
    DEEP_SKY = "deep_sky"


@dataclass(frozen=True)
class Subject:
    key: str
    name: str
    engine: Engine
    summary: str
    # Typical number of light frames for a usable result, low to high.
    lights: tuple[int, int]
    darks: bool
    flats: bool
    bias: bool
    # Whether a tracking mount is close to required rather than merely helpful.
    tracking: bool


SUBJECTS: tuple[Subject, ...] = (
    Subject(
        key="planetary",
        name="Planets",
        engine=Engine.PLANETARY,
        summary="Video or burst of short frames; the sharpest few percent are stacked.",
        lights=(1000, 10000),
        darks=False,
        flats=True,
        bias=False,
        tracking=False,
    ),
    Subject(
        key="lunar",
        name="Moon",
        engine=Engine.PLANETARY,
        summary="Bright and large: short frames, aligned on craters rather than the disc.",
        lights=(100, 3000),
        darks=False,
        flats=True,
        bias=False,
        tracking=False,
    ),
    Subject(
        key="nebula",
        name="Nebulae",
        engine=Engine.DEEP_SKY,
        summary="Faint extended glow: hours of integration and full calibration.",
        lights=(40, 300),
        darks=True,
        flats=True,
        bias=True,
        tracking=True,
    ),
    Subject(
        key="galaxy",
        name="Galaxies",
        engine=Engine.DEEP_SKY,
        summary="Small, faint targets: long focal length, many frames, full calibration.",
        lights=(60, 500),
        darks=True,
        flats=True,
        bias=True,
        tracking=True,
    ),
    Subject(
        key="starfield",
        name="Star fields",
        engine=Engine.DEEP_SKY,
        summary="Wide or medium fields of stars; short untracked frames work.",
        lights=(10, 100),
        darks=True,
        flats=True,
        bias=True,
        tracking=False,
    ),
    Subject(
        key="milkyway",
        name="Milky Way",
        engine=Engine.DEEP_SKY,
        summary="Wide-angle core or band, often with foreground; untracked frames work.",
        lights=(10, 60),
        darks=True,
        flats=True,
        bias=True,
        tracking=False,
    ),
)


def subject(key: str) -> Subject:
    for candidate in SUBJECTS:
        if candidate.key == key:
            return candidate
    raise KeyError(key)
