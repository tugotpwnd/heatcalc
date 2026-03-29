# heatcalc/core/models.py
import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Literal
from ..utils.qt import signals


SOLAR_COLOUR_TABLE = {
    "White": 10.0,
    "Cream": 12.0,
    "Yellow": 12.9,
    "Light grey / blue / green": 16.5,
    "Medium grey / blue / green": 21.0,
    "Dark grey / blue / green": 24.4,
    "Black": 25.0,
}


@dataclass
class ProjectMeta:
    job_number: str = ""
    project_title: str = ""          # ✅ rename
    enclosure: str = ""
    designer_name: str = ""          # ✅ rename
    date: str = ""
    revision: str = "A"

    # ---- Louvre definition (PROJECT-WIDE, AUTHORITATIVE) ----
    louvre_definition: Dict[str, Any] = field(default_factory=lambda: {
        "draw_width_mm": 80.0,
        "draw_height_mm": 20.0,
        "inlet_area_cm2": 6.5,          # manufacturer free area PER louvre
        "edge_margin_mm": 50.0,
        "louvre_spacing_mm": 15.0,
        "mesh": {
            "ip_rating_n": 2,
            "aperture_mm": None,
            "open_area_factor": 1.0,
        },
    })

    # Thermal assumptions
    ambient_C: float = 40.0
    altitude_m: float = 0.0
    ip_rating_n: int = 2  # IP2X default (finger-safe, vent-compatible)
    enclosure_material: str = "Sheet metal"
    enclosure_k_W_m2K: float = 5.5
    allow_material_dissipation: bool = False

    # ---- Solar (NEW) ----
    solar_enabled: bool = False
    solar_colour: str = "White"
    solar_delta_K: float = 10.0

    default_vent_label: str | None = "100×100"
    default_vent_area_cm2: float = 100.0
    iec60890_checklist: List[Dict[str, str]] = field(default_factory=list)

    def mark_changed(self):
        signals.project_meta_changed.emit()


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
    bus_network: "BusNetwork" = field(default_factory=lambda: BusNetwork())

    @property
    def rect(self):
        return (self.x_mm, self.y_mm, self.width_mm, self.height_mm)

    @property
    def total_heat_w(self) -> float:
        # Sum tier-level components + cell components
        tier_sum = sum(c.heat_loss_w for c in self.components)
        cell_sum = sum(cell.total_heat_w for cell in self.cells)
        return tier_sum + cell_sum

from dataclasses import dataclass, field
from typing import Optional, List

@dataclass
class BusNetwork:
    """
    Electrical bus network drawn within a tier.
    """

    segments: List["BusSegment"] = field(default_factory=list)
    loads: List["BusLoad"] = field(default_factory=list)
    joins: List["BusJoin"] = field(default_factory=list)

    source_segment: Optional[int] = None

@dataclass
class BusSegment:

    x0_mm: float
    y0_mm: float

    x1_mm: float
    y1_mm: float

    width_mm: float
    thickness_mm: float

    bars_in_parallel: int = 1

    orientation_to_wall: Literal["broad", "edge"] = "broad"
    gap_to_wall_mm: float = 50.0

@dataclass
class BusLoad:

    x_mm: float
    y_mm: float

    I_A: float
    max_terminal_temp_c: float = 105.0

