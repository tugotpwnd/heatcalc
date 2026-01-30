# heatcalc/ui/iec60890_dialog.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QComboBox, QDialogButtonBox, QMessageBox, QWidget, QApplication
)


IEC60890_TITLE = (
    "The conditions in the following table (in line with clause 10.10.4.3.1) "
    "shall be fulfilled in order to apply the calculation methodology in IEC 60890."
)

# (item, assessment condition) — verbatim from your prompt
IEC60890_ROWS = [
    # ---- Clause 5.1 – Enclosure construction ----
    ("5.1-1", "The enclosure is metallic (steel, aluminium or stainless steel) and coated on both internal and external surfaces."),
    ("5.1-2", "The enclosure is of single-layer construction or multiple layers without air gaps."),
    ("5.1-3", "The enclosure or section contains no more than five horizontal partitions."),

    # ---- Ventilation assumptions ----
    ("5.1-4", "The enclosure is either unventilated or provided only with free natural ventilation openings."),
    ("5.1-5", "No additional filters are fitted to ventilation openings beyond whats required my the IP rating (Annex E)."),
    ("5.1-6", "For natural ventilation, the outlet opening area is at least 110 % of the inlet opening area."),
    ("5.1-7", "The minimum total inlet opening area is not less than 10 cm²."),
    ("5.1-8", "For IP5X or higher, ventilation openings are ignored in the calculation."),
    ("5.1-9", "For IP ratings lower than IP5X, the effective free opening area is used."),
    ("5.1-10", "Where compartments exist, each horizontal partition provides free ventilation openings of at least 50 % of its cross-sectional area."),

    # ---- Power loss inputs ----
    ("5.1-11", "Power losses include switchgear, interconnecting conductors, busbars, and electronic devices."),
    # ---- Environmental assumptions ----
    ("5.1-12", "The enclosure is not exposed to solar radiation."),

    ("5.1-13", "Power loss data for all built-in components is available from the manufacturer."),
    ("5.1-14", "Power losses are approximately evenly distributed within the enclosure."),

    # ---- Clause 10.10.4.3 – Calculation limits ----
    ("10.10-1", "The rated current of the assembly does not exceed 1600 A."),
    ("10.10-2", "All circuits operate at no more than 80 % of their free-air thermal current rating (Ith/In)."),
    ("10.10-3", "Circuit protection devices are suitable for the calculated internal temperature."),
    ("10.10-4", "Mechanical layout does not significantly impede air circulation."),
    ("10.10-5", "Conductors above 200 A are arranged to minimise eddy-current and hysteresis losses."),
    ("10.10-6", "Conductor cross-sections are sized to at least 125 % of permitted current rating (IEC 60364-5-52)."),
    ("10.10-7", "For calculation verification, no more than three horizontal partitions are present."),
]


CHOICES = ["Compliant", "N/A", "Non-Compliant"]

@dataclass
class IEC60890Answer:
    item: str
    condition: str
    result: str  # one of CHOICES

def normalize_saved_list(raw: object) -> List[IEC60890Answer]:
    out: List[IEC60890Answer] = []
    if isinstance(raw, list):
        for row in raw:
            try:
                out.append(IEC60890Answer(
                    item=str(row.get("item", "")),
                    condition=str(row.get("condition", "")),
                    result=str(row.get("result", "Compliant")),
                ))
            except Exception:
                continue
    return out

