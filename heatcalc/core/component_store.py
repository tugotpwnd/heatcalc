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
    rated_current_A: float | None = None
    derating_temp_start_C: float | None = None
    derating_function: str | None = None

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
    "rated_current_A": ["rated_current_A", "Rated Current (A)", "RatedCurrent", "Rated Current", "Ie", "In"],
    "derating_temp_start_C": ["derating_temp_start_C", "Derating Temp Start (°C)", "DeratingTempStart", "Derating Temp Start"],
    "derating_function": ["derating_function", "Derating Function", "DeratingFunction", "Derating Func"],
}

# The header we WRITE when creating/appending the CSV
CANON_HEADERS: Tuple[str, str, str, str, str, str, str, str] = (
    "Category", "Part #", "Description", "Heat (W)", "Max Temp (°C)",
    "Rated Current (A)", "Derating Temp Start (°C)", "Derating Function"
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
import csv
import io
from pathlib import Path
from typing import List, Optional


class ComponentCatalogError(Exception):
    """User-friendly error for component CSV issues."""
    pass


def _safe_float(value: str, field_name: str, row_idx: int) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except Exception:
        raise ComponentCatalogError(
            f"Invalid numeric value in column '{field_name}' at row {row_idx}: '{value}'"
        )


def _decode_csv_bytes(csv_path: Path) -> tuple[str, str]:
    """
    Read CSV as bytes, then decode robustly.
    Returns: (decoded_text, encoding_used)

    Strategy:
      1. utf-8-sig
      2. utf-8
      3. cp1252 fallback (common Excel export)
    """
    raw = csv_path.read_bytes()

    # UTF-8 with BOM
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        pass

    # Plain UTF-8
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as e_utf8:
        # Work out approximate row for debug / user message
        bad_pos = e_utf8.start
        bad_row = raw[:bad_pos].count(b"\n") + 1
        bad_byte = raw[bad_pos]

        # Try Excel/Windows fallback
        try:
            text = raw.decode("cp1252")
            print(
                f"[WARN] Component CSV '{csv_path.name}' is not valid UTF-8. "
                f"Used cp1252 fallback instead. "
                f"First invalid UTF-8 byte at row {bad_row}, byte 0x{bad_byte:02X}."
            )
            return text, "cp1252"
        except Exception:
            raise ComponentCatalogError(
                f"Couldn't load {csv_path.name}.\n\n"
                f"The file is not valid UTF-8. First invalid character was found at row {bad_row} "
                f"(byte 0x{bad_byte:02X}).\n\n"
                f"This is usually caused by Excel smart quotes or other special characters.\n"
                f"Fix: re-save the file as 'CSV UTF-8 (Comma delimited)' in Excel."
            ) from e_utf8


def load_component_catalog(csv_path: Path) -> List["ComponentRow"]:
    if not csv_path.exists():
        return []

    try:
        text, encoding_used = _decode_csv_bytes(csv_path)

        # Use StringIO so csv module reads already-decoded text
        f = io.StringIO(text)

        # sniff dialect safely
        try:
            sample = f.read(2048)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample)
        except Exception:
            f.seek(0)
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)

        if not reader.fieldnames:
            raise ComponentCatalogError(
                f"Component CSV '{csv_path.name}' has no headers."
            )

        header_map = _map_headers(reader.fieldnames)
        rows: List[ComponentRow] = []

        for idx, rec in enumerate(reader, start=2):  # header is row 1
            try:
                cat  = _norm(rec.get(header_map["category"] or "", ""))
                pn   = _norm(rec.get(header_map["part_number"] or "", ""))
                desc = _norm(rec.get(header_map["description"] or "", ""))

                heat_raw = _norm(rec.get(header_map["heat_w"] or "", ""))
                max_raw  = _norm(rec.get(header_map["max_temp_C"] or "", ""))

                rated_current_raw = _norm(rec.get(header_map["rated_current_A"] or "", ""))
                derating_temp_raw = _norm(rec.get(header_map["derating_temp_start_C"] or "", ""))
                derating_func_raw = _norm(rec.get(header_map["derating_function"] or "", ""))

                if not (cat or pn or desc):
                    continue

                heat = _safe_float(heat_raw, "heat_w", idx) or 0.0

                try:
                    cleaned = (
                        max_raw.replace("°", "")
                               .replace("C", "")
                               .replace("c", "")
                               .strip()
                    )
                    max_temp = int(float(cleaned)) if cleaned else 70
                except Exception:
                    raise ComponentCatalogError(
                        f"Invalid max_temp_C at row {idx}: '{max_raw}'"
                    )

                rated_current = _safe_float(rated_current_raw, "rated_current_A", idx)
                derating_temp = _safe_float(derating_temp_raw, "derating_temp_start_C", idx)

                derating_func = derating_func_raw if derating_func_raw else None

                rows.append(ComponentRow(
                    category=cat,
                    part_number=pn,
                    description=desc,
                    heat_w=heat,
                    max_temp_C=max_temp,
                    rated_current_A=rated_current,
                    derating_temp_start_C=derating_temp,
                    derating_function=derating_func,
                ))

            except ComponentCatalogError:
                raise
            except Exception as e:
                raise ComponentCatalogError(
                    f"Error parsing row {idx} in '{csv_path.name}': {str(e)}"
                ) from e

        if encoding_used.lower() == "cp1252":
            print(
                f"[WARN] Component catalogue '{csv_path.name}' loaded using cp1252 fallback. "
                f"Recommend re-saving as UTF-8."
            )

        return rows

    except ComponentCatalogError:
        raise
    except Exception as e:
        raise ComponentCatalogError(
            f"Failed to load component CSV '{csv_path.name}'. Unexpected error: {str(e)}"
        ) from e

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
            f"{row.rated_current_A:.6g}" if row.rated_current_A is not None else "",
            f"{row.derating_temp_start_C:.6g}" if row.derating_temp_start_C is not None else "",
            row.derating_function or "",
        ])
