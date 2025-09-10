# ==========================
# reports/export_api.py
# ==========================
from __future__ import annotations
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Dict

# Pull the UI types you already use
from PyQt5.QtWidgets import QGraphicsScene

# Import the TierItem class to identify items in the scene
from ..ui.tier_item import TierItem
from ..core import curvefit

# Simple PDF builder (no HTML/CSS)
from .simple_report import (
    export_simple_report,
    ProjectMeta as ReportMeta,
    TierRow as ReportTier,
    ComponentRow as ReportComponent,
    CableRow as ReportCable,
    TierThermal,  # NEW
)

# Optional: GRID→mm conversion if available in your UI
# Fallback to 25 mm per GRID if not present (won’t crash if missing)
try:
    from ..ui.switchboard_tab import GRID  # px/grid
    MM_PER_GRID = 25  # override if your project defines it elsewhere
except Exception:
    GRID = 20
    MM_PER_GRID = 25

MM_PER_GRID_MM = float(MM_PER_GRID)


# -------------------------- scene helpers --------------------------

def _scene_tiers(scene: QGraphicsScene) -> List[TierItem]:
    """Find TierItem instances from the live scene."""
    return [it for it in scene.items() if isinstance(it, TierItem)]


def _dims_m_from_tier(t: TierItem) -> Tuple[float, float, float]:
    """Compute width/height/depth in metres from graphics rect + depth_mm."""
    rect = t._rect if hasattr(t, "_rect") else t.rect()
    wmm = max(1, int(rect.width()  / GRID * MM_PER_GRID_MM))
    hmm = max(1, int(rect.height() / GRID * MM_PER_GRID_MM))
    dmm = max(1, int(getattr(t, "depth_mm", 400)))
    return wmm / 1000.0, hmm / 1000.0, dmm / 1000.0


def _overlap_x(a: TierItem, b: TierItem) -> bool:
    ra, rb = a.shapeRect(), b.shapeRect()
    return not (ra.right() <= rb.left() or rb.right() <= ra.left())


def _overlap_y(a: TierItem, b: TierItem) -> bool:
    ra, rb = a.shapeRect(), b.shapeRect()
    return not (ra.bottom() <= rb.top() or rb.bottom() <= ra.top())


def _compute_face_bs(t: TierItem, tiers: List[TierItem], wall: bool) -> Dict[str, float]:
    """Return b per side: left/right/top based on adjacency + wall flag."""
    left_touch  = any(abs(t.shapeRect().left()  - o.shapeRect().right()) < 1e-3 and _overlap_y(t, o) for o in tiers if o is not t)
    right_touch = any(abs(t.shapeRect().right() - o.shapeRect().left())  < 1e-3 and _overlap_y(t, o) for o in tiers if o is not t)
    top_covered = any(abs(t.shapeRect().top()   - o.shapeRect().bottom()) < 1e-3 and _overlap_x(t, o) for o in tiers if o is not t)

    b_left  = 0.5 if left_touch  else 0.9
    b_right = 0.5 if right_touch else 0.9
    if wall and top_covered:
        b_top = 0.7     # “covered top surface” per Table III
    else:
        b_top = 1.4     # exposed top surface
    return {"left": b_left, "right": b_right, "top": b_top}


# -------------------------- report adapters ------------------------

def _map_tier_item(t: TierItem) -> ReportTier:
    """Convert a live TierItem into a report TierRow (with components & cables)."""
    # Dimensions (prefer mm fields if your TierItem stores them explicitly)
    rect = t._rect if hasattr(t, "_rect") else t.rect()
    wmm = max(1, int(rect.width()  / GRID * MM_PER_GRID_MM))
    hmm = max(1, int(rect.height() / GRID * MM_PER_GRID_MM))
    dmm = int(getattr(t, "depth_mm", 0) or 0)

    # Components
    comps: List[ReportComponent] = []
    for c in getattr(t, "component_entries", []) or []:
        qty  = int(getattr(c, "qty", 1) or 1)
        each = float(getattr(c, "heat_each_w", 0.0) or 0.0)
        comps.append(ReportComponent(
            description=getattr(c, "description", getattr(c, "key", "Component")),
            part_no=getattr(c, "part_number", ""),
            qty=qty,
            heat_each_w=each,
            heat_total_w=qty * each,
        ))

    # Cables
    cabs: List[ReportCable] = []
    for cb in getattr(t, "cables", []) or []:
        cabs.append(ReportCable(
            name=getattr(cb, "name", "Cable"),
            csa_mm2=float(getattr(cb, "csa_mm2", 0.0) or 0.0),
            installation=str(getattr(cb, "installation", "")),
            length_m=float(getattr(cb, "length_m", 0.0) or 0.0),
            current_A=float(getattr(cb, "current_A", 0.0) or 0.0),
            P_Wpm=float(getattr(cb, "P_Wpm", getattr(cb, "Pn_Wpm", 0.0)) or 0.0),
            total_W=float(getattr(cb, "total_W", 0.0) or 0.0),
        ))

    return ReportTier(
        tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
        width_mm=wmm, height_mm=hmm, depth_mm=dmm,
        components=comps, cables=cabs
    )


