"""
The frame library: one folder per night, frames filed by kind, indexed in SQLite.

    <root>/<night>/lights/<focal>_<f>_<exposure>_<ISO>/<subject>/<name>_<subject>_<night>.ARW
    <root>/<night>/darks/<exposure>_<ISO>/     matched to lights by exposure, ISO and night
    <root>/<night>/flats/<focal>_<f>/          matched by lens, aperture and night
    <root>/<night>/bias/<ISO>/                 matched by ISO from any night

Darks and flats only match within a night because sensor temperature, dust and focus change
between nights; bias is stable for months. Several nights of one subject can still be stacked,
each night calibrated with its own masters.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from simplestax.frames import FrameInfo, FrameKind, night_of, read_info
from simplestax.planning import shutter_label

INDEX_NAME = "library.sqlite"
# EXIF exposures are rounded by the camera (3.2 s is stored as 16/5); allow a little slack.
TOLERANCE = 0.05
BIAS_MAX_S = 1 / 250

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    path TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    night TEXT NOT NULL,
    subject TEXT,
    focal REAL,
    f_number REAL,
    exposure REAL,
    iso INTEGER,
    camera TEXT,
    taken TEXT,
    source_name TEXT
);
CREATE TABLE IF NOT EXISTS subjects (name TEXT PRIMARY KEY COLLATE NOCASE);
"""


@dataclass(frozen=True)
class Profile:
    """The lens and exposure a set of lights was shot with."""

    focal_mm: float
    f_number: float
    exposure_s: float
    iso: int

    @property
    def lens_dir(self) -> str:
        return f"{self.focal_mm:g}mm_f{self.f_number:g}"

    @property
    def exposure_dir(self) -> str:
        return f"{shutter_label(self.exposure_s).replace('/', '-').rstrip('″')}s_ISO{self.iso}"

    @property
    def light_dir(self) -> str:
        return f"{self.lens_dir}_{self.exposure_dir}"


def slug(subject: str) -> str:
    return re.sub(r"[^\w-]+", "-", subject.strip()).strip("-")


def _differs(actual: float | None, expected: float) -> bool:
    return actual is not None and abs(actual - expected) > TOLERANCE * expected


def mismatches(kind: FrameKind, info: FrameInfo, profile: Profile) -> list[str]:
    problems = []
    if info.taken is None:
        problems.append("no capture date in the file; filed by its modified time")
    if info.iso is not None and info.iso != profile.iso and kind is not FrameKind.FLAT:
        problems.append(f"ISO {info.iso}, profile says {profile.iso}")
    if kind in (FrameKind.LIGHT, FrameKind.DARK) and _differs(info.exposure_s, profile.exposure_s):
        problems.append(
            f"exposure {shutter_label(info.exposure_s)}, "
            f"profile says {shutter_label(profile.exposure_s)}"
        )
    if kind in (FrameKind.LIGHT, FrameKind.FLAT):
        if _differs(info.focal_mm, profile.focal_mm):
            problems.append(f"{info.focal_mm:g} mm lens, profile says {profile.focal_mm:g} mm")
        if _differs(info.f_number, profile.f_number):
            problems.append(f"f/{info.f_number:g}, profile says f/{profile.f_number:g}")
    if kind is FrameKind.BIAS and info.exposure_s is not None and info.exposure_s > BIAS_MAX_S:
        problems.append(f"{shutter_label(info.exposure_s)} is too long for a bias frame")
    return problems


class Check(str, Enum):
    """One EXIF value a frame can be checked against the shot profile."""

    EXPOSURE = "exposure"
    ISO = "iso"
    FOCAL = "focal"
    APERTURE = "aperture"


# Which settings each kind has to share with the lights.
CHECKS: dict[FrameKind, tuple[Check, ...]] = {
    FrameKind.LIGHT: (Check.EXPOSURE, Check.ISO, Check.FOCAL, Check.APERTURE),
    FrameKind.DARK: (Check.EXPOSURE, Check.ISO),
    FrameKind.FLAT: (Check.FOCAL, Check.APERTURE),
    FrameKind.BIAS: (Check.EXPOSURE, Check.ISO),
}


