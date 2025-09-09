# heatcalc/ui/main_window.py
import json
from pathlib import Path

from PyQt5.QtWidgets import (
    QMainWindow, QTabWidget, QVBoxLayout, QWidget, QLabel,
    QAction, QFileDialog, QCheckBox, QMessageBox, QInputDialog
)
from PyQt5.QtCore import Qt

from .tier_item import TierItem
from ..version import APP_NAME, PROJECT_EXTENSION
from ..core.models import Project
from ..services.autosave import AutoSaveController
from ..services.persistence import ProjectPersistence
from ..services.settings import SettingsManager
from ..utils.qt import signals

from .project_meta_widget import ProjectMetaWidget
from .switchboard_tab import SwitchboardTab
from .curvefit_tab import CurveFitTab
from .temp_rise_tab import TempRiseTab
from .toast_message import ToastMessage

# >>> NEW: simple report exporter types/functions
from ..reports.export_api import export_project_report
from ..utils.resources import get_resource_path
REQUIRED_META_KEYS = ["job_number", "project_title", "designer_name", "date", "revision"]


class MainWindow(QMainWindow):
    def __init__(self, settings: SettingsManager, project: Project | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.project: Project = project or Project()

        self.setWindowTitle(APP_NAME)
        self.resize(1920, 1080)

        # ---- Tabs -----------------------------------------------------------
        self.tabs = QTabWidget()
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(False)
        self.setCentralWidget(self.tabs)

        # Project Info
        self.meta_widget = ProjectMetaWidget(self.project)
        self.project_tab = QWidget()
        self.project_tab_layout = QVBoxLayout(self.project_tab)
        self.project_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.project_tab_layout.setSpacing(0)
        self.project_tab_layout.addWidget(self.meta_widget)
        self.tabs.addTab(self.project_tab, "Project Info")

        # Switchboard Designer
        self.switchboard_tab = SwitchboardTab(self.project, parent=self)
        self.tabs.addTab(self.switchboard_tab, "Switchboard Designer")

        self.curvefit_tab = CurveFitTab(self.project, self.switchboard_tab.scene, parent=self)
        self.tabs.addTab(self.curvefit_tab, "Curve fitting")

        # ---- Connect changes to autosave save and curve refitting ----------------------------------------
        self.switchboard_tab.tierGeometryCommitted.connect(
            self.curvefit_tab.on_tier_geometry_committed
        )
        # Any change in geom should call autosave
        self.switchboard_tab.tierGeometryCommitted.connect(
            self._project_changed
        )
        self.switchboard_tab.tierContentsChanged.connect(
            self._project_changed
        )
        # ---------------------------------------------------------------------------------------------------

        # Temperature rise (per-tier) – manual calculate
        self.temp_tab = TempRiseTab(self.switchboard_tab.scene, parent=self)
        self.tabs.addTab(self.temp_tab, "Temperature rise")

        # autosave toggle
        self.cb_autosave = QCheckBox("Autosave")
        self.cb_autosave.setChecked(self.settings.autosave_enabled)
        self.cb_autosave.stateChanged.connect(self._toggle_autosave)
        self.statusBar().addPermanentWidget(self.cb_autosave)

        self.statusBar().showMessage("Ready")

        # ---- Persistence / autosave ----------------------------------------
        self.persistence = ProjectPersistence()
        self.current_path: Path | None = None
        self.autosaver = AutoSaveController(self._get_project_json, self.settings, self)

        # ---- Menu -----------------------------------------------------------
        self._build_menu()

        # ---- Autosave Notifier ----------------------------------------------
        msg = "💾 Autosave is ON"
        toast = ToastMessage(msg, self)
        toast.show_centered(self)

    # ======================= Autosave Trigger =================================
    def _project_changed(self):
        signals.project_changed.emit()

    # ======================= Menu / Actions =================================
    def _build_menu(self):
        m = self.menuBar()
        filem = m.addMenu("File")

        act_new = QAction("New", self)
        act_new.triggered.connect(self.action_new)
        filem.addAction(act_new)

        act_open = QAction("Open…", self)
        act_open.triggered.connect(self._do_open)
        filem.addAction(act_open)

        act_saveas = QAction("Save As…", self)
        act_saveas.triggered.connect(self._do_save)
        filem.addAction(act_saveas)

        # >>> NEW: Print/Export Report
        act_report = QAction("Print Report…", self)
        act_report.triggered.connect(self._do_print_report)
        filem.addAction(act_report)

    def action_new(self):
        """Clear current project (ask if discarding), reset tabs and path."""
        if not self._confirm_discard():
            return
        self.project = Project()  # fresh meta container
        self.current_path = None
        # clear the switchboard and meta UIs
        self.switchboard_tab.import_state({"tiers": [], "uniform_depth_value": 200})
        self._rebuild_tabs()
        self.statusBar().showMessage("New project")

    # --- OPEN ---
    def _do_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "JSON (*.json)")
        if not path:
            return
        with open(path, "r") as f:
            data = json.load(f)

        # Support both new payloads {"meta":..., "designer":...} and old ones {"tiers":[...]}.
        designer_state = data.get("designer", data)

        # Restore switchboard (tiers + toggles) via the tab’s API
        self.switchboard_tab.import_state(designer_state)

        # 2) Meta: set both the data model and the UI
        meta = data.get("meta", {})
        self.project.meta.job_number = meta.get("job_number", "")
        self.project.meta.project_title = meta.get("project_title", "")
        self.project.meta.enclosure = meta.get("enclosure", "")
        self.project.meta.designer_name = meta.get("designer_name", "")
        self.project.meta.date = meta.get("date", "")
        self.project.meta.revision = meta.get("revision", "")

        # push into the UI widget so the user sees the loaded values
        self.meta_widget.set_meta({
            "job_number": self.project.meta.job_number,
            "project_title": self.project.meta.project_title,
            "enclosure": self.project.meta.enclosure,
            "designer_name": self.project.meta.designer_name,
            "date": self.project.meta.date,
            "revision": self.project.meta.revision,
        })

        self.curvefit_tab.on_tier_geometry_committed() # Refresh the curve fitting UI
        self.current_path = Path(path)  # NEW
        self.autosaver.set_current_path(self.current_path)  # NEW
        self.statusBar().showMessage(f"Opened: {Path(path).name}")

    # --- SAVE (Save As) ---
    def _do_save(self):
        if not self._validate_meta_or_remind():
            return
        payload = self._get_project_json()
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", "", "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(payload, indent=2))
        self.current_path = Path(path)  # NEW
        self.autosaver.set_current_path(self.current_path)  # NEW
        self.statusBar().showMessage(f"Saved: {Path(path).name}")

    # ======================= Internals ======================================
    def _rebuild_tabs(self):
        """Recreate tab contents that depend on the current Project instance."""
        # Project Info
        self.project_tab_layout.removeWidget(self.meta_widget)
        self.meta_widget.setParent(None)
        self.meta_widget = ProjectMetaWidget(self.project)
        self.project_tab_layout.addWidget(self.meta_widget)

        # Switchboard Designer
        idx = self.tabs.indexOf(self.switchboard_tab)
        if idx != -1:
            self.tabs.removeTab(idx)
        self.switchboard_tab.setParent(None)
        self.switchboard_tab = SwitchboardTab(self.project, parent=self)
        self.tabs.insertTab(1, self.switchboard_tab, "Switchboard Designer")

    def _toggle_autosave(self, state: int):
        enabled = state == Qt.Checked
        self.settings.autosave_enabled = enabled
        if enabled and self.current_path is None:
            self._do_save()  # pick a location, sets current_path + autosaver path
        signals.autosave_changed.emit(enabled)

    def _save_now(self):
        if self.current_path is None:
            self.action_save_as()
            return
        self.persistence.save_project(self._get_project_json(), self.current_path)

    def _get_project_json(self) -> dict:
        meta = self.meta_widget.get_meta()
        # keep model in sync (optional)
        self.project.meta.job_number = meta["job_number"]
        self.project.meta.project_title = meta["project_title"]
        self.project.meta.enclosure = meta["enclosure"]
        self.project.meta.designer_name = meta["designer_name"]
        self.project.meta.date = meta["date"]
        self.project.meta.revision = meta["revision"]
        return {"meta": meta, "designer": self.switchboard_tab.export_state()}

    def _confirm_discard(self) -> bool:
        if self.current_path is None:
            return True
        r = QMessageBox.question(self, "Discard current project?", "Start a new project and discard the current one?")
        return r == QMessageBox.Yes


    def _collect_meta_safely(self) -> dict:
        m = getattr(self.project, "meta", None)
        # Safely read attributes (supporting possible aliases like 'title')
        def g(obj, *names, default=""):
            for n in names:
                if hasattr(obj, n):
                    v = getattr(obj, n)
                    return v if v is not None else default
            return default

        if m is None:
            # Return empty dict with all expected keys (so validator can complain once)
            return {k: "" for k in REQUIRED_META_KEYS}

        return {
            "job_number":    g(m, "job_number"),
            "project_title": g(m, "project_title", "title"),
            "enclosure":     g(m, "enclosure"),
            "designer_name": g(m, "designer_name", "designer"),
            "date":          g(m, "date"),
            "revision":      g(m, "revision"),
        }

    def _validate_meta_or_remind(self) -> bool:
        meta = self._collect_meta_safely()
        missing = [k.replace("_", " ").title() for k, v in meta.items() if not str(v).strip()]
        if missing:
            msg = "Please complete project metadata before saving:\n\n• " + "\n• ".join(missing)
            QMessageBox.information(self, "Missing Project Info", msg)
            # Optional: jump to the Meta tab if you have one
            try:
                # If you stored the Meta tab widget: self.tabs.setCurrentWidget(self.meta_widget)
                # or if by index, e.g. index 0:
                # self.tabs.setCurrentIndex(0)
                pass
            except Exception:
                pass
            return False
        return True

        # ======================= Report =================================

    # add this handler method to MainWindow
    def _do_print_report(self):
        amb, ok = QInputDialog.getInt(self, "Ambient Temperature", "Enter ambient temperature (°C):", 25, -20, 90, 1)
        if not ok:
            return

        # Pick a filename
        out_path_str, _ = QFileDialog.getSaveFileName(self, "Export PDF Report", "", "PDF (*.pdf)")
        if not out_path_str:
            return
        out_path = Path(out_path_str)

        # cable_path = Path(__file__).resolve().parents[1] / "data" / "cable_table.csv"
        header_path = get_resource_path("heatcalc/data/logo.png")
        footer_path = get_resource_path("heatcalc/data/title.png")

        try:
            export_project_report(self.project, self.switchboard_tab, self.curvefit_tab, out_path, ambient_C=amb, header_logo_path=footer_path, footer_image_path=header_path)
            self.statusBar().showMessage(f"Report written: {out_path.name}")
            QMessageBox.information(self, "Report Exported", f"Saved:\n{out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Report Export Failed", f"Could not export report:\n\n{e}")
            raise