# -------------------------- thermal math ---------------------------

def _calc_thermal_for_tier(t: TierItem, inlet_area_cm2: float, ambient_C: float) -> TierThermal:
    """Compute Δt at 0.5t and 1.0t, then absolute temps and compliance/airflow."""
    # Geometry
    w_m, h_m, d_m = _dims_m_from_tier(t)

    # Effective area Ae
    tiers: List[TierItem] = []
    try:
        # try to read siblings from the same scene for adjacency
        scene = t.scene()
        if scene is not None:
            tiers = [it for it in scene.items() if isinstance(it, TierItem)]
    except Exception:
        pass
    wall = bool(getattr(t, "wall_mounted", False))
    b_faces = _compute_face_bs(t, tiers, wall)
    areas = {
        "top":  w_m * d_m,
        "bot":  w_m * d_m,
        "left": h_m * d_m,
        "right": h_m * d_m,
        "front": w_m * h_m,
        "back":  w_m * h_m,
    }
    b_used = {
        "top": b_faces["top"],
        "bot": 0.0,  # bottom ignored per standard
        "left": b_faces["left"],
        "right": b_faces["right"],
        "front": 0.9,
        "back": 0.5 if wall else 0.9,
    }
    Ae = sum(areas[k] * b_used[k] for k in areas)

    # Power and factors
    P = float(getattr(t, "total_heat")() if hasattr(t, "total_heat") else 0.0)
    vent = bool(getattr(t, "is_ventilated", False))
    curve_no = int(getattr(t, "curve_no", 3))
    x = 0.715 if vent else 0.804
    d_fac = 1.0

    if vent:
        Ab = max(1e-9, w_m * d_m)
        f = (h_m ** 1.35) / Ab
        k = curvefit.k_vents(ae=max(1.0, min(14.0, Ae)), opening_area_cm2=float(inlet_area_cm2))
        c = curvefit.c_vents(f=f, opening_area_cm2=float(inlet_area_cm2))
        g = None
    else:
        if Ae <= 1.25:
            k = curvefit.k_small_no_vents(Ae)
            g = h_m / max(1e-9, w_m)
            c = curvefit.c_small_no_vents(g)
            f = None
        else:
            k = curvefit.k_no_vents(Ae)
            Ab = max(1e-9, w_m * d_m)
            f = (h_m ** 1.35) / Ab
            c = curvefit.c_no_vents(curve_no, f)
            g = None

    dt_mid = k * d_fac * (P ** x)
    dt_top = c * dt_mid

    T_mid = ambient_C + dt_mid
    T_top = ambient_C + dt_top

    maxC = int(getattr(t, "max_temp_C", 70))
    compliant_mid = (T_mid <= maxC)
    compliant_top = (T_top <= maxC)

    airflow_m3h = None
    if not compliant_top and maxC > ambient_C:
        airflow_m3h = _min_airflow_m3h(P, ambient_C, maxC)

    return TierThermal(
        tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
        Ae=Ae, P_W=P, k=k, c=c, x=x, f=f, g=g,
        vent=vent, curve=curve_no,
        ambient_C=ambient_C,
        dt_mid=dt_mid, dt_top=dt_top,
        T_mid=T_mid, T_top=T_top,
        max_C=maxC,
        compliant_mid=compliant_mid,
        compliant_top=compliant_top,
        airflow_m3h=airflow_m3h,
    )



