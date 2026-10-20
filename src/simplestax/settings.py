"""Persistent preferences: whether the guided wizard opens, globally and per subject."""

from __future__ import annotations

from PySide6.QtCore import QSettings


class Preferences:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = (
            settings if settings is not None else QSettings("GPD-Research", "SimpleStax")
        )

    def _flag(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    @property
    def wizard_on_start(self) -> bool:
        return self._flag("wizard/on_start", True)

    @wizard_on_start.setter
    def wizard_on_start(self, enabled: bool) -> None:
        self._settings.setValue("wizard/on_start", enabled)

    def wizard_enabled(self, subject_key: str) -> bool:
        return self._flag(f"wizard/subject/{subject_key}", True)

    def set_wizard_enabled(self, subject_key: str, enabled: bool) -> None:
        self._settings.setValue(f"wizard/subject/{subject_key}", enabled)

    @property
    def last_subject(self) -> str | None:
        value = self._settings.value("last_subject")
        return str(value) if value else None

    @last_subject.setter
    def last_subject(self, key: str) -> None:
        self._settings.setValue("last_subject", key)
