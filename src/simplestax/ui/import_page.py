from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from simplestax.frames import FrameInfo, FrameKind, parse_number, read_info
from simplestax.library import ImportItem, ImportResult, Library, Profile
from simplestax.planning import SHUTTER_STEPS_S, shutter_label
from simplestax.settings import Preferences
from simplestax.stages import Stage
from simplestax.subjects import Subject
from simplestax.ui.frame_picker import FramePicker
from simplestax.ui.widgets import ProgressMeter, mark_next_step

KIND_TIPS = {
    FrameKind.LIGHT: "Pick the photos of the target. They are renamed with the subject and the "
    "night, and filed under this lens and exposure.",
    FrameKind.DARK: "Pick the lens-cap-on frames shot at the same exposure and ISO as the "
    "lights, on the same night (the sensor's temperature has to match).",
    FrameKind.FLAT: "Pick the evenly lit frames shot with the same lens, aperture and focus as "
    "the lights. They map vignetting and dust.",
    FrameKind.BIAS: "Pick the lens-cap-on frames at the fastest shutter speed and the lights' "
    "ISO. They can be reused for months.",
}


def parse_exposure(text: str) -> float | None:
    return parse_number(text.replace("″", "").replace('"', "").removesuffix("s").strip())


class ImportWorker(QThread):
    progress = Signal(int, int, str)
    result_ready = Signal(object)

    def __init__(
        self, library: Library, items: list[ImportItem], profile: Profile, move: bool
    ) -> None:
        super().__init__()
        self.library, self.items, self.profile, self.move = library, items, profile, move

    def run(self) -> None:
        result = self.library.import_frames(self.items, self.profile, self.move, self.progress.emit)
        self.result_ready.emit(result)


