# heatcalc/ui/curvefit_tab.py
from __future__ import annotations
from typing import List, Dict, Tuple
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem, QLabel
from PyQt5.QtCore import Qt

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from ..core import curvefit
from .tier_item import TierItem
from .designer_view import GRID

MM_PER_GRID = 25

# -------------- helpers: adjacency + b-map + Ae/f/g -----------------

_EPS = 1e-3

def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> bool:
    return not (a1 <= b0 or b1 <= a0)

def _touching_sides(t: TierItem, others: list[TierItem]) -> Dict[str, bool]:
    """Return which sides of t are touching another tier: top/bottom/left/right."""
    r = t.shapeRect()
    def any_touch(pred):
        for o in others:
            if o is t:
                continue
            ro = o.shapeRect()
            if pred(ro):
                return True
        return False

    left_touch   = any_touch(lambda ro: abs(r.left() - ro.right())   < _EPS and _overlap_1d(r.top(), r.bottom(), ro.top(), ro.bottom()))
    right_touch  = any_touch(lambda ro: abs(r.right() - ro.left())   < _EPS and _overlap_1d(r.top(), r.bottom(), ro.top(), ro.bottom()))
    top_touch    = any_touch(lambda ro: abs(r.top() - ro.bottom())   < _EPS and _overlap_1d(r.left(), r.right(), ro.left(), ro.right()))
    bottom_touch = any_touch(lambda ro: abs(r.bottom() - ro.top())   < _EPS and _overlap_1d(r.left(), r.right(), ro.left(), ro.right()))
    return {"top": top_touch, "bottom": bottom_touch, "left": left_touch, "right": right_touch}

def _b_map_for_tier(t: TierItem, touching: Dict[str, bool]) -> Dict[str, float]:
    """
    Map each face -> b factor per Table III:
      top:    exposed 1.4, covered 0.7
      bottom: floor not taken into account -> 0.0
      left/right/front/rear: exposed 0.9, covered (central or wall contact) 0.5
      rear is 'covered' when wall-mounted, otherwise exposed.
      left/right considered covered when touching another tier on that side.
      top considered covered when another tier directly on top.
    """
    # top
    b_top = 0.7 if touching["top"] else 1.4
    # bottom (floor) not taken into account
    b_bottom = 0.0
    # sides
    b_left  = 0.5 if touching["left"]  else 0.9
    b_right = 0.5 if touching["right"] else 0.9
    # front is always an exposed side in this model
    b_front = 0.9
    # rear depends on wall-mounted flag
    b_rear  = 0.5 if getattr(t, "wall_mounted", False) else 0.9
    return {"top": b_top, "bottom": b_bottom, "left": b_left, "right": b_right, "front": b_front, "rear": b_rear}

def _dimensions_m(t: TierItem) -> Tuple[float, float, float]:
    """Return (w,h,d) in metres taken from rect (grid) + depth_mm field."""
    wmm = max(1, int(t._rect.width()  / GRID * MM_PER_GRID))
    hmm = max(1, int(t._rect.height() / GRID * MM_PER_GRID))
    dmm = max(1, int(getattr(t, "depth_mm", 400)))
    return wmm / 1000.0, hmm / 1000.0, dmm / 1000.0

