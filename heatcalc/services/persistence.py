from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

from ..core.models import Project

class ProjectPersistence:
    """Read/write .heatcalc project files (JSON)."""

    def save_project(self, payload: dict, path: Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(payload, indent=2))

    def load_project(self, path: Path) -> dict:
        path = Path(path)
        return json.loads(path.read_text())