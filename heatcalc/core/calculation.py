"""IEC 60890 calculation entry points.

This module will host the computation of effective surface area A_e, temperature
rise at mid-height and top, and the factors b,k,d,c,x. For now we stub with
placeholders so the app can wire autosave & flow.
"""
from typing import Dict
from .models import Project


def run_iec60890(project: Project) -> Project:
    # TODO: implement real method. For now, produce deterministic placeholders.
    total_heat = sum(cell.total_heat_w for tier in project.layout.tiers for cell in tier.cells)
    area_guess = 1.0 + 0.0001 * total_heat
    project.outputs.ae_m2 = area_guess
    project.outputs.delta_t_mid_c = 0.05 * total_heat
    project.outputs.delta_t_top_c = 0.07 * total_heat
    project.outputs.factors = {"b": 0.9, "k": 1.1, "d": 0.4, "c": 0.3, "x": 0.2}
    return project
