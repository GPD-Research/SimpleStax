import pytest

from simplestax.simbad import LookupError, Target
from simplestax.subjects import subject
from simplestax.ui.pages import PlanPage


def _page(qapp, resolve):
    page = PlanPage(resolve=resolve)
    page.set_subject(subject("nebula"))
    return page


def _lookup(page, name):
    page.target.setText(name)
    page.look_up_target()
    page.wait_for_lookup()
    qapp_process(page)


def qapp_process(page):
    from PySide6.QtWidgets import QApplication

    for _ in range(5):
        QApplication.processEvents()


def test_simbad_fills_declination_but_stays_editable(qapp):
    page = _page(qapp, lambda name: Target("M 42", 83.82, -5.3876))
    _lookup(page, "m42")
    assert page.declination.value() == pytest.approx(-5.39, abs=0.01)
    assert "M 42" in page.target_status.text()
    page.declination.setValue(40)
    assert page.declination.value() == 40


def test_unknown_or_offline_keeps_the_typed_declination(qapp):
    page = _page(qapp, lambda name: None)
    page.declination.setValue(12)
    _lookup(page, "nothing")
    assert page.declination.value() == 12
    assert "doesn't know" in page.target_status.text()

    def offline(name):
        raise LookupError("offline")

    page = _page(qapp, offline)
    _lookup(page, "M31")
    assert "Couldn't reach" in page.target_status.text()
    assert page.lookup_button.isEnabled()


def test_sensor_table_and_camera_model_fill_the_size(qapp):
    page = _page(qapp, lambda name: None)
    assert page.manufacturer.currentText() == "Custom"
    assert not page.sensor_format.isEnabled()
    page.manufacturer.setCurrentText("Canon")
    page.sensor_format.setCurrentText("Full frame")
    assert (page.sensor_width.value(), page.sensor_height.value()) == (36.0, 24.0)

    assert page.apply_camera("ILCE-6000")
    assert page.manufacturer.currentText() == "Sony"
    assert (page.sensor_width.value(), page.sensor_height.value()) == (23.5, 15.6)
    assert not page.apply_camera("Mystery Cam")
    assert page.manufacturer.currentText() == "Sony"
