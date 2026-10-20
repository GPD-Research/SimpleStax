import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def preferences(tmp_path):
    from PySide6.QtCore import QSettings

    from simplestax.settings import Preferences

    return Preferences(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