@dataclass
class BusJoin:

    x_mm: float
    y_mm: float

    R_contact_20_uohm: float

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
    # DEPRECATED – ambient now lives in ProjectMeta
    ambient_temp_c: float = 25.0
    airflow_m3ph: Optional[float] = None


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
        meta_data = data.get("meta", {})

        solar = meta_data.get("solar", {})

        meta = ProjectMeta(**{
            k: v for k, v in meta_data.items()
            if k not in {"louvre_definition", "solar"}
        })

        # ---- Merge louvre definition ----
        if "louvre_definition" in meta_data:
            merged = dict(meta.louvre_definition)
            merged.update(meta_data["louvre_definition"])
            meta.louvre_definition = merged

        # ---- Solar (NEW) ----
        meta.solar_enabled = bool(solar.get("enabled", False))
        meta.solar_colour = solar.get("colour", "White")
        meta.solar_delta_K = float(
            solar.get("delta_K", SOLAR_COLOUR_TABLE.get(meta.solar_colour, 0.0))
        )

        layout_data = data.get("layout", {})
        tiers = []
        for t in layout_data.get("tiers", []):
            cells = [Cell(**c) for c in t.get("cells", [])]
            net_data = t.get("bus_network", {})

            bus_network = BusNetwork(
                segments=[BusSegment(**s) for s in net_data.get("segments", [])],
                loads=[
                    BusLoad(
                        x_mm=l.get("x_mm", 0.0),
                        y_mm=l.get("y_mm", 0.0),
                        I_A=float(l.get("I_A", 0.0)),
                        max_terminal_temp_c=float(l.get("max_terminal_temp_c", 105.0))
                    )
                    for l in net_data.get("loads", [])
                ],
                joins=[BusJoin(**j) for j in net_data.get("joins", [])],
                source_segment=net_data.get("source_segment"),
            )

            tiers.append(Tier(
                rows=t.get("rows", 1),
                cols=t.get("cols", 1),
                cells=cells,
                name=t.get("name", ""),
                x_mm=t.get("x_mm", 0.0),
                y_mm=t.get("y_mm", 0.0),
                width_mm=t.get("width_mm", 600.0),
                height_mm=t.get("height_mm", 1200.0),
                order_index=t.get("order_index", 0),
                arrangement=t.get("arrangement", "standard"),
                components=[Component(**c) for c in t.get("components", [])],
                bus_network=bus_network
            ))

        layout = BoardLayout(
            tiers=tiers,
            enclosure_type=layout_data.get("enclosure_type", "no_vent"),
            swbd_width_mm=layout_data.get("swbd_width_mm", 1800.0),
            swbd_height_mm=layout_data.get("swbd_height_mm", 2200.0),
        )

        return cls(
            meta=meta,
            layout=layout,
            inputs=CalcInputs(**data.get("inputs", {})),
            outputs=CalcOutputs(**data.get("outputs", {})),
        )

    def mark_changed(self) -> None:
        signals.project_changed.emit()


from dataclasses import dataclass, field
from typing import List, Optional


from dataclasses import dataclass

@dataclass
class BusbarJointSpec:
    """
    Represents a bolted connection / overlap joint in a busbar.
    Position is measured from the start of the bus.
    """

    x_m: float = 0.0

    overlap_m: float = 0.05
    bolt_count: int = 4
    bolt_dia_mm: float = 10.0
    torque_Nm: float = 45.0

    other_bar_width_mm: float | None = None
    other_bar_thickness_mm: float | None = None
    other_bar_count: int | None = None

    joint_type: Literal["bolted_overlap", "clamped_edge"] = "bolted_overlap"
    nut_factor: float = 0.20
    e_streamline: float = 0.5

    csa_factor: float = 1.0
    h_contact: float = 5000.0  # W/m²K

@dataclass
class BusbarBranchSpec:
    """
    A tee-off branch connected to a parent bus at x_m.
    The branch has its own geometry and length, and draws I_branch_A from the parent.
    """
    x_m: float
    I_branch_A: float
    branch: "BusbarSpec"   # nested BusbarSpec describing the branch conductor

# In BusbarSpec add:
branches: List[BusbarBranchSpec] = field(default_factory=list)


