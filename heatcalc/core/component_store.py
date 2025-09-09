# heatcalc/core/component_store.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import sys
import csv

@dataclass(frozen=True)
class ComponentRow:
    category: str
    part_number: str
    description: str
    heat_w: float
    max_temp_C: int = 70  # default safe rating

# Accept common header variants (case/spacing insensitive)
ALIASES: Dict[str, list[str]] = {
    "category":    ["category", "Category", "group", "Group"],
    "part_number": ["part_number", "PartNumber", "partnumber", "Part #", "Part#", "Part No", "PartNo", "PN"],
    "description": ["description", "Description", "Desc", "Name", "Title"],
    "heat_w":      ["heat_w", "HeatLoss", "Heat (W)", "Heat", "Watts", "W", "PowerLoss", "Power (W)"],
    "max_temp_C":  [
        "max_temp_C", "Max Temp (°C)", "MaxTemp", "Max Temperature", "Temperature (°C)",
        "Temp (°C)", "Max T", "Tmax", "MaxTempC", "Max Temp C", "Max Temperature (°C)"
    ],
}

# The header we WRITE when creating/appending the CSV
CANON_HEADERS: Tuple[str, str, str, str, str] = (
    "Category", "Part #", "Description", "Heat (W)", "Max Temp (°C)"
)

def _norm(s: str) -> str:
    return (s or "").strip()

def _map_headers(fieldnames: List[str]) -> Dict[str, Optional[str]]:
    """Return a map from canonical field -> actual CSV header (or None if not found)."""
    out: Dict[str, Optional[str]] = {k: None for k in ALIASES}
    lower = {fn.lower().strip(): fn for fn in fieldnames}
    for canon, candidates in ALIASES.items():
        for cand in candidates:
            key = cand.lower().strip()
            if key in lower:
                out[canon] = lower[key]
                break
    return out

def resolve_components_csv() -> Path:
    """
    Preferred resolution order:
      1) <folder of the EXE>/components.csv   (when bundled by PyInstaller)
      2) <cwd>/components.csv                  (if user placed it)
      3) repo fallback: heatcalc/data/components.csv
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        p = exe_dir / "components.csv"
        if p.exists():
            return p
        # If missing, still *prefer* to create it next to exe for user editing:
        return p

    # non-frozen: prefer a local components.csv if developer placed one
    cwd_csv = Path.cwd() / "components.csv"
    if cwd_csv.exists():
        return cwd_csv

    # fallback to repo copy
    return Path(__file__).resolve().parents[1] / "data" / "components.csv"

def load_component_catalog(csv_path: Path) -> List[ComponentRow]:
    if not csv_path.exists():
        return []

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except Exception:
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            return []

        header_map = _map_headers(reader.fieldnames)
        rows: List[ComponentRow] = []
        for rec in reader:
            cat  = _norm(rec.get(header_map["category"] or "", ""))
            pn   = _norm(rec.get(header_map["part_number"] or "", ""))
            desc = _norm(rec.get(header_map["description"] or "", ""))
            heat_raw = _norm(rec.get(header_map["heat_w"] or "", ""))
            max_raw  = _norm(rec.get(header_map["max_temp_C"] or "", ""))

            try:
                heat = float(heat_raw.replace(",", "")) if heat_raw else 0.0
            except Exception:
                heat = 0.0

            try:
                cleaned = max_raw.replace("°", "").replace("C", "").replace("c", "").strip()
                max_temp = int(float(cleaned)) if cleaned else 70
            except Exception:
                max_temp = 70

            if not (cat or pn or desc):
                continue

            rows.append(ComponentRow(
                category=cat,
                part_number=pn,
                description=desc,
                heat_w=heat,
                max_temp_C=max_temp,
            ))
        return rows

def _ensure_csv_with_header(csv_path: Path) -> None:
    if not csv_path.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CANON_HEADERS)

def append_component_to_csv(csv_path: Path, row: ComponentRow) -> None:
    """Append a single component line; create file with canonical header if needed."""
    _ensure_csv_with_header(csv_path)
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            row.category,
            row.part_number,
            row.description,
            f"{row.heat_w:.6g}",
            int(row.max_temp_C),
        ])
