from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QPalette

from simplestax.frames import FrameInfo, FrameKind
from simplestax.library import Check, Profile, fits
from simplestax.ui.frame_picker import UNKNOWN, FramePicker, raw_files

PROFILE = Profile(focal_mm=55, f_number=1.8, exposure_s=30, iso=1600)
WHEN = datetime(2026, 10, 21, 23, 0)
INFOS = {
    "a_match.ARW": FrameInfo(WHEN, 30, 1600, 55, 1.8),
    "b_short.ARW": FrameInfo(WHEN, 10, 1600, 55, 1.8),
    "c_wide_open.ARW": FrameInfo(WHEN, 30, 1600, 55, 1.4),
    "d_manual.ARW": FrameInfo(WHEN, 30, 1600, None, None),
}


@pytest.fixture
def card(tmp_path):
    for name in [*INFOS, "notes.txt"]:
        (tmp_path / name).write_bytes(b"raw")
    return tmp_path


def _picker(card, kind=FrameKind.LIGHT):
    picker = FramePicker(kind, PROFILE, card, reader=lambda path: INFOS[path.name])
    picker.wait_for_exif()
    return picker


def _names(paths):
    return {Path(p).name for p in paths}


def test_fits_treats_missing_values_as_unknown():
    manual = INFOS["d_manual.ARW"]
    assert fits(FrameKind.LIGHT, Check.FOCAL, manual, PROFILE) is None
    assert fits(FrameKind.LIGHT, Check.EXPOSURE, manual, PROFILE) is True
    assert fits(FrameKind.LIGHT, Check.EXPOSURE, INFOS["b_short.ARW"], PROFILE) is False
    fast = FrameInfo(exposure_s=1 / 4000, iso=1600)
    assert fits(FrameKind.BIAS, Check.EXPOSURE, fast, PROFILE) is True
    assert fits(FrameKind.BIAS, Check.EXPOSURE, INFOS["a_match.ARW"], PROFILE) is False


def test_raw_files_skips_other_files(card):
    assert [p.name for p in raw_files(card)] == sorted(INFOS)
    assert raw_files(card / "missing") == []


def test_mismatches_are_dimmed_but_manual_glass_is_not(qapp, card):
    picker = _picker(card)
    dimmed = {p.name for p in picker._paths if picker.is_dimmed(p)}
    assert dimmed == {"b_short.ARW", "c_wide_open.ARW"}
    disabled = picker.table.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
    rows = {picker.table.item(r, 0).text(): r for r in range(picker.table.rowCount())}
    assert picker.table.item(rows["b_short.ARW"], 0).foreground().color() == disabled
    assert picker.table.item(rows["a_match.ARW"], 0).foreground().color() != disabled
    assert picker.table.item(rows["d_manual.ARW"], 3).text() == UNKNOWN
    assert "manual" in picker.table.item(rows["d_manual.ARW"], 3).toolTip()
    assert picker.summary.text().startswith("2 of 4 match")


def test_select_matching_and_dimmed_frames_stay_selectable(qapp, card):
    picker = _picker(card)
    assert not picker.ok.isEnabled()
    picker.select_matching()
    assert _names(picker.selected_paths()) == {"a_match.ARW", "d_manual.ARW"}
    assert "2 of 4 match · 2 picked" in picker.summary.text()
    picker.table.selectAll()
    assert len(picker.selected_paths()) == 4
    assert picker.ok.isEnabled()


def test_unticking_a_filter_undims(qapp, card):
    picker = _picker(card)
    picker.checks[Check.EXPOSURE].setChecked(False)
    assert not picker.is_dimmed(card / "b_short.ARW")
    assert picker.is_dimmed(card / "c_wide_open.ARW")


def test_flats_ignore_exposure(qapp, card):
    picker = _picker(card, FrameKind.FLAT)
    assert set(picker.checks) == {Check.FOCAL, Check.APERTURE}
    assert not picker.is_dimmed(card / "b_short.ARW")
    assert picker.is_dimmed(card / "c_wide_open.ARW")


def test_import_reuses_picker_exif(qapp, preferences, card, tmp_path):
    from simplestax.subjects import subject
    from simplestax.ui.import_page import ImportPage

    preferences.library_root = tmp_path / "lib"
    page = ImportPage(preferences)
    page.set_subject(subject("nebula"))
    page.confirm = lambda items, flagged: "all"
    page.subject_name.setCurrentText("M42")
    page.exposure.setCurrentText("30″")
    page.focal.setValue(55)
    page.iso.setValue(1600)
    infos = {card / name: info for name, info in INFOS.items()}
    page.import_paths(FrameKind.LIGHT, [card / "a_match.ARW"], infos)
    page.wait_for_import()
    filed = list((tmp_path / "lib").rglob("a_match_M42_2026-10-21.ARW"))
    assert len(filed) == 1
