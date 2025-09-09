# heatcalc/ui/project_meta_widget.py
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QLabel, QVBoxLayout, QHBoxLayout,
    QFrame, QStackedLayout, QSizePolicy, QSpacerItem, QGraphicsDropShadowEffect
)

from ..core.models import Project
from ..utils.resources import get_resource_path


class ProjectMetaWidget(QWidget):
    """
    Modern "card on image" editor for Project.meta.
    Background: heatcalc/assets/menuwindow.png (scaled).
    Card: white, rounded, shadow; fixed max width so inputs don't stretch.
    """
    FIELDS = [
        ("job_number", "Job #"),
        ("project_title", "Project title"),
        ("enclosure", "Enclosure"),
        ("designer_name", "Designer"),
        ("date", "Date"),
        ("revision", "Revision"),
    ]

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self._project = project
        self._edits: dict[str, QLineEdit] = {}

        # ----- Background layer ------------------------------------------------
        bg = QLabel(self)
        bg.setObjectName("bg")
        # load your background image; safe if it doesn't exist
        # Background image
        bg_path = Path(get_resource_path("heatcalc/assets/menuwindow.png"))
        if bg_path.exists():
            bg.setPixmap(QPixmap(str(bg_path)))  # ← cast to str
            bg.setScaledContents(True)
        else:
            bg.setStyleSheet(
                "#bg { background: qlineargradient(x1:0,y1:0, x2:1,y2:1, stop:0 #eef3f7, stop:1 #dfe7ef); }")



        # ----- Foreground card -------------------------------------------------
        card = QFrame(self)
        card.setObjectName("card")
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        card.setMaximumWidth(720)  # <- stops inputs stretching full width
        card.setFrameShape(QFrame.NoFrame)

        # Drop shadow for the card
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(Qt.black)
        card.setGraphicsEffect(shadow)

        # Card layout (logo + form)
        v = QVBoxLayout(card)
        v.setContentsMargins(28, 28, 28, 28)
        v.setSpacing(16)

        # Logo row (optional)
        logo_row = QWidget(card)
        lr = QHBoxLayout(logo_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(10)

        logo_label = QLabel()
        logo_label.setObjectName("logo")
        # Try a few likely logo assets; choose whichever exists first
        for candidate in [
            "heatcalc/data/title.png",
            "heatcalc/data/logo.png",
            "heatcalc/assets/Logo.ico",  # if you only have an .ico
        ]:
            p = Path(get_resource_path(candidate))
            if p.exists():
                pm = QPixmap(str(p))  # ← cast to str
                if not pm.isNull():
                    logo_label.setPixmap(pm)
                    logo_label.setScaledContents(True)
                    logo_label.setFixedHeight(56)
                    break

        title = QLabel("IEC 60890 Heat Calc")
        title.setObjectName("title")

        lr.addWidget(logo_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
        lr.addStretch(1)
        lr.addWidget(title, 0, Qt.AlignRight | Qt.AlignVCenter)

        v.addWidget(logo_row)

        # Form grid
        form_host = QWidget(card)
        form = QFormLayout(form_host)
        form.setFormAlignment(Qt.AlignTop)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        # initialize from project.meta
        meta_dict = {}
        try:
            meta_dict = asdict(project.meta)
        except Exception:
            meta_dict = {k: getattr(project.meta, k, "") for k, _ in self.FIELDS}

        for key, label in self.FIELDS:
            le = QLineEdit(str(meta_dict.get(key, "")))
            le.setObjectName("metaEdit")
            le.setPlaceholderText(label)
            # live-back to project.meta
            le.textChanged.connect(lambda text, k=key: self._on_text(k, text))
            form.addRow(label + ":", le)
            self._edits[key] = le

        v.addWidget(form_host)

        # little bottom spacer inside card
        v.addItem(QSpacerItem(0, 4))

        # ----- Root layout (bg label + centered card) -------------------------
        # keep a reference so we can resize it later
        self._bg = bg
        self._bg.lower()  # ensure it's behind everything

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(0)

        # center the card
        root.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, 0, Qt.AlignCenter)
        row.addStretch(1)
        root.addLayout(row)
        root.addStretch(2)

        # ----- Styling ---------------------------------------------------------
        self.setStyleSheet("""
            #card {
                background: white;
                border-radius: 16px;
            }
            #title {
                font-family: 'Segoe UI', 'Helvetica', 'Arial';
                font-size: 20px;
                font-weight: 600;
                color: #0D4FA2;
            }
            QLabel {
                font-size: 12px;
            }
            QFormLayout > QLabel { /* Qt can't target this directly; kept for reference */ }
            QLineEdit#metaEdit {
                padding: 8px 10px;
                border: 1px solid #D9DEE5;
                border-radius: 8px;
                background: #FAFCFF;
                selection-background-color: #cfe3ff;
            }
            QLineEdit#metaEdit:focus {
                border: 1px solid #4C94FF;
                background: #FFFFFF;
            }
        """)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_bg") and self._bg is not None:
            self._bg.setGeometry(self.rect())

    # --- data wiring ---------------------------------------------------------

    def _on_text(self, key: str, text: str):
        if not hasattr(self._project, "meta") or self._project.meta is None:
            return
        try:
            setattr(self._project.meta, key, text)
        except Exception:
            pass

    def refresh_from_project(self):
        for key, le in self._edits.items():
            val = getattr(self._project.meta, key, "")
            if le.text() != str(val):
                le.blockSignals(True)
                le.setText(str(val))
                le.blockSignals(False)

    def set_meta(self, meta: dict):
        for key, _label in self.FIELDS:
            val = str(meta.get(key, ""))
            le = self._edits.get(key)
            if le is None:
                continue
            le.blockSignals(True)
            le.setText(val)
            le.blockSignals(False)
            try:
                setattr(self._project.meta, key, val)
            except Exception:
                pass

    def get_meta(self) -> dict:
        out = {}
        for key, _label in self.FIELDS:
            le = self._edits.get(key)
            out[key] = le.text().strip() if le is not None else ""
        return out
