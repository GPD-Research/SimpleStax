from simplestax.stages import Stage
from simplestax.ui.main_window import MainWindow


def test_guided_steps_follow_the_per_subject_switch(qapp, preferences):
    window = MainWindow(preferences)
    assert window.visible_stages() == list(Stage)
    assert window.current_stage() is Stage.PLAN

    window.guided_box.setChecked(False)
    assert Stage.PLAN not in window.visible_stages()
    assert window.current_stage() is Stage.IMPORT
    assert preferences.wizard_enabled(window.subject.key) is False

    window.subject_box.setCurrentIndex(window.subject_box.findData("galaxy"))
    assert window.guided and Stage.PLAN in window.visible_stages()

    window.subject_box.setCurrentIndex(window.subject_box.findData("planetary"))
    assert not window.guided


def test_skip_to_import(qapp, preferences):
    window = MainWindow(preferences)
    window.plan_page.skip_requested.emit()
    assert window.current_stage() is Stage.IMPORT


def test_wizard_off_on_start(qapp, preferences):
    preferences.wizard_on_start = False
    assert MainWindow(preferences).current_stage() is Stage.IMPORT


def test_plan_feeds_the_shoot_checklist(qapp, preferences):
    window = MainWindow(preferences)
    window.subject_box.setCurrentIndex(window.subject_box.findData("nebula"))
    window.plan_page.lights.setValue(120)
    text = window.shoot_page.checklist.text()
    assert "120 light frames" in text and "darks" in text
    assert "NPF" in window.plan_page.result.text()
