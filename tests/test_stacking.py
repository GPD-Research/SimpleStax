import json
from datetime import datetime
from pathlib import Path

from simplestax.frames import FrameInfo, FrameKind
from simplestax.library import Library, Profile
from simplestax.stacking import (
    StackJob,
    calibration_advice,
    engine_command,
    output_folder,
    parse_engine_line,
    plan_job,
)

PROFILE = Profile(55, 1.8, 3.2, 5000)


def library_with(tmp_path, kinds):
    lib = Library(tmp_path / "lib")
    info = FrameInfo(taken=datetime(2026, 10, 20, 22, 0), exposure_s=3.2, iso=5000)
    for kind, n in kinds.items():
        sources = []
        for i in range(n):
            src = tmp_path / "src" / f"{kind.value}{i}.ARW"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(b"x")
            sources.append(src)
        items = lib.plan_import(sources, kind, PROFILE, "M31", reader=lambda _p: info)
        lib.import_frames(items, PROFILE)
    return lib


def test_sessions_and_job(tmp_path):
    lib = library_with(tmp_path, {FrameKind.LIGHT: 3, FrameKind.BIAS: 2})
    (session,) = lib.sessions("m31")
    assert session.night == "2026-10-20" and session.lights == 3 and session.profile == PROFILE
    job = plan_job(lib, "M31", [session], {FrameKind.BIAS}, tmp_path / "out")
    assert len(job.groups[0].lights) == 3 and len(job.groups[0].bias) == 2
    assert StackJob.from_json(job.to_json()) == job
    unused = plan_job(lib, "M31", [session], set(), tmp_path / "out")
    assert unused.groups[0].bias == []


def test_advice_explains_lights_only():
    text = " ".join(calibration_advice({}, set()))
    assert "No darks" in text and "outlier rejection" in text and "No flats" in text
    used = calibration_advice({FrameKind.DARK: 20}, {FrameKind.DARK})
    assert used[0].startswith("Darks (20)")


def test_engine_plumbing(tmp_path):
    assert engine_command(Path("j.json"))[-3:] == ["--engine", "deepsky", "j.json"]
    folder = output_folder(tmp_path, "M 31", datetime(2026, 10, 21, 1, 2, 3))
    assert folder == tmp_path / "stacks" / "M-31" / "2026-10-21_010203"
    assert parse_engine_line(json.dumps({"progress": 0.5})) == {"progress": 0.5}
    assert parse_engine_line("Traceback…") is None


def test_pages_hand_the_selection_to_stack(qapp, preferences, tmp_path, monkeypatch):
    from simplestax.engines import pss
    from simplestax.stages import Stage

    monkeypatch.setenv(pss.ENV_OVERRIDE, str(tmp_path / "no-pss"))
    from simplestax.ui.main_window import MainWindow

    library_with(tmp_path, {FrameKind.LIGHT: 2})
    preferences.library_root = tmp_path / "lib"
    window = MainWindow(preferences)
    window.subject_box.setCurrentIndex(window.subject_box.findData("galaxy"))
    page = window.calibrate_page
    page.refresh()
    assert page.subject_name.currentText() == "M31" and page.sessions.count() == 1
    assert not page.use[FrameKind.DARK].isEnabled()
    assert "No darks" in page.advice.text()
    page.next_button.click()
    assert window.current_stage() is Stage.STACK
    stack = window.stack_page
    assert stack.run_button.isEnabled() and "2 lights" in stack.summary.text()
    stack.handle_line(json.dumps({"progress": 0.25, "message": "registering"}))
    assert "25%" in stack.meter.text()
    window.subject_box.setCurrentIndex(window.subject_box.findData("planetary"))
    assert not stack.run_button.isEnabled()
    assert stack.install_button.isVisibleTo(stack) and "isn't installed" in stack.status.text()
