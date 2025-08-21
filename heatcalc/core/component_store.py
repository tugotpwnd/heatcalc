from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional
from pathlib import Path
import csv

@dataclass(frozen=True)
class ComponentRow:
    category: str
    part_number: str
    description: str
    heat_w: float
    max_temp_C: int = 70  # NEW: per-component rating with safe default

# Accept common header variants (case/spacing insensitive)
ALIASES: Dict[str, list[str]] = {
    "category":    ["category", "Category", "group", "Group"],
    "part_number": ["part_number", "PartNumber", "partnumber", "Part #", "Part#", "Part No", "PartNo", "PN"],
    "description": ["description", "Description", "Desc", "Name", "Title"],
    "heat_w":      ["heat_w", "HeatLoss", "Heat (W)", "Heat", "Watts", "W", "PowerLoss", "Power (W)"],
    # NEW — lots of forgiving aliases; primary label we recommend in the CSV is “Max Temp (°C)”
    "max_temp_C":  [
        "max_temp_C", "Max Temp (°C)", "MaxTemp", "Max Temperature", "Temperature (°C)",
        "Temp (°C)", "Max T", "Tmax", "MaxTempC", "Max Temp C", "Max Temperature (°C)"
    ],
}

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

def load_component_catalog(csv_path: Path) -> List[ComponentRow]:
    if not csv_path.exists():
        return []

    # Sniff delimiter just in case (comma/semicolon/tab)
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
                # allow values like "70 C", "70°", etc.
                cleaned = max_raw.replace("°", "").replace("C", "").replace("c", "").strip()
                max_temp = int(float(cleaned)) if cleaned else 70
            except Exception:
                max_temp = 70

            # Skip totally empty lines
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
