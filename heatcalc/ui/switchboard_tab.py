# heatcalc/ui/switchboard_tab.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional

from PyQt5.QtCore import Qt, QSortFilterProxyModel, QModelIndex, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QGroupBox,
    QLabel, QFormLayout, QLineEdit, QCheckBox, QSpinBox,
    QSplitter, QListWidget, QListWidgetItem, QAbstractItemView, QTableView,
    QToolButton, QComboBox
)

from .designer_view import DesignerView, GRID, snap
from .tier_item import TierItem, _Handle
from ..core.component_library import DEFAULT_COMPONENTS  # we’ll enrich this map with catalog entries
from ..core.component_store import load_component_catalog, ComponentRow
from .component_table_model import ComponentTableModel
from .cable_adder import CableAdderWidget

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


class SwitchboardTab(QWidget):
    tierGeometryCommitted = pyqtSignal()
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


        # Selected tier basics
        gb_sel = QGroupBox("Selected tier")
        sel_form = QFormLayout(gb_sel)
        self.lbl_sel_name = QLabel("-")
        self.ed_name = QLineEdit("")
        self.ed_name.editingFinished.connect(self._apply_name)
        self.lbl_size = QLabel("-")
        self.cb_vent = QCheckBox("Ventilated")
        self.cb_vent.toggled.connect(self._apply_vent)
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
        sel_form.addRow("", self.cb_vent)

        left_lay.addWidget(gb_sel)

        # Selected tier contents (list)
        gb_contents = QGroupBox("Tier contents")
        v_contents = QVBoxLayout(gb_contents)
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
        gb_db = QGroupBox("Component library")
        v_db = QVBoxLayout(gb_db)

        top_row = QWidget(); tr = QHBoxLayout(top_row); tr.setContentsMargins(0, 0, 0, 0)
        self.cmb_category = QComboBox(); self.cmb_category.addItem("All categories")
        self.ed_search = QLineEdit(); self.ed_search.setPlaceholderText("Search description / part #")
        tr.addWidget(self.cmb_category, 0); tr.addWidget(self.ed_search, 1)
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
        gb_cab = QGroupBox("Cable adder")
        v_cab = QVBoxLayout(gb_cab)

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
        from heatcalc.utils.resources import get_resource_path
        csv_in_bundle = get_resource_path("heatcalc/data/components.csv")
        rows = []
        try:
            rows = load_component_catalog(csv_in_bundle)
        except Exception as e:
            # optional: print/log to help diagnose if ever empty again
            print(f"[ComponentCatalog] Failed to load {csv_in_bundle}: {e}")

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
            t.requestDelete.connect(self._delete_item)
            t.geometryCommitted.connect(self._on_tier_geometry_committed)
            t.positionCommitted.connect(self._on_tier_geometry_committed)
            self.scene.addItem(t)

        self._recompute_all_curves()
        self._update_left_from_selection()
        self.tierGeometryCommitted.emit()

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
        t.requestDelete.connect(self._delete_item)

        # Live left-panel size while dragging
        t.rectChanged.connect(lambda: self._update_left_from_selection())

        # Update curves only on commit (mouse release)
        t.geometryCommitted.connect(self._on_tier_geometry_committed)
        t.positionCommitted.connect(self._on_tier_geometry_committed)

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
        self._update_left_from_selection()

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

        self.lbl_total_heat.setText(f"Total heat: {total:.1f} W")

    # ------------------------------------------------------------------ #
    # Curve number from adjacency + wall-mounted
    # ------------------------------------------------------------------ #
    def _recompute_all_curves(self):
        wall = self.cb_wall.isChecked()
        tiers = list(self._tiers())
        for t in tiers:
            left_touch  = any(abs(t.shapeRect().left()  - o.shapeRect().right()) < 1e-3 and self._overlap_y(t, o) for o in tiers if o is not t)
            right_touch = any(abs(t.shapeRect().right() - o.shapeRect().left())  < 1e-3 and self._overlap_y(t, o) for o in tiers if o is not t)
            top_covered = any(abs(t.shapeRect().top()   - o.shapeRect().bottom())< 1e-3 and self._overlap_x(t, o) for o in tiers if o is not t)

            both = left_touch and right_touch
            one  = (left_touch ^ right_touch)

            if not left_touch and not right_touch and not top_covered:
                t.curve_no = 3 if wall else 1
            elif one and not top_covered:
                t.curve_no = 4 if wall else 2
            elif both and not top_covered:
                t.curve_no = 5 if wall else 3
            elif wall and both and top_covered:
                t.curve_no = 4
            else:
                t.curve_no = 4 if wall else 3

            t.wall_mounted = wall
            t.update()
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


    # --- persistence API ---------------------------------------------
    def export_state(self) -> dict:
        tiers = []
        for it in self.scene.items():
            if isinstance(it, TierItem):
                tiers.append(it.to_dict())
        return {
            "wall_mounted_global": bool(self.cb_wall.isChecked()),
            "use_uniform_depth": bool(getattr(self, "cb_uniform_depth", None) and self.cb_uniform_depth.isChecked()),
            "uniform_depth_mm": int(getattr(self, "sp_uniform_depth", None).value() if getattr(self, "sp_uniform_depth", None) else 400),
            "tiers": list(reversed(tiers)),  # reverse to keep visual stacking order
        }

    def import_state(self, state: dict) -> None:
        # clear existing tiers
        for it in list(self.scene.items()):
            if isinstance(it, TierItem):
                self.scene.removeItem(it)

        self.cb_wall.setChecked(bool(state.get("wall_mounted_global", False)))
        if getattr(self, "cb_uniform_depth", None):
            self.cb_uniform_depth.setChecked(bool(state.get("use_uniform_depth", False)))
        if getattr(self, "sp_uniform_depth", None):
            self.sp_uniform_depth.setValue(int(state.get("uniform_depth_mm", 400)))

        for td in state.get("tiers", []):
            t = TierItem.from_dict(td)
            self.scene.addItem(t)
        self._recompute_all_curves()
        self._update_left_from_selection()


    @staticmethod
    def _overlap_x(a: TierItem, b: TierItem) -> bool:
        ra, rb = a.shapeRect(), b.shapeRect()
        return not (ra.right() <= rb.left() or rb.right() <= ra.left())

    @staticmethod
    def _overlap_y(a: TierItem, b: TierItem) -> bool:
        ra, rb = a.shapeRect(), b.shapeRect()
        return not (ra.bottom() <= rb.top() or rb.bottom() <= ra.top())
