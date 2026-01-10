import os
import sys
from pathlib import Path
from . import __package__ as _


def app_data_dir() -> Path:
    """Return a writable per-user app data directory, cross-platform."""
    # Avoid external deps; follow common conventions
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "HeatCalc"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ──────────────────────────────────────────────────────────────────────────────
# File: heatcalc/utils/qt.py
# ──────────────────────────────────────────────────────────────────────────────
from PyQt5.QtCore import QObject, pyqtSignal


class ProjectSignals(QObject):
    """Centralized signals that models/services can emit without circular deps."""
    # Emitted whenever the project data changes in a way that should persist
    project_changed = pyqtSignal()
    # Emitted when autosave policy toggled
    autosave_changed = pyqtSignal(bool)



signals = ProjectSignals()
