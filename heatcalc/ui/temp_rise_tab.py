from __future__ import annotations
from typing import List, Tuple, Dict, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSplitter,
    QListWidget, QListWidgetItem, QGroupBox, QFormLayout, QSpinBox, QLabel,
    QLineEdit, QCheckBox
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from ..core.louvre_calc import (
    tier_effective_inlet_area_cm2,
    tier_max_effective_inlet_area_cm2,
)
from .tier_item import TierItem
from .designer_view import GRID
from ..core import curvefit
from ..core.iec60890_calc import calc_tier_iec60890

# Compliance colours (IEC 60890 – explicit states)
COL_COMPLIANT_TEMP        = QColor(0, 150, 0)    # Green: base IEC compliant
COL_COMPLIANT_DISS        = QColor(230, 140, 0)  # Orange: compliant via enclosure dissipation
COL_COMPLIANT_VENT_SEL    = QColor(0, 120, 200)  # Blue: compliant via SELECTED ventilation
COL_VENT_OPTIONAL         = QColor(150, 150, 150)# Grey: not compliant, ventilation could help
COL_NON_COMPLIANT         = QColor(200, 0, 0)    # Red: active cooling required

class TempRiseTab(QWidget):
    """
    Computes temperature rise per tier using IEC 60890-style factors and visualizes a
    characteristic curve for each selected tier.

    Additions in this version:
    - Ambient temperature must be prvided before calculation is enabled.
    - Curves are plotted as ABSOLUTE temperatures (ambient + Δt0.5, ambient + Δt1.0).
    - A per-tier result panel shows final temperatures at 0.5t and 1.0t and cooling guidance.
    - Tier entries are coloured GREEN if within max temperature, RED if exceeding.
    - If exceeding, an estimated minimum airflow (m³/h) is computed to meet the max temp.
    """

    def __init__(self, scene_provider, project, parent=None):
        super().__init__(parent)
        self.project = project
        self._scene_provider = scene_provider  # callable → returns current scene
        self._results: List[Dict] = []
        self.ambient_C: Optional[float] = None

        # -------- Left: controls + tier list --------
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(6, 6, 6, 6)
        left_l.setSpacing(8)

        # Options
        opts = QGroupBox("Options")
        of = QFormLayout(opts)

        self.lbl_ambient = QLabel()
        self.lbl_ambient.setStyleSheet("color: #888;")
        of.addRow("Ambient:", self.lbl_ambient)

        # Calculate button (disabled until ambient provided)
        self.btn_calc = QPushButton("Calculate")
        self.btn_calc.clicked.connect(self.calculate_all)

        left_l.addWidget(opts)
        left_l.addWidget(self.btn_calc)

        # Tier list (so you can click through the plots quickly)
        self.tier_list = QListWidget()
        self.tier_list.currentRowChanged.connect(self._show_plot_for_row)
        left_l.addWidget(QLabel("Tiers"))
        left_l.addWidget(self.tier_list, 1)

        # -------- Compliance legend ---------------------------------
        legend = QGroupBox("Compliance legend")
        lf = QVBoxLayout(legend)
        lf.setContentsMargins(8, 6, 8, 6)

        def _legend_row(col: QColor, text: str) -> QWidget:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            swatch = QLabel("■")
            swatch.setStyleSheet(f"color: {col.name()}; font-size: 14px;")
            lbl = QLabel(text)
            h.addWidget(swatch)
            h.addWidget(lbl)
            h.addStretch()
            return w

        lf.addWidget(_legend_row(COL_COMPLIANT_TEMP, "Base IEC temperature compliant"))
        lf.addWidget(_legend_row(COL_COMPLIANT_DISS, "Compliant via enclosure dissipation"))
        lf.addWidget(_legend_row(COL_COMPLIANT_VENT_SEL, "Compliant via selected ventilation"))
        lf.addWidget(_legend_row(COL_VENT_OPTIONAL, "Ventilation optional to achieve compliance"))
        lf.addWidget(_legend_row(COL_NON_COMPLIANT, "Active cooling required"))

        left_l.addWidget(legend)

        # Results placeholder under the tier list (final temps + cooling guidance)
        results = QGroupBox("Results (selected tier)")
        rf = QFormLayout(results)
        self.lbl_final_mid = QLabel("–")
        self.lbl_final_top = QLabel("–")
        self.lbl_guidance = QLabel("–")
        self.lbl_guidance.setWordWrap(True)
        rf.addRow("Final at 0.5t:", self.lbl_final_mid)
        rf.addRow("Final at 1.0t:", self.lbl_final_top)
        rf.addRow("Cooling:", self.lbl_guidance)
        left_l.addWidget(results)

        # -------- Right: plotting area (one axes; we redraw for selection) ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(6, 6, 6, 6)
        self.fig = Figure(figsize=(5, 4), constrained_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self._title = QLabel("")
        self._title.setAlignment(Qt.AlignCenter)
        rv.addWidget(self._title)
        rv.addWidget(self.canvas, 1)

        # -------- Layout ------------------------------------------------------
        split = QSplitter(self)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)  # left
        split.setStretchFactor(1, 3)  # right
        lay = QHBoxLayout(self)
        lay.addWidget(split)

        self.refresh_from_project()

    # --------------------------------------------------------------------- UI
    def refresh_from_project(self):
        amb = float(getattr(self.project.meta, "ambient_C", 40.0))
        self.lbl_ambient.setText(f"{amb:.1f} °C")

    # --------------------------------------------------------------------- calc
    def calculate_all(self):
        """Compute Δt values for each tier and populate the list + first plot.
        Also colours tier entries based on maximum temperature compliance.
        """
        scene = self._scene_provider()
        if scene is None:
            return  # or safely exit

        tiers = [it for it in scene.items() if isinstance(it, TierItem)]

        self.tier_list.clear()
        self._results.clear()

        amb = float(self.project.meta.ambient_C)
        project_altitude_m=float(self.project.meta.altitude_m)


        for t in tiers:


            louvre_def = self.project.meta.louvre_definition
            ip_rating_n = int(self.project.meta.ip_rating_n)

            inlet_area_cm2 = tier_effective_inlet_area_cm2(
                tier=t,
                louvre_def=louvre_def,
                ip_rating_n=ip_rating_n,
            )

            vent_test_area_cm2 = tier_max_effective_inlet_area_cm2(
                tier=t,
                louvre_def=louvre_def,
                ip_rating_n=ip_rating_n,
            )

            res = calc_tier_iec60890(
                tier=t,
                tiers=tiers,
                wall_mounted=t.wall_mounted,
                inlet_area_cm2=inlet_area_cm2,
                ambient_C=amb,
                altitude_m=project_altitude_m,
                enclosure_k_W_m2K=float(self.project.meta.enclosure_k_W_m2K),
                allow_material_dissipation=bool(self.project.meta.allow_material_dissipation),
                ip_rating_n=ip_rating_n,
                vent_test_area_cm2=vent_test_area_cm2,
            )

            # compute final absolute temp
            # calc already returns absolute temperatures
            res["tier"] = t
            res["vent"] = t.is_ventilated
            res["curve"] = t.curve_no
            res["name"] = t.name

            self._results.append(res)

            # Use the tier's effective limit (manual or auto-lowest-component)
            eff_limit = int(getattr(t, "effective_max_temp_C", lambda: int(getattr(t, "max_temp_C", 70)))())
            # ------------------------------------------------------------
            # Determine IEC compliance mode (explicit + selected)
            # ------------------------------------------------------------

            if res["compliant_top"]:
                if t.is_ventilated:
                    colour = COL_COMPLIANT_VENT_SEL
                    compliance_tag = "IEC compliant (with ventilation selected)"
                else:
                    colour = COL_COMPLIANT_TEMP
                    compliance_tag = "IEC compliant (base)"

            elif res["P_cooling"] <= 0.0:
                colour = COL_COMPLIANT_DISS
                compliance_tag = "Compliant via enclosure dissipation"

            elif (not t.is_ventilated) and res.get("vent_recommended"):
                colour = COL_VENT_OPTIONAL
                compliance_tag = "Ventilation optional for compliance"

            else:
                colour = COL_NON_COMPLIANT
                compliance_tag = "Active cooling required"

            mode = "auto" if getattr(t, "use_auto_component_temp", False) else "manual"
            if res.get("T_075") is not None:
                text = (
                    f"{t.name} — T(0.5t)={res['T_mid']:.1f}°C, "
                    f"T(0.75t)={res['T_075']:.1f}°C, "
                    f"T(1.0t)={res['T_top']:.1f}°C  "
                    f"[limit {eff_limit}°C, {mode} — {compliance_tag}]"

                )
            else:
                text = (
                    f"{t.name} — T(0.5t)={res['T_mid']:.1f}°C, "
                    f"T(1.0t)={res['T_top']:.1f}°C  "
                    f"[limit {eff_limit}°C, {mode} — {compliance_tag}]"
                )

            item = QListWidgetItem(text)
            item.setForeground(colour)
            self.tier_list.addItem(item)

        if self._results:
            self.tier_list.setCurrentRow(0)
            self._show_plot_for_row(0)
        else:
            self.ax.cla();
            self.canvas.draw_idle()
            self._title.setText("No tiers on the scene")

    def _show_plot_for_row(self, row: int):
        if not (0 <= row < len(self._results)):
            self.ax.cla()
            self.canvas.draw_idle()
            self._update_results_panel(None)
            return

        r = self._results[row]
        amb = float(self.project.meta.ambient_C)

        self.ax.cla()
        self.ax.grid(True, alpha=0.25)
        self.ax.set_xlabel("Temperature (°C)")
        self.ax.set_ylabel("Multiple of enclosure height")
        self.ax.set_ylim(0, 1.02)

        import numpy as np

        def _quad_bezier(p0, p1, p2, n=40):
            t = np.linspace(0.0, 1.0, n)
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
            return x, y

        # ------------------------------------------------------------
        # IEC 60890 temperature-rise characteristic (straight-line)
        # ------------------------------------------------------------

        # Ambient → mid-height
        x0, y0 = amb, 0.0
        x1, y1 = amb + r["dt_mid"], 0.5

        self.ax.plot([x0, x1], [y0, y1], lw=2)

        if r.get("dt_075") is not None:
            # --------------------------------------------------------
            # Ae ≤ 1.25 m² (IEC 60890 Fig. 2)
            # --------------------------------------------------------
            x075, y075 = amb + r["dt_075"], 0.75
            x2, y2 = amb + r["dt_top"], 1.0  # same x as x075

            # mid → 0.75
            self.ax.plot([x1, x075], [y1, y075], lw=2)

            # 0.75 → 1.0 (vertical)
            self.ax.plot([x075, x075], [y075, y2], lw=2)

            self.ax.scatter([x1, x075, x2], [y1, y075, y2], s=35, zorder=5)
            self.ax.annotate("T@0.5t", (x1, y1), xytext=(6, -6),
                             textcoords="offset points", fontsize=9)
            self.ax.annotate("T@0.75t", (x075, y075), xytext=(6, -6),
                             textcoords="offset points", fontsize=9)
            self.ax.annotate("T@1.0t", (x2, y2), xytext=(6, -6),
                             textcoords="offset points", fontsize=9)

        else:
            # --------------------------------------------------------
            # Ae > 1.25 m² (IEC 60890 Fig. 1)
            # --------------------------------------------------------
            x2, y2 = amb + r["dt_top"], 1.0

            self.ax.plot([x1, x2], [y1, y2], lw=2)

            self.ax.scatter([x1, x2], [y1, y2], s=35, zorder=5)
            self.ax.annotate("T@0.5t", (x1, y1), xytext=(6, -6),
                             textcoords="offset points", fontsize=9)
            self.ax.annotate("T@1.0t", (x2, y2), xytext=(6, -6),
                             textcoords="offset points", fontsize=9)

        # ------------------------------------------------------------
        # Title (unchanged)
        # ------------------------------------------------------------
        title_bits = [r["name"]]
        title_bits.append("Ventilated" if r["vent"] else "No ventilation")
        title_bits.append(
            f"Ae={r['Ae']:.3f} m², "
            f"P={r['P']:.1f} W, "
            f"k={r['k']:.3f}, "
            f"c={r['c']:.3f}, "
            f"x={r['x']:.3f}"
        )
        if r["f"] is not None:
            title_bits.append(f"f={r['f']:.3f}")
        if r["g"] is not None:
            title_bits.append(f"g={r['g']:.3f}")
        title_bits.append(f"Ambient={amb:.1f}°C")

        cf = r.get("curvefit", {})
        if cf.get("snapped"):
            used = []
            if cf.get("k"):
                used.append(f"k→Ae={cf['k']['used_ae']}")
            if cf.get("c"):
                used.append(f"c→f={cf['c']['used_f']}")
            title_bits.append("Snapped: " + ", ".join(used))

        self._title.setText("  ·  ".join(title_bits))
        self.canvas.draw_idle()

        # Update the results panel for this tier
        self._update_results_panel(r)

    # ---- results panel + airflow calc ---------------------------------------
    def _update_results_panel(self, r: Optional[Dict]):
        if not r:
            self.lbl_final_mid.setText("–")
            self.lbl_final_top.setText("–")
            self.lbl_guidance.setText("–")
            return

        amb = float(self.project.meta.ambient_C)

        # Effective temperatures from calc
        Tmid = r["T_mid"]
        Ttop = r["T_top"]
        T075 = r.get("T_075")

        tier: TierItem = r["tier"]
        maxC = int(tier.effective_max_temp_C())
        mode = "auto" if tier.use_auto_component_temp else "manual"

        # ------------------------------------------------------------
        # Display values (UNCHANGED)
        # ------------------------------------------------------------
        self.lbl_final_mid.setText(f"{Tmid:.1f} °C")

        if T075 is not None:
            self.lbl_final_top.setText(
                f"T(0.75t)={T075:.1f} °C,  T(1.0t)={Ttop:.1f} °C  (limit {maxC} °C, {mode})"
            )
        else:
            self.lbl_final_top.setText(
                f"{Ttop:.1f} °C (limit {maxC} °C, {mode})"
            )

        # ------------------------------------------------------------
        # Compliance + cooling guidance (NEW LOGIC)
        # ------------------------------------------------------------
        # ------------------------------------------------------------
        # Compliance + cooling guidance (IEC order of precedence)
        # ------------------------------------------------------------

        if r["compliant_top"]:
            self.lbl_guidance.setText(
                "IEC 60890 compliant.\n"
                "Base temperature rise is within the effective tier limit.\n"
                "→ No mitigation required."
            )
            return

        if r["P_cooling"] <= 0.0:
            self.lbl_guidance.setText(
                "IEC 60890 base temperature rise exceeds the effective limit,\n"
                "however heat dissipation via the enclosure is sufficient.\n"
                "→ Active cooling is NOT required.\n\n"
                "Assessment performed in accordance with IEC 60890 order of precedence."
            )
            return

        msg = (
            "IEC 60890 base temperature rise exceeds the effective limit.\n"
            "Residual heat remains after enclosure dissipation.\n\n"
            f"• Heat via enclosure: {r['P_material']:.0f} W\n"
            f"• Heat for cooling: {r['P_cooling']:.0f} W\n"
            f"→ Required airflow: ≥ {r['airflow_m3h']:.0f} m³/h"
        )

        if r.get("vent_recommended"):
            msg += "\n\n✓ Natural ventilation may be sufficient."

        self.lbl_guidance.setToolTip(
            "Temperature rise values shown are per IEC 60890 base calculation.\n"
            "Compliance is determined using the IEC order of precedence:\n"
            "1) Base compliance\n"
            "2) Enclosure dissipation\n"
            "3) Ventilation / cooling"
        )

        self.lbl_guidance.setText(msg)