def _min_airflow_m3h(
    P: float,
    amb_C: float,
    max_C: float,
    *,
    # --- Air properties (defaults suit ~20–35°C, sea level) ---
    rho_kg_per_m3: float = 1.20,    # Air density [kg/m³]. Typical range 1.15–1.23 for 15–35°C.
    cp_J_per_kgK: float = 1005.0,   # Air cp [J/(kg·K)]. ~1000–1007 J/kg·K across 0–40°C.
    # --- Design uplift ---
    safety_factor: float = 1.2,     # Accounts for mixing inefficiency, filter aging, minor leaks, etc.
    # --- Output handling ---
    min_flow_m3h: float = 0.0       # Clamp to non-negative; keep 0.0 if P<=0
) -> Optional[float]:
    """
    Minimum volumetric airflow [m³/h] to keep cabinet at or below max_C for a heat load P.

    Methodology (unchanged from your original):
        - Perfectly mixed, single-pass energy balance at steady state:
              Vdot = P / (ρ * cp * ΔT)
          where:
              Vdot is volumetric flow [m³/s]
              P     is total heat to be removed [W]
              ρ     is air density [kg/m³]
              cp    is specific heat at constant pressure [J/(kg·K)]
              ΔT    = (max_C - amb_C) [K]  (allowed air temperature rise)
        - Convert m³/s → m³/h by × 3600.
        - Apply a safety factor (>1) to cover non-idealities.

    Notes / justification of defaults:
        • ρ ≈ 1.20 kg/m³ and cp ≈ 1005 J/kg·K are standard engineering values for warm indoor air.
        • Schneider’s example uses ρ ≈ 1.1 kg/m³ and c ≈ 1.0 kJ/kg·K; to reproduce that exactly,
          call with rho_kg_per_m3=1.1 and cp_J_per_kgK=1000.0.
        • Safety factor 1.2 gives ≈20% headroom for imperfect mixing, grills/filters, and aging.
          Increase if you expect heavy filters, long ducts, or poor airflow paths.

    Returns:
        m³/h as float; None if ΔT <= 0 (i.e., max_C <= amb_C makes the problem ill-posed).

    Edge cases:
        • If P <= 0, returns max(min_flow_m3h, 0.0) (i.e., 0 by default).
    """
    # Allowed air temperature rise (K). If non-positive, cannot meet the setpoint by ventilation alone.
    dT_allow = max_C - amb_C
    if dT_allow <= 0:
        return None

    # Trivial non-heating case
    if P <= 0:
        return max(min_flow_m3h, 0.0)

    # Ideal required m³/s from steady-state heat balance
    denom = rho_kg_per_m3 * cp_J_per_kgK * dT_allow
    Vdot_m3_s_ideal = P / denom

    # Convert to m³/h and apply safety factor
    Vdot_m3_h = Vdot_m3_s_ideal * 3600.0 * safety_factor

    # Never return negative due to any numerical issue; clamp to user-defined minimum
    return max(Vdot_m3_h, min_flow_m3h)


# -------------------------- meta helpers ---------------------------

def _meta_from_project(project: Any) -> ReportMeta:
    m = getattr(project, "meta", None)
    def g(obj, *names, default=""):
        for n in names:
            if hasattr(obj, n):
                v = getattr(obj, n)
                return v if v is not None else default
        return default

    return ReportMeta(
        job_number=g(m, "job_number"),
        project_title=g(m, "project_title", "title"),
        enclosure=g(m, "enclosure"),
        designer=g(m, "designer_name", "designer"),
        revision=g(m, "revision"),
        date=g(m, "date"),
    )


# -------------------------- public API -----------------------------

def export_project_report(
    project: Any,
    switchboard_tab: Any,
    curvefit_tab: Any,
    out_pdf: Path,
    *,
    ambient_C: Optional[float] = None,
    inlet_area_cm2: float = 300.0,
    header_logo_path: Optional[Path] = None,   # <— NEW
    footer_image_path: Optional[Path] = None,  # <— NEW
    iec60890_checklist
) -> Path:
    """
    Single entry-point you call from UI. No data helpers needed in MainWindow.

    Args:
      project:         your core.models.Project instance (for meta)
      switchboard_tab: your SwitchboardTab instance (for scene + tiers)
      curvefit_tab:    your CurveFitTab instance (optional curve points)
      out_pdf:         destination PDF path
      ambient_C:       ambient temperature used to compute absolute temps
      inlet_area_cm2:  assumed inlet area for ventilated enclosures
    """
    # Meta
    meta = _meta_from_project(project)

    # Scene + tier items
    scene: QGraphicsScene = getattr(switchboard_tab, "scene", None)
    if scene is None:
        raise RuntimeError("SwitchboardTab.scene is not available")

    live_tiers = _scene_tiers(scene)
    report_tiers = [_map_tier_item(t) for t in live_tiers]
    total_w = sum(t.heat_w for t in report_tiers)
    totals = {"heat_total_w": round(total_w, 3)}

    # Optional enclosure type (won’t crash if absent)
    enclosure = ""
    try:
        enclosure = getattr(project.meta, "enclosure_type", "") or ""
    except Exception:
        pass

    # Optional curve points for the legacy overall curve page
    xs, ys = None, None
    for meth in ("export_curve_points", "get_curve_points"):
        if hasattr(curvefit_tab, meth):
            try:
                xs, ys = getattr(curvefit_tab, meth)()
                break
            except Exception:
                pass

    # Thermal results per tier (if ambient provided)
    tier_thermals: List[TierThermal] = []
    if ambient_C is not None:
        for t in live_tiers:
            tr = _calc_thermal_for_tier(t, inlet_area_cm2, float(ambient_C))
            tier_thermals.append(tr)

    # Generate the PDF
    out_pdf = Path(out_pdf)
    return export_simple_report(
        out_pdf=out_pdf,
        meta=meta,
        enclosure_type=enclosure,
        tiers=report_tiers,
        totals=totals,
        scene=scene,
        curve_xs=xs, curve_ys=ys,
        ambient_C=ambient_C,
        tier_thermals=tier_thermals if tier_thermals else None,
        header_logo_path=header_logo_path,       # <— NEW
        footer_image_path=footer_image_path,     # <— NEW
        iec60890_checklist=iec60890_checklist
    )