class ImportPage(QWidget):
    """A shot profile, a subject, and one button per frame kind that files the picked frames."""

    camera_seen = Signal(str)

    def __init__(self, preferences: Preferences) -> None:
        super().__init__()
        self.preferences = preferences
        self.library = Library(preferences.library_root)
        self._subject: Subject | None = None
        self._worker: ImportWorker | None = None

        self.root = QLineEdit(str(self.library.root), readOnly=True)
        self.root.setToolTip("The folder SimpleStax files frames into, one sub-folder per night.")
        choose = QPushButton("Choose…")
        choose.setToolTip("Pick a different library folder.")
        choose.clicked.connect(self._choose_root)
        root_row = QHBoxLayout()
        root_row.addWidget(self.root, 1)
        root_row.addWidget(choose)

        self.focal = QDoubleSpinBox(minimum=1, maximum=5000, value=55, suffix=" mm")
        self.focal.setToolTip("Focal length the lights and flats were shot at.")
        self.f_number = QDoubleSpinBox(minimum=0.7, maximum=64, value=1.8, singleStep=0.1)
        self.f_number.setToolTip("Aperture the lights and flats were shot at.")
        self.exposure = QComboBox(editable=True)
        self.exposure.addItems([shutter_label(step) for step in SHUTTER_STEPS_S])
        self.exposure.setCurrentText(shutter_label(3.2))
        self.exposure.setToolTip(
            "Shutter speed of the lights (and darks). Pick a step or type one, e.g. 1/500 or 3.2."
        )
        self.iso = QSpinBox(minimum=50, maximum=409600, value=1600, singleStep=100)
        self.iso.setToolTip("ISO of the lights; darks and bias must match it.")
        self.subject_name = QComboBox(editable=True)
        self.subject_name.lineEdit().setPlaceholderText("m42, Andromeda…")
        self.subject_name.setToolTip(
            "What the lights show. Added to their file names, and used later to pick what to stack."
        )
        self.move = QCheckBox("Move files instead of copying")
        self.move.setToolTip("Off: the originals stay where they are. On: they are moved.")

        profile_box = QGroupBox("Shot profile")
        form = QFormLayout(profile_box)
        form.addRow("Library", root_row)
        form.addRow("Focal length", self.focal)
        form.addRow("Aperture f/", self.f_number)
        form.addRow("Exposure", self.exposure)
        form.addRow("ISO", self.iso)
        form.addRow("Subject", self.subject_name)
        form.addRow("", self.move)

        self.buttons: dict[FrameKind, QPushButton] = {}
        button_row = QHBoxLayout()
        for kind in FrameKind:
            button = QPushButton(f"Select {kind.value}…")
            button.setToolTip(KIND_TIPS[kind])
            button.clicked.connect(lambda _=False, k=kind: self._pick(k))
            self.buttons[kind] = button
            button_row.addWidget(button)

        self.status = QLabel(wordWrap=True)
        self.report = QLabel(wordWrap=True)
        self.meter = ProgressMeter()

        heading = QLabel(f"<h2>{Stage.IMPORT.title}</h2><p>{Stage.IMPORT.blurb}</p>")
        heading.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(profile_box)
        layout.addLayout(button_row)
        layout.addWidget(self.meter)
        layout.addWidget(self.status)
        layout.addWidget(self.report)
        layout.addStretch()

        for box in (self.focal, self.f_number, self.iso):
            box.valueChanged.connect(self.refresh)
        self.exposure.currentTextChanged.connect(self.refresh)
        self.subject_name.currentTextChanged.connect(self.refresh)
        self._load_subjects()

    def profile(self) -> Profile | None:
        exposure = parse_exposure(self.exposure.currentText())
        if exposure is None:
            return None
        return Profile(self.focal.value(), self.f_number.value(), exposure, self.iso.value())

    def set_subject(self, subject: Subject) -> None:
        self._subject = subject
        self.refresh()

    def apply_plan(self, focal_mm: float, f_number: float, exposure_s: float) -> None:
        self.focal.setValue(focal_mm)
        self.f_number.setValue(f_number)
        self.exposure.setCurrentText(shutter_label(exposure_s))

    def needed_kinds(self) -> list[FrameKind]:
        subject = self._subject
        flags = {
            FrameKind.DARK: subject is None or subject.darks,
            FrameKind.FLAT: subject is None or subject.flats,
            FrameKind.BIAS: subject is None or subject.bias,
        }
        return [FrameKind.LIGHT] + [kind for kind, needed in flags.items() if needed]

    def next_step(self) -> QWidget | None:
        """The control that needs the user before stacking can go ahead, or None when ready."""
        profile = self.profile()
        if profile is None:
            return self.exposure
        name = self.subject_name.currentText().strip()
        if not name:
            return self.subject_name
        night = self.library.latest_night(name, profile)
        if night is None:
            return self.buttons[FrameKind.LIGHT]
        counts = self.library.counts(night, name, profile)
        for kind in self.needed_kinds():
            if not counts[kind]:
                return self.buttons[kind]
        return None

    def refresh(self) -> None:
        target = self.next_step()
        for widget in (self.exposure, self.subject_name, *self.buttons.values()):
            mark_next_step(widget, widget is target)
        self.status.setText(self._status_text(target))

    def _status_text(self, target: QWidget | None) -> str:
        profile = self.profile()
        name = self.subject_name.currentText().strip()
        if profile is None:
            return "Exposure isn't a shutter speed; type one like 1/500 or 3.2."
        if not name:
            return "Type the subject first, so the lights can be named after it."
        night = self.library.latest_night(name, profile)
        if night is None:
            return f"No {name} lights with this profile yet. Select them first."
        counts = self.library.counts(night, name, profile)
        found = ", ".join(f"{counts[kind]} {kind.value}" for kind in self.needed_kinds())
        if target is None:
            return f"Night of {night}, {name}: {found}. Ready to calibrate."
        missing = next(kind for kind, button in self.buttons.items() if button is target)
        return f"Night of {night}, {name}: {found}. Next: select the {missing.value}."

    def _load_subjects(self) -> None:
        text = self.subject_name.currentText()
        self.subject_name.blockSignals(True)
        self.subject_name.clear()
        self.subject_name.addItems(self.library.subjects())
        self.subject_name.setCurrentText(text)
        self.subject_name.blockSignals(False)
        self.refresh()

    def _choose_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Library folder", str(self.library.root))
        if folder:
            self.set_root(folder)

    def set_root(self, folder) -> None:
        self.library = Library(folder)
        self.preferences.library_root = self.library.root
        self.root.setText(str(self.library.root))
        self._load_subjects()

    def _pick(self, kind: FrameKind) -> None:
        profile = self.profile()
        if profile is None:
            return
        picker = FramePicker(kind, profile, self.preferences.import_folder, self)
        if picker.exec() == FramePicker.DialogCode.Accepted:
            self.preferences.import_folder = Path(picker.folder.text())
            self.import_paths(kind, picker.selected_paths(), picker.infos)

    def import_paths(
        self, kind: FrameKind, paths, infos: dict[Path, FrameInfo] | None = None
    ) -> None:
        profile = self.profile()
        if profile is None or (self._worker and self._worker.isRunning()):
            return
        try:
            known = infos or {}
            items = self.library.plan_import(
                paths,
                kind,
                profile,
                self.subject_name.currentText(),
                reader=lambda path: known.get(path) or read_info(path),
            )
        except ValueError as error:
            self.report.setText(str(error))
            return
        flagged = [item for item in items if item.problems]
        if flagged:
            choice = self.confirm(items, flagged)
            if choice == "cancel":
                return
            if choice == "matching":
                items = [item for item in items if not item.problems]
        if not items:
            return
        for button in self.buttons.values():
            button.setEnabled(False)
        verb = "Moving" if self.move.isChecked() else "Copying"
        self._worker = ImportWorker(self.library, items, profile, self.move.isChecked())
        self._worker.progress.connect(
            lambda done, total, name: self.meter.set_fraction(
                done / total if total else 1.0, f"{verb} {done}/{total} {name}".rstrip()
            )
        )
        self._worker.result_ready.connect(self._finished)
        self._worker.start()

    def confirm(self, items: list[ImportItem], flagged: list[ImportItem]) -> str:
        """'all', 'matching' or 'cancel' for a batch where some frames don't fit the profile."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Some frames don't match")
        box.setText(f"{len(flagged)} of {len(items)} frames don't match the shot profile.")
        box.setDetailedText(
            "\n".join(f"{item.source.name}: {'; '.join(item.problems)}" for item in flagged)
        )
        everything = box.addButton("Import all", QMessageBox.ButtonRole.AcceptRole)
        matching = box.addButton("Skip those", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        everything.setToolTip("File every frame anyway.")
        matching.setToolTip("Only file the frames that match the profile.")
        box.exec()
        clicked = box.clickedButton()
        return "all" if clicked is everything else "matching" if clicked is matching else "cancel"

    def wait_for_import(self) -> None:
        if self._worker:
            self._worker.wait()
        QApplication.processEvents()

    def _finished(self, result: ImportResult) -> None:
        self.meter.finish()
        cameras = [item.info.camera for item in result.imported if item.info.camera]
        if cameras:
            self.camera_seen.emit(cameras[0])
        for button in self.buttons.values():
            button.setEnabled(True)
        lines = [f"Filed {len(result.imported)} frames."] if result.imported else []
        lines += [f"{item.source.name}: {error}" for item, error in result.failed]
        self.report.setText("<br>".join(lines))
        self._load_subjects()
