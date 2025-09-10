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
IEC60890_ROWS: List[Tuple[str, str]] = [
    ("a)", "The power loss data for all built-in components is available from the component manufacturer"),
    ("b)", "There is an approximately even distribution of power losses inside the enclosure"),
    ("c)", "The rated current of the circuits of the ASSEMBLY to be verified (see 10.10.1) shall not exceed 80 % of the "
           "rated conventional free air thermal current (Ith) if any, or the rated current (In) of the switching devices "
           "and electrical components included in the circuit. Circuit protection devices shall be selected to ensure adequate "
           "protection to outgoing circuits, e.g. thermal motor protection devices at the calculated temperature in the ASSEMBLY"),
    ("d)", "The mechanical parts and the installed equipment are so arranged that air circulation is not significantly impeded."),
    ("e)", "Conductors carrying currents in excess of 200 A, and the adjacent structural parts are so arranged that eddy-current "
           "and hysteresis losses are minimized."),
    ("f)", "All conductors shall have a minimum cross-sectional area based on 125 % of the permitted current rating of the "
           "associated circuit. Selection of cables shall be in accordance with IEC 60364-5-52. Examples on how to adapt this "
           "standard for conditions inside an ASSEMBLY are given in Annex H. The cross-section of bars shall be as tested or as "
           "given in Annex N. Where the device manufacturer specifies a conductor with a larger cross-sectional area this shall be used"),
    ("g)", "For enclosures with natural ventilation, the cross-section of the air outlet openings is at least 1.1 times the cross "
           "section of the air inlet openings."),
    ("h)", "There are no more than three horizontal partitions in the ASSEMBLY or a section of an ASSEMBLY"),
    ("i)", "For enclosures with compartments and natural ventilation the cross section of the ventilating openings in each "
           "horizontal partition is at least 50 % of the horizontal cross section of the compartment"),
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
        answers = self._collect()
        nonc = [a for a in answers if a.result == "Non-Compliant"]
        if nonc:
            # Build a user-facing explanation
            details = "\n\n".join([f"{a.item}  {a.condition}" for a in nonc])
            msg = (
                "Cannot use IEC-60890 for Temperature Rise Calculations because "
                "the following condition(s) are Non-Compliant:\n\n" + details
            )
            QMessageBox.critical(self, "IEC 60890 Preconditions Not Met", msg)
            return  # keep dialog open
        # Store to instance for caller retrieval
        self.answers = [asdict(a) for a in answers]
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
    """
    Call this from your 'Export/Print' action.
    - If previous answers exist and contain no 'Non-Compliant', asks user if they still apply.
    - If yes, returns previous answers.
    - Else, opens the dialog.
    Returns None only if the user cancels.
    """
    prev = _extract_saved_list(project_meta)

    if prev and all(a.result != "Non-Compliant" for a in prev):
        ret = QMessageBox.question(
            parent,
            "IEC 60890 Preconditions",
            "Use the previously saved IEC 60890 pre-condition responses?\n\n"
            "You can press 'No' to review or change them.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes
        )
        if ret == QMessageBox.Yes:
            return [asdict(a) for a in prev]
        elif ret == QMessageBox.Cancel:
            return None

    dlg = IEC60890ChecklistDialog(parent, previous=prev)
    if dlg.exec_() == QDialog.Accepted:
        return dlg.answers  # list[dict]
    return None