class IEC60890ChecklistDialog(QDialog):
    def __init__(self, parent=None, previous=None):
        super().__init__(parent)
        self.setWindowTitle("IEC 60890 Preconditions")
        self.resize(980, 520)

        # --- font (Qt uses system fonts; "Times New Roman" matches ReportLab Times-Roman closely)
        app_font = QFont("Times New Roman", 10)
        self.setFont(app_font)

        v = QVBoxLayout(self)

        lbl = QLabel(IEC60890_TITLE)
        lbl.setWordWrap(True)  # <-- wrap title
        v.addWidget(lbl)

        self.table = QTableWidget(self)
        self.table.setFont(app_font)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Item", "Assessment Conditions", "Compliance Check"])
        self.table.verticalHeader().setVisible(False)
        self.table.setRowCount(len(IEC60890_ROWS))
        self.table.setWordWrap(True)  # <-- allow wrapping
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        # sensible widths; last column can stretch if dialog resizes
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 720)
        self.table.setColumnWidth(2, 160)
        self.table.horizontalHeader().setStretchLastSection(True)

        prev_map = {a.item: a.result for a in (previous or [])}

        for r, (item, cond) in enumerate(IEC60890_ROWS):
            it_item = QTableWidgetItem(item)
            it_item.setFlags(Qt.ItemIsEnabled)
            it_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 0, it_item)

            it_cond = QTableWidgetItem(cond)
            it_cond.setFlags(Qt.ItemIsEnabled)
            it_cond.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
            self.table.setItem(r, 1, it_cond)

            cb = QComboBox(self.table)
            cb.setFont(app_font)
            cb.addItems(CHOICES)
            default = prev_map.get(item, "Compliant")
            if default not in CHOICES:
                default = "Compliant"
            cb.setCurrentText(default)
            self.table.setCellWidget(r, 2, cb)

        # Let Qt compute row heights based on wrapped content
        self.table.resizeRowsToContents()

        v.addWidget(self.table)

        btns = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, Qt.Horizontal, self)
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _collect(self) -> List[IEC60890Answer]:
        ans: List[IEC60890Answer] = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0).text().strip()
            cond = self.table.item(r, 1).text().strip()
            cb: QComboBox = self.table.cellWidget(r, 2)  # type: ignore
            res = cb.currentText()
            ans.append(IEC60890Answer(item=item, condition=cond, result=res))
        return ans

    def _validate_and_accept(self):
        self.answers = [asdict(a) for a in self._collect()]
        self.accept()


# --- replace these two functions in heatcalc/ui/iec60890_dialog.py ---

def _extract_saved_list(container) -> List[IEC60890Answer]:
    """
    Accepts either:
      • a dict-like project meta (with key 'iec60890_checklist'), or
      • a dataclass/object with attribute 'iec60890_checklist'
    Returns a normalized list[IEC60890Answer].
    """
    raw = None
    try:
        # dict-like path
        if isinstance(container, dict):
            raw = container.get("iec60890_checklist")
        else:
            # dataclass / object path
            raw = getattr(container, "iec60890_checklist", None)
    except Exception:
        raw = None
    return normalize_saved_list(raw)

def ensure_checklist_before_report(parent: QWidget, project_meta) -> Optional[List[Dict]]:
    prev = _extract_saved_list(project_meta)

    if prev:
        ret = QMessageBox.question(
            parent,
            "IEC 60890 Preconditions",
            "Use previously saved IEC 60890 precondition responses?\n\n"
            "Select 'No' to review or update them before generating the report.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes
        )

        if ret == QMessageBox.Cancel:
            return None

        if ret == QMessageBox.No:
            dlg = IEC60890ChecklistDialog(parent, previous=prev)
            if dlg.exec_() != QDialog.Accepted:
                return None
            answers = dlg.answers
        else:
            answers = [asdict(a) for a in prev]
    else:
        dlg = IEC60890ChecklistDialog(parent)
        if dlg.exec_() != QDialog.Accepted:
            return None
        answers = dlg.answers

    non_compliant = [
        a for a in answers
        if a.get("result") == "Non-Compliant"
    ]

    if non_compliant:
        details = "\n".join(
            f"• {a['item']} – {a['condition']}"
            for a in non_compliant
        )

        msg = (
            "One or more IEC 60890 calculation assumptions have been marked as "
            "NON-COMPLIANT.\n\n"
            "IEC 60890 permits calculation outside strict assumptions only where "
            "engineering judgement is applied and deviations are justified in the "
            "design documentation.\n\n"
            "The following deviations have been identified:\n\n"
            f"{details}\n\n"
            "By continuing, you confirm that these deviations will be addressed "
            "and justified separately in the engineering report or design decision register.\n\n"
            "Do you wish to continue with report generation?"
        )

        ret = QMessageBox.warning(
            parent,
            "IEC 60890 – Engineering Judgement Required",
            msg,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel
        )

        if ret != QMessageBox.Yes:
            return None

    return answers
