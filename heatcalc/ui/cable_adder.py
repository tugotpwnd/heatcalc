
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
from PyQt5 import QtCore, QtWidgets
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

# Local import
from .cable_table import load_cable_table, interpolate_loss, cable_path

INSTALLATION_TYPES = [
    "Single length (note 2)",
    "Multi-core bundle (6 cores, note 1)",
    "Other / custom factor",
]


@dataclass
class CableSelection:
    name: str
    csa_mm2: float
    installation: str
    air_temp_C: int
    current_A: float
    length_m: float
    In_A: float
    Pn_Wpm: float
    P_Wpm: float
    total_W: float
    factor_install: float = 1.0  # reserved for future multipliers


class CableAdderWidget(QtWidgets.QWidget):
    cableAdded = QtCore.pyqtSignal(dict)  # payload is CableSelection.asdict()

    def __init__(self, parent=None, cable_csv: Optional[str] = None):
        super().__init__(parent)
        self._csv_path = Path(cable_csv) if cable_csv else cable_path
        self._rows = load_cable_table(self._csv_path)

        self._build_ui()
        self._wire()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.ed_name = QtWidgets.QLineEdit()
        self.ed_name.setPlaceholderText("e.g., 'MSB Feeder A'")
        form.addRow("Cable name:", self.ed_name)

        # CSA
        self.cb_csa = QtWidgets.QComboBox()
        for r in self._rows:
            self.cb_csa.addItem(f"{r.csa:.1f} mm²", r.csa)
        form.addRow("CSA:", self.cb_csa)

        # Installation
        self.cb_inst = QtWidgets.QComboBox()
        self.cb_inst.addItems(INSTALLATION_TYPES)
        form.addRow("Installation:", self.cb_inst)

        # Air temperature
        self.cb_temp = QtWidgets.QComboBox()
        self.cb_temp.addItems(["35", "55"])
        self.cb_temp.setCurrentIndex(0)
        form.addRow("Air temp (°C):", self.cb_temp)

        # Load current
        self.sp_current = QtWidgets.QDoubleSpinBox()
        self.sp_current.setRange(0.0, 2000.0)
        self.sp_current.setDecimals(1)
        self.sp_current.setSuffix(" A")
        form.addRow("Load current:", self.sp_current)

        # Length
        self.sp_len = QtWidgets.QDoubleSpinBox()
        self.sp_len.setRange(0.0, 1000.0)
        self.sp_len.setDecimals(1)
        self.sp_len.setSuffix(" m")
        form.addRow("Cable length:", self.sp_len)

        # Optional custom factor for "Other / custom factor"
        self.sp_factor = QtWidgets.QDoubleSpinBox()
        self.sp_factor.setRange(0.1, 3.0)
        self.sp_factor.setSingleStep(0.05)
        self.sp_factor.setDecimals(2)
        self.sp_factor.setValue(1.0)
        form.addRow("Multiplier (install):", self.sp_factor)

        # Live preview
        self.lbl_preview = QtWidgets.QLabel("—")
        self.lbl_preview.setWordWrap(True)
        layout.addWidget(self.lbl_preview)

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        layout.addLayout(btn_row)

        self.btn_add = QtWidgets.QPushButton("Add Cable → Tier Heat")
        self.btn_add.setEnabled(False)
        btn_row.addWidget(self.btn_add)

        self.btn_clear = QtWidgets.QPushButton("Clear")
        btn_row.addWidget(self.btn_clear)

        layout.addStretch(1)

    def _wire(self):
        for w in (self.ed_name, self.cb_csa, self.cb_inst, self.cb_temp, self.sp_current, self.sp_len, self.sp_factor):
            if isinstance(w, QtWidgets.QLineEdit):
                w.textChanged.connect(self._update_preview)
            else:
                w.currentIndexChanged.connect(self._update_preview) if isinstance(w, QtWidgets.QComboBox) else w.valueChanged.connect(self._update_preview)

        self.btn_add.clicked.connect(self._emit_add)
        self.btn_clear.clicked.connect(self._clear)
        self._update_preview()

    # ---------- Logic ----------
    def _current_selection(self) -> Optional[CableSelection]:
        name = self.ed_name.text().strip()
        if not name:
            return None
        csa = float(self.cb_csa.currentData())
        air = int(self.cb_temp.currentText())
        I = float(self.sp_current.value())
        L = float(self.sp_len.value())
        if I <= 0 or L <= 0:
            return None

        det = interpolate_loss(csa, I, air_temp_C=air, csv_path=self._csv_path)
        P_wpm = det["P_Wpm"]

        inst_txt = self.cb_inst.currentText()
        factor = float(self.sp_factor.value() if "custom" in inst_txt.lower() else 1.0)
        total = P_wpm * L * factor

        return CableSelection(
            name=name,
            csa_mm2=csa,
            installation=inst_txt,
            air_temp_C=air,
            current_A=I,
            length_m=L,
            In_A=det["In_A"],
            Pn_Wpm=det["Pn_Wpm"],
            P_Wpm=P_wpm * factor,
            total_W=total,
            factor_install=factor,
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
        if not sel:
            return
        payload = sel.__dict__.copy()
        self.cableAdded.emit(payload)
        # Give user feedback
        self.lbl_preview.setText(self.lbl_preview.text() + "<br><i>Added to tier heat.</i>")

    def _clear(self):
        self.ed_name.clear()
        self.sp_current.setValue(0.0)
        self.sp_len.setValue(0.0)
        self.cb_inst.setCurrentIndex(0)
        self.cb_temp.setCurrentIndex(0)
        self.sp_factor.setValue(1.0)
        self._update_preview()
