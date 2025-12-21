# heatcalc/ui/curvefit_tab.py
from __future__ import annotations
from typing import List, Dict

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem, QLabel
from PyQt5.QtCore import Qt

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from ..core import curvefit
from .tier_item import TierItem

from ..core.iec60890_geometry import (
    touching_sides,
    b_map_for_tier,
    dimensions_m,
    effective_area_and_fg,
)


class _Plot(QWidget):
    """One axes + canvas with crosshair + live xy."""
    def __init__(self, title: str, xlabel: str, ylabel: str, parent=None):
        super().__init__(parent)
        self.fig = Figure(figsize=(5, 4), constrained_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title(title)
        self.ax.set_xlabel(xlabel)
        self.ax.set_ylabel(ylabel)
        self.ax.grid(True, alpha=0.25)

        self.vl = self.ax.axvline(0, color="0.5", lw=0.8, ls="--", alpha=0.6)
        self.hl = self.ax.axhline(0, color="0.5", lw=0.8, ls="--", alpha=0.6)
        self.xy = self.ax.text(
            0.02, 0.02, "", transform=self.ax.transAxes,
            ha="left", va="bottom", fontsize=8,
            bbox=dict(facecolor="white", alpha=0.6, edgecolor="0.8")
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)

    def connect_crosshair(self):
        def _on_move(ev):
            if ev.inaxes is self.ax and ev.xdata is not None and ev.ydata is not None:
                x, y = float(ev.xdata), float(ev.ydata)
                self.vl.set_xdata([x, x]); self.hl.set_ydata([y, y])
                self.xy.set_text(f"x={x:.3g}, y={y:.3g}")
                self.canvas.draw_idle()
        self.canvas.mpl_connect("motion_notify_event", _on_move)


class CurveFitTab(QWidget):
    def __init__(self, project, scene, parent=None):
        super().__init__(parent)
        self.project = project
        self.scene = scene

        self.tabs = QTabWidget(self)
        self.p3 = _Plot("Fig.3 — k vs Ae (no ventilation)", "Ae (m²)", "k")
        self.p4 = _Plot("Fig.4 — c vs f (no ventilation)", "f (h/b)", "c")
        self.p5 = _Plot("Fig.5 — k vs inlet area (ventilated)", "Inlet area (cm²)", "k")
        self.p6 = _Plot("Fig.6 — c vs inlet area (ventilated)", "Inlet area (cm²)", "c")
        self.p7 = _Plot("Fig.7 — k vs Ae (no vents, Ae ≤ 1.25)", "Ae (m²)", "k")
        self.p8 = _Plot("Fig.8 — c vs g (no vents, Ae ≤ 1.25)", "g = h/w", "c")

        for w, name in (
            (self.p3, "Fig.3"), (self.p4, "Fig.4"), (self.p5, "Fig.5"),
            (self.p6, "Fig.6"), (self.p7, "Fig.7"), (self.p8, "Fig.8")
        ):
            self.tabs.addTab(w, name)
            w.connect_crosshair()

        self.tbl = QTableWidget(0, 9, self)
        self.tbl.setHorizontalHeaderLabels(["Tier", "Ae (m²)", "Vent", "Curve", "b", "k", "d", "c", "x"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setAlternatingRowColors(True)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 3)
        lay.addWidget(QLabel("Per-tier variable mapping (current estimates)"))
        lay.addWidget(self.tbl, 2)

        # caches
        self._family_drawn = False
        self._markers: Dict[str, list] = {k: [] for k in ("p3", "p4", "p5", "p6", "p7", "p8")}

        self.redraw_all()

    def on_tier_geometry_committed(self):
        self.refresh_table()
        self._redraw_markers_only()

    def redraw_all(self):
        self._plot_fig3_lines()
        self._plot_fig4_lines()
        self._plot_fig5_lines()
        self._plot_fig6_lines()
        self._plot_fig7_lines()
        self._plot_fig8_lines()
        self._family_drawn = True
        self.refresh_table()
        self._redraw_markers_only()

    def _clear_markers(self, key: str, ax):
        for art in self._markers[key]:
            try:
                art.remove()
            except Exception:
                pass
        self._markers[key].clear()
        ax.figure.canvas.draw_idle()

    # ---- families ----
    def _plot_fig3_lines(self):
        ax = self.p3.ax; ax.cla(); ax.grid(True, alpha=0.25)
        xs = curvefit.AE_RANGE_LARGE
        ys = [curvefit.k_no_vents(ae) for ae in xs]
        ax.plot(xs, ys, label="k(Ae) — no vents")
        ax.legend(); self.p3.canvas.draw_idle()

    def _plot_fig4_lines(self):
        ax = self.p4.ax; ax.cla(); ax.grid(True, alpha=0.25)
        fvals = curvefit.F_RANGE
        for curve_no in (1, 2, 3, 4, 5):
            ys = [curvefit.c_no_vents(curve_no, f) for f in fvals]
            ax.plot(fvals, ys, label=f"Curve {curve_no}")
        ax.legend(title="Installation type"); self.p4.canvas.draw_idle()

    def _plot_fig5_lines(self):
        ax = self.p5.ax; ax.cla(); ax.grid(True, alpha=0.25)
        areas = curvefit.OPENING_AREA_RANGE
        for ae in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12, 14):
            ys = [curvefit.k_vents(ae, a) for a in areas]
            ax.plot(areas, ys, label=f"Ae={ae:g} m²", alpha=0.95)
        ax.set_ylim(0.06, 0.38)
        ax.legend(ncol=3, fontsize=8, title="Ae family")
        self.p5.canvas.draw_idle()

    def _plot_fig6_lines(self):
        ax = self.p6.ax; ax.cla(); ax.grid(True, alpha=0.25)
        areas = curvefit.OPENING_AREA_RANGE
        for f in (1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            ys = [curvefit.c_vents(f, a) for a in areas]
            ax.plot(areas, ys, label=f"f={f:g}", alpha=0.95)
        ax.set_ylim(1.2, 2.25)
        ax.legend(ncol=3, fontsize=8, title="f family")
        self.p6.canvas.draw_idle()

    def _plot_fig7_lines(self):
        ax = self.p7.ax; ax.cla(); ax.grid(True, alpha=0.25)
        xs = curvefit.AE_RANGE_SMALL
        ys = [curvefit.k_small_no_vents(ae) for ae in xs]
        ax.plot(xs, ys, label="k(Ae) — no vents (small)")
        ax.legend(); self.p7.canvas.draw_idle()

    def _plot_fig8_lines(self):
        ax = self.p8.ax; ax.cla(); ax.grid(True, alpha=0.25)
        xs = curvefit.G_RANGE
        ys = [curvefit.c_small_no_vents(g) for g in xs]
        ax.plot(xs, ys, label="c(g) — Ae ≤ 1.25")
        ax.legend(); self.p8.canvas.draw_idle()

    # ---- markers only (called on geometryCommitted) ----
    def _redraw_markers_only(self):
        if not self._family_drawn:
            return

        # clear previous
        self._clear_markers("p3", self.p3.ax)
        self._clear_markers("p4", self.p4.ax)
        self._clear_markers("p5", self.p5.ax)
        self._clear_markers("p6", self.p6.ax)
        self._clear_markers("p7", self.p7.ax)
        self._clear_markers("p8", self.p8.ax)

        tiers: List[TierItem] = [it for it in self.scene.items() if isinstance(it, TierItem)]
        if not tiers:
            return

        for t in tiers:
            touch = touching_sides(t, tiers)
            bmap = b_map_for_tier(t, touch)
            Ae, f, g = effective_area_and_fg(t, bmap)

            vent = bool(getattr(t, "is_ventilated", False))
            curve = int(getattr(t, "curve_no", 3))

            # choose representative inlet area for marker placement
            inlet_cm2 = 300.0

            if vent:
                # Fig.5 and Fig.6 are functions of opening area with family parameter Ae/f
                k5 = curvefit.k_vents(ae=max(1.0, min(14.0, Ae)), opening_area_cm2=inlet_cm2)
                c6 = curvefit.c_vents(f=f, opening_area_cm2=inlet_cm2)
                m5, = self.p5.ax.plot(inlet_cm2, k5, "o", ms=6)
                m6, = self.p6.ax.plot(inlet_cm2, c6, "o", ms=6)
                self._markers["p5"].append(m5); self._markers["p6"].append(m6)
            else:
                if Ae <= 1.25:
                    k7 = curvefit.k_small_no_vents(Ae)
                    c8 = curvefit.c_small_no_vents(g)
                    m7, = self.p7.ax.plot(Ae, k7, "o", ms=6)
                    m8, = self.p8.ax.plot(g, c8, "o", ms=6)
                    self._markers["p7"].append(m7); self._markers["p8"].append(m8)
                else:
                    k3 = curvefit.k_no_vents(Ae)
                    c4 = curvefit.c_no_vents(curve, f=f)
                    m3, = self.p3.ax.plot(Ae, k3, "o", ms=6)
                    m4, = self.p4.ax.plot(f, c4, "o", ms=6)
                    self._markers["p3"].append(m3); self._markers["p4"].append(m4)

        # flush draws
        self.p3.canvas.draw_idle(); self.p4.canvas.draw_idle()
        self.p5.canvas.draw_idle(); self.p6.canvas.draw_idle()
        self.p7.canvas.draw_idle(); self.p8.canvas.draw_idle()

    # ---- table ----
    def refresh_table(self):
        tiers: List[TierItem] = [it for it in self.scene.items() if isinstance(it, TierItem)]
        others = tiers[:]

        headers = ["Tier", "Vent", "Curve", "W (m)", "H (m)", "D (m)", "Ae (m²)",
                   "b_top", "b_bot", "b_L", "b_R", "b_front", "b_rear",
                   "f", "g", "k", "c", "x"]
        if self.tbl.columnCount() != len(headers):
            self.tbl.setColumnCount(len(headers))
            self.tbl.setHorizontalHeaderLabels(headers)
            self.tbl.verticalHeader().setVisible(False)
            self.tbl.setAlternatingRowColors(True)

        self.tbl.setRowCount(len(tiers))

        for row, t in enumerate(reversed(tiers)):
            touch = touching_sides(t, others)
            bmap = b_map_for_tier(t, touch)
            Ae, f, g = effective_area_and_fg(t, bmap)
            w, h, d = dimensions_m(t)

            vent = bool(getattr(t, "is_ventilated", False))
            curve = int(getattr(t, "curve_no", 3))
            x = 0.715 if vent else 0.804

            # pick representative inlet area for these numbers
            inlet_cm2 = 300.0

            if vent:
                k = curvefit.k_vents(ae=max(1.0, min(14.0, Ae)), opening_area_cm2=inlet_cm2)
                c = curvefit.c_vents(f=f, opening_area_cm2=inlet_cm2)
            else:
                if Ae <= 1.25:
                    k = curvefit.k_small_no_vents(Ae)
                    c = curvefit.c_small_no_vents(g)
                else:
                    k = curvefit.k_no_vents(Ae)
                    c = curvefit.c_no_vents(curve, f)

            def cell(txt: str) -> QTableWidgetItem:
                it = QTableWidgetItem(txt)
                it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                return it

            self.tbl.setItem(row, 0, cell(str(getattr(t, "name", getattr(t, "tag", "Tier")))))
            self.tbl.setItem(row, 1, cell("Yes" if vent else "No"))
            self.tbl.setItem(row, 2, cell(str(curve)))
            self.tbl.setItem(row, 3, cell(f"{w:.3f}"))
            self.tbl.setItem(row, 4, cell(f"{h:.3f}"))
            self.tbl.setItem(row, 5, cell(f"{d:.3f}"))
            self.tbl.setItem(row, 6, cell(f"{Ae:.3f}"))
            self.tbl.setItem(row, 7, cell(f"{bmap['top']:.2f}"))
            self.tbl.setItem(row, 8, cell(f"{bmap['bottom']:.2f}"))
            self.tbl.setItem(row, 9, cell(f"{bmap['left']:.2f}"))
            self.tbl.setItem(row, 10, cell(f"{bmap['right']:.2f}"))
            self.tbl.setItem(row, 11, cell(f"{bmap['front']:.2f}"))
            self.tbl.setItem(row, 12, cell(f"{bmap['rear']:.2f}"))
            self.tbl.setItem(row, 13, cell(f"{f:.3f}"))
            self.tbl.setItem(row, 14, cell(f"{g:.3f}"))
            self.tbl.setItem(row, 15, cell(f"{k:.3f}"))
            self.tbl.setItem(row, 16, cell(f"{c:.3f}"))
            self.tbl.setItem(row, 17, cell(f"{x:.3f}"))

        self.tbl.resizeColumnsToContents()
