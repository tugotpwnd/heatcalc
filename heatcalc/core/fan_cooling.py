"""Fan operating-point estimate using the existing Annex K heat balance.

Invert P = P_890(dT) + (1160 * altitude_factor) * airflow/3600 * dT.
The enclosure term uses the same sealed coefficients as minimum fan sizing;
natural openings are not counted a second time. Mid-height temperature retains
the sealed IEC profile ratio: this is an estimate, not a forced-flow CFD model.
"""
from __future__ import annotations

import math


def temperature_rise_for_airflow(*, power_W, airflow_m3h, k, c, x,
                                 altitude_factor=1.0):
    values = (power_W, airflow_m3h, k, c, x, altitude_factor)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Fan calculation inputs must be finite.")
    if power_W < 0 or airflow_m3h < 0 or min(k, c, x, altitude_factor) <= 0:
        raise ValueError("Invalid power, airflow or enclosure coefficients.")
    low, high = 0.0, c * k * power_W ** x
    conductance = 1160.0 * altitude_factor * airflow_m3h / 3600.0
    for _ in range(80):
        rise = (low + high) / 2.0
        removed = (rise / (c * k)) ** (1.0 / x) + conductance * rise
        if removed < power_W:
            low = rise
        else:
            high = rise
    return (low + high) / 2.0


def apply_fan_operating_point(result, *, airflow_m3h, altitude_m):
    """Return a fresh result; retain minimum-required airflow separately."""
    from .iec60890_calc import annex_k_sealed_p890, air_k_factor_from_altitude_m

    flow = float(airflow_m3h)
    if not math.isfinite(flow) or flow < 0:
        raise ValueError("Selected airflow must be finite and non-negative.")
    out = dict(result)
    out["selected_airflow_m3h"] = flow
    out["uncooled_T_top"] = result["T_top"]
    out["uncooled_T_mid"] = result["T_mid"]
    if flow == 0:
        return out

    ak = annex_k_sealed_p890(
        Ae=result["Ae"], h_m=result["h_m"], w_m=result["w_m"],
        d_m=result["d_m"], curve_no=result["curve_no"],
        delta_allow_K=max(0.0, result["limit_C"] - result["ambient_C"]
                          - result.get("solar_dt", 0.0)),
    )
    rise = temperature_rise_for_airflow(
        power_W=result["P"], airflow_m3h=flow, k=ak["k"], c=ak["c"],
        x=ak["x"], altitude_factor=air_k_factor_from_altitude_m(altitude_m),
    )
    base = result["ambient_C"] + result.get("solar_dt", 0.0)
    delta_allow = result["limit_C"] - base
    fan_power_required = max(0.0, result["P"] - ak["P_890"])
    minimum_flow = (3600.0 * fan_power_required /
                    (1160.0 * air_k_factor_from_altitude_m(altitude_m) * delta_allow)
                    if delta_allow > 0 else 0.0)
    out.update(
        T_top=base + rise, T_mid=base + rise / ak["c"],
        dt_top=rise, dt_mid=rise / ak["c"],
        T_075=base + rise if result["Ae"] <= 1.25 else None,
        dt_075=rise if result["Ae"] <= 1.25 else None,
        compliant_top=delta_allow > 0 and base + rise <= result["limit_C"] + 1e-7,
        compliant_mid=delta_allow > 0 and base + rise / ak["c"] <= result["limit_C"] + 1e-7,
        fan_estimate=True,
        airflow_m3h=minimum_flow,
        P_890=min(result["P"], ak["P_890"]),
        P_fan=fan_power_required, P_cooling=fan_power_required,
        fan_heat_removed_W=1160.0 * air_k_factor_from_altitude_m(altitude_m)
                           * flow / 3600.0 * rise,
    )
    return out
