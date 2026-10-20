from datetime import datetime
from pathlib import Path

import pytest

from simplestax.frames import FrameInfo, FrameKind
from simplestax.library import Library, Profile

PROFILE = Profile(focal_mm=50, f_number=1.8, exposure_s=3.2, iso=1600)


def _info(taken="2026-10-21 22:00", exposure=3.2, iso=1600, focal=None, f_number=None):
    return FrameInfo(datetime.fromisoformat(taken), exposure, iso, focal, f_number, "ILCE-6000")


def _files(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"raw")
    return [folder / name for name in names]


def _import(library, sources, kind, info, subject="", move=False):
    items = library.plan_import(sources, kind, PROFILE, subject, reader=lambda _: info)
    return items, library.import_frames(items, PROFILE, move=move)


def test_layout_and_light_names(tmp_path):
    library = Library(tmp_path / "lib")
    lights = _files(tmp_path / "card", "DSC0001.ARW", "DSC0002.ARW")
    items, result = _import(library, lights, FrameKind.LIGHT, _info("2026-10-22 02:30"), "M 42")
    assert [str(item.relative) for item in items] == [
        "2026-10-21/lights/50mm_f1.8_3.2s_ISO1600/M-42/DSC0001_M-42_2026-10-21.ARW",
        "2026-10-21/lights/50mm_f1.8_3.2s_ISO1600/M-42/DSC0002_M-42_2026-10-21.ARW",
    ]
    assert len(result.imported) == 2 and all(path.exists() for path in lights)
    assert library.subjects() == ["M 42"]
    assert len(library.lights("m 42")) == 2 and library.lights("Andromeda") == []

    darks = _files(tmp_path / "card", "D1.ARW")
    flats = _files(tmp_path / "card", "F1.ARW")
    bias = _files(tmp_path / "card", "B1.ARW")
    for sources, kind, folder in (
        (darks, FrameKind.DARK, "darks/3.2s_ISO1600"),
        (flats, FrameKind.FLAT, "flats/50mm_f1.8"),
        (bias, FrameKind.BIAS, "bias/ISO1600"),
    ):
        info = _info(exposure=1 / 4000) if kind is FrameKind.BIAS else _info()
        items, _ = _import(library, sources, kind, info)
        assert str(items[0].relative) == f"2026-10-21/{folder}/{sources[0].name}"
        assert not items[0].problems


def test_calibration_matching_rules(tmp_path):
    library = Library(tmp_path / "lib")
    card = tmp_path / "card"
    _import(library, _files(card, "L1.ARW"), FrameKind.LIGHT, _info(), "m42")
    _import(library, _files(card, "D1.ARW"), FrameKind.DARK, _info("2026-09-01 22:00"))
    _import(library, _files(card, "F1.ARW"), FrameKind.FLAT, _info("2026-10-22 07:00"))
    _import(library, _files(card, "B1.ARW"), FrameKind.BIAS, _info("2026-03-01 20:00", 1 / 4000))
    assert library.latest_night("M42", PROFILE) == "2026-10-21"
    counts = library.counts("2026-10-21", "m42", PROFILE)
    # The dark is from another night; the morning flat and the old bias still count.
    assert counts == {FrameKind.LIGHT: 1, FrameKind.DARK: 0, FrameKind.FLAT: 1, FrameKind.BIAS: 1}
    other = Profile(85, 1.8, 2.0, 1600)
    assert library.latest_night("m42", other) is None


def test_mismatches_are_flagged(tmp_path):
    library = Library(tmp_path / "lib")
    card = tmp_path / "card"
    light = _files(card, "L.ARW")
    wrong = _info(exposure=2.5, iso=800, focal=85, f_number=2.0)
    problems = library.plan_import(light, FrameKind.LIGHT, PROFILE, "m42", lambda _: wrong)[
        0
    ].problems
    assert len(problems) == 4
    slow_bias = library.plan_import(light, FrameKind.BIAS, PROFILE, reader=lambda _: _info())
    assert "too long for a bias" in slow_bias[0].problems[0]
    undated = library.plan_import(light, FrameKind.DARK, PROFILE, reader=lambda _: FrameInfo())
    assert "no capture date" in undated[0].problems[0]


def test_lights_need_a_subject(tmp_path):
    with pytest.raises(ValueError):
        Library(tmp_path).plan_import([], FrameKind.LIGHT, PROFILE, "  ")


def test_move_and_name_collisions(tmp_path):
    library = Library(tmp_path / "lib")
    first = _files(tmp_path / "a", "DSC0001.ARW")
    second = _files(tmp_path / "b", "DSC0001.ARW")
    _import(library, first, FrameKind.DARK, _info(), move=True)
    items, _ = _import(library, second, FrameKind.DARK, _info())
    assert not first[0].exists()
    assert items[0].relative.name == "DSC0001-1.ARW"
    again = library.plan_import(
        [library.root / items[0].relative], FrameKind.DARK, PROFILE, reader=lambda _: _info()
    )
    assert again[0].problems == ["already in the library"]


def test_failed_copy_is_reported(tmp_path):
    library = Library(tmp_path / "lib")
    source = _files(tmp_path / "card", "L.ARW")
    items = library.plan_import(source, FrameKind.DARK, PROFILE, reader=lambda _: _info())
    source[0].unlink()
    result = library.import_frames(items, PROFILE)
    assert not result.imported and len(result.failed) == 1
