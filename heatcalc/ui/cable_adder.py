
"""
cable_adder.py
--------------
PyQt5 widget that sits next to your Component Library in the Switchboard Designer tab.
It lets a user pick:
 - Cable name
 - CSA (mm^2)
 - Installation (simple dropdown for now; can map to multipliers later)
 - Air temperature inside enclosure (35°C or 55°C)
 - Load current (A)
 - Length (m)

It computes per-metre loss and total heat (W) using IEC 60890 Table B.1 and emits a 'cableAdded' signal.
Integrate by connecting the signal to your tier/component-heat-loss accumulator.
"""

from __future__ import annotations
from PyQt5 import QtCore, QtWidgets, QtGui
from dataclasses import dataclass
from typing import Optional
from pathlib import Path
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor

# Local import
from ..utils.resources import get_resource_path
from .cable_table import load_cable_table, interpolate_loss, cable_path

@dataclass
class CableSelection:
    name: str
    csa_mm2: float
    installation: str           # keep human label for report
    air_temp_C: int
    current_A: float
    length_m: float
    In_A: float
    Pn_Wpm: float
    P_Wpm: float
    total_W: float
    install_type: int           # NEW: 1/2/3 index
    factor_install: float = 1.0 # kept for backward compatibility (always 1.0)

class CableAdderWidget(QtWidgets.QWidget):
    cableAdded = QtCore.pyqtSignal(dict)

    def __init__(self, parent=None, cable_csv: Optional[str] = None):
        super().__init__(parent)
        self._csv_path = Path(cable_csv) if cable_csv else cable_path
        self._table_index = load_cable_table(self._csv_path)  # not strictly needed here, but OK
        self._build_ui()
        self._wire()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.ed_name = QtWidgets.QLineEdit()
        self.ed_name.setPlaceholderText("e.g. 'MSB Feeder A'")
        form.addRow("Cable name:", self.ed_name)

        # CSA combo (from available rows in any series)
        # Derive unique CSA values from the loaded table
        csa_values = sorted({row.csa for series in self._table_index.values() for row in series})
        self.cb_csa = QtWidgets.QComboBox()
        for csa in csa_values:
            self.cb_csa.addItem(f"{csa:.1f} mm²", csa)
        form.addRow("CSA:", self.cb_csa)

        # Installation type: 3 image tiles (mutually exclusive)
        inst_box = QtWidgets.QGroupBox("Installation type")
        inst_lay = QtWidgets.QHBoxLayout(inst_box)
        self.btn_group = QtWidgets.QButtonGroup(self); self.btn_group.setExclusive(True)

        def _make_inst_btn(idx: int, label: str, icon_name: str):
            b = QtWidgets.QToolButton()
            b.setCheckable(True)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            # Try to load an icon; fall back to a simple colored square
            icon_path = str(get_resource_path(f"heatcalc/assets/{icon_name}")) if 'get_resource_path' in globals() else ""
            icon = QtGui.QIcon(icon_path) if icon_path else QtGui.QIcon()
            if icon.isNull():
                pm = QtGui.QPixmap(64, 40); pm.fill(QtGui.QColor("#ddd"))
                painter = QtGui.QPainter(pm); painter.drawText(pm.rect(), QtCore.Qt.AlignCenter, str(idx)); painter.end()
                icon = QtGui.QIcon(pm)
            b.setIcon(icon)
            b.setIconSize(QtCore.QSize(72, 48))
            b.setText(label)
            self.btn_group.addButton(b, idx)
            inst_lay.addWidget(b)
            return b

        # You can replace the icon file names with your real images
        self.btn_inst1 = _make_inst_btn(1, "Type 1", "cable_install_type1.png")
        self.btn_inst2 = _make_inst_btn(2, "Type 2", "cable_install_type2.png")
        self.btn_inst3 = _make_inst_btn(3, "Type 3", "cable_install_type3.png")
        self.btn_inst1.setChecked(True)  # default selection
        form.addRow(inst_box)

        # Air temperature (from CSV – still choose 35/55 or any in CSV; we keep a simple 35/55 chooser)
        self.cb_temp = QtWidgets.QComboBox()
        self.cb_temp.addItems(sorted({str(t) for (t, _i) in self._table_index.keys()}))
        form.addRow("Air temp (°C):", self.cb_temp)

        # Load current
        self.sp_current = QtWidgets.QDoubleSpinBox()
        self.sp_current.setRange(0.0, 2000.0)
        self.sp_current.setDecimals(1)
        self.sp_current.setSuffix(" A")
        form.addRow("Load current:", self.sp_current)

        # Length
        self.sp_len = QtWidgets.QDoubleSpinBox()
        self.sp_len.setRange(0.0, 10000.0)
        self.sp_len.setDecimals(1)
        self.sp_len.setSuffix(" m")
        form.addRow("Cable length:", self.sp_len)

        # Live preview
        self.lbl_preview = QtWidgets.QLabel("—")
        self.lbl_preview.setWordWrap(True)
        layout.addWidget(self.lbl_preview)

        # Buttons
        btn_row = QtWidgets.QHBoxLayout(); layout.addLayout(btn_row)
        self.btn_add = QtWidgets.QPushButton("Add Cable → Tier Heat"); self.btn_add.setEnabled(False); btn_row.addWidget(self.btn_add)
        self.btn_clear = QtWidgets.QPushButton("Clear"); btn_row.addWidget(self.btn_clear)
        layout.addStretch(1)

    def _wire(self):
        for w in (self.ed_name, self.cb_csa, self.cb_temp, self.sp_current, self.sp_len):
            if isinstance(w, QtWidgets.QLineEdit): w.textChanged.connect(self._update_preview)
            else:
                (w.currentIndexChanged.connect(self._update_preview)
                    if isinstance(w, QtWidgets.QComboBox)
                    else w.valueChanged.connect(self._update_preview))
        self.btn_group.buttonClicked.connect(self._update_preview)
        self.btn_add.clicked.connect(self._emit_add)
        self.btn_clear.clicked.connect(self._clear)
        self._update_preview()

    # ---------- Logic ----------
    def _selected_install(self) -> tuple[int,str]:
        idx = self.btn_group.checkedId() or 1
        label = {1:"Type 1", 2:"Type 2", 3:"Type 3"}[idx]
        return idx, label

    def _current_selection(self) -> Optional[CableSelection]:
        name = (self.ed_name.text() or "").strip()
        if not name: return None
        csa = float(self.cb_csa.currentData())
        air = int(self.cb_temp.currentText())
        I   = float(self.sp_current.value())
        L   = float(self.sp_len.value())
        if I <= 0 or L <= 0: return None

        inst_idx, inst_label = self._selected_install()
        det = interpolate_loss(csa, I, air_temp_C=air, install_type=inst_idx, csv_path=self._csv_path)
        P_wpm = det["P_Wpm"]
        total = P_wpm * L

        return CableSelection(
            name=name,
            csa_mm2=csa,
            installation=inst_label,
            air_temp_C=air,
            current_A=I,
            length_m=L,
            In_A=det["In_A"],
            Pn_Wpm=det["Pn_Wpm"],
            P_Wpm=P_wpm,
            total_W=total,
            install_type=inst_idx,
            factor_install=1.0,  # always 1 now
        )

    def _update_preview(self):
        sel = self._current_selection()
        self.btn_add.setEnabled(sel is not None)
        if not sel:
            self.lbl_preview.setText("Enter name, current and length to see loss.")
            return
        self.lbl_preview.setText(
            f"<b>{sel.name}</b> • {sel.csa_mm2:.1f} mm² • {sel.installation}<br>"
            f"Air {sel.air_temp_C}°C — Iₙ={sel.In_A:.1f} A, Pₙ={sel.Pn_Wpm:.2f} W/m<br>"
            f"I={sel.current_A:.1f} A, L={sel.length_m:.1f} m → <b>P={sel.P_Wpm:.2f} W/m</b>, "
            f"<b>Total={sel.total_W:.1f} W</b>"
        )

    def _emit_add(self):
        sel = self._current_selection()
        if not sel: return
        payload = sel.__dict__.copy()
        self.cableAdded.emit(payload)
        self.lbl_preview.setText(self.lbl_preview.text() + "<br><i>Added to tier heat.</i>")

    def _clear(self):
        self.ed_name.clear()
        self.sp_current.setValue(0.0)
        self.sp_len.setValue(0.0)
        self.cb_temp.setCurrentIndex(0)
        self.btn_inst1.setChecked(True)
        self._update_preview()
