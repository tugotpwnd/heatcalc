# heatcalc/ui/cable_table.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional
import csv

from ..utils.resources import get_resource_path

cable_path = get_resource_path("heatcalc/data/cable_table.csv")

# ---------------- Data model ----------------
@dataclass(frozen=True)
class CableRow:
    csa_mm2: float
    r20_mohm_per_m: float          # informational only
    Imax: Dict[int, Optional[float]]
    Pv: Dict[int, Optional[float]] # W/m at Imax (from table)


Table = Dict[float, CableRow]  # keyed by CSA


# ---------------- Loader ----------------
def load_cable_table(csv_path: str | Path = cable_path) -> Table:
    table: Table = {}

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for rec in reader:
            raw_csa = (rec.get("CSA_mm2") or "").strip()
            if not raw_csa:
                continue

            csa = float(raw_csa)
            r20 = float(rec["R_20"])

            def _val(key: str) -> Optional[float]:
                v = (rec.get(key) or "").strip()
                return None if v in ("", "-", "–") else float(v)

            table[csa] = CableRow(
                csa_mm2=csa,
                r20_mohm_per_m=r20,
                Imax={
                    1: _val("Imax-1"),
                    2: _val("Imax-2"),
                    3: _val("Imax-3"),
                },
                Pv={
                    1: _val("Pv-1"),
                    2: _val("Pv-2"),
                    3: _val("Pv-3"),
                },
            )

    return table


def cable_loss(
    *,
    table: Table,
    csa_mm2: float,
    install_type: int,
    current_A: float,
    length_m: float,
) -> dict:

    row = table.get(csa_mm2)
    if row is None:
        raise ValueError(f"CSA {csa_mm2} mm² not found in cable table")

    Imax = row.Imax.get(install_type)
    Pv   = row.Pv.get(install_type)

    if Imax is None or Pv is None:
        raise ValueError(
            f"Installation type {install_type} is not permitted for {csa_mm2} mm² cable"
        )

    P_Wpm = Pv * (current_A / Imax) ** 2
    total_W = P_Wpm * length_m

    return {
        "Imax_A": Imax,
        "Pv_Wpm": Pv,  # ← table Pv at Imax
        "P_Wpm": P_Wpm,
        "total_W": total_W,
    }
