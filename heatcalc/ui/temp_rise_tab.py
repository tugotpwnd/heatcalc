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

from .tier_item import TierItem
from .designer_view import GRID
from ..core import curvefit
from ..core.airflow import required_airflow_with_wall_loss
from ..core.iec60890_calc import calc_tier_iec60890

MM_PER_GRID = 25.0  # same mapping you’ve been using
CM2_DEFAULT = 300  # default inlet cross-section (cm^2) when ventilated


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

    def __init__(self, scene, project, parent=None):
        super().__init__(parent)
        self.project = project
        self._scene_provider = lambda: scene
        self._results: List[Dict] = []  # one dict per tier with calc outputs
        self.ambient_C: Optional[float] = None

        # -------- Left: controls + tier list --------
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(6, 6, 6, 6)
        left_l.setSpacing(8)

        # Options
        opts = QGroupBox("Options")
        of = QFormLayout(opts)
        # Ambient temperature (required)
        self.ed_ambient = QLineEdit()
        self.ed_ambient.setPlaceholderText("Ambient temperature (°C)")
        self.ed_ambient.setValidator(QIntValidator(-50, 90, self))
        self.ed_ambient.textChanged.connect(self._on_ambient_changed)
        of.addRow("Ambient (°C):", self.ed_ambient)

        # Allow enclosure material heat dissipation (project-wide)
        self.cb_material_diss = QCheckBox("Allow enclosure material heat dissipation")
        self.cb_material_diss.stateChanged.connect(self._on_material_dissipation_toggled)
        of.addRow("", self.cb_material_diss)

        # Inlet opening area for ventilated enclosures
        self.sb_opening = QSpinBox()
        self.sb_opening.setRange(0, 5000)
        self.sb_opening.setValue(CM2_DEFAULT)
        of.addRow("Inlet area (cm²):", self.sb_opening)

        # Calculate button (disabled until ambient provided)
        self.btn_calc = QPushButton("Calculate")
        self.btn_calc.setEnabled(False)
        self.btn_calc.clicked.connect(self.calculate_all)

        left_l.addWidget(opts)
        left_l.addWidget(self.btn_calc)

        # Tier list (so you can click through the plots quickly)
        self.tier_list = QListWidget()
        self.tier_list.currentRowChanged.connect(self._show_plot_for_row)
        left_l.addWidget(QLabel("Tiers"))
        left_l.addWidget(self.tier_list, 1)

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
        split.setStretchFactor(1, 1)
        lay = QHBoxLayout(self)
        lay.addWidget(split)

        # ------------------------------------------------------------------ #
        # Load in meta
        # ------------------------------------------------------------------ #
        allow = bool(getattr(self.project.meta, "allow_material_dissipation", False))
        self.cb_material_diss.blockSignals(True)
        self.cb_material_diss.setChecked(allow)
        self.cb_material_diss.blockSignals(False)

        # --- Initialise ambient from project meta ---
        proj = self.project
        amb = getattr(proj.meta, "ambient_C", None)

        self.ed_ambient.blockSignals(True)
        if amb is None:
            self.ed_ambient.clear()
            self.ambient_C = None
        else:
            self.ed_ambient.setText(f"{int(amb)}")
            self.ambient_C = float(amb)
        self.ed_ambient.blockSignals(False)

        self._update_calc_enabled()

    # --------------------------------------------------------------------- UI

    def _on_material_dissipation_toggled(self, state: int):
        allow = bool(state == Qt.Checked)
        self.project.meta.allow_material_dissipation = allow

    def _on_ambient_changed(self, text: str):
        text = (text or "").strip()
        proj = self.project


        if text == "":
            self.ambient_C = None
            proj.meta.ambient_C = None
        else:
            try:
                val = float(int(text))  # validator ensures int
                self.ambient_C = val
                proj.meta.ambient_C = val
            except Exception:
                self.ambient_C = None
                proj.meta.ambient_C = None

        self._update_calc_enabled()

    def _update_calc_enabled(self):
        self.btn_calc.setEnabled(self.ambient_C is not None)

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

        amb = float(self.ambient_C or 0.0)

        for t in tiers:
            res = calc_tier_iec60890(
                tier=t,
                tiers=tiers,
                wall_mounted=t.wall_mounted,
                inlet_area_cm2=self.sb_opening.value(),
                ambient_C=self.ambient_C,
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
            within = res["T_top"] <= eff_limit
            colour = QColor(0, 150, 0) if within else QColor(200, 0, 0)

            mode = "auto" if getattr(t, "use_auto_component_temp", False) else "manual"
            if res.get("T_075") is not None:
                text = (
                    f"{t.name} — T(0.5t)={res['T_mid']:.1f}°C, "
                    f"T(0.75t)={res['T_075']:.1f}°C, "
                    f"T(1.0t)={res['T_top']:.1f}°C  "
                    f"[limit {eff_limit}°C, {mode}]"
                )
            else:
                text = (
                    f"{t.name} — T(0.5t)={res['T_mid']:.1f}°C, "
                    f"T(1.0t)={res['T_top']:.1f}°C  "
                    f"[limit {eff_limit}°C, {mode}]"
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
        amb = float(self.ambient_C or 0.0)

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

        amb = float(self.ambient_C or 0.0)

        # Effective temperatures from calc
        Tmid = r["T_mid"]
        Ttop = r["T_top"]
        T075 = r.get("T_075")

        tier: TierItem = r["tier"]
        maxC = int(getattr(tier, "max_temp_C", 70))

        # ------------------------------------------------------------
        # Display values (UNCHANGED)
        # ------------------------------------------------------------
        self.lbl_final_mid.setText(f"{Tmid:.1f} °C")

        if T075 is not None:
            self.lbl_final_top.setText(
                f"T(0.75t)={T075:.1f} °C,  T(1.0t)={Ttop:.1f} °C  (limit {maxC} °C)"
            )
        else:
            self.lbl_final_top.setText(
                f"{Ttop:.1f} °C (limit {maxC} °C)"
            )

        # ------------------------------------------------------------
        # Compliance + cooling guidance (NEW LOGIC)
        # ------------------------------------------------------------
        if Ttop <= maxC:
            self.lbl_guidance.setText("No cooling required.")
            return

        # ---- NEW: enclosure wall dissipation + residual airflow ----
        # Effective enclosure surface area from IEC 60890 calc
        Ae = r.get("Ae", 0.0)

        res = required_airflow_with_wall_loss(
            P_W=r["P"],
            amb_C=amb,
            max_internal_C=maxC,
            enclosure_area_m2=Ae,
            allow_wall_dissipation=self.project.meta.allow_material_dissipation,
            k_W_per_m2K=self.project.meta.enclosure_k_W_m2K,
        )

        if res.airflow_m3h is None:
            self.lbl_guidance.setText(
                "Cooling required, but max temperature ≤ ambient (check inputs)."
            )
            return

        # ---- Build explicit, auditable message ----
        if self.project.meta.allow_material_dissipation:
            self.lbl_guidance.setText(
                f"Cooling required.\n"
                f"• Heat dissipated via enclosure: {res.q_walls_W:.0f} W\n"
                f"• Heat remaining for ventilation: {res.q_fans_W:.0f} W\n"
                f"→ Required airflow: ≥ {res.airflow_m3h:.0f} m³/h"
            )
        else:
            self.lbl_guidance.setText(
                f"Cooling required: ≥ {res.airflow_m3h:.0f} m³/h airflow "
                f"to limit to {maxC} °C."
            )
