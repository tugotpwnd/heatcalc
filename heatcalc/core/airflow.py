from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AirflowResult:
    deltaT_K: float
    q_walls_W: float
    q_fans_W: float
    airflow_m3h: Optional[float]


def required_airflow_with_wall_loss(
    *,
    P_W: float,
    amb_C: float,
    max_internal_C: float,
    enclosure_area_m2: float,
    allow_wall_dissipation: bool,
    k_W_per_m2K: float,
    rho_kg_per_m3: float = 1.20,
    cp_J_per_kgK: float = 1005.0,
    safety_factor: float = 1.20,
) -> AirflowResult:
    """
    Parallel heat paths:
        P = q_walls + q_fans
        q_walls = k * A * ΔT
        q_fans  = P - q_walls
    """

    deltaT = max_internal_C - amb_C

    if P_W <= 0.0:
        return AirflowResult(deltaT, 0.0, 0.0, 0.0)

    if deltaT <= 0.0:
        return AirflowResult(deltaT, 0.0, P_W, None)

    q_walls = 0.0
    if allow_wall_dissipation and enclosure_area_m2 > 0.0:
        q_walls = min(k_W_per_m2K * enclosure_area_m2 * deltaT, P_W)

    q_fans = max(0.0, P_W - q_walls)

    airflow_m3s = q_fans / (rho_kg_per_m3 * cp_J_per_kgK * deltaT)
    airflow_m3h = airflow_m3s * 3600.0 * safety_factor

    return AirflowResult(deltaT, q_walls, q_fans, airflow_m3h)
