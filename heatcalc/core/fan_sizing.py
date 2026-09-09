"""Shared operating-point data for the interactive fan chart and its report."""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from .compliance_61439 import evaluate_derating
from .iec60890_calc import calc_tier_iec60890


@dataclass
class DeviceOperatingPoint:
    label: str
    name: str
    quantity: int
    rated_A: float
    current_A: float
    max_temp_C: float
    currents_A: list[float]


@dataclass
class FanSizingData:
    tier_name: str
    airflow_m3h: float
    maximum_m3h: float
    result: dict
    flows_m3h: list[float]
    tops_C: list[float]
    mids_C: list[float]
    devices: list[DeviceOperatingPoint]
    excluded_count: int


def has_valid_derating_curve(component, temperatures):
    """Do not plot the evaluator's 80% fallback as a manufacturer curve."""
    expression = getattr(component, "derating_function", None)
    rated = getattr(component, "rated_current_A", None)
    if not isinstance(expression, str) or not expression.strip() or rated is None:
        return False
    try:
        if not math.isfinite(float(rated)) or float(rated) <= 0:
            return False
        code = compile(expression, "<derating curve>", "eval")
        start = getattr(component, "derating_temp_start_C", None)
        # Always probe above the threshold as well, even if this chart's whole
        # temperature range is below it (where the main evaluator bypasses it).
        probes = [*temperatures, float(start) + 1 if start is not None else 40.0]
        for temperature in probes:
            if start is not None and temperature <= start:
                continue
            value = eval(code, {"__builtins__": {}}, {"x": temperature, "math": math})
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                return False
    except Exception:
        return False
    return True


def build_fan_sizing_data(switchboard, tier, result, maximum_m3h):
    flow = float(result.get("selected_airflow_m3h", 0))
    maximum = max(float(maximum_m3h), flow, 1.0)
    # Include the operating point exactly. Zero is a separate natural-cooling
    # point because positive flow uses the sealed fan balance.
    flows = sorted(set(np.linspace(maximum / 240, maximum, 120).tolist()
                       + ([flow] if flow > 0 else [])))
    tiers = list(switchboard._tiers())
    meta = switchboard.project.meta
    results = [calc_tier_iec60890(
        tier=tier, tiers=tiers, wall_mounted=switchboard.cb_wall.isChecked(),
        inlet_area_cm2=result.get("inlet_area_cm2", 0),
        ambient_C=result["ambient_C"], altitude_m=meta.altitude_m,
        ip_rating_n=meta.ip_rating_n, solar_delta_K=result.get("solar_dt", 0),
        P_override_W=result["P"], selected_airflow_m3h=value,
    ) for value in flows]
    tops = [r["T_top"] for r in results]
    devices = []
    for component in tier.component_entries:
        if not has_valid_derating_curve(component, [*tops, result["T_top"]]):
            continue
        name = component.description or component.key
        if component.part_number:
            name += f" ({component.part_number})"
        devices.append(DeviceOperatingPoint(
            label=f"D{len(devices) + 1:02d}", name=name, quantity=component.qty,
            rated_A=float(component.rated_current_A),
            current_A=evaluate_derating(component, result["T_top"]),
            max_temp_C=float(component.max_temp_C),
            currents_A=[evaluate_derating(component, t) for t in tops],
        ))
    return FanSizingData(
        tier_name=tier.name, airflow_m3h=flow, maximum_m3h=maximum,
        result=result, flows_m3h=flows, tops_C=tops,
        mids_C=[r["T_mid"] for r in results], devices=devices,
        excluded_count=len(tier.component_entries) - len(devices),
    )
