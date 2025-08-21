import sys, os
from pathlib import Path

def get_resource_path(rel_path: str | os.PathLike) -> Path:
    base = getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])  # bundle or project root
    return Path(base) / rel_path
