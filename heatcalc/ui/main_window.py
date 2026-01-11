# heatcalc/ui/main_window.py
import json
from pathlib import Path

from PyQt5.QtWidgets import (
    QMainWindow, QTabWidget, QVBoxLayout, QWidget, QLabel,
    QAction, QFileDialog, QCheckBox, QMessageBox, QInputDialog
)
from PyQt5.QtCore import Qt

from .iec60890_dialog import ensure_checklist_before_report
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
from .tier_select_dialog import select_tiers_for_report
from .louvre_definition_tab import LouvreDefinitionTab


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


        # Has to innit before meta widget
        self.switchboard_tab = SwitchboardTab(self.project, parent=self)

        # Project Info
        self.meta_widget = ProjectMetaWidget(
            project=self.project,
            switchboard=self.switchboard_tab,
        )

        self.project_tab = QWidget()
        self.project_tab_layout = QVBoxLayout(self.project_tab)
        self.project_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.project_tab_layout.setSpacing(0)
        self.project_tab_layout.addWidget(self.meta_widget)
        self.tabs.addTab(self.project_tab, "Project Info")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Switchboard Designer

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
        self.temp_tab = TempRiseTab(lambda: self.switchboard_tab.scene, self.project, parent=self)
        self.tabs.addTab(self.temp_tab, "Temperature rise")

        # ---------------------------------------------------------------------------------------------------
        # Louvre tab
        self.louvre_tab = LouvreDefinitionTab(self.project, parent=self)
        self.tabs.addTab(self.louvre_tab, "Louvre definition")

        # ---------------------------------------------------------------------------------------------------
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

    # =======================  Vent and Lovure helpers. (Dont belong here but fuck you)=================================

    def _update_louvre_lock_state(self, widget: LouvreDefinitionTab):
        sb = self.switchboard_tab
        ip = int(getattr(self.project.meta, "ip_rating_n", 2))

        # Case 1: IP too high
        if ip > 4:
            widget.set_locked(
                True,
                f"Louvre definition is locked for IP{ip}X.\n"
                "Natural louvre ventilation is not permitted above IP4X."
            )
            return

        # Case 2: tiers already ventilated
        if sb.any_tiers_ventilated():
            names = ", ".join(sb.ventilated_tier_names())
            widget.set_locked(
                True,
                "Louvre definition is locked while tiers have ventilation enabled.\n\n"
                "Disable vents on all tiers to edit this tab.\n\n"
                f"Ventilated tiers:\n• {names}"
            )
            return

        # Otherwise editable
        widget.set_locked(False)

    # =======================  Tab change, with vent and lovure shit sorry. =================================

    def _on_tab_changed(self, idx: int):
        widget = self.tabs.widget(idx)

        # -------------------------------------------------
        # Louvre definition guard: ZERO ventilated tiers
        # -------------------------------------------------
        if isinstance(widget, LouvreDefinitionTab):
            self._update_louvre_lock_state(widget)
            widget.refresh()  # read-only refresh
            return

        # -------------------------------------------------
        # Default behaviour
        # -------------------------------------------------
        if hasattr(widget, "refresh_from_project"):
            widget.refresh_from_project()

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
        """Unload current project and reset UI WITHOUT touching disk."""
        if not self._confirm_discard():
            return

        # ---- HARD STOP autosave ----
        self.settings.autosave_enabled = False
        self.cb_autosave.blockSignals(True)
        self.cb_autosave.setChecked(False)
        self.cb_autosave.blockSignals(False)
        signals.autosave_changed.emit(False)

        # ---- Detach from any file ----
        self.current_path = None

        # ---- Reset in-memory project ONLY ----
        self.project = Project()  # fresh meta container
        self.project.meta.iec60890_checklist = []

        # ---- Clear UI state ----
        self.switchboard_tab.import_state({
            "tiers": [],
            "uniform_depth_value": 200,
        })

        self._rebuild_tabs()

        self.autosaver.set_current_path(None)

        # ---- UI feedback ----
        self._update_window_title("Untitled project")
        self.statusBar().showMessage("New project (not saved)")

    def _update_window_title(self, name: str):
        self.setWindowTitle(f"{APP_NAME} — {name}")

    # --- OPEN ---
    def _do_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "JSON (*.json)")
        if not path:
            return

        with open(path, "r") as f:
            data = json.load(f)

        # ---- Load project safely via model ------------------------------------
        try:
            self.project = Project.from_json(data)
        except Exception as e:
            QMessageBox.critical(self, "Open Failed", f"Could not load project:\n\n{e}")
            return

        # ---- Rebuild all tabs so they bind to the new Project ------------------
        self._rebuild_tabs()

        # ---- Backward compatibility: legacy files without 'designer' ----------
        designer_state = data.get("designer", data)

        # Restore switchboard layout
        self.switchboard_tab.import_state(designer_state)

        # ---- Refresh all UIs from model ----------------------------------------
        self.meta_widget.refresh_from_project()

        if hasattr(self.switchboard_tab, "refresh_from_project"):
            self.switchboard_tab.refresh_from_project()

        self.curvefit_tab.on_tier_geometry_committed()

        # ---- Autosave plumbing ------------------------------------------------
        self.current_path = Path(path)
        self.autosaver.set_current_path(self.current_path)

        self.statusBar().showMessage(f"Opened: {Path(path).name}")

    # --- SAVE (Save As) ---
    def _do_save(self):
        if not self._validate_meta_or_remind():
            return

        payload = self._get_project_json()

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "", "JSON (*.json)"
        )
        if not path:
            return

        path = Path(path)

        # WRITE FIRST
        path.write_text(json.dumps(payload, indent=2))

        # THEN attach persistence
        self.current_path = path
        self.autosaver.set_current_path(path)

        # Autosave may now legally run
        self.statusBar().showMessage(f"Saved: {path.name}")

    def _wire_autosave_triggers(self):
        self.switchboard_tab.tierGeometryCommitted.connect(
            self._project_changed, Qt.UniqueConnection
        )
        self.switchboard_tab.tierContentsChanged.connect(
            self._project_changed, Qt.UniqueConnection
        )

    # ======================= Internals ======================================
    def _rebuild_tabs(self):
        """Recreate tab contents that depend on the current Project instance."""

        # ---- Remove old widgets --------------------------------------------
        if self.meta_widget:
            self.project_tab_layout.removeWidget(self.meta_widget)
            self.meta_widget.setParent(None)

        if self.switchboard_tab:
            idx = self.tabs.indexOf(self.switchboard_tab)
            if idx != -1:
                self.tabs.removeTab(idx)
            self.switchboard_tab.setParent(None)

        if self.curvefit_tab:
            idx = self.tabs.indexOf(self.curvefit_tab)
            if idx != -1:
                self.tabs.removeTab(idx)
            self.curvefit_tab.setParent(None)

        if self.temp_tab:
            idx = self.tabs.indexOf(self.temp_tab)
            if idx != -1:
                self.tabs.removeTab(idx)
            self.temp_tab.setParent(None)

        if getattr(self, "louvre_tab", None):
            idx = self.tabs.indexOf(self.louvre_tab)
            if idx != -1:
                self.tabs.removeTab(idx)
            self.louvre_tab.setParent(None)

        # ---- Recreate Switchboard FIRST ------------------------------------
        self.switchboard_tab = SwitchboardTab(self.project, parent=self)
        self.tabs.insertTab(1, self.switchboard_tab, "Switchboard Designer")

        # ---- Recreate Project Meta (needs switchboard) ---------------------
        self.meta_widget = ProjectMetaWidget(
            project=self.project,
            switchboard=self.switchboard_tab,
            parent=self,
        )
        self.project_tab_layout.addWidget(self.meta_widget)

        # ---- Recreate Curve Fit --------------------------------------------
        self.curvefit_tab = CurveFitTab(
            self.project,
            self.switchboard_tab.scene,
            parent=self,
        )
        self.tabs.insertTab(2, self.curvefit_tab, "Curve fitting")

        # ---- Recreate Temperature Rise -------------------------------------
        self.temp_tab = TempRiseTab(
            lambda: self.switchboard_tab.scene,
            self.project,
            parent=self,
        )
        self.tabs.insertTab(3, self.temp_tab, "Temperature rise")

        # ---- Recreate Louvre -------------------------------------
        self.louvre_tab = LouvreDefinitionTab(
            self.project,
            parent=self
        )
        self.tabs.insertTab(4, self.louvre_tab, "Louvre definition")

        self._wire_autosave_triggers()

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
        m = self.project.meta

        meta_out = {
            "job_number": m.job_number,
            "project_title": getattr(m, "project_title", ""),
            "enclosure": m.enclosure,
            "designer_name": getattr(m, "designer_name", ""),
            "date": m.date,
            "revision": m.revision,

            # Thermal assumptions
            "ambient_C": float(getattr(m, "ambient_C", 40.0)),
            "altitude_m": float(getattr(m, "altitude_m", 0.0)),
            "ip_rating_n": int(getattr(m, "ip_rating_n", 2)),

            "enclosure_material": m.enclosure_material,
            "enclosure_k_W_m2K": m.enclosure_k_W_m2K,
            "allow_material_dissipation": m.allow_material_dissipation,

            # ---- Louvre definition (NEW) ----
            "louvre_definition": getattr(m, "louvre_definition", {}),

            # Legacy / misc
            "default_vent_area_cm2": getattr(m, "default_vent_area_cm2", 0.0),
            "default_vent_label": getattr(m, "default_vent_label", None),
            "iec60890_checklist": getattr(m, "iec60890_checklist", []),
        }

        return {
            "meta": meta_out,
            "designer": self.switchboard_tab.export_state(),
        }

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

    def _do_print_report(self):
        # 0) IEC 60890 preconditions (before anything else)
        answers = ensure_checklist_before_report(self, getattr(self.project, "meta", {}))
        if answers is None:
            return  # user cancelled

        # Persist answers into meta so they autosave + reload later
        try:
            if hasattr(self.project.meta, "iec60890_checklist"):
                self.project.meta.iec60890_checklist = answers
        except Exception:
            pass

        # Persist answers into model only
        if hasattr(self.project.meta, "iec60890_checklist"):
            self.project.meta.iec60890_checklist = answers

        self._project_changed()

        if getattr(self, "_project_changed", None):
            self._project_changed()

        # 1) Tier selection (scopes what appears in the PDF)
        scene = getattr(self.switchboard_tab, "scene", None)
        if scene is None:
            return

        tiers = [it for it in scene.items() if isinstance(it, TierItem)]
        tier_tags = [str(getattr(t, "name", getattr(t, "tag", ""))) for t in tiers]
        tier_tags = [t for t in tier_tags if t.strip()]

        if tier_tags:
            selected_tags = select_tiers_for_report(self, tier_tags)
            if selected_tags is None:
                return  # user cancelled
            if not selected_tags:
                QMessageBox.information(self, "No tiers selected", "No tiers were selected for the report.")
                return
        else:
            selected_tags = []


        # 3) Output PDF path
        out_path_str, _ = QFileDialog.getSaveFileName(self, "Export PDF Report", "", "PDF (*.pdf)")
        if not out_path_str:
            return
        out_path = Path(out_path_str)

        header_path = get_resource_path("heatcalc/data/logo.png")
        footer_path = get_resource_path("heatcalc/data/company_logo.png")

        try:
            export_project_report(
                self.project,
                self.switchboard_tab,
                self.curvefit_tab,
                out_path,
                ambient_C=self.project.meta.ambient_C,
                header_logo_path=footer_path,
                footer_image_path=header_path,
                iec60890_checklist=answers,
                selected_tier_tags=selected_tags,
            )
            self.statusBar().showMessage(f"Report written: {out_path.name}")
            QMessageBox.information(self, "Report Exported", f"Saved:\n{out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Report Export Failed", f"Could not export report:\n\n{e}")
            raise
