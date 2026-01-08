# heatcalc/ui/project_meta_widget.py
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QLabel, QVBoxLayout, QHBoxLayout,
    QFrame, QStackedLayout, QSizePolicy, QSpacerItem, QGraphicsDropShadowEffect, QCheckBox
)
from PyQt5.QtWidgets import QDoubleSpinBox
from ..utils.qt import signals
THERMAL_FIELDS = [
    ("ambient_C", "Ambient temperature (°C)"),
]

from ..ui.tier_item import STANDARD_VENTS_CM2
from PyQt5.QtWidgets import QComboBox
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

        # --- Thermal assumptions ------------------------------------------------
        self.sp_ambient = QDoubleSpinBox()
        self.sp_ambient.setRange(-20.0, 80.0)
        self.sp_ambient.setDecimals(1)
        self.sp_ambient.setSuffix(" °C")

        amb = getattr(self._project.meta, "ambient_C", 40.0)
        self.sp_ambient.setValue(float(amb))

        self.sp_ambient.valueChanged.connect(self._on_ambient_changed)
        form.addRow("Ambient (°C):", self.sp_ambient)

        v.addWidget(form_host)
        # little bottom spacer inside card
        v.addItem(QSpacerItem(0, 4))

        # --- Altitude (IEC TR 60890 Annex K) -------------------------------
        self.sp_altitude = QDoubleSpinBox()
        self.sp_altitude.setRange(0.0, 4000.0)
        self.sp_altitude.setDecimals(0)
        self.sp_altitude.setSuffix(" m")

        alt = getattr(self._project.meta, "altitude_m", 0.0)
        self.sp_altitude.setValue(float(alt))

        self.sp_altitude.valueChanged.connect(self._on_altitude_changed)
        form.addRow("Altitude above sea level:", self.sp_altitude)

        # --- Default vent size (for recommendations) -------------------------------
        self.cmb_default_vent = QComboBox()
        self.cmb_default_vent.addItem("— None —", 0.0)

        for label, area in STANDARD_VENTS_CM2.items():
            self.cmb_default_vent.addItem(f"{label} ({area:.0f} cm²)", area)

        # initialise from project meta
        meta = self._project.meta
        if getattr(meta, "default_vent_area_cm2", 0.0) > 0.0:
            idx = self.cmb_default_vent.findData(meta.default_vent_area_cm2)
            if idx >= 0:
                self.cmb_default_vent.setCurrentIndex(idx)

        self.cmb_default_vent.currentIndexChanged.connect(self._on_default_vent_changed)
        form.addRow("Default vent (IEC):", self.cmb_default_vent)

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

        if hasattr(self, "sp_ambient"):
            amb = getattr(self._project.meta, "ambient_C", 40.0)
            self.sp_ambient.blockSignals(True)
            self.sp_ambient.setValue(float(amb))
            self.sp_ambient.blockSignals(False)

        if hasattr(self, "sp_altitude"):
            alt = getattr(self._project.meta, "altitude_m", 0.0)
            self.sp_altitude.blockSignals(True)
            self.sp_altitude.setValue(float(alt))
            self.sp_altitude.blockSignals(False)

        # >>> ADD THIS BLOCK <<<
        if hasattr(self, "cmb_default_vent"):
            area = float(getattr(self._project.meta, "default_vent_area_cm2", 0.0))
            self.cmb_default_vent.blockSignals(True)

            if area > 0.0:
                idx = self.cmb_default_vent.findData(area)
                if idx >= 0:
                    self.cmb_default_vent.setCurrentIndex(idx)
                else:
                    self.cmb_default_vent.setCurrentIndex(0)  # None
            else:
                self.cmb_default_vent.setCurrentIndex(0)

            self.cmb_default_vent.blockSignals(False)

    # --- Meta Set  ---------------------------------------------------------
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

    def _on_ambient_changed(self, val: float):
        try:
            self._project.meta.ambient_C = float(val)
            signals.project_changed.emit()
            signals.project_meta_changed.emit()
        except Exception:
            pass

    def _on_altitude_changed(self, val: float):
        try:
            self._project.meta.altitude_m = float(val)
            signals.project_changed.emit()
            signals.project_meta_changed.emit()
        except Exception:
            pass

    def _on_default_vent_changed(self, idx: int):
        try:
            area = float(self.cmb_default_vent.itemData(idx) or 0.0)
            label = self.cmb_default_vent.currentText().split(" ")[0] if area > 0 else None

            self._project.meta.default_vent_area_cm2 = area
            self._project.meta.default_vent_label = label

            signals.project_changed.emit()
            signals.project_meta_changed.emit()
        except Exception:
            pass

    def get_meta(self) -> dict:
        out = {}
        for key, _label in self.FIELDS:
            le = self._edits.get(key)
            out[key] = le.text().strip() if le is not None else ""
        return out