def _effective_area_and_fg(t: TierItem, bmap: Dict[str, float]) -> Tuple[float, float, float]:
    """
    Return (Ae, f, g)
      Ae = sum(b_i * A_i) over faces (m^2)
      f  = h^1.35 / A_b (A_b=base area=w*d)   [defined for Ae>1.25 cases]
      g  = h / w                               [used for Ae<=1.25 cases]
    """
    w, h, d = _dimensions_m(t)
    # areas of faces
    A_top = w * d
    A_bottom = w * d
    A_left = h * d
    A_right = h * d
    A_front = w * h
    A_rear = w * h
    Ae = (
        bmap["top"]    * A_top +
        bmap["bottom"] * A_bottom +
        bmap["left"]   * A_left +
        bmap["right"]  * A_right +
        bmap["front"]  * A_front +
        bmap["rear"]   * A_rear
    )
    Ab = A_top  # base area = w*d
    f = (h ** 1.35) / max(1e-9, Ab)
    g = h / max(1e-9, w)
    return Ae, f, g



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
        self.xy = self.ax.text(0.02, 0.02, "", transform=self.ax.transAxes,
                               ha="left", va="bottom", fontsize=8,
                               bbox=dict(facecolor="white", alpha=0.6, edgecolor="0.8"))

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

        for w, name in ((self.p3, "Fig.3"), (self.p4, "Fig.4"), (self.p5, "Fig.5"),
                        (self.p6, "Fig.6"), (self.p7, "Fig.7"), (self.p8, "Fig.8")):
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

    # ---- public slot: call this when tiers finish a move/resize/add/delete
    def on_tier_geometry_committed(self):
        self.refresh_table()
        self._redraw_markers_only()

    # ---- full draw once ----
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

    # ---- helpers: clear existing markers on an axes ----
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
        for t in tiers:
            # dimensions
            wmm = int(t._rect.width() / GRID * MM_PER_GRID)
            hmm = int(t._rect.height() / GRID * MM_PER_GRID)
            w = max(0.001, wmm) / 1000.0
            h = max(0.001, hmm) / 1000.0
            Ae_face = w * h
            vent = t.is_ventilated
            curve = t.curve_no

            # quick b/d/x placeholders (your deeper Ae computation now lives elsewhere)
            b = 0.5 if t.wall_mounted else 0.9
            d = 1.00
            x = 0.715 if vent else 0.804

            # pick representative inlet area & f for the marks
            inlet = 300.0
            f = 3.0
            g = max(0.1, h / max(0.001, w))

            # compute points
            if vent:
                k5 = curvefit.k_vents(ae=max(1.0, min(14.0, Ae_face)), opening_area_cm2=inlet)
                c6 = curvefit.c_vents(f=f, opening_area_cm2=inlet)
                m5, = self.p5.ax.plot(inlet, k5, "o", ms=6)
                m6, = self.p6.ax.plot(inlet, c6, "o", ms=6)
                self._markers["p5"].append(m5); self._markers["p6"].append(m6)
            else:
                if Ae_face <= 1.25:
                    k3s = curvefit.k_small_no_vents(Ae_face)
                    m7, = self.p7.ax.plot(Ae_face, k3s, "o", ms=6)
                    self._markers["p7"].append(m7)
                else:
                    k3 = curvefit.k_no_vents(Ae_face)
                    m3, = self.p3.ax.plot(Ae_face, k3, "o", ms=6)
                    self._markers["p3"].append(m3)
                c4 = curvefit.c_no_vents(curve, f=f)
                m4, = self.p4.ax.plot(f, c4, "o", ms=6)
                self._markers["p4"].append(m4)

            c8 = curvefit.c_small_no_vents(g)
            m8, = self.p8.ax.plot(g, c8, "o", ms=6)
            self._markers["p8"].append(m8)

        # flush draws
        self.p3.canvas.draw_idle(); self.p4.canvas.draw_idle()
        self.p5.canvas.draw_idle(); self.p6.canvas.draw_idle()
        self.p7.canvas.draw_idle(); self.p8.canvas.draw_idle()

    # ---- table ----
    # -------------- replace your refresh_table with this version ----------------
    def refresh_table(self):
        tiers: List[TierItem] = [it for it in self.scene.items() if isinstance(it, TierItem)]
        others = tiers[:]  # for adjacency checks

        # widen columns: Tier | Vent | Curve | W | H | D | Ae | b_top | b_bot | b_L | b_R | b_front | b_rear | f | g | k | c | x
        headers = ["Tier", "Vent", "Curve", "W (m)", "H (m)", "D (m)", "Ae (m²)",
                   "b_top", "b_bot", "b_L", "b_R", "b_front", "b_rear",
                   "f  (h^1.35/Ab)", "g (h/w)", "k", "c", "x"]
        if self.tbl.columnCount() != len(headers):
            self.tbl.setColumnCount(len(headers))
            self.tbl.setHorizontalHeaderLabels(headers)
            self.tbl.verticalHeader().setVisible(False)
            self.tbl.setAlternatingRowColors(True)

        self.tbl.setRowCount(len(tiers))

        for row, t in enumerate(reversed(tiers)):
            touch = _touching_sides(t, others)
            bmap = _b_map_for_tier(t, touch)
            Ae, f, g = _effective_area_and_fg(t, bmap)
            w, h, d = _dimensions_m(t)

            vent = bool(getattr(t, "is_ventilated", False))
            curve = int(getattr(t, "curve_no", 3))
            # x exponent
            x = 0.715 if vent else 0.804

            # k & c per your curve families
            if vent:
                # choose a nominal inlet area for now; you can expose it in UI later
                inlet_cm2 = 300.0
                # family parameter is Ae (clamped to chart bounds)
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

            self.tbl.setItem(row, 0, cell(t.name))
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

        # also refresh markers on the plots to the new Ae/f/g values
        if hasattr(self, "refresh_markers"):
            self.refresh_markers()
