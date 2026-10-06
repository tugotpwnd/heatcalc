"""Selected-tier results and a saved fan operating point."""
from __future__ import annotations

import json
from dataclasses import asdict

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QGroupBox, QLineEdit,
    QDoubleSpinBox, QPushButton, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QDialog, QLayout, QMessageBox, QSizePolicy,
)

from ..core.compliance_61439 import evaluate_derating
from .bus_items import BusLineItem, BusLoadItem, BusSourceItem, BusJoinItem
from .tier_item import TierItem


def component_name(component):
    name = component.description or component.key
    return f"{name} ({component.part_number})" if component.part_number else name


class ThermalPreviewPanel(QWidget):
    def __init__(self, switchboard):
        super().__init__(switchboard)
        self.swb = switchboard
        self.result = None
        self.solved_signature = None
        self._render_key = None
        self._tier = None
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.title = QLabel("Select a tier to preview")
        self.title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.status = QLabel("Run Solve network to calculate temperatures and current capacity.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.solve_button = QPushButton("Solve network / refresh preview")
        self.solve_button.clicked.connect(self.solve)
        layout.addWidget(self.solve_button)

        overview = QGroupBox("Selected tier overview")
        overview.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        form = QFormLayout(overview)
        self.overview_form = form
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.values = {}
        for key, label in (
            ("air", "Internal air:"), ("limit", "Tier limit:"),
            ("lowest", "Lowest temperature rating:"),
            ("largest", "Largest component heat load:"),
            ("heat", "Heat breakdown:"), ("flow", "Minimum required airflow:"),
        ):
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextFormat(Qt.PlainText)
            self.values[key] = value
            form.addRow(label, value)
        layout.addWidget(overview)

        self.fan_group = QGroupBox("Selected fan · this tier")
        fan = QFormLayout(self.fan_group)
        fan.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.fan_name = QLineEdit()
        self.fan_name.setPlaceholderText("Optional fan model / reference")
        self.flow = QDoubleSpinBox()
        self.flow.setRange(0, 1000000)
        self.flow.setDecimals(1)
        self.flow.setSingleStep(25)
        self.flow.setSuffix(" m³/h")
        self.flow.setSpecialValueText("No selected fan")
        fan.addRow("Fan:", self.fan_name)
        fan.addRow("Delivered airflow:", self.flow)
        self.pending = QLabel("")
        self.pending.setWordWrap(True)
        fan.addRow(self.pending)
        self.flow.valueChanged.connect(self._fan_edited)
        self.fan_name.textChanged.connect(self._fan_edited)
        self.apply_button = QPushButton("Save fan && solve network")
        self.apply_button.clicked.connect(self.apply_fan)
        fan.addRow(self.apply_button)
        self.curve_button = QPushButton("Plot fan sizing curves…")
        self.curve_button.clicked.connect(self.show_curve)
        fan.addRow(self.curve_button)
        note = QLabel(
            "Enter delivered airflow through the installed enclosure and filters. "
            "0 uses natural cooling. The fan estimate retains the existing solar "
            "allowance and approaches the ambient + solar floor as airflow increases. "
            "Mid-height temperature uses the enclosure profile ratio."
        )
        note.setWordWrap(True)
        fan.addRow(note)
        layout.addWidget(self.fan_group)

        capacity_heading = QLabel("Component current capacity · evaluated at top air temperature")
        capacity_heading.setWordWrap(True)
        layout.addWidget(capacity_heading)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Component", "Qty", "Rated (A)", "At solved temp (A)",
            "At tier limit (A)", "Temperature rating / data",
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setFixedHeight(260)
        layout.addWidget(self.table)
        note = QLabel(
            "Current capacity is per device, with the same 80% rated-current ceiling "
            "as the PDF. Missing or invalid curves use the existing 80% fallback. "
            "An exceeded temperature rating is flagged separately."
        )
        note.setWordWrap(True)
        self.derating_note = note
        layout.addWidget(note)
        layout.addStretch()
        # Compare saved inputs, not scene repaint events (which also fire on solve).
        self.timer = QTimer(self)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self._refresh_if_visible)
        self.timer.start()
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # At narrow widths put values below their labels, leaving enough width
        # for complete temperatures and equipment names.
        policy = QFormLayout.WrapAllRows if self.width() < 440 else QFormLayout.DontWrapRows
        if self.overview_form.rowWrapPolicy() != policy:
            self.overview_form.setRowWrapPolicy(policy)

    def signature(self):
        entries = []
        for item in self.swb.scene.items():
            if isinstance(item, (TierItem, BusLineItem, BusLoadItem, BusSourceItem, BusJoinItem)):
                state = item.to_dict()
                # UI persistence omits some thermal spec fields; include all of
                # them here so changes to wall gaps/joints invalidate results.
                if isinstance(item, (BusLineItem, BusJoinItem)):
                    state["thermal_spec"] = asdict(item.spec)
                if isinstance(item, TierItem):
                    for component in state["component_entries"]:
                        component.pop("derated_current_A", None)  # report output
                entries.append(json.dumps(state, sort_keys=True, default=str))
        return json.dumps({
            "items": sorted(entries), "meta": asdict(self.swb.project.meta),
            "wall": self.swb.cb_wall.isChecked(),
        }, sort_keys=True, default=str)

    def _refresh_if_visible(self):
        if self.isVisible():
            self.refresh()

    def set_result(self, result):
        self.result = result
        self.solved_signature = self.signature()
        self._render_key = None
        self.refresh()

    def solve(self):
        self.solve_button.setEnabled(False)
        try:
            self.swb.bus_panel.solve_network()
        except Exception as exc:
            self.result = None
            self._render_key = None
            self.refresh()
            QMessageBox.warning(self, "Solve failed", str(exc))
        finally:
            self.solve_button.setEnabled(True)

    def apply_fan(self):
        tier = self.swb._selected_tier()
        if tier is None:
            return
        tier.selected_airflow_m3h = self.flow.value()
        tier.selected_fan_name = self.fan_name.text().strip()
        self._fan_edited()
        self.swb.tierContentsChanged.emit()
        self.solve()

    def _fan_edited(self):
        tier = self.swb._selected_tier()
        changed = tier is not None and (
            self.flow.value() != tier.selected_airflow_m3h or
            self.fan_name.text().strip() != tier.selected_fan_name
        )
        self.pending.setText("Unsaved fan settings — save and solve to apply." if changed else "")
        self.pending.setVisible(changed)

    def refresh(self):
        use_curve = self.swb.project.meta.use_manufacturer_derating
        self.derating_note.setText(
            "Manufacturer curves replace the 80% Ith cap where valid. Missing or invalid curves "
            "retain the 80% limit. Current is per device; temperature ratings still apply."
            if use_curve else
            "Current capacity is per device, capped at 80% of rated current. Missing or invalid "
            "curves use the 80% fallback. Temperature ratings still apply."
        )
        tier = self.swb._selected_tier()
        signature = self.signature() if self.result is not None else None
        key = (id(tier), signature, id(self.result))
        if key == self._render_key:
            return
        self._render_key = key
        if tier is not self._tier:
            self._tier = tier
            self.flow.setValue(getattr(tier, "selected_airflow_m3h", 0.0))
            self.fan_name.setText(getattr(tier, "selected_fan_name", ""))
            self._fan_edited()
        self.fan_group.setEnabled(tier is not None)
        self.curve_button.setEnabled(False)
        self.table.setRowCount(0)
        for value in self.values.values():
            value.setText("—")
        self.title.setText(tier.name if tier else "Select a tier to preview")
        self.title.setTextFormat(Qt.PlainText)
        if tier is None:
            self.status.setText("Select a tier on the drawing to see its results.")
            return
        res = (self.result or {}).get("tiers", {}).get(tier)
        if res is None or signature != self.solved_signature:
            self.status.setText("Inputs changed — solve again to update this preview." if self.result
                                else "Run Solve network to calculate this tier.")
            return
        converged = self.result["solver_converged"] and self.result["global"].converged
        self.curve_button.setEnabled(bool(converged))
        top, mid, limit = res["T_top"], res["T_mid"], res["limit_C"]
        mode = "Selected fan estimate" if res.get("fan_estimate") else "Natural cooling"
        status = "Within tier temperature limit" if res["compliant_top"] else "Above tier temperature limit"
        if not res.get("cooling_possible", True):
            status = "Ambient / solar conditions reach the limit; fan cooling cannot meet it"
        self.status.setText(f"{mode} · {status}." if converged else
                            "Solver did not converge — these results are provisional. Solve again before using them.")
        self.values["air"].setText(f"Mid {mid:.1f} °C  ·  Top {top:.1f} °C")
        self.values["limit"].setText(f"{limit:.1f} °C  ·  Margin {limit - top:+.1f} °C")
        components = tier.component_entries
        if components:
            lowest = min(components, key=lambda c: c.max_temp_C)
            largest = max(components, key=lambda c: c.heat_each_w * c.qty)
            self.values["lowest"].setText(f"{lowest.max_temp_C:.1f} °C — {component_name(lowest)}")
            self.values["largest"].setText(
                f"{largest.heat_each_w * largest.qty:.1f} W — {component_name(largest)} ×{largest.qty}"
            )
        coupling = res.get("coupling", {})
        self.values["heat"].setText(
            f"Components {tier.components_total_heat_W():.1f} W · "
            f"Cables {tier.cables_total_heat_W():.1f} W · "
            f"Busbars / joints {coupling.get('P_bus_W', 0):.1f} W\n"
            f"Total {res['P']:.1f} W"
        )
        required = res.get("airflow_m3h", 0.0)
        flow = res.get("selected_airflow_m3h", 0.0)
        self.values["flow"].setText(
            f"{required:.1f} m³/h · Selected {flow:.1f} m³/h"
            if res.get("cooling_possible", True) else "Not achievable with ambient-air fans"
        )
        self.table.setRowCount(len(components))
        for row, component in enumerate(components):
            current = evaluate_derating(component, top, use_manufacturer_curve=use_curve)
            at_limit = evaluate_derating(component, limit, use_manufacturer_curve=use_curve)
            rating = f"Max {component.max_temp_C:.1f} °C"
            if top > component.max_temp_C:
                rating += " — EXCEEDED"
            if not component.derating_function:
                rating += " · No curve"
            values = [component_name(component), str(component.qty),
                      self._amps(component.rated_current_A), self._amps(current),
                      self._amps(at_limit), rating]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, 220)

    @staticmethod
    def _amps(value):
        return "No rating" if value is None else f"{value:.1f}"

    def show_curve(self):
        self.refresh()
        tier = self.swb._selected_tier()
        if tier is None or not self.curve_button.isEnabled():
            return
        from .fan_sizing_dialog import FanSizingDialog
        dialog = FanSizingDialog(self, tier)
        dialog.exec_()
        dialog.deleteLater()
