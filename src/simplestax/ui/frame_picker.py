"""A frame picker that reads each RAW's EXIF and dims the ones that don't fit the shot profile."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from simplestax.frames import RAW_SUFFIXES, FrameInfo, FrameKind, read_info
from simplestax.library import BIAS_MAX_S, CHECKS, Check, Profile, fits
from simplestax.planning import shutter_label
from simplestax.ui.widgets import ProgressMeter

COLUMNS = ("File", "Exposure", "ISO", "Focal", "Aperture", "Taken")
CHECK_COLUMN = {Check.EXPOSURE: 1, Check.ISO: 2, Check.FOCAL: 3, Check.APERTURE: 4}
UNKNOWN = "—"
UNKNOWN_TIP = (
    "The file doesn't record this (common with manual or adapted lenses), so it isn't "
    "filtered on. Select it yourself if you know it belongs."
)


def raw_files(folder: Path) -> list[Path]:
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    return sorted(
        (p for p in entries if p.is_file() and p.suffix.lower() in RAW_SUFFIXES),
        key=lambda p: p.name.lower(),
    )


def check_label(kind: FrameKind, check: Check, profile: Profile) -> str:
    if check is Check.EXPOSURE:
        if kind is FrameKind.BIAS:
            return f"Exposure ≤ {shutter_label(BIAS_MAX_S)}"
        return f"Exposure {shutter_label(profile.exposure_s)}"
    if check is Check.ISO:
        return f"ISO {profile.iso}"
    if check is Check.FOCAL:
        return f"Focal {profile.focal_mm:g} mm"
    return f"Aperture f/{profile.f_number:g}"


def _cells(info: FrameInfo) -> list[str]:
    return [
        shutter_label(info.exposure_s) if info.exposure_s else UNKNOWN,
        str(info.iso) if info.iso else UNKNOWN,
        f"{info.focal_mm:g} mm" if info.focal_mm else UNKNOWN,
        f"f/{info.f_number:g}" if info.f_number else UNKNOWN,
        info.taken.strftime("%Y-%m-%d %H:%M") if info.taken else UNKNOWN,
    ]


class ExifReader(QThread):
    read = Signal(int, object)

    def __init__(self, paths: list[Path], reader: Callable[[Path], FrameInfo]) -> None:
        super().__init__()
        self.paths, self.reader = paths, reader

    def run(self) -> None:
        for row, path in enumerate(self.paths):
            if self.isInterruptionRequested():
                return
            self.read.emit(row, self.reader(path))


class FramePicker(QDialog):
    """Multi-select list of a folder's RAWs; frames whose EXIF contradicts the profile are dimmed.

    Frames missing a value are never dimmed for it, and every frame stays selectable.
    """

    def __init__(
        self,
        kind: FrameKind,
        profile: Profile,
        folder: Path,
        parent=None,
        reader: Callable[[Path], FrameInfo] = read_info,
    ) -> None:
        super().__init__(parent)
        self.kind, self.profile, self.reader = kind, profile, reader
        self.infos: dict[Path, FrameInfo] = {}
        self._paths: list[Path] = []
        self._worker: ExifReader | None = None
        self.setWindowTitle(f"Select {kind.value}")
        self.resize(820, 560)

        self.folder = QLineEdit(readOnly=True)
        self.folder.setToolTip("The folder whose raw frames are listed below.")
        browse = QPushButton("Folder…")
        browse.setToolTip("Open a different folder, e.g. the camera's memory card.")
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)

        self.checks: dict[Check, QCheckBox] = {}
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter by EXIF:"))
        for check in CHECKS[kind]:
            box = QCheckBox(check_label(kind, check, profile), checked=True)
            box.setToolTip(
                f"Dim frames whose {check.value} doesn't match. Frames that don't record it "
                "stay bright and selectable."
            )
            box.toggled.connect(self._restyle_all)
            self.checks[check] = box
            filter_row.addWidget(box)
        filter_row.addStretch()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setToolTip(
            "Click, Shift-click or Ctrl-click to pick frames. Dimmed frames don't match the "
            "profile but can still be picked."
        )
        self.table.itemSelectionChanged.connect(self._update_summary)

        select_matching = QPushButton("Select matching")
        select_matching.setToolTip("Pick every frame that isn't dimmed.")
        select_matching.clicked.connect(self.select_matching)
        self.summary = QLabel()
        self.meter = ProgressMeter()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(f"Import {kind.value}")
        self.ok.setToolTip("File the picked frames into the library.")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        bottom = QHBoxLayout()
        bottom.addWidget(select_matching)
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(folder_row)
        layout.addLayout(filter_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.meter)
        layout.addLayout(bottom)
        self.load(Path(folder))

    def load(self, folder: Path) -> None:
        self._stop()
        self.folder.setText(str(folder))
        self.infos.clear()
        self._paths = raw_files(folder)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._paths))
        for row, path in enumerate(self._paths):
            name = QTableWidgetItem(path.name)
            name.setData(Qt.ItemDataRole.UserRole, str(path))
            self.table.setItem(row, 0, name)
            for column in range(1, len(COLUMNS)):
                self.table.setItem(row, column, QTableWidgetItem("…"))
        self._update_summary()
        if not self._paths:
            self.meter.finish()
            return
        self.meter.set_fraction(0.0, f"Reading EXIF 0/{len(self._paths)}")
        self._worker = ExifReader(self._paths, self.reader)
        self._worker.read.connect(self._info_read)
        self._worker.finished.connect(self._read_done)
        self._worker.start()

    def wait_for_exif(self) -> None:
        if self._worker:
            self._worker.wait()
        QApplication.processEvents()

    def _info_read(self, row: int, info: FrameInfo) -> None:
        if self.sender() is not self._worker or row >= len(self._paths):
            return
        self.infos[self._paths[row]] = info
        for column, text in enumerate(_cells(info), start=1):
            self.table.item(row, column).setText(text)
        self._restyle(row)
        total = len(self._paths)
        self.meter.set_fraction(len(self.infos) / total, f"Reading EXIF {len(self.infos)}/{total}")
        self._update_summary()

    def _read_done(self) -> None:
        if self.sender() is self._worker and len(self.infos) == len(self._paths):
            self.meter.finish()
            self.table.setSortingEnabled(True)

    def _path(self, row: int) -> Path:
        return Path(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def problems(self, path: Path) -> list[Check] | None:
        """Enabled checks the frame fails; None while its EXIF hasn't been read yet."""
        info = self.infos.get(path)
        if info is None:
            return None
        return [
            check
            for check, box in self.checks.items()
            if box.isChecked() and fits(self.kind, check, info, self.profile) is False
        ]

    def matches(self, path: Path) -> bool:
        return self.problems(path) == []

    def _restyle(self, row: int) -> None:
        failed = self.problems(self._path(row)) or []
        palette = self.table.palette()
        group = QPalette.ColorGroup.Disabled if failed else QPalette.ColorGroup.Active
        brush = palette.brush(group, QPalette.ColorRole.Text)
        info = self.infos.get(self._path(row))
        for column in range(len(COLUMNS)):
            item = self.table.item(row, column)
            item.setForeground(brush)
            tip = ""
            if failed:
                tip = "Doesn't match the profile: " + ", ".join(c.value for c in failed) + "."
            if info is not None and column in CHECK_COLUMN.values() and item.text() == UNKNOWN:
                tip = UNKNOWN_TIP
            item.setToolTip(tip)

    def _restyle_all(self) -> None:
        for row in range(self.table.rowCount()):
            self._restyle(row)
        self._update_summary()

    def is_dimmed(self, path: Path) -> bool:
        return bool(self.problems(path))

    def select_matching(self) -> None:
        self.table.clearSelection()
        mode = self.table.selectionMode()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for row in range(self.table.rowCount()):
            if self.matches(self._path(row)):
                self.table.selectRow(row)
        self.table.setSelectionMode(mode)

    def selected_paths(self) -> list[Path]:
        rows = sorted(index.row() for index in self.table.selectionModel().selectedRows())
        return [self._path(row) for row in rows]

    def _update_summary(self) -> None:
        total = len(self._paths)
        matching = sum(self.matches(path) for path in self._paths)
        picked = len(self.table.selectionModel().selectedRows())
        if not total:
            self.summary.setText("No raw frames in this folder.")
        else:
            self.summary.setText(f"{matching} of {total} match · {picked} picked")
        self.ok.setEnabled(picked > 0)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder with frames", self.folder.text())
        if folder:
            self.load(Path(folder))

    def _stop(self) -> None:
        if self._worker:
            self._worker.requestInterruption()
            self._worker.wait()
            self._worker = None

    def done(self, result: int) -> None:
        self._stop()
        super().done(result)