def fits(kind: FrameKind, check: Check, info: FrameInfo, profile: Profile) -> bool | None:
    """Whether the frame's EXIF fits the profile on [check]; None when the file doesn't say."""
    if check is Check.EXPOSURE:
        if info.exposure_s is None:
            return None
        if kind is FrameKind.BIAS:
            return info.exposure_s <= BIAS_MAX_S * (1 + TOLERANCE)
        return not _differs(info.exposure_s, profile.exposure_s)
    if check is Check.ISO:
        return None if info.iso is None else info.iso == profile.iso
    actual, expected = (
        (info.focal_mm, profile.focal_mm)
        if check is Check.FOCAL
        else (info.f_number, profile.f_number)
    )
    return None if actual is None else not _differs(actual, expected)


@dataclass(frozen=True)
class Session:
    """The lights of one subject shot on one night with one profile."""

    night: str
    profile: Profile
    lights: int


@dataclass
class ImportItem:
    source: Path
    kind: FrameKind
    night: str
    relative: Path
    info: FrameInfo
    subject: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    imported: list[ImportItem] = field(default_factory=list)
    failed: list[tuple[ImportItem, str]] = field(default_factory=list)


class Library:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / INDEX_NAME)
        db.executescript(SCHEMA)
        return db

    def subjects(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT name FROM subjects ORDER BY name COLLATE NOCASE")
            return [name for (name,) in rows]

    def folder(self, kind: FrameKind, profile: Profile, night: str, subject: str = "") -> Path:
        base = Path(night) / kind.value
        if kind is FrameKind.LIGHT:
            return base / profile.light_dir / slug(subject)
        if kind is FrameKind.DARK:
            return base / profile.exposure_dir
        if kind is FrameKind.FLAT:
            return base / profile.lens_dir
        return base / f"ISO{profile.iso}"

    def plan_import(
        self,
        sources: Iterable[Path],
        kind: FrameKind,
        profile: Profile,
        subject: str = "",
        reader: Callable[[Path], FrameInfo] = read_info,
    ) -> list[ImportItem]:
        if kind is FrameKind.LIGHT and not slug(subject):
            raise ValueError("Light frames need a subject name.")
        taken_paths: set[Path] = set()
        items = []
        for source in sources:
            source = Path(source)
            info = reader(source)
            taken = info.taken or datetime.fromtimestamp(source.stat().st_mtime)
            night = night_of(taken).isoformat()
            name = source.name
            if kind is FrameKind.LIGHT:
                name = f"{source.stem}_{slug(subject)}_{night}{source.suffix}"
            folder = self.folder(kind, profile, night, subject)
            relative = self._free_name(folder / name, taken_paths)
            taken_paths.add(relative)
            items.append(
                ImportItem(
                    source=source,
                    kind=kind,
                    night=night,
                    relative=relative,
                    info=info,
                    subject=subject.strip() if kind is FrameKind.LIGHT else "",
                    problems=self._inside(source) + mismatches(kind, info, profile),
                )
            )
        return items

    def _inside(self, source: Path) -> list[str]:
        if source.resolve().is_relative_to(self.root.resolve()):
            return ["already in the library"]
        return []

    def _free_name(self, relative: Path, reserved: set[Path]) -> Path:
        candidate, number = relative, 1
        while candidate in reserved or (self.root / candidate).exists():
            candidate = relative.with_name(f"{relative.stem}-{number}{relative.suffix}")
            number += 1
        return candidate

    def import_frames(
        self,
        items: list[ImportItem],
        profile: Profile,
        move: bool = False,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> ImportResult:
        result = ImportResult()
        with self._connect() as db:
            for done, item in enumerate(items):
                if on_progress:
                    on_progress(done, len(items), item.source.name)
                target = self.root / item.relative
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if move:
                        shutil.move(item.source, target)
                    else:
                        shutil.copy2(item.source, target)
                except OSError as error:
                    result.failed.append((item, str(error)))
                    continue
                self._record(db, item, profile)
                db.commit()
                result.imported.append(item)
            if on_progress:
                on_progress(len(items), len(items), "")
        return result

    @staticmethod
    def _record(db: sqlite3.Connection, item: ImportItem, profile: Profile) -> None:
        info, kind = item.info, item.kind
        lens = kind in (FrameKind.LIGHT, FrameKind.FLAT)
        timed = kind in (FrameKind.LIGHT, FrameKind.DARK)
        db.execute(
            "INSERT OR REPLACE INTO frames VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.relative.as_posix(),
                kind.value,
                item.night,
                item.subject or None,
                profile.focal_mm if lens else None,
                profile.f_number if lens else None,
                profile.exposure_s if timed else info.exposure_s,
                profile.iso if kind is not FrameKind.FLAT else info.iso,
                info.camera,
                info.taken.isoformat() if info.taken else None,
                item.source.name,
            ),
        )
        if item.subject:
            db.execute("INSERT OR IGNORE INTO subjects VALUES (?)", (item.subject,))

    def latest_night(self, subject: str, profile: Profile) -> str | None:
        p = profile
        with self._connect() as db:
            (night,) = db.execute(
                "SELECT MAX(night) FROM frames WHERE kind = 'lights' AND subject = ? "
                "COLLATE NOCASE AND focal = ? AND f_number = ? AND exposure = ? AND iso = ?",
                (subject.strip(), p.focal_mm, p.f_number, p.exposure_s, p.iso),
            ).fetchone()
        return night

    @staticmethod
    def _matching(
        kind: FrameKind, night: str, subject: str, p: Profile
    ) -> tuple[str, tuple[object, ...]]:
        """WHERE clause for the frames of [kind] that go with this night's lights of [subject]."""
        if kind is FrameKind.LIGHT:
            return (
                "kind = 'lights' AND night = ? AND subject = ? COLLATE NOCASE AND focal = ? "
                "AND f_number = ? AND exposure = ? AND iso = ?",
                (night, subject.strip(), p.focal_mm, p.f_number, p.exposure_s, p.iso),
            )
        if kind is FrameKind.DARK:
            return (
                "kind = 'darks' AND night = ? AND exposure = ? AND iso = ?",
                (night, p.exposure_s, p.iso),
            )
        if kind is FrameKind.FLAT:
            return (
                "kind = 'flats' AND night = ? AND focal = ? AND f_number = ?",
                (night, p.focal_mm, p.f_number),
            )
        return "kind = 'bias' AND iso = ?", (p.iso,)

    def counts(self, night: str, subject: str, profile: Profile) -> dict[FrameKind, int]:
        """Frames that would calibrate this night's lights of [subject], by kind."""
        with self._connect() as db:
            counts = {}
            for kind in FrameKind:
                where, args = self._matching(kind, night, subject, profile)
                query = f"SELECT COUNT(*) FROM frames WHERE {where}"
                counts[kind] = db.execute(query, args).fetchone()[0]
            return counts

    def frames(self, kind: FrameKind, night: str, subject: str, profile: Profile) -> list[Path]:
        where, args = self._matching(kind, night, subject, profile)
        with self._connect() as db:
            rows = db.execute(f"SELECT path FROM frames WHERE {where} ORDER BY taken, path", args)
            return [self.root / path for (path,) in rows]

    def sessions(self, subject: str) -> list[Session]:
        """Every night and profile [subject] has lights for, newest night first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT night, focal, f_number, exposure, iso, COUNT(*) FROM frames "
                "WHERE kind = 'lights' AND subject = ? COLLATE NOCASE "
                "GROUP BY night, focal, f_number, exposure, iso ORDER BY night DESC",
                (subject.strip(),),
            )
            return [
                Session(night, Profile(focal, f_number, exposure, iso), count)
                for night, focal, f_number, exposure, iso, count in rows
            ]

    def lights(self, subject: str | None = None) -> list[Path]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT path FROM frames WHERE kind = 'lights' "
                "AND (? IS NULL OR subject = ? COLLATE NOCASE) ORDER BY night, path",
                (subject, subject),
            )
            return [self.root / path for (path,) in rows]
