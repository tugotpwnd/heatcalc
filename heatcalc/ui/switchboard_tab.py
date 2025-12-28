# heatcalc/ui/switchboard_tab.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtCore import Qt, QSortFilterProxyModel, QModelIndex, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QGroupBox,
    QLabel, QFormLayout, QLineEdit, QCheckBox, QSpinBox,
    QSplitter, QListWidget, QListWidgetItem, QAbstractItemView, QTableView,
    QToolButton, QComboBox, QMessageBox, QSizePolicy, QDoubleSpinBox
)
from PyQt5.QtGui import QFontMetrics

from .designer_view import DesignerView, GRID, snap
from .tier_item import TierItem, _Handle, CableEntry
from ..core.component_library import DEFAULT_COMPONENTS  # we’ll enrich this map with catalog entries
from PyQt5.QtWidgets import QDialog, QDialogButtonBox
from ..core.component_store import (
    load_component_catalog, ComponentRow,
    resolve_components_csv, append_component_to_csv
)
from .component_table_model import ComponentTableModel
from .cable_adder import CableAdderWidget
from ..core.iec60890_calc import calc_tier_iec60890
from ..core.iec60890_geometry import apply_curve_state_to_tiers, apply_covered_sides_to_tiers
from ..utils.qt import signals

# Enclosure material → effective heat transfer coefficient (W/m²·K)
ENCLOSURE_MATERIALS = {
    "Sheet metal": 5.5,
    "Aluminium": 5.5,
    "Stainless steel": 5.5,
    "Cast aluminium": 5.5,
    "Polycarbonate": 3.5,
    "Plastic": 3.5,
}

from .tier_item import STANDARD_VENTS_CM2


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



class CollapsibleGroupBox(QGroupBox):
    def __init__(self, title="", parent=None, start_expanded=True):
        super().__init__(title, parent)
        self.setCheckable(True)
        self.setChecked(bool(start_expanded))
        self._content = QWidget(self)
        self._inner_layout = QVBoxLayout(self._content)
        self._inner_layout.setContentsMargins(0, 0, 0, 0)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._content)
        super().setLayout(outer)

        # size policy: expand when open, fixed when closed
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toggled.connect(self._on_toggled)

        # start in correct visual state
        self._apply_collapsed_look(not start_expanded)

    def setLayout(self, layout):
        """Put caller's layout inside the collapsible content area."""
        self._inner_layout.addLayout(layout)

    # --- helpers ---------------------------------------------------------
    def _header_height_px(self) -> int:
        fm = QFontMetrics(self.font())
        # room for check box + text + frame margins
        return int(fm.height() + fm.leading() + 10)

    def _apply_collapsed_look(self, collapsed: bool):
        self._content.setVisible(not collapsed)
        if collapsed:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setMaximumHeight(self._header_height_px())
        else:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            self.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX

    def _on_toggled(self, checked: bool):
        self._apply_collapsed_look(not checked)


