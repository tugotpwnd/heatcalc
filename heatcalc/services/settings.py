import json
from pathlib import Path
from typing import Any, Dict
from ..version import SETTINGS_FILENAME
from ..utils.paths import app_data_dir
from .logger import get_logger



class SettingsManager:
    """JSON-backed settings with autosave option and recent file list."""

    DEFAULTS: Dict[str, Any] = {
        "autosave_enabled": True,
        "recent_files": [],
        "user": {
            "designer_name": "",
        },
    }

    def __init__(self) -> None:
        self._log = get_logger()
        self._path: Path = app_data_dir() / SETTINGS_FILENAME
        self._data: Dict[str, Any] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                self._log.debug("Settings loaded: %s", self._data)
            except Exception as e:
                self._log.exception("Failed to load settings, using defaults: %s", e)
                self._data = dict(self.DEFAULTS)
        else:
            self._data = dict(self.DEFAULTS)
            self.save()

    def save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            self._log.debug("Settings saved to %s", self._path)
        except Exception as e:
            self._log.exception("Failed to save settings: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    # Convenience
    @property
    def autosave_enabled(self) -> bool:
        return bool(self._data.get("autosave_enabled", True))

    @autosave_enabled.setter
    def autosave_enabled(self, val: bool) -> None:
        self._data["autosave_enabled"] = bool(val)
        self.save()

    def add_recent(self, path: Path) -> None:
        recents = list(self._data.get("recent_files", []))
        spath = str(path)
        if spath in recents:
            recents.remove(spath)
        recents.insert(0, spath)
        self._data["recent_files"] = recents[:10]
        self.save()
