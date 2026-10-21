from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from simplestax import __version__
from simplestax.settings import Preferences
from simplestax.stages import Stage, visible_stages
from simplestax.subjects import SUBJECTS, Subject, subject
from simplestax.ui.import_page import ImportPage
from simplestax.ui.pages import PlanPage, ShootPage, StagePage
from simplestax.ui.stack_pages import CalibratePage, StackPage


class MainWindow(QMainWindow):
    """Subject picker on top, the steps down the left, the current step on the right."""

    def __init__(self, preferences: Preferences | None = None) -> None:
        super().__init__()
        self.preferences = preferences or Preferences()
        self.setWindowTitle(f"SimpleStax {__version__}")
        self.resize(960, 640)

        self.subject_box = QComboBox()
        for item in SUBJECTS:
            self.subject_box.addItem(item.name, item.key)
        self.subject_box.setToolTip(
            "What you're photographing. Each subject has its own recommended pipeline."
        )
        self.guided_box = QCheckBox("Guided planning for this subject")
        self.guided_box.setToolTip(
            "Show the Plan and Shoot steps for this subject. Turn off to go straight to Import."
        )

        self.steps = QListWidget()
        self.steps.setFixedWidth(180)
        self.steps.setToolTip("The steps from planning to a stacked image. Click one to open it.")
        self.pages = QStackedWidget()
        self.plan_page = PlanPage()
        self.shoot_page = ShootPage()
        self.import_page = ImportPage(self.preferences)
        self.calibrate_page = CalibratePage(self.preferences)
        self.stack_page = StackPage()
        self.page_for: dict[Stage, QWidget] = {
            Stage.PLAN: self.plan_page,
            Stage.SHOOT: self.shoot_page,
            Stage.IMPORT: self.import_page,
            Stage.CALIBRATE: self.calibrate_page,
            Stage.STACK: self.stack_page,
        }
        for stage in Stage:
            self.page_for.setdefault(stage, StagePage(stage))
            self.pages.addWidget(self.page_for[stage])

        top = QHBoxLayout()
        top.addWidget(QLabel("Subject"))
        top.addWidget(self.subject_box)
        top.addStretch()
        top.addWidget(self.guided_box)
        body = QHBoxLayout()
        body.addWidget(self.steps)
        body.addWidget(self.pages, 1)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(top)
        layout.addLayout(body, 1)
        self.setCentralWidget(root)

        self.plan_page.plan_changed.connect(self.shoot_page.set_plan)
        self.plan_page.plan_changed.connect(self._plan_to_import)
        self.plan_page.skip_requested.connect(lambda: self.show_stage(Stage.IMPORT))
        self.calibrate_page.selection_ready.connect(self._to_stack)
        self.steps.currentItemChanged.connect(self._step_selected)
        self.subject_box.currentIndexChanged.connect(self._subject_changed)
        self.guided_box.toggled.connect(self._guided_toggled)

        last = self.preferences.last_subject
        index = self.subject_box.findData(last) if last else 0
        self.subject_box.setCurrentIndex(max(index, 0))
        self._subject_changed()
        if not self.preferences.wizard_on_start:
            self.show_stage(Stage.IMPORT)

    @property
    def subject(self) -> Subject:
        return subject(self.subject_box.currentData())

    @property
    def guided(self) -> bool:
        return self.guided_box.isChecked()

    def visible_stages(self) -> list[Stage]:
        return [
            self.steps.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.steps.count())
        ]

    def current_stage(self) -> Stage | None:
        item = self.steps.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def show_stage(self, stage: Stage) -> None:
        for row in range(self.steps.count()):
            if self.steps.item(row).data(Qt.ItemDataRole.UserRole) is stage:
                self.steps.setCurrentRow(row)
                return

    def _subject_changed(self) -> None:
        current = self.subject
        self.preferences.last_subject = current.key
        self.import_page.set_subject(current)
        self.stack_page.set_subject(current)
        self.plan_page.set_subject(current)
        self.guided_box.blockSignals(True)
        self.guided_box.setChecked(self.preferences.wizard_enabled(current.key))
        self.guided_box.blockSignals(False)
        self._rebuild_steps()

    def _to_stack(self, selection) -> None:
        self.stack_page.set_selection(selection)
        self.show_stage(Stage.STACK)

    def _plan_to_import(self, _plan=None) -> None:
        page = self.plan_page
        if page.exposure_s is not None:
            self.import_page.apply_plan(page.focal.value(), page.f_number.value(), page.exposure_s)

    def _guided_toggled(self, enabled: bool) -> None:
        self.preferences.set_wizard_enabled(self.subject.key, enabled)
        self._rebuild_steps()

    def _rebuild_steps(self) -> None:
        previous = self.current_stage()
        self.steps.blockSignals(True)
        self.steps.clear()
        for number, stage in enumerate(visible_stages(self.guided), start=1):
            item = QListWidgetItem(f"{number}. {stage.title}")
            item.setData(Qt.ItemDataRole.UserRole, stage)
            self.steps.addItem(item)
        self.steps.blockSignals(False)
        stages = self.visible_stages()
        self.show_stage(previous if previous in stages else stages[0])
        self._step_selected(self.steps.currentItem())

    def _step_selected(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is not None:
            self.pages.setCurrentWidget(self.page_for[item.data(Qt.ItemDataRole.UserRole)])
