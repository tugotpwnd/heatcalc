# heatcalc/ui/project_meta_widget.py
from __future__ import annotations
from dataclasses import asdict
from PyQt5.QtWidgets import QWidget, QFormLayout, QLineEdit
from PyQt5.QtCore import Qt

from ..core.models import Project


class ProjectMetaWidget(QWidget):
    """
    Simple editor for Project.meta. Keeps a dict of QLineEdits instead of
    trying to store them as attributes (which caused the AttributeError).
    """
    FIELDS = [
        ("job_number", "Job #"),
        ("project_title", "Project title"),
        ("designer_name", "Designer"),
        ("date", "Date"),
        ("revision", "Revision"),
    ]

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self._project = project
        self._edits: dict[str, QLineEdit] = {}

        form = QFormLayout(self)
        form.setFormAlignment(Qt.AlignTop)

        # initialize from project.meta
        meta_dict = {}
        try:
            meta_dict = asdict(project.meta)
        except Exception:
            # if meta is a simple object without dataclass, fall back to getattr
            meta_dict = {k: getattr(project.meta, k, "") for k, _ in self.FIELDS}

        for key, label in self.FIELDS:
            le = QLineEdit(str(meta_dict.get(key, "")))
            # Route text changes straight into the underlying project.meta
            le.textChanged.connect(lambda text, k=key: self._on_text(k, text))
            form.addRow(label + ":", le)
            self._edits[key] = le

    # --- helpers ------------------------------------------------------------

    def _on_text(self, key: str, text: str):
        """Update the project's meta object live as the user types."""
        if not hasattr(self._project, "meta") or self._project.meta is None:
            return
        try:
            setattr(self._project.meta, key, text)
        except Exception:
            # keep UI robust even if meta type changes
            pass

    def refresh_from_project(self):
        """If caller replaces Project/meta, call this to refresh the UI."""
        for key, le in self._edits.items():
            val = getattr(self._project.meta, key, "")
            if le.text() != str(val):
                le.blockSignals(True)
                le.setText(str(val))
                le.blockSignals(False)

    # REPLACE these two methods in ProjectMetaWidget
    def set_meta(self, meta: dict):
        """Push values into the UI AND mirror them into self._project.meta."""
        for key, _label in self.FIELDS:
            val = str(meta.get(key, ""))
            le = self._edits.get(key)
            if le is None:
                continue
            # avoid feedback loop while we set text programmatically
            le.blockSignals(True)
            le.setText(val)
            le.blockSignals(False)
            # mirror into the underlying model if possible
            try:
                setattr(self._project.meta, key, val)
            except Exception:
                pass

    def get_meta(self) -> dict:
        """Read values from the UI controls."""
        out = {}
        for key, _label in self.FIELDS:
            le = self._edits.get(key)
            out[key] = le.text().strip() if le is not None else ""
        return out
