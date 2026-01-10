from PyQt5.QtCore import QObject, pyqtSignal


class ProjectSignals(QObject):
    """Centralized signals that models/services can emit without circular deps."""
    # Emitted whenever the project data changes in a way that should persist
    project_changed = pyqtSignal()
    # Emitted when autosave policy toggled
    autosave_changed = pyqtSignal(bool)
    project_meta_changed = pyqtSignal()



signals = ProjectSignals()
