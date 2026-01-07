# heatcalc/ui/curvefit_tab.py
from __future__ import annotations

from typing import Dict, List, Tuple

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QHBoxLayout, QPushButton

import matplotlib as mpl

from ..core import curvefit
from .tier_item import TierItem

from .curve_figures.figure_definitions import FIGURE_DEFS, FigureDef
from .curve_figures.curve_figure_widget import CurveFigureWidget, TierPoint


class CurveFitTab(QWidget):
    """
    IEC curve viewer (UI-focused).
    - One tab per IEC figure.
    - Zoom/pan/reset via Matplotlib toolbar.
    - Scrollable container.
    - Base curve legend explains curve families (Ae or f etc).
    - Tier usage shown by annotated markers on the plot (no tier legend clutter).
    """

    def __init__(self, project, scene, parent=None):
        super().__init__(parent)
        self.project = project
        self.scene = scene

        self.tabs = QTabWidget(self)
        self._widgets: Dict[str, CurveFigureWidget] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # --- top toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(6, 6, 6, 0)

        btn_reload = QPushButton("Reload curves")
        btn_reload.setToolTip("Replot IEC curves using current figure definitions")
        btn_reload.clicked.connect(self.redraw_all)

        toolbar.addWidget(btn_reload)
        toolbar.addStretch(1)

        lay.addLayout(toolbar)
        lay.addWidget(self.tabs)

        # Build one figure tab per IEC figure definition
        for fd in FIGURE_DEFS:
            w = CurveFigureWidget(
                title=fd.title,
                xlabel=fd.xlabel,
                ylabel=fd.ylabel,
                legend_title=fd.legend_title,
                parent=self,
            )
            self._widgets[fd.key] = w
            self.tabs.addTab(w, fd.tab_label)

        self.redraw_all()

    # -----------------------------------------------------------------
    # Public hook (call when geometry / tiers change)
    # -----------------------------------------------------------------
    def on_tier_geometry_committed(self):
        self.redraw_all()

    # -----------------------------------------------------------------
    # Main redraw
    # -----------------------------------------------------------------
    def redraw_all(self):
        tiers = self._tiers_on_scene()
        tier_color_map = self._build_tier_color_map(tiers)

        # 1) Base curves for every figure
        for fd in FIGURE_DEFS:
            w = self._widgets.get(fd.key)
            if not w:
                continue
            w.set_tier_color_map(tier_color_map)
            w.clear()
            w.draw_base_curves(fd.draw_base)
            w.redraw()

        # 2) Tier markers + annotations per figure
        self._draw_tier_markers(tiers)
        self._final_redraw()

    # -----------------------------------------------------------------
    # Scene helpers
    # -----------------------------------------------------------------
    def _tiers_on_scene(self) -> List[TierItem]:
        if self.scene is None:
            return []
        return [it for it in self.scene.items() if isinstance(it, TierItem)]

    def _build_tier_color_map(self, tiers: List[TierItem]) -> Dict[str, Tuple[float, float, float, float]]:
        """
        Deterministic tier→color mapping (consistent across all figures).
        Uses Matplotlib default prop cycle to avoid hardcoding a palette.
        """
        cycle = list(mpl.rcParams["axes.prop_cycle"].by_key().get("color", []))
        if not cycle:
            cycle = ["#1f77b4"]  # safe fallback

        out: Dict[str, Tuple[float, float, float, float]] = {}
        for i, t in enumerate(sorted(tiers, key=lambda x: (getattr(x, "name", ""), getattr(x, "tag", "")))):
            name = getattr(t, "name", getattr(t, "tag", "Tier")) or "Tier"
            c = cycle[i % len(cycle)]
            out[name] = mpl.colors.to_rgba(c)
        return out

    # -----------------------------------------------------------------
    # Tier markers
    # -----------------------------------------------------------------
    def _draw_tier_markers(self, tiers: List[TierItem]) -> None:
        if not tiers:
            return

        for tier in tiers:
            tier_name = getattr(tier, "name", getattr(tier, "tag", "Tier")) or "Tier"

            # IMPORTANT:
            # For ventilated figures, use the tier’s selected vent area as the inlet area.
            inlet_area_cm2 = float(getattr(tier, "vent_area_for_iec", lambda: 0.0)())

            used = curvefit.evaluate_tier(
                tier=tier,
                all_tiers=tiers,
                inlet_area_cm2=inlet_area_cm2 if inlet_area_cm2 > 0 else 300.0,  # fallback
            )

            # used contains keys like "k", "c" → CurvePoint(figure, x, y, snapped_param)
            for coeff_key, cp in used.items():
                fig_key = cp.figure  # "Fig3"..."Fig8"
                w = self._widgets.get(fig_key)
                if not w:
                    continue

                var_label = "k" if coeff_key == "k" else "c" if coeff_key == "c" else coeff_key

                # Base annotation: tier name + x + y
                text = (
                    f"{tier_name}\n"
                    f"x = {cp.x:.1f}\n"
                    f"{var_label} = {cp.y:.3f}"
                )

                # If the point was snapped to a defined IEC curve family, show it
                if cp.snapped_param is not None:
                    if fig_key == "Fig5":
                        text += f"\n(Ae = {cp.snapped_param:g} m²)"
                    elif fig_key == "Fig6":
                        text += f"\n(f = {cp.snapped_param:g})"

                pt = TierPoint(
                    tier_name=tier_name,
                    x=float(cp.x),
                    y=float(cp.y),
                    text=text,
                    color=None,  # CurveFigureWidget resolves via tier color map
                )

                w.draw_tier_points([pt])

    def _final_redraw(self):
        for w in self._widgets.values():
            w.redraw()
