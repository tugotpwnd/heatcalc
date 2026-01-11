# --- imports at top of file ---
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Tuple, Any
import csv, bisect, re
from ..utils.resources import get_resource_path

cable_path = get_resource_path("heatcalc/data/cable_table.csv")

@dataclass(frozen=True)
class CableRowInstall:
    csa: float
    In: float
    Pn: float

# (temp_C, install_type) -> sorted list of CableRowInstall
TableIndex = Dict[Tuple[int, int], List[CableRowInstall]]

# Matches: In_A_(35)-1  /  Pn_Wpm_(55)-3   (temperature in parentheses, type after dash)
_COL_RE = re.compile(r'^(In|Pn)[^()]*\((\d+)\)\s*-\s*(\d+)\s*$')

def load_cable_table(csv_path: str | Path = cable_path) -> TableIndex:
    rows_per_key: TableIndex = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        # map (temp, inst) -> {'IN': colname, 'PN': colname}
        col_map: Dict[Tuple[int,int], Dict[str,str]] = {}
        for col in headers:
            if col == "CSA_mm2":
                continue
            m = _COL_RE.match((col or "").strip())
            if not m:
                continue
            kind, t, inst = m.groups()
            key = (int(t), int(inst))
            d = col_map.setdefault(key, {})
            d[kind.upper()] = col

        if not col_map:
            print("[cable_table] !! No (temp,install) column pairs detected. Check CSV headers and regex.")


        f.seek(0); reader = csv.DictReader(f)
        for rec in reader:
            try:
                csa = float((rec.get("CSA_mm2") or "").strip())
            except Exception:
                continue
            for key, kinds in col_map.items():
                in_col = kinds.get("IN"); pn_col = kinds.get("PN")
                if not in_col or not pn_col:
                    continue
                try:
                    In = float((rec.get(in_col) or "0").strip())
                    Pn = float((rec.get(pn_col) or "0").strip())
                except Exception:
                    In, Pn = 0.0, 0.0
                rows_per_key.setdefault(key, []).append(CableRowInstall(csa, In, Pn))

    # sort by CSA and print series coverage
    for key, series in rows_per_key.items():
        series.sort(key=lambda r: r.csa)
    return rows_per_key

def _interp(series: List[CableRowInstall], x_csa: float, attr: str) -> float:
    """Linear interpolate attribute 'In' or 'Pn' as a function of CSA."""
    xs = [r.csa for r in series]
    i = bisect.bisect_left(xs, x_csa)
    if not series:
        return 0.0
    if i == 0:
        return getattr(series[0], attr)
    if i >= len(series):
        return getattr(series[-1], attr)
    x0, x1 = xs[i-1], xs[i]
    y0, y1 = getattr(series[i-1], attr), getattr(series[i], attr)
    if x1 == x0:
        return y0
    t = (x_csa - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

def interpolate_loss(
    csa_mm2: float,
    I_A: float,
    air_temp_C: int = 35,
    install_type: int = 1,
    csv_path: str | Path = cable_path
) -> Dict[str, Any]:
    """Return dict with In_A, Pn_Wpm, P_Wpm for selected (temp, install)."""
    table = load_cable_table(csv_path)
    if not table:
        print("[cable_table] !! Empty table after load.")
        return {"csa_mm2": csa_mm2, "I_A": I_A, "air_temp_C": air_temp_C,
                "install_type": install_type, "In_A": 0.0, "Pn_Wpm": 0.0, "P_Wpm": 0.0}

    key = (int(air_temp_C), int(install_type))
    if key not in table:
        print(f"[cable_table] Key {key} not found. Available: {sorted(table.keys())}")
        # fallback: same temp, type 1; else first available
        key = (int(air_temp_C), 1) if (int(air_temp_C), 1) in table else sorted(table.keys())[0]
        print(f"[cable_table] Falling back to {key}")

    series = table[key]
    In = _interp(series, float(csa_mm2), "In")
    Pn = _interp(series, float(csa_mm2), "Pn")
    P  = Pn * ((I_A / In) ** 2) if In > 0 else 0.0

    print(f"[cable_table] Lookup CSA={csa_mm2} I={I_A} using key={key} → In={In:.3f}, Pn={Pn:.3f}, P={P:.3f} W/m")
    return {
        "csa_mm2": float(csa_mm2),
        "I_A": float(I_A),
        "air_temp_C": int(key[0]),
        "install_type": int(key[1]),
        "In_A": float(In),
        "Pn_Wpm": float(Pn),
        "P_Wpm": float(P),
    }
