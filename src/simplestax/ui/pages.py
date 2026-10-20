from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simplestax.planning import (
    CalibrationPlan,
    crop_factor,
    master_noise_factor,
    npf_exposure,
    pixel_pitch_um,
    recommend_calibration,
    rule_500,
    shutter_at_most,
    shutter_label,
)
from simplestax.stages import Stage
from simplestax.subjects import Engine, Subject
from simplestax.ui.widgets import ProgressMeter


def _heading(stage: Stage) -> QLabel:
    label = QLabel(f"<h2>{stage.title}</h2><p>{stage.blurb}</p>")
    label.setWordWrap(True)
    return label


class PlanPage(QWidget):
    """Lens and sensor in, exposure limit and frame counts out, updated as the fields change."""

    plan_changed = Signal(object)
    skip_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._subject: Subject | None = None
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
                self.declination,
                "How far the target is from the celestial equator. Stars near the pole move "
                "slower, so longer exposures stay sharp.",
            ),
            (self.lights, "How many light frames you plan to shoot. More frames, less noise."),
        ):
            field.setToolTip(tip)

        inputs = QGroupBox("Lens and sensor")
        form = QFormLayout(inputs)
        form.addRow("Focal length", self.focal)
        form.addRow("Aperture f/", self.f_number)
        form.addRow("Sensor width", self.sensor_width)
        form.addRow("Sensor height", self.sensor_height)
        form.addRow("Image width", self.image_width)
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
        if subject.engine is Engine.PLANETARY:
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