@dataclass
class BusbarSpec:
    """
    Busbar specification describing the electrical load, geometry,
    surface properties and installation layout of a conductor set.

    This object is intentionally declarative and contains only
    physical parameters. All physics calculations are performed
    in the physics layer.

    Units are kept in engineering-friendly form (mm, m, A) but
    helper properties expose SI units for solver calculations.
    """

    # ==========================================================
    # IDENTIFICATION
    # ==========================================================

    name: str = "BUS"

    # ==========================================================
    # ELECTRICAL PARAMETERS
    # ==========================================================

    I_total_A: float = 0.0
    """
    Total phase current carried by the busbar set (A).
    """

    bars_in_parallel: int = 1
    """
    Number of parallel conductors sharing the phase current.
    """

    S_ac: float = 1.3
    """
    AC resistance correction factor.
    Accounts for skin/proximity effects if required.
    Default = 1.0 (DC assumption).
    """

    # ==========================================================
    # GEOMETRY (PER SINGLE BAR)
    # ==========================================================

    width_mm: float = 100.0
    """
    Busbar width (mm).
    """

    thickness_mm: float = 10.0
    """
    Busbar thickness (mm).
    """

    length_m: float = 1.0
    """
    Actual conductor length (m) used to compute total I²R loss.
    """

    L_char_mm: float = 100.0
    """
    Characteristic length used for natural convection correlations.
    Typically the vertical dimension of the conductor surface.
    """

    branches: list[BusbarBranchSpec] = field(default_factory=list)
    """
    Branches, nested
    """

    # ==========================================================
    # JOINT / SEGMENT MODEL
    # ==========================================================

    joints: List[BusbarJointSpec] = field(default_factory=list)
    """
    Optional list of bolted joints along the bus.
    If empty the solver behaves exactly as before.
    """

    segments_per_m: float = 20.0
    """
    Resolution used when the segmented thermal solver is active.
    Higher value → more accurate joint hotspot modelling.
    Ignored if no joints are present.
    """

    # ==========================================================
    # LAYOUT / STACKING
    # ==========================================================

    face_to_face_dim: Literal["width", "thickness"] = "thickness"

    """
    Orientation of parallel bars:

    "thickness"
        bars stacked face-to-face through thickness

    "width"
        bars stacked face-to-face through width

    This affects radiation blocking between conductors.
    """

    # ==========================================================
    # SURFACE / THERMAL PROPERTIES
    # ==========================================================

    eps_bus: float = 0.4
    """
    Busbar emissivity.
    Typical values:
        polished copper ≈ 0.05-0.10
        oxidised copper ≈ 0.3-0.5
    """

    eps_env: float = 0.90
    """
    Effective emissivity of surrounding enclosure surfaces.
    Painted steel typically ≈0.9
    """

    # ==========================================================
    # CONVECTION CONDITIONS
    # ==========================================================

    convection_mode: Literal["vertical", "horizontal"] = "vertical"
    """
    Orientation for natural convection correlation.

    Options:
        "vertical"
        "horizontal"
    """

    v_mps: float = 0.0
    """
    Air velocity (m/s).

    0 → natural convection
    >0 → forced convection
    """

    # ==========================================================
    # ENCLOSURE COUPLING
    # ==========================================================

    use_air_temp: str = "top"
    """
    Which enclosure temperature drives convection:

    "top"
        most conservative (hottest air)

    "mid"
        mid-height temperature

    "075"
        IEC60890 0.75 height temperature
    """

    # ==========================================================
    # HELPER PROPERTIES (SI units)
    # ==========================================================

    @property
    def width_m(self) -> float:
        return self.width_mm / 1000.0

    @property
    def thickness_m(self) -> float:
        return self.thickness_mm / 1000.0

    @property
    def L_char_m(self) -> float:
        return self.L_char_mm / 1000.0

    @property
    def has_joints(self) -> bool:
        return len(self.joints) > 0

    # ==========================================================
    # DERIVED ELECTRICAL PARAMETERS
    # ==========================================================

    @property
    def current_per_bar_A(self) -> float:
        """
        Current carried by each individual conductor.
        """
        if self.bars_in_parallel <= 0:
            raise ValueError("bars_in_parallel must be >= 1")
        return self.I_total_A / self.bars_in_parallel

    # ==========================================================
    # DERIVED GEOMETRY
    # ==========================================================

    @property
    def cross_section_area_m2(self) -> float:
        """
        Conductor cross-section area.
        """
        return self.width_m * self.thickness_m

    @property
    def perimeter_m(self) -> float:
        """
        External perimeter of a rectangular conductor.
        Used for convection surface area per metre.
        """
        return 2 * (self.width_m + self.thickness_m)

    # ==========================================================
    # VALIDATION
    # ==========================================================

    def validate(self):
        """
        Basic input validation to prevent solver errors.
        """

        if self.width_mm <= 0:
            raise ValueError("width_mm must be positive")

        if self.thickness_mm <= 0:
            raise ValueError("thickness_mm must be positive")

        if self.bars_in_parallel < 1:
            raise ValueError("bars_in_parallel must be >= 1")

        if self.I_total_A < 0:
            raise ValueError("I_total_A cannot be negative")

        if self.face_to_face_dim not in ("width", "thickness"):
            raise ValueError("face_to_face_dim must be 'width' or 'thickness'")

        if self.convection_mode not in ("vertical", "horizontal"):
            raise ValueError("convection_mode must be 'vertical' or 'horizontal'")