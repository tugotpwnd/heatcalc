from PyQt5.QtWidgets import QWidget, QFormLayout, QLineEdit
from PyQt5.QtCore import pyqtSignal
from ..core.models import Project
from ..utils.qt import signals


class ProjectMetaWidget(QWidget):
    changed = pyqtSignal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self._project = project

        self.job = QLineEdit(project.meta.job_number)
        self.title = QLineEdit(project.meta.title)
        self.designer = QLineEdit(project.meta.designer)
        self.date = QLineEdit(project.meta.date)
        self.revision = QLineEdit(project.meta.revision)

        lay = QFormLayout(self)
        lay.addRow("Job Number", self.job)
        lay.addRow("Project Title", self.title)
        lay.addRow("Designer", self.designer)
        lay.addRow("Date", self.date)
        lay.addRow("Revision", self.revision)

        for w, attr in [
            (self.job, "job_number"),
            (self.title, "title"),
            (self.designer, "designer"),
            (self.date, "date"),
            (self.revision, "revision"),
        ]:
            w.textChanged.connect(lambda _t, a=attr: self._on_text(a))

    def _on_text(self, attr: str):
        setattr(self._project.meta, attr, getattr(self, attr).text())
        self._project.mark_changed()
        self.changed.emit()