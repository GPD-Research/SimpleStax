from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simplestax import simbad
from simplestax.planning import (
    CalibrationPlan,
    crop_factor,
    master_noise_factor,
    moon_pixels,
    npf_exposure,
    pixel_pitch_um,
    recommend_calibration,
    rule_500,
    shutter_at_most,
    shutter_label,
)
from simplestax.sensors import SENSOR_FORMATS, guess_format, sensor_size
from simplestax.stages import Stage
from simplestax.subjects import Engine, Subject
from simplestax.ui.widgets import ProgressMeter


def _heading(stage: Stage) -> QLabel:
    label = QLabel(f"<h2>{stage.title}</h2><p>{stage.blurb}</p>")
    label.setWordWrap(True)
    return label


CUSTOM = "Custom"


class TargetLookup(QThread):
    """Asks SIMBAD for a target off the GUI thread; `found` carries the Target or None."""

    found = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, name: str, resolve: Callable[[str], simbad.Target | None]) -> None:
        super().__init__()
        self.name = name
        self._resolve = resolve

    def run(self) -> None:
        try:
            self.found.emit(self.name, self._resolve(self.name))
        except simbad.LookupError as error:
            self.failed.emit(self.name, str(error))


class PlanPage(QWidget):
    """Lens and sensor in, exposure limit and frame counts out, updated as the fields change."""

    plan_changed = Signal(object)
    skip_requested = Signal()

    def __init__(self, resolve: Callable[[str], simbad.Target | None] = simbad.resolve) -> None:
        super().__init__()
        self._subject: Subject | None = None
        self._resolve = resolve
        self._lookup: TargetLookup | None = None
        self.target = QLineEdit()
        self.target.setPlaceholderText("M42, NGC 7000, Vega…")
        self.lookup_button = QPushButton("Look up")
        self.target_status = QLabel()
        self.target_status.setWordWrap(True)
        self.manufacturer = QComboBox()
        self.manufacturer.addItems([CUSTOM, *SENSOR_FORMATS])
        self.sensor_format = QComboBox()
        self.focal = self._double(14, 2000, 55, " mm")
        self.f_number = self._double(0.95, 32, 1.8, "", step=0.1)
        self.sensor_width = self._double(4, 60, 23.5, " mm", step=0.1)
        self.sensor_height = self._double(3, 45, 15.6, " mm", step=0.1)
        self.image_width = QSpinBox(minimum=640, maximum=20000, value=6000, suffix=" px")
        self.declination = self._double(-90, 90, 0, "°")
        self.lights = QSpinBox(minimum=1, maximum=20000, value=60)
        self.exposure_s: float | None = None
        for field, tip in (
            (self.focal, "Focal length of the lens you'll shoot with."),
            (self.f_number, "Aperture you'll shoot at; wide open gathers the most light."),
            (self.sensor_width, "Sensor width: 23.5 mm for Sony APS-C, 35.9 mm for full frame."),
            (self.sensor_height, "Sensor height: 15.6 mm for Sony APS-C, 24 mm for full frame."),
            (self.image_width, "Width of a full-size image in pixels, e.g. 6000 on the a6000."),
            (
                self.target,
                "Name of what you're shooting. Press Enter or Look up to fill the declination "
                "from SIMBAD; you can still type over it.",
            ),
            (
                self.declination,
                "How far the target is from the celestial equator. Stars near the pole move "
                "slower, so longer exposures stay sharp.",
            ),
            (self.lookup_button, "Ask SIMBAD for the target's position and fill the declination."),
            (self.manufacturer, "Camera maker; pick one to fill the sensor size from a table."),
            (self.sensor_format, "Sensor format for that maker; Custom keeps your own numbers."),
            (self.lights, "How many light frames you plan to shoot. More frames, less noise."),
        ):
            field.setToolTip(tip)

        inputs = QGroupBox("Lens and sensor")
        form = QFormLayout(inputs)
        form.addRow("Focal length", self.focal)
        form.addRow("Aperture f/", self.f_number)
        form.addRow("Camera maker", self.manufacturer)
        form.addRow("Sensor format", self.sensor_format)
        form.addRow("Sensor width", self.sensor_width)
        form.addRow("Sensor height", self.sensor_height)
        form.addRow("Image width", self.image_width)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target, 1)
        target_row.addWidget(self.lookup_button)
        form.addRow("Target", target_row)
        form.addRow("", self.target_status)
        form.addRow("Target declination", self.declination)
        form.addRow("Light frames", self.lights)

        self.result = QLabel()
        self.result.setWordWrap(True)
        skip = QPushButton("Skip to import")
        skip.setToolTip("Already have the frames? Go straight to importing them.")
        skip.clicked.connect(self.skip_requested)

        layout = QVBoxLayout(self)
        layout.addWidget(_heading(Stage.PLAN))
        layout.addWidget(inputs)
        layout.addWidget(self.result)
        layout.addStretch()
        layout.addWidget(skip)

        for field in (
            self.focal,
            self.f_number,
            self.sensor_width,
            self.sensor_height,
            self.image_width,
            self.declination,
            self.lights,
        ):
            field.valueChanged.connect(self.refresh)
        self.target.returnPressed.connect(self.look_up_target)
        self.lookup_button.clicked.connect(self.look_up_target)
        self.manufacturer.currentTextChanged.connect(self._maker_changed)
        self.sensor_format.currentTextChanged.connect(self._format_changed)
        self._maker_changed(CUSTOM)

    def _maker_changed(self, maker: str) -> None:
        self.sensor_format.blockSignals(True)
        self.sensor_format.clear()
        self.sensor_format.addItems(list(SENSOR_FORMATS.get(maker, {})) or [CUSTOM])
        self.sensor_format.blockSignals(False)
        self.sensor_format.setEnabled(maker != CUSTOM)
        self._format_changed(self.sensor_format.currentText())

    def _format_changed(self, sensor_format: str) -> None:
        size = sensor_size(self.manufacturer.currentText(), sensor_format)
        if size is None:
            return
        self.sensor_width.setValue(size.width_mm)
        self.sensor_height.setValue(size.height_mm)

    def apply_camera(self, model: str) -> bool:
        """Pick the maker and format for an EXIF model string; False when the camera is unknown."""
        guess = guess_format(model)
        if guess is None:
            return False
        maker, sensor_format = guess
        self.manufacturer.setCurrentText(maker)
        self.sensor_format.setCurrentText(sensor_format)
        return True

    def look_up_target(self) -> None:
        name = " ".join(self.target.text().split())
        if not name or (self._lookup and self._lookup.isRunning()):
            return
        self.target_status.setText(f"Looking up {name} in SIMBAD…")
        self.lookup_button.setEnabled(False)
        self._lookup = TargetLookup(name, self._resolve)
        self._lookup.found.connect(self._target_found)
        self._lookup.failed.connect(self._target_failed)
        self._lookup.start()

    def wait_for_lookup(self) -> None:
        if self._lookup:
            self._lookup.wait()

    def _target_found(self, name: str, target: simbad.Target | None) -> None:
        self.lookup_button.setEnabled(True)
        if target is None:
            self.target_status.setText(f"SIMBAD doesn't know “{name}”; enter the declination.")
            return
        self.declination.setValue(target.dec_deg)
        self.target_status.setText(
            f"{target.name}: RA {target.ra_deg:.3f}°, Dec {target.dec_deg:+.2f}° (SIMBAD)."
        )

    def _target_failed(self, name: str, reason: str) -> None:
        self.lookup_button.setEnabled(True)
        self.target_status.setText(f"Couldn't reach SIMBAD for {name}; enter the declination.")

    @staticmethod
    def _double(low: float, high: float, value: float, suffix: str, step: float = 1.0):
        box = QDoubleSpinBox(minimum=low, maximum=high, value=value, suffix=suffix)
        box.setSingleStep(step)
        return box

    def set_subject(self, subject: Subject) -> None:
        self._subject = subject
        low, high = subject.lights
        self.lights.setValue(max(low, min(high, self.lights.value())))
        self.refresh()

    def plan(self) -> CalibrationPlan | None:
        if self._subject is None:
            return None
        return recommend_calibration(self._subject, self.lights.value())

    def refresh(self) -> None:
        subject = self._subject
        if subject is None:
            return
        pitch = pixel_pitch_um(self.sensor_width.value(), self.image_width.value())
        crop = crop_factor(self.sensor_width.value(), self.sensor_height.value())
        npf = npf_exposure(
            self.focal.value(), self.f_number.value(), pitch, self.declination.value()
        )
        plan = self.plan()
        lines = [
            f"<b>{subject.name}</b>: {subject.summary}",
            f"Pixel pitch {pitch:.2f} µm, crop factor {crop:.2f}.",
        ]
        self.exposure_s = None if subject.engine is Engine.PLANETARY else shutter_at_most(npf)
        if subject.key == "lunar":
            across = moon_pixels(self.focal.value(), pitch)
            fits = across * 1.1 < self.image_width.value() / 1.5  # the short side of 3:2
            lines.append(
                "Shoot video or bursts at about 1/250–1/1000 s, ISO 100–400; the Moon is bright. "
                f"It is about <b>{across:.0f} px</b> across here, "
                + (
                    "so the whole disc fits."
                    if fits
                    else "bigger than the frame: shoot close-ups along the terminator, where "
                    "the craters cast shadows."
                )
            )
        elif subject.engine is Engine.PLANETARY:
            lines.append(
                "Shoot video or a fast burst; exposure is set by the planet's brightness, "
                "as short as the histogram allows."
            )
        else:
            lines.append(
                f"Longest untracked exposure: {npf:.1f} s (NPF rule), so set "
                f"<b>{shutter_label(shutter_at_most(npf))}</b>; the 500 rule "
                f"would allow {rule_500(self.focal.value(), crop):.1f} s."
            )
        lines.append(
            f"Frames: {plan.lights} lights, {plan.darks} darks, {plan.flats} flats, "
            f"{plan.bias} bias ({plan.total} in all)."
        )
        if plan.darks:
            factor = master_noise_factor(plan.lights, plan.darks)
            extra = 100 * (factor - 1)
            lines.append(f"Without dithering, the master dark adds {extra:.0f}% noise.")
        self.result.setText("<br>".join(lines))
        self.plan_changed.emit(plan)


class ShootPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.checklist = QLabel()
        self.checklist.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(_heading(Stage.SHOOT))
        layout.addWidget(self.checklist)
        layout.addStretch()

    def set_plan(self, plan: CalibrationPlan | None) -> None:
        if plan is None:
            return
        steps = [f"{plan.lights} light frames of the target, focus and settings locked."]
        if plan.darks:
            steps.append(f"{plan.darks} darks: lens cap on, same exposure, ISO and temperature.")
        if plan.flats:
            steps.append(
                f"{plan.flats} flats: same focus and aperture, even light, histogram in "
                "the middle third, nothing clipped."
            )
        if plan.bias:
            steps.append(f"{plan.bias} bias: lens cap on, fastest shutter speed, same ISO.")
        self.checklist.setText("<ol>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>")


class StagePage(QWidget):
    """A step whose processing lands in a later release; it already owns its progress meter."""

    def __init__(self, stage: Stage) -> None:
        super().__init__()
        self.stage = stage
        self.meter = ProgressMeter()
        layout = QVBoxLayout(self)
        layout.addWidget(_heading(stage))
        pending = QLabel("Not built yet.")
        pending.setEnabled(False)
        layout.addWidget(pending)
        layout.addWidget(self.meter)
        layout.addStretch()
