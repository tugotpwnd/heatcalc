
"""
cable_table.py
---------------
Utility to load cable operating current (I_n) and power loss per metre (P_n in W/m)
for insulated copper conductors inside switchboard enclosures at 70°C max conductor temp.

Data source: IEC 60890 Table B.1 (transcribed).
You can edit 'cable_table.csv' to change or extend the dataset.

API:
- load_cable_table(csv_path: str | Path) -> list[dict]
- interpolate_loss(csa_mm2: float, I_A: float, air_temp_C: int = 35) -> dict
"""

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict
import csv
import bisect
from ..utils.resources import get_resource_path

# cable_path = Path(__file__).resolve().parents[1] / "data" / "cable_table.csv"
cable_path = get_resource_path("heatcalc/data/cable_table.csv")

@dataclass(frozen=True)
class CableRow:
    csa: float
    In_35: float
    Pn_35: float
    In_55: float
    Pn_55: float


def load_cable_table(csv_path: str | Path = cable_path) -> List[CableRow]:
    rows: List[CableRow] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                CableRow(
                    csa=float(r["CSA_mm2"]),
                    In_35=float(r["In_A_35C"]),
                    Pn_35=float(r["Pn_Wpm_35C"]),
                    In_55=float(r["In_A_55C"]),
                    Pn_55=float(r["Pn_Wpm_55C"]),
                )
            )
    rows.sort(key=lambda x: x.csa)
    return rows


def _col(rows: List[CableRow], attr: str) -> List[float]:
    return [getattr(r, attr) for r in rows]


def _lerp(x0: float, y0: float, x1: float, y1: float, x: float) -> float:
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def _interp(rows: List[CableRow], x_attr: str, y_attr: str, x: float) -> float:
    xs = _col(rows, x_attr)
    i = bisect.bisect_left(xs, x)
    if i == 0:
        return getattr(rows[0], y_attr)
    if i >= len(rows):
        return getattr(rows[-1], y_attr)
    x0 = xs[i - 1]
    x1 = xs[i]
    y0 = getattr(rows[i - 1], y_attr)
    y1 = getattr(rows[i], y_attr)
    return _lerp(x0, y0, x1, y1, x)


def interpolate_loss(csa_mm2: float, I_A: float, air_temp_C: int = 35, csv_path: str | Path = cable_path) -> Dict:
    """
    Given CSA (mm^2), load current I (A), and air temp inside enclosure (35 or 55°C),
    compute per-metre loss using P = Pn * (I/In)^2 and return a details dict.

    Returns:
        {
          "csa_mm2": ...,
          "air_temp_C": 35|55,
          "In_A": ...,
          "Pn_Wpm": ...,
          "I_A": ...,
          "P_Wpm": ...,
        }
    """
    rows = load_cable_table(csv_path)
    if air_temp_C not in (35, 55):
        air_temp_C = 35

    if air_temp_C == 35:
        In = _interp(rows, "csa", "In_35", csa_mm2)
        Pn = _interp(rows, "csa", "Pn_35", csa_mm2)
    else:
        In = _interp(rows, "csa", "In_55", csa_mm2)
        Pn = _interp(rows, "csa", "Pn_55", csa_mm2)

    factor = (I_A / In) ** 2 if In > 0 else 0.0
    return {
        "csa_mm2": csa_mm2,
        "air_temp_C": air_temp_C,
        "In_A": In,
        "Pn_Wpm": Pn,
        "I_A": I_A,
        "P_Wpm": Pn * factor,
    }
