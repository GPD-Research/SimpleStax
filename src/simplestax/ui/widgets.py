from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLabel

from simplestax.progress import ascii_bar, bounce_bar


class ProgressMeter(QLabel):
    """A monospaced text meter: filling when the total is known, bouncing when it is not."""

    def __init__(self) -> None:
        super().__init__()
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._tick = 0
        self.hide()

    def set_fraction(self, fraction: float, message: str = "") -> None:
        self.setText(f"{ascii_bar(fraction)}  {message}".rstrip())
        self.show()

    def pulse(self, message: str = "") -> None:
        self._tick += 1
        self.setText(f"{bounce_bar(self._tick)}  {message}".rstrip())
        self.show()

    def finish(self) -> None:
        self._tick = 0
        self.hide()
