"""Calibrate (which nights and calibration frames) and Stack (run the engine, show the result)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from simplestax.frames import FrameKind
from simplestax.library import Library, Session
from simplestax.planning import shutter_label
from simplestax.settings import Preferences
from simplestax.stacking import (
    CALIBRATION_KINDS,
    DEFAULT_KAPPA,
    calibration_advice,
    engine_command,
    output_folder,
    parse_engine_line,
    plan_job,
)
from simplestax.stages import Stage
from simplestax.subjects import Engine, Subject
from simplestax.ui.widgets import ProgressMeter, mark_next_step

KIND_USE_TIPS = {
    FrameKind.DARK: "Subtract the master dark: hot pixels, amp glow and the zero level.",
    FrameKind.FLAT: "Divide by the master flat: vignetting and dust shadows.",
    FrameKind.BIAS: "Use the master bias as the zero level for flats (and lights without darks).",
}


def _heading(stage: Stage) -> QLabel:
    label = QLabel(f"<h2>{stage.title}</h2><p>{stage.blurb}</p>")
    label.setWordWrap(True)
    return label


def session_label(session: Session, counts: dict[FrameKind, int]) -> str:
    p = session.profile
    kinds = ", ".join(f"{counts[k]} {k.value}" for k in CALIBRATION_KINDS)
    return (
        f"{session.night}   {p.focal_mm:g} mm f/{p.f_number:g}  {shutter_label(p.exposure_s)}  "
        f"ISO {p.iso}   {session.lights} lights; {kinds}"
    )


@dataclass
class StackSelection:
    library: Library
    subject: str
    sessions: list[Session]
    use: set[FrameKind]


class CalibratePage(QWidget):
    selection_ready = Signal(object)

    def __init__(self, preferences: Preferences) -> None:
        super().__init__()
        self.preferences = preferences
        self.library = Library(preferences.library_root)
        self._counts: dict[str, dict[FrameKind, int]] = {}

        self.subject_name = QComboBox()
        self.subject_name.setToolTip("The subject whose lights you want to stack.")
        self.sessions = QListWidget()
        self.sessions.setToolTip(
            "Nights with lights of this subject. Tick one or more; each night is calibrated "
            "with its own frames before they are stacked together."
        )
        self.use: dict[FrameKind, QCheckBox] = {}
        use_row = QHBoxLayout()
        for kind in CALIBRATION_KINDS:
            box = QCheckBox(f"Use {kind.value}")
            box.setToolTip(KIND_USE_TIPS[kind])
            box.setChecked(True)
            box.toggled.connect(self._refresh_advice)
            self.use[kind] = box
            use_row.addWidget(box)
        use_row.addStretch()
        self.advice = QLabel()
        self.advice.setWordWrap(True)
        self.next_button = QPushButton("Continue to Stack")
        self.next_button.setToolTip("Take the ticked nights and calibration choices to Stack.")
        self.next_button.clicked.connect(self._continue)

        form = QFormLayout()
        form.addRow("Subject", self.subject_name)
        layout = QVBoxLayout(self)
        layout.addWidget(_heading(Stage.CALIBRATE))
        layout.addLayout(form)
        layout.addWidget(self.sessions, 1)
        layout.addLayout(use_row)
        layout.addWidget(self.advice)
        layout.addWidget(self.next_button)

        self.subject_name.currentTextChanged.connect(self._load_sessions)
        self.sessions.itemChanged.connect(self._refresh_advice)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.refresh()
        super().showEvent(event)

    def refresh(self) -> None:
        self.library = Library(self.preferences.library_root)
        current = self.subject_name.currentText()
        self.subject_name.blockSignals(True)
        self.subject_name.clear()
        self.subject_name.addItems(self.library.subjects())
        index = self.subject_name.findText(current)
        self.subject_name.setCurrentIndex(max(index, 0))
        self.subject_name.blockSignals(False)
        self._load_sessions()

    def _load_sessions(self) -> None:
        subject = self.subject_name.currentText()
        self.sessions.blockSignals(True)
        self.sessions.clear()
        self._counts = {}
        for number, session in enumerate(self.library.sessions(subject) if subject else []):
            counts = self.library.counts(session.night, subject, session.profile)
            item = QListWidgetItem(session_label(session, counts))
            item.setData(Qt.ItemDataRole.UserRole, session)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if number == 0 else Qt.CheckState.Unchecked)
            self._counts[item.text()] = counts
            self.sessions.addItem(item)
        self.sessions.blockSignals(False)
        self._refresh_advice()

    def checked_sessions(self) -> list[Session]:
        items = [self.sessions.item(row) for row in range(self.sessions.count())]
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in items
            if item.checkState() == Qt.CheckState.Checked
        ]

    def available(self) -> dict[FrameKind, int]:
        totals = dict.fromkeys(FrameKind, 0)
        for row in range(self.sessions.count()):
            item = self.sessions.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                for kind, n in self._counts[item.text()].items():
                    totals[kind] += n
        return totals

    def chosen_use(self) -> set[FrameKind]:
        available = self.available()
        return {k for k, box in self.use.items() if box.isChecked() and available[k]}

    def _refresh_advice(self, *_args) -> None:
        available = self.available()
        for kind, box in self.use.items():
            box.setEnabled(available[kind] > 0)
        sessions = self.checked_sessions()
        if not sessions:
            self.advice.setText("Import lights with a subject name first, then tick a night.")
        else:
            lines = calibration_advice(available, self.chosen_use())
            self.advice.setText("<br>".join(lines))
        self.next_button.setEnabled(bool(sessions))
        mark_next_step(self.next_button, bool(sessions))

    def _continue(self) -> None:
        sessions = self.checked_sessions()
        if sessions:
            subject = self.subject_name.currentText()
            self.selection_ready.emit(
                StackSelection(self.library, subject, sessions, self.chosen_use())
            )


class StackPage(QWidget):
    """Runs the deep-sky engine in its own process and shows its progress and result."""

    stacked = Signal(dict)
    finish_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.selection: StackSelection | None = None
        self.result: dict | None = None
        self._process: QProcess | None = None
        self._buffer = b""
        self._error = ""

        self.summary = QLabel("Pick the nights to stack on the Calibrate step.")
        self.summary.setWordWrap(True)
        self.kappa = QDoubleSpinBox(minimum=1.5, maximum=6.0, value=DEFAULT_KAPPA, singleStep=0.5)
        self.kappa.setToolTip(
            "Outlier rejection: a pixel is dropped from one frame when it is this many standard "
            "deviations away from the other frames (satellites, planes, hot pixels). Lower "
            "rejects more."
        )
        self.flatten = QCheckBox("Remove light-pollution gradient")
        self.flatten.setChecked(True)
        self.flatten.setToolTip(
            "Fit a smooth surface to the sky background of each colour, with stars and the "
            "subject left out, and subtract it. Takes out skyglow brightening toward the horizon "
            "and leaves the background neutral grey."
        )
        self.run_button = QPushButton("Stack")
        self.run_button.setToolTip("Calibrate, register and stack the lights.")
        self.run_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop the stack that is running.")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        self.open_button = QPushButton("Open folder")
        self.open_button.setToolTip("Show the stacked FITS and TIFF in the file manager.")
        self.open_button.clicked.connect(self._open_folder)
        self.open_button.setEnabled(False)
        self.finish_button = QPushButton("Continue to Finish")
        self.finish_button.setToolTip(
            "Open the stack in Finish for the automatic finish and export."
        )
        self.finish_button.clicked.connect(self.finish_requested)
        self.meter = ProgressMeter()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)

        form = QFormLayout()
        form.addRow("Rejection (sigma)", self.kappa)
        form.addRow("", self.flatten)
        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.finish_button)
        layout = QVBoxLayout(self)
        layout.addWidget(_heading(Stage.STACK))
        layout.addWidget(self.summary)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.meter)
        layout.addWidget(self.status)
        layout.addWidget(self.preview, 1)
        self._planetary = False
        self._update_buttons()

    def set_subject(self, subject: Subject) -> None:
        self._planetary = subject.engine is Engine.PLANETARY
        if self._planetary:
            self.status.setText(
                "Planetary stacking runs on PlanetarySystemStacker; that step isn't wired up yet."
            )
        self._update_buttons()

    def set_selection(self, selection: StackSelection) -> None:
        self.selection = selection
        lights = sum(s.lights for s in selection.sessions)
        nights = ", ".join(s.night for s in selection.sessions)
        used = ", ".join(k.value for k in CALIBRATION_KINDS if k in selection.use) or "none"
        self.summary.setText(
            f"<b>{selection.subject}</b>: {lights} lights from {nights}. Calibration: {used}."
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        running = self._process is not None
        ready = self.selection is not None and not self._planetary and not running
        self.run_button.setEnabled(ready)
        self.cancel_button.setEnabled(running)
        mark_next_step(self.run_button, ready and self.result is None)
        self.finish_button.setEnabled(self.result is not None and not running)
        mark_next_step(self.finish_button, self.result is not None and not running)

    def start(self) -> None:
        selection = self.selection
        if selection is None or self._process is not None:
            return
        out = output_folder(selection.library.root, selection.subject)
        job = plan_job(
            selection.library,
            selection.subject,
            selection.sessions,
            selection.use,
            out,
            self.kappa.value(),
            self.flatten.isChecked(),
        )
        out.mkdir(parents=True, exist_ok=True)
        job_path = out / "job.json"
        job_path.write_text(job.to_json())
        self.result, self._error, self._buffer = None, "", b""
        self.preview.clear()
        self.status.setText("")
        self.meter.set_fraction(0, "starting")
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._finished)
        command = engine_command(job_path)
        self._process = process
        self._update_buttons()
        process.start(command[0], command[1:])

    def cancel(self) -> None:
        if self._process is not None:
            self._error = "Cancelled."
            self._process.kill()

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
            self.show_result(message["result"])

    def show_result(self, result: dict) -> None:
        self.result = result
        lines = [
            f"Stacked {result['stacked']} lights; {result['rejected_percent']}% of samples "
            "rejected as outliers.",
            f"Saved {result['fits']} and {Path(result['tiff']).name}.",
        ]
        lines += [f"Skipped {name}: {why}" for name, why in result.get("skipped", [])]
        self.status.setText("<br>".join(lines))
        pixmap = QPixmap(result["preview"])
        if not pixmap.isNull():
            self.preview.setPixmap(
                pixmap.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation)
            )
        self.open_button.setEnabled(True)
        self._update_buttons()
        self.stacked.emit(result)

    def _finished(self, *_args) -> None:
        self._read()
        if self._buffer:
            self.handle_line(self._buffer.decode(errors="replace"))
            self._buffer = b""
        self.meter.finish()
        if self.result is None:
            self.status.setText(self._error or "The stacking engine stopped without a result.")
        self._process = None
        self._update_buttons()

    def _open_folder(self) -> None:
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result["fits"]).parent)))
