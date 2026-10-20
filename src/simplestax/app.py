from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from simplestax import __version__
from simplestax.ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if "--version" in argv[1:]:
        print(f"simplestax {__version__}")
        return 0
    app = QApplication(argv)
    app.setApplicationName("SimpleStax")
    app.setOrganizationName("GPD-Research")
    app.setDesktopFileName("simplestax")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
