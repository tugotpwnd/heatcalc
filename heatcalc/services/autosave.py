import json
from pathlib import Path
from typing import Optional, Dict, Any
from PyQt5.QtCore import QObject, QTimer
from .logger import get_logger
from .persistence import ProjectPersistence
from ..utils.qt import signals


class AutoSaveController(QObject):
    """Listens for project changes and saves after a short debounce."""

    def __init__(self, get_project_json_callback, settings_manager, parent=None) -> None:
        super().__init__(parent)
        self._log = get_logger()
        self._settings = settings_manager
        self._persist = ProjectPersistence()
        self._current_path: Optional[Path] = None
        self._get_project_json = get_project_json_callback

        self._timer = QTimer(self)
        self._timer.setInterval(1000)  # debounce 1s
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

        signals.project_changed.connect(self._on_project_changed)
        signals.autosave_changed.connect(self._on_autosave_changed)

    def set_current_path(self, path: Optional[Path]) -> None:
        self._current_path = path

    def _on_project_changed(self) -> None:
        print("[AUTOSAVE] project_changed received")
        if self._settings.autosave_enabled:
            self._timer.start()

    def _on_autosave_changed(self, enabled: bool) -> None:
        self._log.info("Autosave toggled: %s", enabled)
        if enabled:
            # save immediately on enabling
            self._on_timeout()

    def _on_timeout(self) -> None:
        print("[AUTOSAVE] debounce timeout fired")
        project = self._get_project_json.__self__.project
        print("[DEBUG] project id in autosave:", id(project))

        if not self._settings.autosave_enabled or self._current_path is None:
            print("[AUTOSAVE] aborted (autosave off or no path)")
            return

        data = self._get_project_json()

        print("[AUTOSAVE] JSON payload (meta.louvre_definition):")
        print(json.dumps(data["meta"]["louvre_definition"], indent=2))

        self._persist.save_project(data, self._current_path)
        print("[AUTOSAVE] JSON written to disk")
