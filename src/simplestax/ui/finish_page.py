"""Finish: the automatic finish for the subject, every value on a slider, then export."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from simplestax.finish import FinishSettings, Measurements, auto_settings, finish, measure, sample
from simplestax.output import read_fits
from simplestax.stacking import engine_command, parse_engine_line
from simplestax.stages import Stage
from simplestax.ui.widgets import ProgressMeter, mark_next_step

PREVIEW_PIXELS = 1_200_000
PREVIEW_WIDTH = 720


@dataclass(frozen=True)
class Control:
    name: str
    label: str
    low: float
    high: float
    step: float
    tip: str


# In the order the chain applies them.
CONTROLS = (
    Control(
        "black_sigmas",
        "Black point",
        0.5,
        6.0,
        0.1,
        "How far below the sky the black point sits, in units of sky noise. Lower clips more of "
        "the background noise to black; higher keeps faint detail at the cost of a greyer sky.",
    ),
    Control(
        "white",
        "White point",
        99.0,
        100.0,
        0.01,
        "Brightness percentile that becomes pure white. Lower brightens the subject and lets more "
        "star cores saturate; 100 keeps every star core.",
    ),
    Control(
        "colour_balance",
        "Star colour balance",
        0.0,
        1.0,
        0.05,
        "Balances the colour so stars average out white. 0 keeps the camera's white balance.",
    ),
    Control(
        "background",
        "Sky brightness",
        0.05,
        0.5,
        0.01,
        "Where the stretch puts the sky background on the display scale. Higher shows more faint "
        "detail and more noise.",
    ),
    Control(
        "chroma_nr",
        "Colour noise reduction",
        0.0,
        8.0,
        0.1,
        "Smooths colour speckle, keeping brightness detail sharp. The value is a blur radius in "
        "pixels of the full-size image.",
    ),
    Control(
        "saturation",
        "Saturation",
        0.0,
        2.5,
        0.05,
        "Colour strength after the stretch. 1 leaves it as it is.",
    ),
    Control(
        "scnr",
        "Green removal (SCNR)",
        0.0,
        1.0,
        0.05,
        "Pulls green down to the average of red and blue wherever it sticks out. Almost nothing "
        "in deep sky is green, so a green tint is a cast. Automatic strength comes from how much "
        "green the image shows.",
    ),
)


def to_qimage(display: np.ndarray) -> QImage:
    pixels = (np.clip(display, 0, 1) * 255 + 0.5).astype(np.uint8).transpose(1, 2, 0)
    pixels = np.ascontiguousarray(pixels)
    h, w, _ = pixels.shape
    return QImage(pixels.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class FinishPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.stack: dict | None = None
        self.subject_key = ""
        self.measurements: Measurements | None = None
        self.auto: FinishSettings | None = None
        self.result: dict | None = None
        self._small: np.ndarray | None = None
        self._scale = 1
        self._process: QProcess | None = None
        self._buffer = b""
        self._error = ""

        heading = QLabel(f"<h2>{Stage.FINISH.title}</h2><p>{Stage.FINISH.blurb}</p>")
        heading.setWordWrap(True)
        self.summary = QLabel("Stack an image first; it opens here with an automatic finish.")
        self.summary.setWordWrap(True)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumWidth(PREVIEW_WIDTH)

        form = QFormLayout()
        self.sliders: dict[str, QSlider] = {}
        self.values: dict[str, QLabel] = {}
        for control in CONTROLS:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, round((control.high - control.low) / control.step))
            slider.setToolTip(control.tip)
            slider.valueChanged.connect(self._changed)
            value = QLabel()
            value.setMinimumWidth(48)
            row = QHBoxLayout()
            row.addWidget(slider, 1)
            row.addWidget(value)
            label = QLabel(control.label)
            label.setToolTip(control.tip)
            form.addRow(label, row)
            self.sliders[control.name], self.values[control.name] = slider, value

        self.reset_button = QPushButton("Reset to automatic")
        self.reset_button.setToolTip("Put every slider back to the value chosen for this image.")
        self.reset_button.clicked.connect(self.reset)
        self.export_button = QPushButton("Export")
        self.export_button.setToolTip(
            "Run the finish on the full-size stack and save finished.tif (16-bit) and "
            "finished.png next to it."
        )
        self.export_button.clicked.connect(self.export)
        self.open_button = QPushButton("Open folder")
        self.open_button.setToolTip("Show the finished image in the file manager.")
        self.open_button.clicked.connect(self._open_folder)
        self.meter = ProgressMeter()
        self.status = QLabel()
        self.status.setWordWrap(True)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self.render)

        controls = QVBoxLayout()
        controls.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.open_button)
        controls.addLayout(buttons)
        controls.addWidget(self.meter)
        controls.addWidget(self.status)
        controls.addStretch()
        body = QHBoxLayout()
        body.addWidget(self.preview, 3)
        body.addLayout(controls, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.summary)
        layout.addLayout(body, 1)
        self._update_buttons()

    def set_stack(self, result: dict, subject_key: str) -> None:
        """Open a stack's linear FITS and choose the automatic settings for it."""
        self.stack, self.subject_key, self.result = result, subject_key, None
        self.status.setText("")
        self.meter.set_fraction(0.1, "reading the stack")
        QApplication.processEvents()
        try:
            rgb = read_fits(Path(result["fits"]))
            self._small, self._scale = sample(rgb, PREVIEW_PIXELS)
            self.meter.set_fraction(0.5, "measuring sky, stars and colour")
            QApplication.processEvents()
            self.measurements = measure(rgb)
            self.auto = auto_settings(rgb, self.measurements, subject_key)
        except (OSError, ValueError) as error:
            self.stack = self._small = self.measurements = self.auto = None
            self.status.setText(f"Couldn't open the stack: {error}")
            self.meter.finish()
            self._update_buttons()
            return
        self.meter.finish()
        self.summary.setText(f"Finishing <b>{Path(result['fits']).parent}</b>.")
        self.reset()

    def settings(self) -> FinishSettings:
        values = {}
        for control in CONTROLS:
            values[control.name] = control.low + self.sliders[control.name].value() * control.step
        return FinishSettings(**values)

    def apply(self, settings: FinishSettings) -> None:
        for control in CONTROLS:
            slider = self.sliders[control.name]
            slider.blockSignals(True)
            value = min(max(getattr(settings, control.name), control.low), control.high)
            slider.setValue(round((value - control.low) / control.step))
            slider.blockSignals(False)
        self._show_values()

    def reset(self) -> None:
        if self.auto is not None:
            self.apply(self.auto)
            self.render()
        self._update_buttons()

    def _show_values(self) -> None:
        settings = self.settings()
        for control in CONTROLS:
            digits = 2 if control.step < 0.1 else 1
            self.values[control.name].setText(f"{getattr(settings, control.name):.{digits}f}")

    def _changed(self, *_args) -> None:
        self._show_values()
        self._render_timer.start()

    def render(self) -> None:
        if self._small is None or self.measurements is None:
            return
        display = finish(self._small, self.settings(), self.measurements, scale=self._scale)
        pixmap = QPixmap.fromImage(to_qimage(display))
        width = min(PREVIEW_WIDTH, max(self.preview.width(), 200))
        self.preview.setPixmap(
            pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        )

    def _update_buttons(self) -> None:
        loaded = self.stack is not None and self._process is None
        self.export_button.setEnabled(loaded)
        self.reset_button.setEnabled(loaded)
        for slider in self.sliders.values():
            slider.setEnabled(loaded)
        self.open_button.setEnabled(self.result is not None)
        mark_next_step(self.export_button, loaded and self.result is None)

    def export(self) -> None:
        if self.stack is None or self.measurements is None or self._process is not None:
            return
        folder = Path(self.stack["fits"]).parent
        job_path = folder / "finish_job.json"
        job = {
            "fits": self.stack["fits"],
            "output": str(folder),
            "settings": self.settings().to_dict(),
            "measurements": self.measurements.to_dict(),
        }
        job_path.write_text(json.dumps(job, indent=2))
        self.result, self._error, self._buffer = None, "", b""
        self.meter.set_fraction(0, "starting")
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._finished)
        command = engine_command(job_path, "finish")
        self._process = process
        self._update_buttons()
        process.start(command[0], command[1:])

    def _read(self) -> None:
        if self._process is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            self.handle_line(line.decode(errors="replace"))

    def handle_line(self, line: str) -> None:
        message = parse_engine_line(line)
        if message is None:
            return
        if "progress" in message:
            self.meter.set_fraction(float(message["progress"]), message.get("message", ""))
        elif "error" in message:
            self._error = str(message["error"])
        elif "result" in message:
            self.result = message["result"]
            self.status.setText(f"Saved {self.result['tiff']} and {Path(self.result['png']).name}.")

    def _finished(self, *_args) -> None:
        self._read()
        if self._buffer:
            self.handle_line(self._buffer.decode(errors="replace"))
            self._buffer = b""
        self.meter.finish()
        if self.result is None:
            self.status.setText(self._error or "The finish engine stopped without a result.")
        self._process = None
        self._update_buttons()

    def _open_folder(self) -> None:
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result["tiff"]).parent)))
