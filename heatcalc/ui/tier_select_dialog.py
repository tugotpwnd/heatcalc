from __future__ import annotations

import re
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QPushButton,
    QDialogButtonBox,
)


def natural_tier_key(tag: str):
    """
    Natural, safe sort key:
    - numbers sort numerically
    - text sorts alphabetically
    - never mixes int/str comparisons
    """
    text = str(tag or "").strip()
    parts = re.findall(r"\d+|[A-Za-z]+", text)

    key = []
    for p in parts:
        if p.isdigit():
            key.append((0, int(p)))      # numbers first, numeric compare
        else:
            key.append((1, p.upper()))  # then text, case-insensitive
    return key



class TierSelectDialog(QDialog):
    def __init__(self, parent=None, tier_tags: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("Select tiers to include")
        self.resize(420, 520)

        self._selected: List[str] = []

        v = QVBoxLayout(self)

        lbl = QLabel(
            "Select the tiers to calculate and include in the report.\n"
            "(This does not change the model — it only filters the exported PDF.)"
        )
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        self.listw = QListWidget(self)
        self.listw.setSelectionMode(self.listw.NoSelection)
        v.addWidget(self.listw, 1)

        tags = list(tier_tags or [])
        tags.sort(key=natural_tier_key)
        for t in tags:
            it = QListWidgetItem(str(t))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.listw.addItem(it)

        row = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_none = QPushButton("Select None")
        btn_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        btn_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        row.addWidget(btn_all)
        row.addWidget(btn_none)
        row.addStretch(1)
        v.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _set_all(self, state: Qt.CheckState):
        for i in range(self.listw.count()):
            self.listw.item(i).setCheckState(state)

    def _accept(self):
        selected: List[str] = []
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            if it.checkState() == Qt.Checked:
                selected.append(it.text().strip())
        self._selected = selected
        self.accept()

    @property
    def selected_tier_tags(self) -> List[str]:
        return list(self._selected)


def select_tiers_for_report(parent, tier_tags: List[str]) -> Optional[List[str]]:
    """Returns list[str] or None if cancelled."""
    dlg = TierSelectDialog(parent=parent, tier_tags=tier_tags)
    if dlg.exec_() == QDialog.Accepted:
        return dlg.selected_tier_tags
    return None
