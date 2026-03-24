# heatcalc/ui/switchboard_tab.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtCore import Qt, QSortFilterProxyModel, QModelIndex, pyqtSignal, QPointF, QRectF
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QGroupBox,
    QLabel, QFormLayout, QLineEdit, QCheckBox, QSpinBox,
    QSplitter, QListWidget, QListWidgetItem, QAbstractItemView, QTableView,
    QToolButton, QComboBox, QMessageBox, QSizePolicy, QDoubleSpinBox
)
from PyQt5.QtGui import QFontMetrics

from .designer_view import DesignerView, GRID, snap
from .tier_item import TierItem, _Handle, CableEntry, tier_effective_inlet_area_cm2
from ..core.bus_thermal_solver import solve_thermal
from ..core.component_library import DEFAULT_COMPONENTS  # we’ll enrich this map with catalog entries
from PyQt5.QtWidgets import QDialog, QDialogButtonBox
from ..core.component_store import (
    load_component_catalog, ComponentRow,
    resolve_components_csv, append_component_to_csv
)
from .component_table_model import ComponentTableModel
from .cable_adder import CableAdderWidget
from ..core.iec60890_geometry import apply_curve_state_to_tiers, apply_covered_sides_to_tiers
from ..core.louvre_calc import tier_max_effective_inlet_area_cm2
from ..core.models import SOLAR_COLOUR_TABLE
from ..core.tier_coupled_solver import calc_tier_iec60890_coupled
from ..utils.qt import signals
from PyQt5.QtWidgets import QPlainTextEdit, QDialog, QVBoxLayout
from heatcalc.ui.bus_items import BusSpecUI, BusLineItem, BusLoadItem, BusJoinItem, BusSourceItem
from .busbar_tools_panel import BusbarToolsPanel

def _vents_allowed_by_ip(project) -> bool:
    """
    Natural ventilation is NOT permitted for above IP4X.
    """
    try:
        return int(getattr(project.meta, "ip_rating_n", 2)) < 5
    except Exception:
        return True  # fail-safe


