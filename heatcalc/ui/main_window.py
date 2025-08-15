from pathlib import Path
from PyQt5.QtWidgets import (
    QMainWindow, QAction, QFileDialog, QWidget, QVBoxLayout,
    QCheckBox, QMessageBox, QLabel
)
from PyQt5.QtCore import Qt

from ..core.models import Project
from ..core.calculation import run_iec60890
from ..services.persistence import ProjectPersistence
from ..services.settings import SettingsManager
from ..services.autosave import AutoSaveController
from ..utils.qt import signals
from ..version import PROJECT_EXTENSION, APP_NAME
from .project_meta_widget import ProjectMetaWidget


class MainWindow(QMainWindow):
    def __init__(self, settings: SettingsManager):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)

        self.settings = settings
        self.persistence = ProjectPersistence()
        self.project = Project()
        self.current_path: Path | None = None

        # Central content
        central = QWidget(self)
        v = QVBoxLayout(central)
        self.setCentralWidget(central)

        self.meta_widget = ProjectMetaWidget(self.project)
        v.addWidget(self.meta_widget)

        self.results_label = QLabel("Results will appear here.")
        v.addWidget(self.results_label)

        # Autosave checkbox
        self.cb_autosave = QCheckBox("Autosave")
        self.cb_autosave.setChecked(self.settings.autosave_enabled)
        self.cb_autosave.stateChanged.connect(self._toggle_autosave)
        v.addWidget(self.cb_autosave, alignment=Qt.AlignLeft)

        # Autosave controller
        self.autosaver = AutoSaveController(self._get_project_json, self.settings, self)

        # Menu
        self._build_menu()

    # ── UI Helpers ──────────────────────────────────────────────────────────
    def _build_menu(self):
        m = self.menuBar()
        filem = m.addMenu("File")

        act_new = QAction("New", self)
        act_new.triggered.connect(self.action_new)
        filem.addAction(act_new)

        act_open = QAction("Open…", self)
        act_open.triggered.connect(self.action_open)
        filem.addAction(act_open)

        act_saveas = QAction("Save As…", self)
        act_saveas.triggered.connect(self.action_save_as)
        filem.addAction(act_saveas)

        calc = m.addMenu("Calculate")
        act_run = QAction("Run IEC 60890", self)
        act_run.triggered.connect(self.action_run)
        calc.addAction(act_run)

    # ── Actions ─────────────────────────────────────────────────────────────
    def action_new(self):
        if not self._confirm_discard():
            return
        self.project = Project()
        self.meta_widget.setParent(None)
        self.meta_widget = ProjectMetaWidget(self.project)
        self.centralWidget().layout().insertWidget(0, self.meta_widget)
        self.current_path = None
        self.autosaver.set_current_path(None)
        self._refresh_results()

    def action_open(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open Project", "", f"HeatCalc (*{PROJECT_EXTENSION})")
        if not p:
            return
        try:
            data = self.persistence.load_project(Path(p))
            self.project = Project.from_json(data)
            self.meta_widget.setParent(None)
            self.meta_widget = ProjectMetaWidget(self.project)
            self.centralWidget().layout().insertWidget(0, self.meta_widget)
            self.current_path = Path(p)
            self.autosaver.set_current_path(self.current_path)
            self.settings.add_recent(self.current_path)
            self._refresh_results()
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))

    def action_save_as(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save Project As", "", f"HeatCalc (*{PROJECT_EXTENSION})")
        if not p:
            return
        self.current_path = Path(p)
        self.autosaver.set_current_path(self.current_path)
        self._save_now()
        self.settings.add_recent(self.current_path)

    def action_run(self):
        self.project = run_iec60890(self.project)
        self._refresh_results()
        self.project.mark_changed()  # trigger autosave of results too

    # ── Internals ───────────────────────────────────────────────────────────
    def _save_now(self):
        if self.current_path is None:
            self.action_save_as()
            return
        self.persistence.save_project(self._get_project_json(), self.current_path)

    def _get_project_json(self):
        return self.project.to_json()

    def _refresh_results(self):
        out = self.project.outputs
        text = (
            f"A_e: {out.ae_m2:.3f} m^2\n"
            f"ΔT_mid: {out.delta_t_mid_c:.2f} °C\n"
            f"ΔT_top: {out.delta_t_top_c:.2f} °C\n"
            f"factors: {out.factors}"
        )
        self.results_label.setText(text)

    def _toggle_autosave(self, state: int):
        enabled = state == Qt.Checked
        self.settings.autosave_enabled = enabled
        signals.autosave_changed.emit(enabled)

    def _confirm_discard(self) -> bool:
        # Later: detect dirty state; for now always ask when creating new
        if self.current_path is None:
            return True
        r = QMessageBox.question(self, "Discard current project?", "Start a new project and discard the current one?")
        return r == QMessageBox.Yes
