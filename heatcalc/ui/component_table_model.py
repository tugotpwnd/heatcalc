# heatcalc/ui/component_table_model.py
from __future__ import annotations
from typing import List, Any
from PyQt5.QtCore import Qt, QAbstractTableModel, QVariant, QModelIndex
from ..core.component_store import ComponentRow  # NOTE: relative import up one level

HEADERS = ["Category", "Part #", "Description", "Heat (W)", "Max Temp (°C)", "Rated (A)", "Derating Start (°C)", "Function"]  # Extended

class ComponentTableModel(QAbstractTableModel):
    def __init__(self, rows: List[ComponentRow]):
        super().__init__()
        self._rows = rows

    def set_rows(self, rows: List[ComponentRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return HEADERS[section]
        return section + 1

    def data(self, index: QModelIndex, role=Qt.DisplayRole) -> Any:
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0: return getattr(row, "category", "Component")
            if col == 1: return getattr(row, "part_number", "")
            if col == 2: return getattr(row, "description", "")
            if col == 3: return f"{float(getattr(row, 'heat_w', 0.0)):.1f}"
            if col == 4: return str(int(getattr(row, "max_temp_C", 70)))

            if col == 5:
                val = getattr(row, 'rated_current_A', None)
                return f"{float(val):.0f}" if val is not None else ""

            if col == 6:
                val = getattr(row, 'derating_temp_start_C', None)
                return f"{float(val):.0f}" if val is not None else ""

            if col == 7: return str(getattr(row, "derating_function", ""))
        if role == Qt.UserRole:
            return row
        return QVariant()

    def row_at(self, row_idx: int) -> ComponentRow:
        return self._rows[row_idx]

    def all_categories(self) -> List[str]:
        return sorted({getattr(r, "category", "Component") for r in self._rows if getattr(r, "category", None)})
