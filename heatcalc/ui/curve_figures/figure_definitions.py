# heatcalc/ui/curve_figures/figure_definitions.py
from __future__ import annotations

import math
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

    # Valid IEC domain ONLY
    xs = [1.25 + i * 0.05 for i in range(int((12.0 - 1.25) / 0.05) + 1)]
    ys = [curvefit.k_fig3(x) for x in xs]

    ax.plot(xs, ys, lw=1.6, label="IEC curve")

    # Axis formatting to match IEC figure
    ax.set_xlim(1.25, 12.0)
    ax.set_ylim(0, 1.2)

    ax.set_xlabel(r"$A_e$ (m²)")
    ax.set_ylabel("Enclosure constant k (—)")

    ax.grid(True, which="major", ls="-", alpha=0.35)
    ax.grid(True, which="minor", ls=":", alpha=0.25)
    ax.minorticks_on()


def _draw_fig4(ax: Axes) -> None:
    # IEC 60890 Fig.4 — c vs f (no ventilation, Ae > 1.25 m²)

    # f range per IEC figure
    fvals = [i * 0.1 for i in range(1, 171)]  # 0.1 .. 17.0

    # Installation types / curve numbers (ordered top → bottom as IEC)
    curve_numbers = [1, 2, 3, 4, 5]

    for curve_no in curve_numbers:
        ys = [curvefit.c_fig4(curve_no, f) for f in fvals]
        ax.plot(
            fvals,
            ys,
            lw=1.4,
            label=f"{curve_no}",
        )

    # Axis formatting to match IEC figure
    ax.set_xlim(0, 17)
    ax.set_ylim(1.1, 1.7)

    ax.set_xlabel("f (—)")
    ax.set_ylabel("Temperature distribution factor c (—)")

    ax.grid(True, which="major", ls="-", alpha=0.35)
    ax.grid(True, which="minor", ls=":", alpha=0.25)
    ax.minorticks_on()


def _draw_fig5(ax: Axes) -> None:
    # IEC 60890 Fig.5 — k vs inlet opening area (ventilated, Ae > 1.25 m²)

    # Inlet opening area range per IEC
    areas = [i * 10 for i in range(1, 101)]  # 10 .. 1000 cm²

    # Effective cooling surface values shown in IEC figure
    ae_values = [1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14]

    for ae in ae_values:
        ys = [curvefit.k_fig5(ae, a) for a in areas]
        ax.plot(
            areas,
            ys,
            lw=1.3,
            label=f"{ae:g}",
        )

    # Axis formatting to match IEC figure
    ax.set_xlim(0, 1000)
    ax.set_ylim(0.05, 0.50)

    ax.set_xlabel(r"$S_{air}$ (cm²)")
    ax.set_ylabel("Enclosure constant k (—)")

    ax.grid(True, which="major", ls="-", alpha=0.35)
    ax.grid(True, which="minor", ls=":", alpha=0.25)
    ax.minorticks_on()


def _draw_fig6(ax: Axes) -> None:
    # IEC 60890 Fig.6 — c vs inlet opening area (ventilated, Ae > 1.25 m²)

    # Inlet opening area range per IEC
    areas = [i * 10 for i in range(1, 101)]  # 10 .. 1000 cm²

    # Representative height/base factors (ordered as in IEC legend)
    f_values = [1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    for f in f_values:
        ys = [curvefit.c_fig6(f, a) for a in areas]
        ax.plot(
            areas,
            ys,
            lw=1.4,
            label=f"{f:g}",
        )

    # Axis formatting to match IEC figure
    ax.set_xlim(0, 1000)
    ax.set_ylim(1.2, 2.3)

    ax.set_xlabel(r"$S_{air}$ (cm²)")
    ax.set_ylabel("Temperature distribution factor c (—)")

    ax.grid(True, which="major", ls="-", alpha=0.35)
    ax.grid(True, which="minor", ls=":", alpha=0.25)

    ax.minorticks_on()

def _draw_fig7(ax: Axes) -> None:
    # IEC 60890 Fig.7 — k vs Ae (no ventilation, Ae ≤ 1.25 m²)

    # Log-spaced Ae values (≈ 0.01 → 1.25 m²)
    xs = [10 ** (-2 + i * (math.log10(1.25) + 2) / 200) for i in range(201)]
    ys = [curvefit.k_fig7(x) for x in xs]

    ax.plot(xs, ys, lw=1.6, label="IEC curve")

    # Log–log axes to match IEC figure
    ax.set_xscale("log")
    ax.set_yscale("log")

    # Axis limits aligned with the standard figure
    ax.set_xlim(0.01, 1.25)
    ax.set_ylim(0.1, 10)

    ax.grid(True, which="both", ls="--", alpha=0.4)



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
