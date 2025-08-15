import json
from pathlib import Path
from typing import Dict, Any, Optional
from .logger import get_logger
from ..version import PROJECT_EXTENSION


class ProjectPersistence:
    """Serialize/deserialize project JSON to disk."""

    def __init__(self) -> None:
        self._log = get_logger()

    def save_project(self, data: Dict[str, Any], path: Path) -> None:
        if path.suffix != PROJECT_EXTENSION:
            path = path.with_suffix(PROJECT_EXTENSION)
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self._log.info("Project saved: %s", path)
        except Exception:
            self._log.exception("Failed to save project")
            raise

    def load_project(self, path: Path) -> Dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            self._log.info("Project loaded: %s", path)
            return data
        except Exception:
            self._log.exception("Failed to load project")
            raise