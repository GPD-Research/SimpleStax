"""What a frame file says about itself: kind, night, and the exposure settings in its EXIF."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path

RAW_SUFFIXES = (".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2", ".dng", ".pef")
# A night runs noon to noon, so frames shot after midnight stay with their evening.
NIGHT_ROLLOVER_HOUR = 12


class FrameKind(str, Enum):
    LIGHT = "lights"
    DARK = "darks"
    FLAT = "flats"
    BIAS = "bias"

    @property
    def label(self) -> str:
        return self.value.capitalize()


@dataclass(frozen=True)
class FrameInfo:
    """EXIF values; None when the camera didn't record them (manual lenses report 0 for both)."""

    taken: datetime | None = None
    exposure_s: float | None = None
    iso: int | None = None
    focal_mm: float | None = None
    f_number: float | None = None
    camera: str = ""


def night_of(taken: datetime) -> date:
    return (taken - timedelta(hours=NIGHT_ROLLOVER_HOUR)).date()


def parse_number(text: str) -> float | None:
    """'1/4000' or '71/10' or '4' to a float; 0 and unreadable values become None."""
    text = text.strip().strip("[]").split(",")[0].strip()
    try:
        if "/" in text:
            top, bottom = text.split("/", 1)
            value = float(top) / float(bottom)
        else:
            value = float(text)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def parse_taken(text: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _exif_tags(path: Path) -> dict[str, str]:
    import exifread

    with path.open("rb") as handle:
        tags = exifread.process_file(handle, details=False)
    return {key: str(value) for key, value in tags.items()}


def read_info(path: Path, tags: Callable[[Path], dict[str, str]] = _exif_tags) -> FrameInfo:
    try:
        found = tags(path)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return FrameInfo()
    iso = parse_number(found.get("EXIF ISOSpeedRatings", ""))
    return FrameInfo(
        taken=parse_taken(found.get("EXIF DateTimeOriginal", "")),
        exposure_s=parse_number(found.get("EXIF ExposureTime", "")),
        iso=int(iso) if iso else None,
        focal_mm=parse_number(found.get("EXIF FocalLength", "")),
        f_number=parse_number(found.get("EXIF FNumber", "")),
        camera=found.get("Image Model", "").strip(),
    )
