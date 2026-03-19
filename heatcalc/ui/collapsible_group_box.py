from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QSizePolicy
)
from PyQt5.QtGui import QFontMetrics

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
