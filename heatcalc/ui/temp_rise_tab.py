from __future__ import annotations
from typing import List, Tuple, Dict, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSplitter,
    QListWidget, QListWidgetItem, QGroupBox, QFormLayout, QSpinBox, QLabel,
    QLineEdit
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from .tier_item import TierItem
from .designer_view import GRID
from ..core import curvefit

MM_PER_GRID = 25.0  # same mapping you’ve been using
CM2_DEFAULT = 300  # default inlet cross-section (cm^2) when ventilated


class TempRiseTab(QWidget):
    """
    Computes temperature rise per tier using IEC 60890-style factors and visualizes a
    characteristic curve for each selected tier.

    Additions in this version:
    - Ambient temperature must be provided before calculation is enabled.
    - Curves are plotted as ABSOLUTE temperatures (ambient + Δt0.5, ambient + Δt1.0).
    - A per-tier result panel shows final temperatures at 0.5t and 1.0t and cooling guidance.
    - Tier entries are coloured GREEN if within max temperature, RED if exceeding.
    - If exceeding, an estimated minimum airflow (m³/h) is computed to meet the max temp.
    """

    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self.scene = scene
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

    # --------------------------------------------------------------------- UI
    def _on_ambient_changed(self, text: str):
        text = (text or "").strip()
        if text == "":
            self.ambient_C = None
        else:
            try:
                self.ambient_C = float(int(text))  # validator ensures int
            except Exception:
                self.ambient_C = None
        self._update_calc_enabled()

    def _update_calc_enabled(self):
        self.btn_calc.setEnabled(self.ambient_C is not None)

    # --------------------------------------------------------------------- calc
    def calculate_all(self):
        """Compute Δt values for each tier and populate the list + first plot.
        Also colours tier entries based on maximum temperature compliance.
        """
        tiers: List[TierItem] = [it for it in self.scene.items() if isinstance(it, TierItem)]
        tiers = list(reversed(tiers))  # visual order consistent with other tabs
        self.tier_list.clear()
        self._results.clear()

        amb = float(self.ambient_C or 0.0)

        for t in tiers:
            res = self._calc_for_tier(t)
            # compute final absolute temps
            res["T_mid"] = amb + res["dt_mid"]
            res["T_top"] = amb + res["dt_top"]
            self._results.append(res)

            # Use the tier's effective limit (manual or auto-lowest-component)
            eff_limit = int(getattr(t, "effective_max_temp_C", lambda: int(getattr(t, "max_temp_C", 70)))())
            within = res["T_top"] <= eff_limit
            colour = QColor(0, 150, 0) if within else QColor(200, 0, 0)

            mode = "auto" if getattr(t, "use_auto_component_temp", False) else "manual"
            text = (f"{t.name} — T(0.5t)={res['T_mid']:.1f}°C, "
                    f"T(1.0t)={res['T_top']:.1f}°C  [limit {eff_limit}°C, {mode}]")
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

    # ---- single-tier math ----------------------------------------------------
    def _calc_for_tier(self, t: TierItem) -> Dict:
        """Return dict of all variables + Δt curve points for one tier."""
        # geometry (mm -> m)
        w_m = (t._rect.width() / GRID) * (MM_PER_GRID / 1000.0)
        h_m = (t._rect.height() / GRID) * (MM_PER_GRID / 1000.0)
        d_m = max(0.001, (t.depth_mm or 400) / 1000.0)  # default 400 mm if missing

        # faces + b factors (uses same rule set we used elsewhere)
        wall = t.wall_mounted
        tiers: List[TierItem] = [it for it in self.scene.items() if isinstance(it, TierItem)]
        b_faces = self._compute_face_bs(t, tiers, wall)

        # Effective cooling surface Ae = Σ(A0 * b)
        areas = {
            "top": w_m * d_m,
            "bot": w_m * d_m,  # multiplied by 0.0 later
            "left": h_m * d_m,
            "right": h_m * d_m,
            "front": w_m * h_m,
            "back": w_m * h_m,
        }
        b_used = {
            "top": b_faces["top"],
            "bot": 0.0,  # standard: floor surface is not taken into account
            "left": b_faces["left"],
            "right": b_faces["right"],
            "front": 0.9,  # exposed side faces (front)
            "back": 0.5 if wall else 0.9,  # rear side reduced when wall-mounted
        }
        Ae = 0.0
        for face in areas:
            Ae += areas[face] * b_used[face]

        # power + factors
        P = max(0.0, t.total_heat())  # W
        vent = t.is_ventilated
        curve_no = t.curve_no
        x = 0.715 if vent else 0.804
        d_fac = 1.0  # Table IV/V not yet parameterized by n → keep 1.0

        if vent:
            # Fig.5 and Fig.6: c depends on inlet area + f, k family depends on Ae + inlet area
            s_cm2 = float(self.sb_opening.value())
            Ab = max(1e-9, w_m * d_m)
            f = (h_m ** 1.35) / Ab
            k = curvefit.k_vents(ae=max(1.0, min(14.0, Ae)), opening_area_cm2=s_cm2)
            c = curvefit.c_vents(f=f, opening_area_cm2=s_cm2)
        else:
            if Ae <= 1.25:
                # Fig.7 + Fig.8
                k = curvefit.k_small_no_vents(Ae)
                g = h_m / max(1e-9, w_m)
                c = curvefit.c_small_no_vents(g)
                f = None
            else:
                # Fig.3 + Fig.4
                k = curvefit.k_no_vents(Ae)
                Ab = max(1e-9, w_m * d_m)
                f = (h_m ** 1.35) / Ab
                c = curvefit.c_no_vents(curve_no, f)

        # temperature rises
        dt_mid = k * d_fac * (P ** x)  # Δt0.5
        dt_top = c * dt_mid  # Δt1.0

        return dict(
            tier=t,
            name=t.name,
            vent=vent,
            curve=curve_no,
            Ae=Ae,
            P=P,
            w=w_m, h=h_m, d=d_m,
            b_faces=b_used,
            f=f if vent or (Ae > 1.25) else None,
            g=(h_m / max(1e-9, w_m)) if (not vent and Ae <= 1.25) else None,
            k=k, c=c, x=x, d_fac=d_fac,
            dt_mid=dt_mid, dt_top=dt_top
        )

    # ---- plotting ------------------------------------------------------------
    def _show_plot_for_row(self, row: int):
        if not (0 <= row < len(self._results)):
            self.ax.cla();
            self.canvas.draw_idle();
            self._update_results_panel(None);
            return
        r = self._results[row]
        amb = float(self.ambient_C or 0.0)

        self.ax.cla()
        self.ax.grid(True, alpha=0.25)
        self.ax.set_xlabel("Temperature (°C)")
        self.ax.set_ylabel("Multiple of enclosure height")
        self.ax.set_ylim(0, 1.02)

        # Characteristic polyline from bottom (ambient, ~0) to mid-height and top
        x0, y0 = amb, 0.0
        x1, y1 = amb + r["dt_mid"], 0.5
        x2, y2 = amb + r["dt_top"], 1.0
        self.ax.plot([x0, x1, x2], [y0, y1, y2], lw=2)

        self.ax.scatter([x1, x2], [y1, y2], s=35, zorder=5)
        self.ax.annotate("T@0.5t", (x1, y1), xytext=(6, -6),
                         textcoords="offset points", fontsize=9)
        self.ax.annotate("T@1.0t", (x2, y2), xytext=(6, -6),
                         textcoords="offset points", fontsize=9)

        title_bits = [r["name"]]
        title_bits.append("Ventilated" if r["vent"] else "No ventilation")
        title_bits.append(f"Ae={r['Ae']:.3f} m², P={r['P']:.1f} W, k={r['k']:.3f}, c={r['c']:.3f}, x={r['x']:.3f}")
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
            self.lbl_final_mid.setText("–");
            self.lbl_final_top.setText("–");
            self.lbl_guidance.setText("–");
            return
        amb = float(self.ambient_C or 0.0)
        Tmid = amb + r["dt_mid"]
        Ttop = amb + r["dt_top"]
        tier: TierItem = r["tier"]
        maxC = int(getattr(tier, "max_temp_C", 70))

        self.lbl_final_mid.setText(f"{Tmid:.1f} °C")
        self.lbl_final_top.setText(f"{Ttop:.1f} °C (limit {maxC} °C)")

        if Ttop <= maxC:
            self.lbl_guidance.setText("No cooling required.")
        else:
            v_m3h = self._min_airflow_m3h(P=r["P"], amb_C=amb, max_C=maxC)
            if v_m3h is None:
                self.lbl_guidance.setText("Cooling required, but max temp ≤ ambient (check inputs).")
            else:
                self.lbl_guidance.setText(
                    f"Cooling required: ≥ {v_m3h:.0f} m³/h airflow to limit to {maxC} °C."
                )

    @staticmethod
    def _min_airflow_m3h(P: float, amb_C: float, max_C: float) -> Optional[float]:
        """Return minimum airflow (m³/h) to keep enclosure ≤ max_C given power P.
        Uses steady-state heat balance:  \dot{Q} = ρ c_p V̇ ΔT  ⇒  V̇ = P/(ρ c_p ΔT).
        Assumes air ρ≈1.20 kg/m³, c_p≈1005 J/(kg·K)."""
        dT_allow = max_C - amb_C
        if dT_allow <= 0:
            return None
        rho = 1.20  # kg/m³
        cp = 1005.0  # J/kg/K
        Vdot_m3_s = P / (rho * cp * dT_allow)
        return Vdot_m3_s * 3600.0  # m³/h

    # ---- shared helpers ------------------------------------------------------
    @staticmethod
    def _overlap_x(a: TierItem, b: TierItem) -> bool:
        ra, rb = a.shapeRect(), b.shapeRect()
        return not (ra.right() <= rb.left() or rb.right() <= ra.left())

    @staticmethod
    def _compute_face_bs(t: TierItem, tiers: List[TierItem], wall: bool) -> Dict[str, float]:
        """Return b per side: left/right/top based on adjacency + wall flag."""
        left_touch = any(abs(t.shapeRect().left() - o.shapeRect().right()) < 1e-3 and
                         TempRiseTab._overlap_x(t, o) for o in tiers if o is not t)
        right_touch = any(abs(t.shapeRect().right() - o.shapeRect().left()) < 1e-3 and
                          TempRiseTab._overlap_x(t, o) for o in tiers if o is not t)
        top_covered = any(abs(t.shapeRect().top() - o.shapeRect().bottom()) < 1e-3 and
                          TempRiseTab._overlap_x(t, o) for o in tiers if o is not t)

        # Side faces:
        b_left = 0.5 if left_touch else 0.9
        b_right = 0.5 if right_touch else 0.9

        # Top face:
        if wall and top_covered:
            b_top = 0.7  # “covered top surface” per Table III
        else:
            b_top = 1.4  # exposed top surface

        return dict(left=b_left, right=b_right, top=b_top)
