import pytest

from simplestax.frames import FrameKind
from simplestax.subjects import subject
from simplestax.ui.import_page import ImportPage, parse_exposure
from simplestax.ui.widgets import NEXT_STEP_STYLE


@pytest.fixture
def page(qapp, preferences, tmp_path):
    preferences.library_root = tmp_path / "lib"
    page = ImportPage(preferences)
    page.set_subject(subject("nebula"))
    page.confirm = lambda items, flagged: "all"
    return page


def _card(tmp_path, *names):
    folder = tmp_path / "card"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"raw")
    return [str(folder / name) for name in names]


def test_parse_exposure():
    assert parse_exposure("3.2″") == 3.2
    assert parse_exposure("1/500") == 1 / 500
    assert parse_exposure("4s") == 4.0
    assert parse_exposure("fast") is None


def test_next_step_walks_subject_then_each_kind(page, tmp_path):
    assert page.next_step() is page.subject_name
    assert page.subject_name.styleSheet() == NEXT_STEP_STYLE

    page.subject_name.setCurrentText("Orion Nebula")
    assert page.next_step() is page.buttons[FrameKind.LIGHT]

    page.import_paths(FrameKind.LIGHT, _card(tmp_path, "L1.ARW", "L2.ARW"))
    page.wait_for_import()
    assert "Filed 2 frames" in page.report.text()
    assert page.subject_name.findText("Orion Nebula") >= 0
    lights = list((tmp_path / "lib").rglob("*_Orion-Nebula_*.ARW"))
    assert len(lights) == 2
    night = lights[0].relative_to(tmp_path / "lib").parts[0]

    for kind in (FrameKind.DARK, FrameKind.FLAT, FrameKind.BIAS):
        assert page.next_step() is page.buttons[kind]
        assert page.buttons[kind].styleSheet() == NEXT_STEP_STYLE
        page.import_paths(kind, _card(tmp_path, f"{kind.value}.ARW"))
        page.wait_for_import()
    assert page.next_step() is None
    assert f"Night of {night}" in page.status.text() and "Ready" in page.status.text()
    assert (tmp_path / "card" / "L1.ARW").exists()


def test_planetary_needs_only_flats(page):
    page.set_subject(subject("planetary"))
    assert page.needed_kinds() == [FrameKind.LIGHT, FrameKind.FLAT]


def test_cancelled_batch_files_nothing(page, tmp_path):
    page.confirm = lambda items, flagged: "cancel"
    page.subject_name.setCurrentText("m42")
    page.import_paths(FrameKind.LIGHT, _card(tmp_path, "L1.ARW"))
    page.wait_for_import()
    assert not list((tmp_path / "lib").rglob("*.ARW"))


def test_bad_exposure_is_the_next_step(page):
    page.exposure.setCurrentText("soon")
    assert page.profile() is None and page.next_step() is page.exposure
