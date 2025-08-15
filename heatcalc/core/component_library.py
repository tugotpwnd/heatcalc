"""In-memory component library for drag/drop later.
For now, a simple dict of common items and their nominal heat loss (W)."""
from typing import Dict


DEFAULT_COMPONENTS: Dict[str, float] = {
    "MCCB 250A": 12.0,
    "Contactor 40A": 5.5,
    "VFD 7.5kW": 90.0,
    "PLC CPU": 8.0,
    "Power Supply 24V/10A": 18.0,
}