class _CatalogProxy(QSortFilterProxyModel):
    """Filter by category + free-text search over part # + description."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._category: Optional[str] = None   # None or "All categories" → no cat filter
        self._text: str = ""

    def setCategory(self, cat: Optional[str]):
        self._category = cat
        self.invalidateFilter()

    def setText(self, text: str):
        self._text = (text or "").lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: ComponentTableModel = self.sourceModel()  # type: ignore
        idx_cat  = model.index(source_row, 0)
        idx_pn   = model.index(source_row, 1)
        idx_desc = model.index(source_row, 2)
        cat_val  = (model.data(idx_cat,  Qt.DisplayRole) or "")
        pn_val   = (model.data(idx_pn,   Qt.DisplayRole) or "")
        desc_val = (model.data(idx_desc, Qt.DisplayRole) or "")

        if self._category and self._category != "All categories":
            if cat_val != self._category:
                return False
        if not self._text:
            return True
        blob = f"{pn_val} {desc_val}".lower()
        return self._text in blob


class _NewComponentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Component")
        lay = QFormLayout(self)

        self.ed_cat  = QLineEdit(); self.ed_cat.setPlaceholderText("e.g., Drives")
        self.ed_pn   = QLineEdit(); self.ed_pn.setPlaceholderText("Part number (optional)")
        self.ed_desc = QLineEdit(); self.ed_desc.setPlaceholderText("Description")
        self.ed_heat = QLineEdit(); self.ed_heat.setPlaceholderText("Heat (W), e.g. 12.5")
        self.ed_tmax = QLineEdit(); self.ed_tmax.setPlaceholderText("Max Temp (°C), e.g. 70")

        lay.addRow("Category:", self.ed_cat)
        lay.addRow("Part #:", self.ed_pn)
        lay.addRow("Description:", self.ed_desc)
        lay.addRow("Heat (W):", self.ed_heat)
        lay.addRow("Max Temp (°C):", self.ed_tmax)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addRow(btns)

    def values(self) -> tuple[str, str, str, float, int] | None:
        cat = (self.ed_cat.text() or "").strip()
        desc = (self.ed_desc.text() or "").strip()
        if not cat or not desc:
            return None
        pn = (self.ed_pn.text() or "").strip()
        try:
            heat = float((self.ed_heat.text() or "0").strip())
        except Exception:
            heat = 0.0
        try:
            tmax = int(float((self.ed_tmax.text() or "70").replace("°","").replace("C","").strip()))
        except Exception:
            tmax = 70
        return cat, pn, desc, heat, tmax



from .collapsible_group_box import CollapsibleGroupBox


class SwitchboardTab(QWidget):
    tierGeometryCommitted = pyqtSignal()
    tierContentsChanged = pyqtSignal()
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project

        # ---- scene/view ----------------------------------------------------
        self.view = DesignerView(self)
        self.view.scene().selectionChanged.connect(self._on_selection_changed)

        # ---------- LEFT panel (wide via splitter) --------------------------
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(8, 8, 8, 8)
        left_lay.setSpacing(10)

        # Global flag (affects curves)
        self.cb_wall = QCheckBox("Wall-mounted installation")
        self.cb_wall.stateChanged.connect(self._recompute_all_curves)
        left_lay.addWidget(self.cb_wall)

        # ---- Global depth (project-wide) -------------------------------

        self.cb_same_depth = QCheckBox("Use same depth for all tiers")  # NEW
        self.sp_same_depth = QSpinBox()  # NEW
        self.sp_same_depth.setRange(10, 5000)
        self.sp_same_depth.setSuffix(" mm")
        self.sp_same_depth.setValue(200)

        left_lay.addWidget(self.cb_same_depth)
        row_same = QWidget();
        rsl = QHBoxLayout(row_same);
        rsl.setContentsMargins(0, 0, 0, 0)
        rsl.addWidget(QLabel("Depth:"))
        rsl.addWidget(self.sp_same_depth)
        rsl.addStretch(1)
        left_lay.addWidget(row_same)

        self.cb_same_depth.toggled.connect(self._toggle_uniform_depth)  # NEW
        self.sp_same_depth.valueChanged.connect(self._apply_uniform_depth_value)  # NEW

        # ---- Outdoor / solar conditions (PROJECT-WIDE) ----
        gb_solar = QGroupBox("Outdoor installation")
        sf = QFormLayout(gb_solar)

        self.cb_solar = QCheckBox("Outdoor installation (solar exposure)")
        self.cmb_solar_colour = QComboBox()
        self.cmb_solar_colour.addItems(SOLAR_COLOUR_TABLE.keys())
        self.lbl_solar_delta = QLabel("+")

        sf.addRow(self.cb_solar)
        sf.addRow("Enclosure colour:", self.cmb_solar_colour)
        sf.addRow("Solar increment:", self.lbl_solar_delta)

        self.cb_solar.toggled.connect(self._on_solar_changed)
        self.cmb_solar_colour.currentTextChanged.connect(self._on_solar_changed)

        left_lay.addWidget(gb_solar)

        # ---- Clip board (Tier copy & paste) -------------------------------
        self._tier_clipboard: dict | None = None

        # Selected tier basics
        gb_sel = CollapsibleGroupBox("Selected tier")
        sel_form = QFormLayout()
        gb_sel.setLayout(sel_form)
        self.lbl_sel_name = QLabel("-")
        self.ed_name = QLineEdit("")
        self.ed_name.editingFinished.connect(self._apply_name)
        self.lbl_size = QLabel("-")

        # ---------------- Vent controls (clean layout) ----------------
        # ---- Vent controls (single-line layout) ------------------------

        self.cb_vent = QCheckBox("Vents enabled")
        self.cb_vent.toggled.connect(self._apply_vent_enabled)
        self.cb_vent.setToolTip(
            "Natural ventilation is disabled automatically for IP ≥ 5X enclosures."
        )

        self.sp_vent_rows = QSpinBox()
        self.sp_vent_rows.setRange(1, 50)
        self.sp_vent_rows.valueChanged.connect(self._apply_vent_grid)

        self.sp_vent_cols = QSpinBox()
        self.sp_vent_cols.setRange(1, 50)
        self.sp_vent_cols.valueChanged.connect(self._apply_vent_grid)

        # Single compact row
        vent_row = QWidget()
        vent_lay = QHBoxLayout(vent_row)
        vent_lay.setContentsMargins(0, 0, 0, 0)
        vent_lay.setSpacing(6)

        vent_lay.addWidget(self.cb_vent)
        vent_lay.addWidget(QLabel("Rows"))
        vent_lay.addWidget(self.sp_vent_rows)
        vent_lay.addWidget(QLabel("Cols"))
        vent_lay.addWidget(self.sp_vent_cols)
        vent_lay.addStretch(1)

        sel_form.addRow("Vents:", vent_row)

        # Informational note
        self.lbl_vent_note = QLabel(
            "Vents are applied to top and bottom faces.\n"
            "Top vents include +1 ROW for chimney effect."
        )
        self.lbl_vent_note.setWordWrap(True)
        sel_form.addRow("", self.lbl_vent_note)

        # ---------------- Depth controls ----------------

        self.sp_depth = QSpinBox()  # NEW
        self.sp_depth.setRange(10, 5000)
        self.sp_depth.setSuffix(" mm")
        self.sp_depth.valueChanged.connect(self._apply_tier_depth)
        sel_form.addRow("Depth (mm):", self.sp_depth)

        # Max temperature controls
        self.sp_max_temp = QSpinBox()
        self.sp_max_temp.setRange(20, 120)
        self.sp_max_temp.setSuffix(" °C")
        self.sp_max_temp.setSingleStep(1)
        self.sp_max_temp.valueChanged.connect(self._apply_max_temp)
        sel_form.addRow("Max temp (°C):", self.sp_max_temp)

        self.cb_auto_limit = QCheckBox("Use lowest component max temp for tier limit")  # NEW
        self.cb_auto_limit.toggled.connect(self._toggle_auto_limit)
        sel_form.addRow("", self.cb_auto_limit)

        self.lbl_effective_limit = QLabel("Effective limit: –")  # helper label
        sel_form.addRow("Effective:", self.lbl_effective_limit)

        sel_form.addRow("Current:", self.lbl_sel_name)
        sel_form.addRow("Rename:", self.ed_name)
        sel_form.addRow("Size (mm):", self.lbl_size)
        left_lay.addWidget(gb_sel)

        # Selected tier contents (list)
        gb_contents = CollapsibleGroupBox("Tier contents")
        v_contents = QVBoxLayout()
        gb_contents.setLayout(v_contents)
        self.list_contents = QListWidget()
        self.list_contents.setSelectionMode(QAbstractItemView.SingleSelection)
        v_contents.addWidget(self.list_contents)

        row_btns = QWidget()
        rb = QHBoxLayout(row_btns); rb.setContentsMargins(0, 0, 0, 0)
        self.btn_remove_item = QToolButton(); self.btn_remove_item.setText("Remove")
        self.btn_clear_items = QToolButton();  self.btn_clear_items.setText("Clear")
        self.btn_remove_item.clicked.connect(self._remove_selected_component)
        self.btn_clear_items.clicked.connect(self._clear_all_components)
        rb.addWidget(self.btn_remove_item); rb.addWidget(self.btn_clear_items); rb.addStretch(1)
        v_contents.addWidget(row_btns)

        self.lbl_total_heat = QLabel("Total heat: 0.0 W")
        v_contents.addWidget(self.lbl_total_heat)
        left_lay.addWidget(gb_contents, 1)

        # Component library (search + category + table)
        gb_db = CollapsibleGroupBox("Component library")
        v_db = QVBoxLayout()
        gb_db.setLayout(v_db)

        top_row = QWidget(); tr = QHBoxLayout(top_row); tr.setContentsMargins(0, 0, 0, 0)
        self.cmb_category = QComboBox(); self.cmb_category.addItem("All categories")
        self.ed_search = QLineEdit(); self.ed_search.setPlaceholderText("Search description / part #")
        tr.addWidget(self.cmb_category, 0); tr.addWidget(self.ed_search, 1)
        v_db.addWidget(top_row)

        self.btn_refresh_components = QToolButton()
        self.btn_refresh_components.setText("↻")
        self.btn_refresh_components.setToolTip("Reload components.csv")
        self.btn_refresh_components.clicked.connect(self._reload_components)

        top_row = QWidget()
        tr = QHBoxLayout(top_row)
        tr.setContentsMargins(0, 0, 0, 0)

        tr.addWidget(self.cmb_category, 0)
        tr.addWidget(self.ed_search, 1)
        tr.addWidget(self.btn_refresh_components, 0)

        v_db.addWidget(top_row)

        self.tbl = QTableView()
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.doubleClicked.connect(self._add_component_from_table)
        self.tbl.setAlternatingRowColors(True)
        v_db.addWidget(self.tbl, 1)

        add_row = QWidget(); ar = QHBoxLayout(add_row); ar.setContentsMargins(0, 0, 0, 0)
        self.sp_qty = QSpinBox(); self.sp_qty.setRange(1, 999); self.sp_qty.setValue(1)
        self.btn_add_comp = QPushButton("Add to tier")
        self.btn_add_comp.clicked.connect(self._add_component_from_table)
        ar.addWidget(QLabel("Qty:")); ar.addWidget(self.sp_qty); ar.addStretch(1); ar.addWidget(self.btn_add_comp)
        v_db.addWidget(add_row)

        left_lay.addWidget(gb_db, 2)

        # --- Cable adder (next to/under component library) ---
        gb_cab = CollapsibleGroupBox("Cable adder")
        v_cab = QVBoxLayout()
        gb_cab.setLayout(v_cab)

        self.cableAdder = CableAdderWidget(self)  # auto-loads cable_table.csv
        self.cableAdder.cableAdded.connect(self._on_cable_added)
        v_cab.addWidget(self.cableAdder)

        left_lay.addWidget(gb_cab)

        # Add/delete tier
        btn_row = QWidget(); br = QHBoxLayout(btn_row); br.setContentsMargins(0, 0, 0, 0)
        self.btn_add = QPushButton("Add tier"); self.btn_del = QPushButton("Delete selected")
        self.btn_add.clicked.connect(self._add_tier); self.btn_del.clicked.connect(self._delete_selected)
        br.addWidget(self.btn_add); br.addWidget(self.btn_del)
        left_lay.addWidget(btn_row)

        # Row for adding a brand-new component into the CSV
        add_new_row = QWidget();
        anr = QHBoxLayout(add_new_row);
        anr.setContentsMargins(0, 0, 0, 0)
        self.btn_add_new_component = QPushButton("Add Component…")
        self.btn_add_new_component.clicked.connect(self._quick_add_component)
        anr.addStretch(1);
        anr.addWidget(self.btn_add_new_component)
        v_db.addWidget(add_new_row)

        # ---- Splitter so left can be ~half ----
        # ---- Splitter: Left panel | Designer | Bus panel ----
        splitter = QSplitter()

        # bus tools panel (RHS)
        self.bus_panel = BusbarToolsPanel(self.view, parent=self)

        splitter.addWidget(left)
        splitter.addWidget(self.view)
        splitter.addWidget(self.bus_panel)

        # stretch behaviour
        splitter.setStretchFactor(0, 0)  # left tools fixed-ish
        splitter.setStretchFactor(1, 1)  # canvas grows
        splitter.setStretchFactor(2, 0)  # bus panel fixed-ish

        # initial sizes
        splitter.setSizes([350, 1200, 300])

        # optional caps
        left.setMaximumWidth(500)
        self.bus_panel.setMaximumWidth(350)

        root = QHBoxLayout(self)
        root.addWidget(splitter)

        # ---- Catalog model / proxy ----------------------------------------
        self.components_csv_path = resolve_components_csv()
        rows = []
        error_txt = None
        try:
            rows = load_component_catalog(self.components_csv_path)
        except Exception as e:
            error_txt = str(e)

        # Fallback if missing/empty/unreadable
        if not rows:
            # Build a tiny built‑in catalog so the app still works
            from ..core.component_store import ComponentRow
            rows = [
                ComponentRow(category="Default", part_number=pn, description=desc, heat_w=float(w))
                for desc, w in DEFAULT_COMPONENTS.items()
                for pn in [""]
            ]

            # Show a one‑time friendly heads‑up
            msg = (
                    "<b>Couldn’t load <code>components.csv</code>.</b><br/><br/>"
                    "Falling back to the limited built‑in library. "
                    "To use your full catalogue, place <code>components.csv</code> in the same folder as this application:<br/>"
                    f"<code>{str(self.components_csv_path)}</code>"
                    + (f"<br/><br/><i>Details:</i> {error_txt}" if error_txt else "")
            )
            try:
                # Non-blocking info box (OK-only)
                QMessageBox.information(self, "Component Catalogue Not Found", msg)
            except Exception:
                # If we’re in an offscreen/test context just ignore
                pass

        self.model = ComponentTableModel(rows)
        self.proxy = _CatalogProxy(self)
        self.proxy.setSourceModel(self.model)
        self.tbl.setModel(self.proxy)
        self.tbl.setSortingEnabled(True)
        self.tbl.sortByColumn(0, Qt.AscendingOrder)

        # Fill categories and wire filters
        for cat in self.model.all_categories():
            self.cmb_category.addItem(cat)
        self.cmb_category.currentTextChanged.connect(self.proxy.setCategory)
        self.ed_search.textChanged.connect(self.proxy.setText)

        # On project meta update, recalculate the live thermal overlay so it's not stale.
        signals.project_meta_changed.connect(self._on_project_meta_changed)

        signals.project_changed.connect(self._on_louvre_definition_changed)

    # ------------------------------------------------------------------ #
    # Scene
    # ------------------------------------------------------------------ #

    @property
    def scene(self):
        return self.view.scene()
    # ------------------------------------------------------------------ #
    # Save / Load
    # ------------------------------------------------------------------ #

    # Helper to rewire emit signals.
    def _wire_tier_signals(self, t: TierItem):
        # Safe to call multiple times thanks to UniqueConnection
        t.requestDelete.connect(self._delete_item, type=Qt.UniqueConnection)
        t.geometryCommitted.connect(self._on_tier_geometry_committed, type=Qt.UniqueConnection)
        t.positionCommitted.connect(self._on_tier_geometry_committed, type=Qt.UniqueConnection)
        # keeps left panel responsive while dragging
        t.rectChanged.connect(lambda: self._update_left_from_selection(), type=Qt.UniqueConnection)

    def export_state(self) -> dict:

        tiers = []
        buses = []

        for item in self.view.scene().items():

            if isinstance(item, TierItem):
                tiers.append(item.to_dict())

            elif isinstance(item, BusLineItem):
                buses.append(item.to_dict())

        return {
            "version": 3,
            "wall_mounted_global": bool(self.cb_wall.isChecked()),
            "uniform_depth": bool(self.cb_same_depth.isChecked()),
            "uniform_depth_value": int(self.sp_same_depth.value()),
            "tiers": tiers,
            "buses": buses
        }

    def import_state(self, state: dict):

        self.cb_wall.setChecked(bool(state.get("wall_mounted_global", False)))
        self.cb_same_depth.setChecked(bool(state.get("uniform_depth", False)))
        self.sp_same_depth.setValue(int(state.get("uniform_depth_value", 200)))

        scene = self.view.scene()

        # ----------------------------------------
        # clear existing items
        # ----------------------------------------
        for item in list(scene.items()):
            scene.removeItem(item)

        tiers_by_id = {}

        # ----------------------------------------
        # create tiers
        # ----------------------------------------
        for td in state.get("tiers", []):
            t = TierItem.from_dict(td)

            t.get_louvre_definition = self._get_louvre_definition
            self._wire_tier_signals(t)

            scene.addItem(t)

            tiers_by_id[t.tier_id] = t

        # ----------------------------------------
        # create buses
        # ----------------------------------------
        for bd in state.get("buses", []):

            tier = tiers_by_id.get(bd.get("tier_id"))

            if tier is None:
                print("WARNING: bus has no tier", bd.get("id"))
                continue

            bus = BusLineItem.from_dict(bd)

            # attach to tier
            tier.add_bus_item(bus)

        # ----------------------------------------
        # refresh visuals
        # ----------------------------------------
        scene.update()

        self._recompute_all_curves()

    def _on_project_meta_changed(self):
        """
        Project-wide meta changed (ambient, altitude, enclosure, etc).
        Live overlay must be recalculated.
        """
        self.refresh_from_project()  # keeps UI in sync (material, k, etc)

        vents_allowed = _vents_allowed_by_ip(self.project)


        if not vents_allowed:
            # Enforce model state (belt + braces)
            for t in self._tiers():
                if t.is_ventilated:
                    t.clear_vent()

        self._update_left_from_selection()

    def _on_louvre_definition_changed(self):
        for t in self._tiers():
            if t.is_ventilated:
                t.update()  # force repaint with new geometry

    def refresh_from_project(self):
        """
        Sync project-wide meta → UI controls.
        Called on project load and whenever meta changes.
        """
        m = self.project.meta

        # ---- Solar / outdoor installation ----
        self.cb_solar.blockSignals(True)
        self.cmb_solar_colour.blockSignals(True)

        self.cb_solar.setChecked(bool(getattr(m, "solar_enabled", False)))

        colour = getattr(m, "solar_colour", "White")
        idx = self.cmb_solar_colour.findText(colour)
        self.cmb_solar_colour.setCurrentIndex(idx if idx >= 0 else 0)

        delta = float(getattr(m, "solar_delta_K", 0.0))
        self.lbl_solar_delta.setText(f"+{delta:.1f} K")
        self.cmb_solar_colour.setEnabled(bool(getattr(m, "solar_enabled", False)))

        self.cb_solar.blockSignals(False)
        self.cmb_solar_colour.blockSignals(False)

        # ---- Recompute curves / overlays ----
        self._recompute_all_curves()

    # ------------------------------------------------------------------ #
    # Copy / Paste
    # ------------------------------------------------------------------ #

    def copy_tier_contents(self, tier: TierItem):
        self._tier_clipboard = {
            "component_entries": [ce.__dict__.copy() for ce in tier.component_entries],
            "cables": [c.to_dict() for c in tier.cables],
        }

    def paste_tier_contents(self, tier: TierItem):
        if not self._tier_clipboard:
            return

        # --- merge components ---
        for ce in self._tier_clipboard["component_entries"]:
            tier.add_component_entry(
                key=ce["key"],
                category=ce["category"],
                part_number=ce["part_number"],
                description=ce["description"],
                heat_each_w=ce["heat_each_w"],
                qty=ce["qty"],
                max_temp_C=ce.get("max_temp_C", 70),
            )

        # --- append cables ---
        for c in self._tier_clipboard["cables"]:
            tier.cables.append(CableEntry.from_dict(c))

        tier.update()
        self._refresh_selected_contents()

    def copy_tier(self, tier: TierItem):
        """Full copy of tier including bus items."""
        d = tier.to_dict()
        bus_data = []
        for item in tier.bus_items():
            if hasattr(item, "to_dict"):
                bus_data.append({
                    "type": item.__class__.__name__,
                    "data": item.to_dict()
                })
        d["bus_items"] = bus_data
        self._tier_full_clipboard = d

    def paste_tier(self, scene_pos: QPointF):
        """Paste the full tier at scene_pos, avoiding overlaps."""
        if not hasattr(self, "_tier_full_clipboard") or not self._tier_full_clipboard:
            return

        d = self._tier_full_clipboard.copy()
        orig_name = d.get("name", "Tier")

        # 1. Iterate name
        existing_names = [t.name for t in self._tiers()]
        import re
        base_name = orig_name
        # If orig_name already has (n), strip it to find base
        match = re.search(r"^(.*) \(\d+\)$", orig_name)
        if match:
            base_name = match.group(1)

        counter = 1
        new_name = orig_name
        while new_name in existing_names:
            new_name = f"{base_name} ({counter})"
            counter += 1
        d["name"] = new_name

        # 2. Position and collision
        w = d.get("w", GRID * 8)
        h = d.get("h", GRID * 6)
        
        # User said "pasted at the users mouse location".
        # event.scenePos() gives us that.
        target_x = snap(scene_pos.x())
        target_y = snap(scene_pos.y())
        
        def overlaps(x, y, w, h):
            rect = QRectF(x, y, w, h)
            for t in self._tiers():
                # use sceneBoundingRect for global check
                if rect.intersects(t.sceneBoundingRect()):
                    return True
            return False

        if overlaps(target_x, target_y, w, h):
            found = False
            # Search in a grid around the target
            for r in range(1, 20):
                for dx in range(-r, r + 1):
                    for dy in [-r, r]:
                        if not overlaps(target_x + dx * GRID, target_y + dy * GRID, w, h):
                            target_x += dx * GRID
                            target_y += dy * GRID
                            found = True; break
                    if found: break
                if found: break
                for dy in range(-r + 1, r):
                    for dx in [-r, r]:
                        if not overlaps(target_x + dx * GRID, target_y + dy * GRID, w, h):
                            target_x += dx * GRID
                            target_y += dy * GRID
                            found = True; break
                    if found: break
                if found: break
        
        d["x"] = target_x
        d["y"] = target_y

        # 3. Create Tier
        new_tier = TierItem.from_dict(d)
        new_tier.get_louvre_definition = self._get_louvre_definition
        self.scene.addItem(new_tier)
        self._wire_tier_signals(new_tier)

        # 4. Bus items
        bus_map = {
            "BusLineItem": BusLineItem,
            "BusLoadItem": BusLoadItem,
            "BusJoinItem": BusJoinItem,
            "BusSourceItem": BusSourceItem
        }
        for b_entry in d.get("bus_items", []):
            cls = bus_map.get(b_entry["type"])
            if cls:
                b_item = cls.from_dict(b_entry["data"])
                new_tier.add_bus_item(b_item)

        new_tier.setSelected(True)
        self._mark_project_dirty()
        self._recompute_all_curves()
        self.view.refresh_tier_stack_visuals()

    # ------------------------------------------------------------------ #
    # Scene helpers / selection
    # ------------------------------------------------------------------ #
    def _tiers(self):
        scene = self.scene
        for item in scene.items():
            if isinstance(item, TierItem):
                yield item

    def get_tiers(self) -> list[TierItem]:
        print(list(self._tiers()))
        return list(self._tiers())

    def _selected_tier(self) -> TierItem | None:
        for it in self._tiers():
            if it.isSelected():
                return it
        return None

    def _on_selection_changed(self):
        # Enforce single select
        selected = [it for it in self._tiers() if it.isSelected()]
        if len(selected) > 1:
            keep = selected[-1]
            for it in selected[:-1]:
                it.setSelected(False)
            keep.setSelected(True)
        self._update_left_from_selection()

    def _on_scene_changed(self, _):
        # live curves + left panel size while moving/resizing
        self._recompute_all_curves()
        self._update_left_from_selection()

    # ------------------------------------------------------------------ #
    # IP Rating related helpers
    # ------------------------------------------------------------------ #

    def any_tiers_ventilated(self) -> bool:
        return any(
            t.is_ventilated
            for t in self._tiers()
        )

    def ventilated_tier_names(self) -> list[str]:
        return [
            t.name or "<unnamed>"
            for t in self._tiers()
            if t.is_ventilated
        ]

    def disable_all_vents(self):
        for t in self._tiers():
            if t.is_ventilated:
                t.clear_vent()

    # ------------------------------------------------------------------ #
    # Solar related helper
    # ------------------------------------------------------------------ #

    def _on_solar_changed(self):
        m = self.project.meta
        m.solar_enabled = self.cb_solar.isChecked()
        m.solar_colour = self.cmb_solar_colour.currentText()
        m.solar_delta_K = SOLAR_COLOUR_TABLE.get(m.solar_colour, 0.0)

        self.lbl_solar_delta.setText(f"+{m.solar_delta_K:.1f} K")
        self.cmb_solar_colour.setEnabled(m.solar_enabled)

        m.mark_changed()
        self._recompute_all_curves()

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _add_tier(self):
        rightmost = 0;
        top = 0
        for it in self._tiers():
            r = it.shapeRect()
            rightmost = max(rightmost, r.right())
            top = min(top, r.top())
        w, h = GRID * 6, GRID * 6
        x = snap(rightmost);
        y = snap(top)
        name = f"Tier {len(list(self._tiers())) + 1}"

        scene = self.scene
        scene.clearSelection()
        depth = self.sp_same_depth.value() if self.cb_same_depth.isChecked() else 200
        t = TierItem(name, x, y, w, h, depth_mm=depth)
        t.get_louvre_definition = self._get_louvre_definition

        self._wire_tier_signals(t)

        # Live left-panel size while dragging
        t.rectChanged.connect(lambda: self._update_left_from_selection())

        scene.addItem(t)
        t.setSelected(True)
        self._update_left_from_selection()
        self._recompute_all_curves()
        self.tierGeometryCommitted.emit()  # a new point exists
        self._on_tier_geometry_committed()  # ensure plots reflect the new tier

    def _delete_selected(self):
        removed = False
        for it in list(self._tiers()):
            if it.isSelected():
                self.scene.removeItem(it)
                removed = True
        if removed:
            self._update_left_from_selection()
            self._recompute_all_curves()
            self.tierGeometryCommitted.emit()

    def _delete_item(self, it):
        print(f"Delete requested on : {it}")
        self.scene.removeItem(it)
        self._update_left_from_selection()
        self._recompute_all_curves()
        self.tierGeometryCommitted.emit()

    def _on_tier_geometry_committed(self):
        # recompute curve IDs (adjacency can change) and notify others
        self._recompute_all_curves()
        self._update_left_from_selection()
        self.tierGeometryCommitted.emit()

    # ------------------------------------------------------------------ #
    # Left panel (selected tier) edits
    # ------------------------------------------------------------------ #
    def _apply_name(self):
        it = self._selected_tier()
        if not it:
            return
        it.name = self.ed_name.text().strip() or it.name
        it.update()
        self._update_left_from_selection()

    def _apply_vent(self, on: bool):
        it = self._selected_tier()
        if not it:
            return
        it.is_ventilated = on
        self._recompute_all_curves()
        self._update_left_from_selection()

    def _mark_project_dirty(self):
        signals.project_changed.emit()

    # ------------------------------------------------------------------ #
    # Vents
    # ------------------------------------------------------------------ #
    # switchboard_tab.py

    def get_louvre_definition(self):
        """
        Public accessor for the currently configured louvre definition.
        Safe for use by reporting / export layers.
        """
        return self._get_louvre_definition()

    def _get_louvre_definition(self) -> dict | None:
        meta = self.project.meta
        return getattr(meta, "louvre_definition", None)

    def _set_vent_controls_enabled(self, enabled: bool):
        self.sp_vent_rows.setEnabled(enabled)
        self.sp_vent_cols.setEnabled(enabled)

    def _apply_vent_enabled(self, on: bool):
        it = self._selected_tier()
        if not it:
            return

        vents_allowed = _vents_allowed_by_ip(self.project)

        if on and not vents_allowed:
            QMessageBox.warning(
                self,
                "Ventilation Not Permitted",
                f"Natural ventilation is not permitted for IP{self.project.meta.ip_rating_n}X enclosures."
            )
            self.cb_vent.blockSignals(True)
            self.cb_vent.setChecked(False)
            self.cb_vent.blockSignals(False)
            return

        it.is_ventilated = bool(on)

        # initialise defaults when enabling
        if on:
            it.vent_rows = max(1, getattr(it, "vent_rows", 1))
            it.vent_cols = max(1, getattr(it, "vent_cols", 1))

        self._update_left_from_selection()
        self._mark_project_dirty()

    def _apply_vent_grid(self):
        it = self._selected_tier()
        if not it or not it.is_ventilated:
            return

        d = self._get_louvre_definition()
        if not d:
            return

        max_rows, max_cols = it.max_louvre_grid(d)

        # Clamp user input
        rows = min(self.sp_vent_rows.value(), max_rows)
        cols = min(self.sp_vent_cols.value(), max_cols)

        it.vent_rows = rows
        it.vent_cols = cols

        # Reflect back to UI if clipped
        self.sp_vent_rows.blockSignals(True)
        self.sp_vent_cols.blockSignals(True)
        self.sp_vent_rows.setValue(rows)
        self.sp_vent_cols.setValue(cols)
        self.sp_vent_rows.blockSignals(False)
        self.sp_vent_cols.blockSignals(False)

        it.update()
        self._mark_project_dirty()

    # ------------------------------------------------------------------ #
    # Overlay
    # ------------------------------------------------------------------ #

    def _toggle_live_overlay(self, on: bool):
        for t in self._tiers():
            t.show_live_overlay = bool(on)
            try:
                t.update()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Solver
    # ------------------------------------------------------------------ #
    def _resolve_edge_tier(self, edge):
        """
        Resolve the true owning TierItem for an edge, even if edge.tier is a child
        object or stale intermediate reference.
        """
        obj = getattr(edge, "tier", None)

        while obj is not None:
            if isinstance(obj, TierItem):
                return obj
            if hasattr(obj, "parentItem"):
                obj = obj.parentItem()
            else:
                break

        return None

    def solve_all_thermal(self):
        from heatcalc.core.bus_graph import extract_graph
        from heatcalc.core.bus_current_solver import (
            solve_currents,
            filter_graph_to_source_component,
            get_disconnected_items,
        )
        from heatcalc.core.iec60890_calc import calc_tier_iec60890
        from PyQt5.QtWidgets import QMessageBox

        scene = self.scene
        tiers = list(self._tiers())

        ambient = float(getattr(self.project.meta, "ambient_C", 40.0))
        wall = bool(self.cb_wall.isChecked())
        altitude_m = float(getattr(self.project.meta, "altitude_m", 0.0))
        ip_rating_n = int(getattr(self.project.meta, "ip_rating_n", 0))
        solar_dt = (
            float(getattr(self.project.meta, "solar_delta_K", 0.0))
            if getattr(self.project.meta, "solar_enabled", False)
            else 0.0
        )

        louvre_def = self._get_louvre_definition()

        # -----------------------------------
        # 1. Build and solve electrical graph
        # -----------------------------------
        graph = extract_graph(scene)

        if graph.source_node is None:
            QMessageBox.warning(self, "Solve Failed", "No source node detected in the network.")
            return None

        disconnected = get_disconnected_items(graph)
        if disconnected:
            QMessageBox.warning(
                self,
                "Disconnected Nodes",
                "Floating nodes detected! Every busline, load, and joint must be connected to the source."
            )
            return None

        filter_graph_to_source_component(graph)
        solve_currents(graph)

        self._graph = graph

        # -------------------------------------------------
        # 2. Resolve edge ownership robustly
        # -------------------------------------------------
        tier_edges = {}
        edge_owner_tier = {}

        for t in tiers:
            owned = [e for e in graph.edges.values() if self._resolve_edge_tier(e) is t]
            tier_edges[t] = owned
            for e in owned:
                edge_owner_tier[e.id] = t

        if not edge_owner_tier:
            QMessageBox.warning(self, "Solve Failed", "No bus edges found in the network.")
            return None

        # -------------------------------------------------
        # 3. Global outer iteration
        #    tier IEC60890 air solve
        #    -> one global copper solve
        #    -> update bus watts per tier
        # -------------------------------------------------
        max_iter = 30
        tol_T = 0.05
        tol_P = 0.5
        relax = 0.5

        P_bus_by_tier = {t: 0.0 for t in tiers}
        prev_T_top_by_tier = {t: ambient for t in tiers}

        last_tier_res = {}
        last_air_by_edge = {}
        last_global_sol = None
        converged = False
        history = []

        for k in range(max_iter):
            tier_res = {}

            # -----------------------------
            # Solve tier enclosure air temps
            # -----------------------------
            for t in tiers:
                inlet_area_cm2 = 0.0
                if louvre_def:
                    inlet_area_cm2 = tier_effective_inlet_area_cm2(
                        tier=t,
                        louvre_def=louvre_def,
                        ip_rating_n=ip_rating_n,
                    )

                P_base = float(getattr(t, "total_heat_w", 0.0))
                P_bus = float(P_bus_by_tier.get(t, 0.0))

                res = calc_tier_iec60890(
                    tier=t,
                    tiers=tiers,
                    wall_mounted=wall,
                    inlet_area_cm2=inlet_area_cm2,
                    ambient_C=ambient,
                    altitude_m=altitude_m,
                    ip_rating_n=ip_rating_n,
                    solar_delta_K=solar_dt,
                    P_override_W=P_base + P_bus,
                )
                res["ambient_C"] = float(ambient)
                tier_res[t] = res

            # -----------------------------
            # Build per-edge air map
            # -----------------------------
            air_by_edge = {}

            for e in graph.edges.values():
                t = edge_owner_tier.get(e.id)
                if t is None:
                    air_by_edge[e.id] = ambient
                else:
                    air_by_edge[e.id] = float(tier_res[t].get("T_top", ambient))

            # -----------------------------
            # Solve copper network globally
            # -----------------------------
            global_sol = solve_thermal(
                graph=graph,
                air_temp_C=air_by_edge,
                debug=False,
            )

            edge_result_by_id = {er.edge_id: er for er in global_sol.edge_results}

            # -----------------------------
            # Re-accumulate bus/joint losses by owning tier
            # -----------------------------
            P_bus_raw_by_tier = {t: 0.0 for t in tiers}

            for e_id, er in edge_result_by_id.items():
                t = edge_owner_tier.get(e_id)
                if t is None:
                    continue
                P_bus_raw_by_tier[t] += float(er.P_gen_W)

            P_bus_new_by_tier = {}
            for t in tiers:
                old = float(P_bus_by_tier.get(t, 0.0))
                raw = float(P_bus_raw_by_tier.get(t, 0.0))
                P_bus_new_by_tier[t] = (1.0 - relax) * old + relax * raw

            dT = max(
                abs(float(tier_res[t]["T_top"]) - float(prev_T_top_by_tier.get(t, ambient)))
                for t in tiers
            ) if tiers else 0.0

            dP = max(
                abs(float(P_bus_new_by_tier[t]) - float(P_bus_by_tier.get(t, 0.0)))
                for t in tiers
            ) if tiers else 0.0

            history.append({
                "iter": k + 1,
                "dT": float(dT),
                "dP": float(dP),
                "T_top_by_tier": {id(t): float(tier_res[t]["T_top"]) for t in tiers},
                "P_bus_by_tier": {id(t): float(P_bus_new_by_tier[t]) for t in tiers},
            })

            last_tier_res = tier_res
            last_air_by_edge = dict(air_by_edge)
            last_global_sol = global_sol

            if dT < tol_T and dP < tol_P:
                converged = True
                P_bus_by_tier = P_bus_new_by_tier
                break

            prev_T_top_by_tier = {t: float(tier_res[t]["T_top"]) for t in tiers}
            P_bus_by_tier = P_bus_new_by_tier

        if last_global_sol is None:
            QMessageBox.warning(self, "Solve Failed", "Global thermal solve did not run.")
            return None

        # -----------------------------------
        # 4. Enrich tier results consistently
        # -----------------------------------
        final_tier_results = {}

        for t in tiers:
            res = dict(last_tier_res.get(t, {}))
            res["coupling"] = {
                "converged": bool(converged),
                "iterations": len(history),
                "history": history,
                "P_base_W": float(getattr(t, "total_heat_w", 0.0)),
                "P_bus_W": float(P_bus_by_tier.get(t, 0.0)),
                "P_total_W": float(getattr(t, "total_heat_w", 0.0)) + float(P_bus_by_tier.get(t, 0.0)),
            }
            final_tier_results[t] = res

        return {
            "graph": graph,
            "tiers": final_tier_results,
            "global": last_global_sol,
            "air_by_edge": last_air_by_edge,
            "edge_owner_tier": edge_owner_tier,
            "tier_edges": tier_edges,
            "solver_history": history,
            "solver_converged": converged,
        }
    # ----- list ops -----
    def _remove_selected_component(self):
        it = self._selected_tier()
        if not it:
            return
        item = self.list_contents.currentItem()
        if not item:
            return
        kind, backing = item.data(Qt.UserRole) or (None, None)

        if kind == "component_entry":
            ce = backing
            it.component_entries = [x for x in it.component_entries if x is not ce]
        elif kind == "cable":
            cab = backing
            it.cables = [c for c in it.cables if c is not cab]

        it.update()
        self._refresh_selected_contents()

    def _clear_all_components(self):
        it = self._selected_tier()
        if not it:
            return
        it.component_entries = []
        it.cables = []
        it.update()
        self._refresh_selected_contents()

    # ------------------------------------------------------------------ #
    # Component library add
    # ------------------------------------------------------------------ #
    def _add_component_from_table(self, _=None):
        it = self._selected_tier()
        if not it:
            return
        sel = self.tbl.selectionModel().selectedRows()
        if not sel:
            return
        src_idx = self.proxy.mapToSource(sel[0])
        row: ComponentRow = self.model.data(src_idx, Qt.UserRole)
        qty = int(self.sp_qty.value())

        key = f"{row.part_number} — {row.description}" if row.part_number else row.description
        category = getattr(row, "category", "Component")
        max_temp = int(getattr(row, "max_temp_C", 70))  # NEW: read from row if provided

        it.add_component_entry(
            key=key,
            category=category,
            part_number=row.part_number or "",
            description=row.description or key,
            heat_each_w=float(row.heat_w),
            qty=qty,
            max_temp_C=max_temp,  # NEW
        )
        it.update()
        self._refresh_selected_contents()

    def _reload_components(self):
        rows = load_component_catalog(self.components_csv_path)
        self.model.set_rows(rows)
        # refresh category combobox (preserve selection if possible)
        current = self.cmb_category.currentText()
        self.cmb_category.blockSignals(True)
        self.cmb_category.clear()
        self.cmb_category.addItem("All categories")
        for cat in self.model.all_categories():
            self.cmb_category.addItem(cat)
        # restore selection if still present
        idx = self.cmb_category.findText(current) if current else -1
        self.cmb_category.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_category.blockSignals(False)

    def _quick_add_component(self):
        dlg = _NewComponentDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        vals = dlg.values()
        if not vals:
            return
        cat, pn, desc, heat, tmax = vals
        new_row = ComponentRow(
            category=cat,
            part_number=pn,
            description=desc,
            heat_w=heat,
            max_temp_C=tmax
        )
        try:
            append_component_to_csv(self.components_csv_path, new_row)
            self._reload_components()
            # pre-select the newly added category to make it visible
            self.cmb_category.setCurrentText(cat if cat else "All categories")
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Add Component Failed", f"Could not append to CSV:\n\n{e}")

    # ------------------------------------------------------------------ #
    # Cable library add
    # ------------------------------------------------------------------ #

    def _on_cable_added(self, payload: dict):
        it = self._selected_tier()
        if not it:
            return
        # 1) Persist the full cable details on the tier
        it.add_cable(payload)
        # 2) Refresh the table/preview (now pulls cables from the tier)
        self._refresh_selected_contents()

    # ------------------------------------------------------------------ #
    # UI refresh helpers
    # ------------------------------------------------------------------ #
    def _update_left_from_selection(self):
        it = self._selected_tier()
        vents_allowed = _vents_allowed_by_ip(self.project)

        # ============================================================
        # NO TIER SELECTED
        # ============================================================
        if it is None:
            self.lbl_sel_name.setText("-")
            self.ed_name.setText("")
            self.lbl_size.setText("-")

            # Vent checkbox
            self.cb_vent.blockSignals(True)
            self.cb_vent.setChecked(False)
            self.cb_vent.setEnabled(False)
            self.cb_vent.blockSignals(False)

            # Vent grid
            self.sp_vent_rows.blockSignals(True)
            self.sp_vent_cols.blockSignals(True)
            self.sp_vent_rows.setValue(1)
            self.sp_vent_cols.setValue(1)
            self.sp_vent_rows.setEnabled(False)
            self.sp_vent_cols.setEnabled(False)
            self.sp_vent_rows.blockSignals(False)
            self.sp_vent_cols.blockSignals(False)

            # Clear remainder
            self.list_contents.clear()
            self.lbl_total_heat.setText("Total heat: 0.0 W")

            self.sp_depth.blockSignals(True)
            self.sp_depth.setValue(
                self.sp_same_depth.value() if self.cb_same_depth.isChecked() else 200
            )
            self.sp_depth.blockSignals(False)
            self.sp_depth.setEnabled(not self.cb_same_depth.isChecked())

            self.sp_max_temp.blockSignals(True)
            self.sp_max_temp.setValue(70)
            self.sp_max_temp.blockSignals(False)
            self.sp_max_temp.setEnabled(True)

            self.cb_auto_limit.blockSignals(True)
            self.cb_auto_limit.setChecked(False)
            self.cb_auto_limit.blockSignals(False)

            self.lbl_effective_limit.setText("Effective limit: –")
            return

        # ============================================================
        # TIER SELECTED
        # ============================================================

        self.lbl_sel_name.setText(it.name)
        self.ed_name.setText(it.name)

        mm_per_grid = 25
        wmm = int(it._rect.width() / GRID * mm_per_grid)
        hmm = int(it._rect.height() / GRID * mm_per_grid)
        self.lbl_size.setText(f"{wmm} × {hmm}")

        # -------- Vent logic (IP aware) --------
        vent_on = bool(it.is_ventilated and vents_allowed)

        # Vent enabled checkbox
        self.cb_vent.blockSignals(True)
        self.cb_vent.setChecked(vent_on)
        self.cb_vent.setEnabled(vents_allowed)
        self.cb_vent.blockSignals(False)

        # Rows / Columns
        self.sp_vent_rows.blockSignals(True)
        self.sp_vent_cols.blockSignals(True)

        self.sp_vent_rows.setValue(getattr(it, "vent_rows", 1))
        self.sp_vent_cols.setValue(getattr(it, "vent_cols", 1))

        self.sp_vent_rows.setEnabled(vent_on)
        self.sp_vent_cols.setEnabled(vent_on)

        self.sp_vent_rows.blockSignals(False)
        self.sp_vent_cols.blockSignals(False)

        # -------- Depth --------
        self.sp_depth.blockSignals(True)
        self.sp_depth.setValue(it.depth_mm)
        self.sp_depth.blockSignals(False)
        self.sp_depth.setEnabled(not self.cb_same_depth.isChecked())

        # -------- Max temperature --------
        self.sp_max_temp.blockSignals(True)
        self.sp_max_temp.setValue(int(getattr(it, "max_temp_C", 70)))
        self.sp_max_temp.blockSignals(False)

        self.cb_auto_limit.blockSignals(True)
        self.cb_auto_limit.setChecked(bool(getattr(it, "use_auto_component_temp", False)))
        self.cb_auto_limit.blockSignals(False)

        self.sp_max_temp.setEnabled(not self.cb_auto_limit.isChecked())
        self._update_effective_limit_label(it)

        self._refresh_selected_contents()

    def _update_effective_limit_label(self, it: TierItem):
        eff = int(it.effective_max_temp_C())
        mode = "auto" if it.use_auto_component_temp else "manual"
        self.lbl_effective_limit.setText(f"Effective limit: {eff}°C ({mode})")

    def _refresh_selected_contents(self):
        #A refresh of contents should trigger autosave
        self.tierContentsChanged.emit()

        it = self._selected_tier()
        self.list_contents.clear()
        total = 0.0

        if it:
            # Components
            if it.component_entries:
                hdr = QListWidgetItem("— Components —")
                hdr.setFlags(hdr.flags() & ~Qt.ItemIsSelectable)
                self.list_contents.addItem(hdr)
                for ce in it.component_entries:
                    subtotal = ce.heat_each_w * ce.qty
                    total += subtotal
                    txt = f"{ce.key}   ×{ce.qty}   ({ce.heat_each_w:.1f} W ea → {subtotal:.1f} W, max {ce.max_temp_C}°C)"
                    li = QListWidgetItem(txt)
                    li.setData(Qt.UserRole, ("component_entry", ce))
                    self.list_contents.addItem(li)

            # Cables
            if it.cables:
                hdr = QListWidgetItem("— Cables —")
                hdr.setFlags(hdr.flags() & ~Qt.ItemIsSelectable)
                self.list_contents.addItem(hdr)
                for cab in it.cables:
                    line = (f"{cab.name} — {cab.csa_mm2:.0f}mm², {cab.length_m:.1f} m, "
                            f"{cab.current_A:.1f} A @ 70°C  "
                            f"(Pv={cab.Pv_Wpm:.2f} W/m, Imax={cab.In_A:.1f} A)  → {cab.total_W:.1f} W")
                    li = QListWidgetItem(line)
                    li.setData(Qt.UserRole, ("cable", cab))
                    self.list_contents.addItem(li)
                    total += float(cab.total_W)

            it.update()
            # update effective label whenever contents change (affects auto mode)
            self._update_effective_limit_label(it)

        self.lbl_total_heat.setText(f"Total heat: {total:.1f} W")

    # ------------------------------------------------------------------ #
    # Curve number from adjacency + wall-mounted
    # ------------------------------------------------------------------ #
    def _recompute_all_curves(self):
        wall = self.cb_wall.isChecked()
        tiers = list(self._tiers())

        apply_curve_state_to_tiers(
            tiers=tiers,
            wall_mounted=wall,
            debug=False
        )

        # visual feedback for covered faces
        apply_covered_sides_to_tiers(tiers)


    # ------------------------------------------------------------------ #
    # Depth & Max tempt
    # ------------------------------------------------------------------ #

    def _toggle_uniform_depth(self, on: bool):
        # enable/disable per-tier editor
        self.sp_depth.setEnabled(not on)
        self.sp_same_depth.setEnabled(on)
        if on:
            # apply current global to all tiers
            val = self.sp_same_depth.value()
            for t in self._tiers():
                t.set_depth_mm(val)
        self._update_left_from_selection()
        self.tierGeometryCommitted.emit()  # update to geom, recalc curves.


    def _apply_uniform_depth_value(self, val: int):
        if not self.cb_same_depth.isChecked():
            return
        for t in self._tiers():
            t.set_depth_mm(val)
        self._update_left_from_selection()
        self.tierGeometryCommitted.emit()  # update to geom, recalc curves.

    def _apply_tier_depth(self, val: int):
        if self.cb_same_depth.isChecked():
            return
        it = self._selected_tier()
        if it:
            it.set_depth_mm(val)
            self._refresh_selected_contents()  # if you later want depth to affect anything shown
            self.tierGeometryCommitted.emit()  # update to geom, recalc curves.

    def _apply_max_temp(self, val: int):
        it = self._selected_tier()
        if it and not self.cb_auto_limit.isChecked():
            it.set_max_temp_C(int(val))
            self._refresh_selected_contents()

    def _toggle_auto_limit(self, on: bool):
        it = self._selected_tier()
        if not it:
            return
        it.set_auto_limit(on)
        # Disable manual editor when auto
        self.sp_max_temp.setEnabled(not on)
        # Keep label in sync
        self._update_effective_limit_label(it)
        self.tierGeometryCommitted.emit()

    @staticmethod
    def _overlap_x(a: TierItem, b: TierItem) -> bool:
        ra, rb = a.shapeRect(), b.shapeRect()
        return not (ra.right() <= rb.left() or rb.right() <= ra.left())

    @staticmethod
    def _overlap_y(a: TierItem, b: TierItem) -> bool:
        ra, rb = a.shapeRect(), b.shapeRect()
        return not (ra.bottom() <= rb.top() or rb.bottom() <= ra.top())

