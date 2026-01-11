from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout,
    QDoubleSpinBox, QLabel, QTextBrowser
)
from PyQt5.QtCore import Qt

from ..utils.qt import signals


# ---------------------------------------------------------------------
# IP mesh derating table
#
# Values represent DESIGN effective open-area factors applied to the
# manufacturer-specified free inlet area.
#
# IP3X:
#   - Geometric free area only
#
# IP4X:
#   - Geometric free area
#   - Further reduced by 20% safety factor to account for increased
#     airflow resistance associated with small hydraulic diameters
# ---------------------------------------------------------------------
IP_MESH_TABLE = {
    2: (None, 1.00),   # IP2X: no mesh derating
    3: (2.0, 0.55),    # IP3X: geometric free area
    4: (0.8, 0.46),    # IP4X: geometric free area × 0.8 safety factor
}


class LouvreDefinitionTab(QWidget):
    """
    Defines louvre visual geometry AND manufacturer-specified
    free inlet area per louvre (authoritative).
    """

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project

        root = QVBoxLayout(self)

        # ---------------- Visual geometry ----------------
        gb_geom = QGroupBox("Louvre visual geometry (drawing only)")
        f = QFormLayout(gb_geom)

        self.sp_width = QDoubleSpinBox()
        self.sp_width.setRange(1.0, 500.0)
        self.sp_width.setSuffix(" mm")

        self.sp_height = QDoubleSpinBox()
        self.sp_height.setRange(1.0, 200.0)
        self.sp_height.setSuffix(" mm")

        f.addRow("Draw width (X):", self.sp_width)
        f.addRow("Draw height (Y):", self.sp_height)

        root.addWidget(gb_geom)

        # ---------------- Thermal inlet area ----------------
        gb_area = QGroupBox("Thermal inlet area (manufacturer specified)")
        f2 = QFormLayout(gb_area)

        self.sp_inlet = QDoubleSpinBox()
        self.sp_inlet.setRange(0.1, 1e6)
        self.sp_inlet.setDecimals(2)
        self.sp_inlet.setSuffix(" cm²")

        f2.addRow("Free inlet area per louvre:", self.sp_inlet)

        root.addWidget(gb_area)

        # ---------------- Layout constraints ----------------
        gb_margin = QGroupBox("Layout constraints")
        f3 = QFormLayout(gb_margin)

        self.sp_edge = QDoubleSpinBox()
        self.sp_edge.setRange(0.0, 100.0)
        self.sp_edge.setSuffix(" mm")

        self.sp_spacing = QDoubleSpinBox()
        self.sp_spacing.setRange(0.0, 100.0)
        self.sp_spacing.setSuffix(" mm")

        f3.addRow("Edge margin:", self.sp_edge)
        f3.addRow("Louvre spacing:", self.sp_spacing)

        root.addWidget(gb_margin)

        # ---------------- IP mesh ----------------
        gb_ip = QGroupBox("IP protection mesh (derating only)")
        f4 = QFormLayout(gb_ip)

        self.lbl_ip = QLabel("-")
        self.lbl_aperture = QLabel("-")
        self.lbl_factor = QLabel("-")

        f4.addRow("IP rating:", self.lbl_ip)
        f4.addRow("Mesh aperture:", self.lbl_aperture)
        f4.addRow("Open area factor:", self.lbl_factor)

        root.addWidget(gb_ip)

        # ---------------- Justification panel ----------------
        gb_just = QGroupBox("Derating justification (rendered)")
        vj = QVBoxLayout(gb_just)

        self.txt_just = QTextBrowser()
        self.txt_just.setReadOnly(True)
        self.txt_just.setMinimumHeight(180)
        self.txt_just.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        vj.addWidget(self.txt_just)
        root.addWidget(gb_just)

        root.addStretch(1)

        # signals
        for sp in (
            self.sp_width, self.sp_height,
            self.sp_inlet,
            self.sp_edge, self.sp_spacing
        ):
            sp.valueChanged.connect(self._commit)

        signals.project_meta_changed.connect(self.refresh)
        self.refresh()

    # -------------------------------------------------

    def refresh(self):
        d = self.project.meta.louvre_definition

        self._block(True)
        self.sp_width.setValue(d["draw_width_mm"])
        self.sp_height.setValue(d["draw_height_mm"])
        self.sp_inlet.setValue(d["inlet_area_cm2"])
        self.sp_edge.setValue(d["edge_margin_mm"])
        self.sp_spacing.setValue(d["louvre_spacing_mm"])
        self._block(False)

        ip = int(getattr(self.project.meta, "ip_rating_n", 2))
        aperture, factor = IP_MESH_TABLE.get(ip, (None, 1.0))

        self.lbl_ip.setText(f"IP{ip}X")
        self.lbl_aperture.setText(
            "None" if aperture is None else f"{aperture:.1f} mm"
        )
        self.lbl_factor.setText(f"{factor:.2f}")

        self._update_justification_text(ip)

    def _update_justification_text(self, ip: int) -> None:
        if ip <= 2:
            self.txt_just.setHtml("""
                <p><b>IP2X:</b> No mesh derating applied.</p>
                <p>The manufacturer-specified free inlet area is used directly.</p>
            """)
            return

        if ip == 3:
            a, d = 2.0, 0.7
            probe = "Ø2.5 mm rigid probe (IP3X)"
            phi_geom = (a / (a + d)) ** 2
            phi_design = phi_geom

            safety_text = "<p>No additional safety factor applied.</p>"

        elif ip == 4:
            a, d = 0.8, 0.25
            probe = "Ø1.0 mm rigid wire (IP4X)"
            phi_geom = (a / (a + d)) ** 2
            phi_design = phi_geom * 0.8

            safety_text = """
                <p>
                A further <b>20% safety factor</b> is applied to the geometric
                free-area factor to conservatively account for increased airflow
                resistance associated with small hydraulic diameters, viscous
                losses, and blockage sensitivity typical of IP4X meshes.
                </p>
            """

        else:
            self.txt_just.setHtml("<p>IP rating not supported.</p>")
            return

        reduction_pct = (1.0 - phi_design) * 100.0

        html = f"""
        <p><b>Probe requirement:</b> {probe}</p>

        <p><b>Geometric model:</b></p>
        <p style="margin-left:16px;">
            φ = (a / (a + d))<sup>2</sup>
        </p>

        <ul>
            <li>Clear aperture, a = {a:.2f} mm</li>
            <li>Wire diameter, d = {d:.2f} mm</li>
        </ul>

        <p>
            Geometric free-area factor:<br/>
            φ<sub>geom</sub> = {phi_geom:.2f}
        </p>

        {safety_text}

        <p>
            <b>Design open-area factor:</b> φ<sub>design</sub> = {phi_design:.2f}<br/>
            <b>Equivalent CSA reduction:</b> {reduction_pct:.1f}%
        </p>

        <p><i>
            Note: This factor represents an effective design allowance for
            ventilation calculations and is not a catalogue free-area value.
        </i></p>
        """

        self.txt_just.setHtml(html)

    def _block(self, on: bool):
        for sp in (
            self.sp_width, self.sp_height,
            self.sp_inlet,
            self.sp_edge, self.sp_spacing
        ):
            sp.blockSignals(on)

    def _commit(self):
        d = self.project.meta.louvre_definition

        d["draw_width_mm"] = self.sp_width.value()
        d["draw_height_mm"] = self.sp_height.value()
        d["inlet_area_cm2"] = self.sp_inlet.value()
        d["edge_margin_mm"] = self.sp_edge.value()
        d["louvre_spacing_mm"] = self.sp_spacing.value()

        from ..utils.debug import debug_meta
        debug_meta(self.project, "AFTER _commit()")

        signals.project_changed.emit()

    def set_locked(self, locked: bool, message: str | None = None):
        for sp in (
            self.sp_width,
            self.sp_height,
            self.sp_inlet,
            self.sp_edge,
            self.sp_spacing,
        ):
            sp.setEnabled(not locked)

        self.setToolTip(message if locked and message else "")

    def refresh_with_guard(self, switchboard_tab):
        if switchboard_tab.any_tiers_ventilated():
            names = ", ".join(switchboard_tab.ventilated_tier_names())
            self.set_locked(
                True,
                f"Louvre definition is locked while tiers have vents enabled.\n"
                f"Ventilated tiers: {names}"
            )
            return

        self.set_locked(False)
        self.refresh()
