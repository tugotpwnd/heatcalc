from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from ..utils.qt import signals


@dataclass
class ProjectMeta:
    job_number: str = ""
    title: str = ""
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
    rows: int
    cols: int
    cells: List[Cell] = field(default_factory=list)


@dataclass
class BoardLayout:
    tiers: List[Tier] = field(default_factory=list)
    enclosure_type: str = "no_vent"  # "no_vent" or "vented"


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
        # Simple de/serialization for now
        meta = ProjectMeta(**data.get("meta", {}))
        layout_data = data.get("layout", {})
        tiers = []
        for t in layout_data.get("tiers", []):
            cells = [Cell(**c) for c in t.get("cells", [])]
            tiers.append(Tier(rows=t.get("rows", 0), cols=t.get("cols", 0), cells=cells))
        layout = BoardLayout(tiers=tiers, enclosure_type=layout_data.get("enclosure_type", "no_vent"))
        inputs = CalcInputs(**data.get("inputs", {}))
        outputs = CalcOutputs(**data.get("outputs", {}))
        return cls(meta=meta, layout=layout, inputs=inputs, outputs=outputs)

    # Call this whenever the project changes to notify autosave
    def mark_changed(self) -> None:
        signals.project_changed.emit()