import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.processEvents()
    gc.collect()
    app.shutdown()


@pytest.fixture
def preferences(tmp_path):
    from PySide6.QtCore import QSettings

    from simplestax.settings import Preferences

    preferences = Preferences(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    preferences.library_root = tmp_path / "library"
    return preferences