class SwitchboardTab(QWidget):
    tierGeometryCommitted = pyqtSignal()
    tierContentsChanged = pyqtSignal()
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project

        # ---- scene/view ----------------------------------------------------
        self.view = DesignerView(self)
        self.scene = self.view.scene()
        self.scene.selectionChanged.connect(self._on_selection_changed)

        # ---------- LEFT panel (wide via splitter) --------------------------
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(8, 8, 8, 8)
        left_lay.setSpacing(10)

        # Global flag (affects curves)
        self.cb_wall = QCheckBox("Wall-mounted installation")
        self.cb_wall.stateChanged.connect(self._recompute_all_curves)
        left_lay.addWidget(self.cb_wall)

        # ---- Enclosure material (project-wide) -------------------------------
        gb_material = QGroupBox("Enclosure material")
        fm = QFormLayout(gb_material)

        self.cmb_material = QComboBox()
        self.cmb_material.addItems(ENCLOSURE_MATERIALS.keys())
        self.cmb_material.currentTextChanged.connect(self._on_material_changed)

        self.lbl_k = QLabel("–")
        self.lbl_k.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.cb_allow_material = QCheckBox("Allow heat dissipation via enclosure material")
        self.cb_allow_material.stateChanged.connect(self._on_allow_material_changed)

        self.cb_show_live = QCheckBox("Show live thermal overlay")
        self.cb_show_live.setChecked(True)
        self.cb_show_live.toggled.connect(self._toggle_live_overlay)

        fm.addRow("Material:", self.cmb_material)
        fm.addRow("k (W/m²·K):", self.lbl_k)
        fm.addRow(self.cb_allow_material)
        fm.addRow(self.cb_show_live)

        left_lay.addWidget(gb_material)

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

        self.cb_vent = QCheckBox("Vent enabled")
        self.cb_vent.toggled.connect(self._apply_vent_enabled)

        self.cmb_vent = QComboBox()
        self.cmb_vent.addItem("— Select —")
        for k in STANDARD_VENTS_CM2:
            self.cmb_vent.addItem(k)
        self.cmb_vent.addItem("Custom…")
        self.cmb_vent.currentTextChanged.connect(self._apply_vent_selection)

        self.sp_vent_cm2 = QDoubleSpinBox()
        self.sp_vent_cm2.setRange(0.0, 1e6)
        self.sp_vent_cm2.setSuffix(" cm²")
        self.sp_vent_cm2.setDecimals(1)
        self.sp_vent_cm2.valueChanged.connect(self._apply_custom_vent)

        # Row: [ Vent size ▼ ][ Custom area ]
        vent_row = QWidget()
        vent_row_lay = QHBoxLayout(vent_row)
        vent_row_lay.setContentsMargins(0, 0, 0, 0)
        vent_row_lay.setSpacing(6)
        vent_row_lay.addWidget(self.cmb_vent, 1)
        vent_row_lay.addWidget(self.sp_vent_cm2, 0)

        # Add to form
        sel_form.addRow(self.cb_vent)
        sel_form.addRow("Vent:", vent_row)

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
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([self.width() // 2, self.width() // 2])
        left.setMaximumWidth(800)  # or whatever feels right (e.g., 480–600)

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
        return {
            "version": 1,
            "wall_mounted_global": bool(self.cb_wall.isChecked()),
            "uniform_depth": bool(self.cb_same_depth.isChecked()),
            "uniform_depth_value": int(self.sp_same_depth.value()),
            "tiers": [t.to_dict() for t in self._tiers()],
        }

    def import_state(self, state: dict):
        for it in list(self._tiers()):
            self.scene.removeItem(it)

        self.cb_wall.setChecked(bool(state.get("wall_mounted_global", False)))
        self.cb_same_depth.setChecked(bool(state.get("uniform_depth", False)))
        self.sp_same_depth.setValue(int(state.get("uniform_depth_value", 200)))

        for td in state.get("tiers", []):
            t = TierItem.from_dict(td)
            self._wire_tier_signals(t)
            self.scene.addItem(t)

        self._recompute_all_curves()
        self._update_left_from_selection()
        self.tierGeometryCommitted.emit()

    def refresh_from_project(self):
        # Material
        mat = getattr(self.project.meta, "enclosure_material", "Sheet metal")
        k = getattr(
            self.project.meta,
            "enclosure_k_W_m2K",
            ENCLOSURE_MATERIALS.get(mat, 5.5),
        )

        self.cmb_material.blockSignals(True)
        idx = self.cmb_material.findText(mat)
        self.cmb_material.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_material.blockSignals(False)

        self.lbl_k.setText(f"{k:.2f}")

        # Allow material dissipation
        allow = bool(getattr(self.project.meta, "allow_material_dissipation", False))
        self.cb_allow_material.blockSignals(True)
        self.cb_allow_material.setChecked(allow)
        self.cb_allow_material.blockSignals(False)

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

    # ------------------------------------------------------------------ #
    # Scene helpers / selection
    # ------------------------------------------------------------------ #
    def _tiers(self):
        for item in self.scene.items():
            if isinstance(item, TierItem):
                yield item

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

        self.scene.clearSelection()
        depth = self.sp_same_depth.value() if self.cb_same_depth.isChecked() else 200
        t = TierItem(name, x, y, w, h, depth_mm=depth)
        self._wire_tier_signals(t)

        # Live left-panel size while dragging
        t.rectChanged.connect(lambda: self._update_left_from_selection())

        self.scene.addItem(t)
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
        self._recompute_live_thermal()
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
        self._recompute_live_thermal()
        self._update_left_from_selection()

    # ------------------------------------------------------------------ #
    # Material dissipation & Material
    # ------------------------------------------------------------------ #

    def _mark_project_dirty(self):
        signals.project_changed.emit()

    def _on_material_changed(self, text: str):
        k = ENCLOSURE_MATERIALS.get(text, 0.0)

        self.project.meta.enclosure_material = text
        self.project.meta.enclosure_k_W_m2K = k

        self.lbl_k.setText(f"{k:.2f}")

        self._mark_project_dirty()  # ✅ autosave trigger
        self._recompute_live_thermal()

    def _on_allow_material_changed(self, checked: bool):
        """
        Project-wide assumption:
        Allow heat dissipation via enclosure material.
        """
        self.project.meta.allow_material_dissipation = bool(checked)

        self._mark_project_dirty()  # ✅ autosave trigger
        self._recompute_live_thermal()


    # ------------------------------------------------------------------ #
    # Vents
    # ------------------------------------------------------------------ #

    def _set_vent_controls_enabled(self, enabled: bool):
        self.cmb_vent.setEnabled(enabled)
        is_custom = self.cmb_vent.currentText() == "Custom…"
        self.sp_vent_cm2.setEnabled(enabled and is_custom)

    def _apply_vent_enabled(self, on: bool):
        it = self._selected_tier()
        if not it:
            return

        if not on:
            it.clear_vent()
        else:
            label = next(iter(STANDARD_VENTS_CM2))
            it.set_vent_preset(label)
            self.cmb_vent.setCurrentText(label)

        self._set_vent_controls_enabled(on)
        self._recompute_live_thermal()
        self._mark_project_dirty()

    def _apply_vent_selection(self, text: str):
        it = self._selected_tier()
        if not it or not it.is_ventilated:
            return

        if text in STANDARD_VENTS_CM2:
            it.set_vent_preset(text)
            self.sp_vent_cm2.setEnabled(False)

        elif text == "Custom…":
            self.sp_vent_cm2.setEnabled(True)
            it.set_custom_vent_cm2(self.sp_vent_cm2.value())

        self._recompute_live_thermal()
        self._mark_project_dirty()

    def _apply_custom_vent(self, val: float):
        it = self._selected_tier()
        if not it or not it.is_ventilated:
            return
        if self.cmb_vent.currentText() == "Custom…":
            it.set_custom_vent_cm2(val)
            self._recompute_live_thermal()
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

    def _recompute_live_thermal(self):
        tiers = list(self._tiers())

        # Project-wide meta (safe defaults)
        ambient = float(getattr(self.project.meta, "ambient_C", 40.0))
        k_mat = float(getattr(self.project.meta, "enclosure_k_W_m2K", 0.0))
        allow_mat = bool(getattr(self.project.meta, "allow_material_dissipation", True))
        wall = bool(self.cb_wall.isChecked())
        default_vent_area_cm2 = float(getattr(self.project.meta, "default_vent_area_cm2", 0.0))

        for t in tiers:
            try:
                t.live_thermal = calc_tier_iec60890(
                    tier=t,
                    tiers=tiers,
                    wall_mounted=wall,
                    inlet_area_cm2=t.vent_area_for_iec(),  # ← ONLY source
                    ambient_C=ambient,
                    enclosure_k_W_m2K=k_mat,
                    allow_material_dissipation=allow_mat,
                    default_vent_area_cm2=default_vent_area_cm2,
                )


            except Exception:
                t.live_thermal = None

            try:
                t.update()
            except Exception:
                pass

    # ----- list ops
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
        if not it:
            self.lbl_sel_name.setText("-")
            self.ed_name.setText("")
            self.lbl_size.setText("-")
            self.cb_vent.blockSignals(True);
            self.cb_vent.setChecked(False);
            self.cb_vent.blockSignals(False)
            self.list_contents.clear()
            self.lbl_total_heat.setText("Total heat: 0.0 W")
            self.sp_depth.blockSignals(True)
            self.sp_depth.setValue(self.sp_same_depth.value() if self.cb_same_depth.isChecked() else 200)
            self.sp_depth.blockSignals(False)
            self.sp_depth.setEnabled(not self.cb_same_depth.isChecked())
            self.sp_max_temp.blockSignals(True)
            self.sp_max_temp.setValue(70)
            self.sp_max_temp.blockSignals(False)
            self.sp_max_temp.setEnabled(True)
            self.cb_auto_limit.blockSignals(True);
            self.cb_auto_limit.setChecked(False);
            self.cb_auto_limit.blockSignals(False)
            self.lbl_effective_limit.setText("Effective limit: –")
            return

        self.lbl_sel_name.setText(it.name)
        self.ed_name.setText(it.name)

        mm_per_grid = 25
        wmm = int(it._rect.width() / GRID * mm_per_grid)
        hmm = int(it._rect.height() / GRID * mm_per_grid)
        self.lbl_size.setText(f"{wmm} × {hmm}")

        self.cb_vent.blockSignals(True)
        self.cb_vent.setChecked(it.is_ventilated)
        self.cb_vent.blockSignals(False)

        self.cmb_vent.blockSignals(True)
        if not it.is_ventilated:
            self.cmb_vent.setCurrentIndex(0)
        elif it.vent_label in STANDARD_VENTS_CM2:
            self.cmb_vent.setCurrentText(it.vent_label)
        else:
            self.cmb_vent.setCurrentText("Custom…")
            self.sp_vent_cm2.setValue(it.vent_area_cm2 or 0.0)
        self.cmb_vent.blockSignals(False)

        self._set_vent_controls_enabled(it.is_ventilated)

        self.sp_depth.blockSignals(True)
        self.sp_depth.setValue(it.depth_mm)
        self.sp_depth.blockSignals(False)
        self.sp_depth.setEnabled(not self.cb_same_depth.isChecked())

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
                            f"{cab.current_A:.1f} A @ {cab.air_temp_C}°C  "
                            f"(Pn={cab.Pn_Wpm:.2f} W/m, In={cab.In_A:.1f} A)  → {cab.total_W:.1f} W")
                    li = QListWidgetItem(line)
                    li.setData(Qt.UserRole, ("cable", cab))
                    self.list_contents.addItem(li)
                    total += float(cab.total_W)

            it.update()
            # update effective label whenever contents change (affects auto mode)
            self._update_effective_limit_label(it)

        self._recompute_live_thermal()
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

        # keep live overlay in sync
        self._recompute_live_thermal()


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
