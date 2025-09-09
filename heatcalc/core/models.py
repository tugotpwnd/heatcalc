# heatcalc/core/models.py
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from ..utils.qt import signals

@dataclass
class ProjectMeta:
    job_number: str = ""
    title: str = ""
    enclosure: str = ""
    designer: str = ""
    date: str = ""
    revision: str = "A"

@dataclass
class Component:
    name: str
    heat_loss_w: float
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Cell:
    row: int
    col: int
    width_mm: float = 0.0
    height_mm: float = 0.0
    components: List[Component] = field(default_factory=list)

    @property
    def total_heat_w(self) -> float:
        return sum(c.heat_loss_w for c in self.components)

@dataclass
class Tier:
    # Grid (kept for future per-cell work)
    rows: int = 1
    cols: int = 1
    cells: List[Cell] = field(default_factory=list)
    # New: placement & identity in switchboard coordinates (mm)
    name: str = ""
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = 600.0
    height_mm: float = 1200.0
    order_index: int = 0
    arrangement: str = "standard"  # placeholder tag
    # Optional attached components directly at tier-level (future)
    components: List[Component] = field(default_factory=list)

    @property
    def rect(self):
        return (self.x_mm, self.y_mm, self.width_mm, self.height_mm)

    @property
    def total_heat_w(self) -> float:
        # Sum tier-level components + cell components
        tier_sum = sum(c.heat_loss_w for c in self.components)
        cell_sum = sum(cell.total_heat_w for cell in self.cells)
        return tier_sum + cell_sum

@dataclass
class BoardLayout:
    tiers: List[Tier] = field(default_factory=list)
    enclosure_type: str = "no_vent"  # "no_vent" or "vented"
    wall_mounted: bool = False       # <-- NEW
    # New: switchboard outer boundary (mm)
    swbd_width_mm: float = 1800.0
    swbd_height_mm: float = 2200.0

@dataclass
class CalcInputs:
    ambient_temp_c: float = 25.0
    airflow_m3ph: Optional[float] = None  # if vented, placeholder

@dataclass
class CalcOutputs:
    ae_m2: float = 0.0
    delta_t_mid_c: float = 0.0
    delta_t_top_c: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)  # b,k,d,c,x

@dataclass
class Project:
    meta: ProjectMeta = field(default_factory=ProjectMeta)
    layout: BoardLayout = field(default_factory=BoardLayout)
    inputs: CalcInputs = field(default_factory=CalcInputs)
    outputs: CalcOutputs = field(default_factory=CalcOutputs)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Project":
        meta = ProjectMeta(**data.get("meta", {}))

        layout_data = data.get("layout", {})
        tiers = []
        for t in layout_data.get("tiers", []):
            # Backward-compatible defaults
            cells = [Cell(**c) for c in t.get("cells", [])]
            tiers.append(Tier(
                rows=t.get("rows", 1), cols=t.get("cols", 1), cells=cells,
                name=t.get("name", ""),
                x_mm=t.get("x_mm", 0.0), y_mm=t.get("y_mm", 0.0),
                width_mm=t.get("width_mm", 600.0), height_mm=t.get("height_mm", 1200.0),
                order_index=t.get("order_index", 0),
                arrangement=t.get("arrangement", "standard"),
                components=[Component(**c) for c in t.get("components", [])],
            ))
        layout = BoardLayout(
            tiers=tiers,
            enclosure_type=layout_data.get("enclosure_type", "no_vent"),
            swbd_width_mm=layout_data.get("swbd_width_mm", 1800.0),
            swbd_height_mm=layout_data.get("swbd_height_mm", 2200.0),
        )

        inputs = CalcInputs(**data.get("inputs", {}))
        outputs = CalcOutputs(**data.get("outputs", {}))
        return cls(meta=meta, layout=layout, inputs=inputs, outputs=outputs)

    def mark_changed(self) -> None:
        signals.project_changed.emit()
