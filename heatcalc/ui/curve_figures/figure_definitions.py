# heatcalc/ui/curve_figures/figure_definitions.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from matplotlib.axes import Axes

from ...core import curvefit


@dataclass(frozen=True)
class FigureDef:
    key: str               # "Fig3", "Fig4", ...
    tab_label: str         # "Fig. 5"
    title: str             # IEC-style title
    xlabel: str
    ylabel: str
    legend_title: Optional[str]
    draw_base: Callable[[Axes], None]


def _draw_fig3(ax: Axes) -> None:
    # IEC 60890 Fig.3 — k vs Ae (no ventilation, Ae > 1.25 m²)
    xs = [i * 0.1 for i in range(5, 121)]  # 0.5 .. 12.0
    ys = [curvefit.k_fig3(x) for x in xs]
    ax.plot(xs, ys, lw=1.6, label="IEC curve")


def _draw_fig4(ax: Axes) -> None:
    # IEC 60890 Fig.4 — c vs f (no ventilation)
    fvals = [1.0 + i * 0.1 for i in range(0, 101)]  # 1.0 .. 11.0
    for curve_no in (1, 2, 3, 4, 5):
        ys = [curvefit.c_fig4(curve_no, f) for f in fvals]
        ax.plot(fvals, ys, lw=1.2, label=f"Curve {curve_no}")


def _draw_fig5(ax: Axes) -> None:
    # IEC 60890 Fig.5 — k vs inlet opening area (ventilated, Ae > 1.25 m²)
    areas = [i * 10 for i in range(0, 71)]  # 0 .. 700 cm²
    for ae in sorted(curvefit.FIG5_CURVES):
        ys = [curvefit.k_fig5(ae, a) for a in areas]
        ax.plot(areas, ys, lw=1.0, alpha=0.85, label=f"Ae = {ae:g} m²")


def _draw_fig6(ax: Axes) -> None:
    # IEC 60890 Fig.6 — c vs inlet opening area (ventilated, Ae > 1.25 m²)
    areas = [i * 10 for i in range(0, 71)]  # 0 .. 700 cm²
    for f in sorted(curvefit.FIG6_CURVES):
        ys = [curvefit.c_fig6(f, a) for a in areas]
        ax.plot(areas, ys, lw=1.0, alpha=0.85, label=f"f = {f:g}")


def _draw_fig7(ax: Axes) -> None:
    # IEC 60890 Fig.7 — k vs Ae (no ventilation, Ae ≤ 1.25 m²)
    xs = [0.05 + i * 0.02 for i in range(0, 65)]  # 0.05 .. 1.35
    ys = [curvefit.k_fig7(x) for x in xs]
    ax.plot(xs, ys, lw=1.6, label="IEC curve")


def _draw_fig8(ax: Axes) -> None:
    # IEC 60890 Fig.8 — c vs g (no ventilation, Ae ≤ 1.25 m²)
    gs = [i * 0.05 for i in range(0, 61)]  # 0 .. 3.0
    ys = [curvefit.c_fig8(g) for g in gs]
    ax.plot(gs, ys, lw=1.6, label="IEC curve")


FIGURE_DEFS: List[FigureDef] = [
    FigureDef(
        key="Fig3",
        tab_label="Fig. 3",
        title=(
            "Figure 3 — Enclosure constant k for enclosures without ventilation openings\n"
            "and effective cooling surface Ae > 1.25 m²"
        ),
        xlabel="Effective cooling surface Ae (m²)",
        ylabel="Enclosure constant k (—)",
        legend_title=None,
        draw_base=_draw_fig3,
    ),
    FigureDef(
        key="Fig4",
        tab_label="Fig. 4",
        title=(
            "Figure 4 — Enclosure constant c for enclosures without ventilation opening\n"
            "and effective cooling surface Ae > 1.25 m²"
        ),
        xlabel="f (—)",
        ylabel="Enclosure constant c (—)",
        legend_title="Installation type curve",
        draw_base=_draw_fig4,
    ),
    FigureDef(
        key="Fig5",
        tab_label="Fig. 5",
        title=(
            "Figure 5 — Enclosure constant k for enclosures with ventilation openings\n"
            "and effective cooling surface Ae > 1.25 m²"
        ),
        xlabel="Inlet opening area (cm²)",
        ylabel="Enclosure constant k (—)",
        legend_title="Effective cooling surface Ae",
        draw_base=_draw_fig5,
    ),
    FigureDef(
        key="Fig6",
        tab_label="Fig. 6",
        title=(
            "Figure 6 — Enclosure constant c for enclosures with ventilation openings\n"
            "and effective cooling surface Ae > 1.25 m²"
        ),
        xlabel="Inlet opening area (cm²)",
        ylabel="Enclosure constant c (—)",
        legend_title="f parameter",
        draw_base=_draw_fig6,
    ),
    FigureDef(
        key="Fig7",
        tab_label="Fig. 7",
        title=(
            "Figure 7 — Enclosure constant k for enclosures without ventilation openings\n"
            "and effective cooling surface Ae ≤ 1.25 m²"
        ),
        xlabel="Effective cooling surface Ae (m²)",
        ylabel="Enclosure constant k (—)",
        legend_title=None,
        draw_base=_draw_fig7,
    ),
    FigureDef(
        key="Fig8",
        tab_label="Fig. 8",
        title=(
            "Figure 8 — Enclosure constant c for enclosures without ventilation openings\n"
            "and effective cooling surface Ae ≤ 1.25 m²"
        ),
        xlabel="g (—)",
        ylabel="Enclosure constant c (—)",
        legend_title=None,
        draw_base=_draw_fig8,
    ),
]
