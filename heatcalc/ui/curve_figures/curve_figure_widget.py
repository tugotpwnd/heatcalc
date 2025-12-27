# heatcalc/ui/curve_figures/curve_figure_widget.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea

from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar

import matplotlib as mpl


@dataclass(frozen=True)
class TierPoint:
    tier_name: str
    x: float
    y: float
    text: str
    color: Optional[Tuple[float, float, float, float]] = None


class CurveFigureWidget(QWidget):
    """
    One IEC figure (one axes) with:
      - standards-style title
      - zoom/pan toolbar
      - scrollable container
      - base curves legend (meaningful)
      - tier points annotated directly on the plot (no tier legend spam)
    """

    def __init__(
        self,
        title: str,
        xlabel: str,
        ylabel: str,
        legend_title: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)

        # Scroll container so large plots remain usable on small screens
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self._inner = QWidget()
        self._scroll.setWidget(self._inner)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._scroll)

        inner_lay = QVBoxLayout(self._inner)
        inner_lay.setContentsMargins(10, 10, 10, 10)
        inner_lay.setSpacing(8)

        self.lbl_title = QLabel(title)
        self.lbl_title.setAlignment(Qt.AlignCenter)
        self.lbl_title.setWordWrap(True)
        inner_lay.addWidget(self.lbl_title)

        # Figure + toolbar
        self.fig = Figure(figsize=(8.5, 5.5), constrained_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self._inner)

        inner_lay.addWidget(self.toolbar)
        inner_lay.addWidget(self.canvas, 1)

        self.ax = self.fig.add_subplot(111)
        self._xlabel = xlabel
        self._ylabel = ylabel
        self._legend_title = legend_title

        self._tier_colors: Dict[str, Tuple[float, float, float, float]] = {}
        self._color_cycle = list(mpl.rcParams["axes.prop_cycle"].by_key().get("color", []))
        self._color_idx = 0

        self._apply_axes_style()

    # ------------------------- styling

    def _apply_axes_style(self) -> None:
        self.ax.set_xlabel(self._xlabel)
        self.ax.set_ylabel(self._ylabel)
        self.ax.grid(True, alpha=0.25)

    # ------------------------- colors

    def set_tier_color_map(self, colors: Dict[str, Tuple[float, float, float, float]]) -> None:
        """Consistent tier colors across all figures."""
        self._tier_colors = dict(colors)

    def _color_for_tier(self, name: str) -> Tuple[float, float, float, float]:
        if name in self._tier_colors:
            return self._tier_colors[name]
        # fallback deterministic assignment
        if not self._color_cycle:
            return (0.1, 0.1, 0.1, 1.0)
        c = self._color_cycle[self._color_idx % len(self._color_cycle)]
        self._color_idx += 1
        rgba = mpl.colors.to_rgba(c)
        self._tier_colors[name] = rgba
        return rgba

    # ------------------------- redraw API

    def clear(self) -> None:
        self.ax.cla()
        self._apply_axes_style()

    def draw_base_curves(self, draw_fn) -> None:
        """
        draw_fn(ax) must draw the IEC curves and set labels for the legend.
        """
        draw_fn(self.ax)

        # Base curve legend (auditable, meaningful)
        handles, labels = self.ax.get_legend_handles_labels()
        if labels:
            if self._legend_title:
                self.ax.legend(handles, labels, fontsize=8, title=self._legend_title)
            else:
                self.ax.legend(handles, labels, fontsize=8)

    def draw_tier_points(self, points: List[TierPoint]) -> None:
        """
        Plot tier points and annotate them directly at the marker.
        No tier legend is created (intentional).
        """
        for p in points:
            color = p.color or self._color_for_tier(p.tier_name)

            # marker
            self.ax.scatter([p.x], [p.y], s=40, zorder=5, color=color, edgecolors="black", linewidths=0.3)

            # annotation
            self.ax.annotate(
                p.text,
                xy=(p.x, p.y),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=8,
                color=color,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=color, alpha=0.85),
                arrowprops=dict(arrowstyle="->", color=color, lw=0.8, alpha=0.9),
            )

    def redraw(self) -> None:
        self.canvas.draw_idle()
