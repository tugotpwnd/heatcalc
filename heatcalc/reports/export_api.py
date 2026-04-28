# reports/export_api.py
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple
import re

from PyQt5.QtWidgets import QGraphicsScene

from ..core.compliance_61439 import evaluate_tier_compliance
from ..core.louvre_calc import tier_max_effective_inlet_area_cm2
from ..ui.tier_item import TierItem, tier_effective_inlet_area_cm2
from ..core.iec60890_calc import calc_tier_iec60890

from .simple_report import (
    export_simple_report,
    ProjectMeta as ReportMeta,
    TierRow as ReportTier,
    ComponentRow as ReportComponent,
    CableRow as ReportCable,
    BusRow as ReportBus,
    TierThermal,
)
from PyQt5.QtWidgets import QMessageBox

try:
    # Used only to convert drawn grid units -> mm for reporting.
    from ..ui.switchboard_tab import GRID
    MM_PER_GRID = 25
except Exception:
    GRID = 20
    MM_PER_GRID = 25

MM_PER_GRID_MM = float(MM_PER_GRID)


def _scene_tiers(scene: QGraphicsScene) -> List[TierItem]:
    return [it for it in scene.items() if isinstance(it, TierItem)]


def _natural_tier_key(tag: str):
    text = str(tag or "").strip()
    parts = re.findall(r"\d+|[A-Za-z]+", text)

    key = []
    for p in parts:
        if p.isdigit():
            key.append((0, int(p)))
        else:
            key.append((1, p.upper()))
    return key


def _dims_m_from_tier(t: TierItem) -> Tuple[float, float, float]:
    rect = t._rect if hasattr(t, "_rect") else t.rect()
    wmm = max(1, int(rect.width() / GRID * MM_PER_GRID_MM))
    hmm = max(1, int(rect.height() / GRID * MM_PER_GRID_MM))
    dmm = max(1, int(getattr(t, "depth_mm", 400)))
    return wmm / 1000.0, hmm / 1000.0, dmm / 1000.0


def _map_tier_item(t: TierItem, solve) -> ReportTier:
    rect = t._rect if hasattr(t, "_rect") else t.rect()
    wmm = max(1, int(rect.width() / GRID * MM_PER_GRID_MM))
    hmm = max(1, int(rect.height() / GRID * MM_PER_GRID_MM))
    dmm = int(getattr(t, "depth_mm", 0) or 0)

    comps: List[ReportComponent] = []
    for c in getattr(t, "component_entries", []) or []:
        qty = int(getattr(c, "qty", 1) or 1)
        each = float(getattr(c, "heat_each_w", 0.0) or 0.0)

        comps.append(
            ReportComponent(
                description=getattr(c, "description", getattr(c, "key", "Component")),
                part_no=getattr(c, "part_number", ""),
                qty=qty,
                heat_each_w=each,
                heat_total_w=qty * each,
                max_temp_C=getattr(c, "max_temp_C", 70),
                rated_current_A=getattr(c, "rated_current_A", None),
                derating_temp_start_C=getattr(c, "derating_temp_start_C", None),
                derating_function=getattr(c, "derating_function", None),
                key=getattr(c, "key", None),
                category=getattr(c, "category", None),
            )
        )

    cabs: List[ReportCable] = []
    for cb in getattr(t, "cables", []) or []:
        cabs.append(
            ReportCable(
                name=getattr(cb, "name", "Cable"),
                csa_mm2=float(getattr(cb, "csa_mm2", 0.0) or 0.0),
                installation=str(getattr(cb, "installation", "")),
                length_m=float(getattr(cb, "length_m", 0.0) or 0.0),
                current_A=float(getattr(cb, "current_A", 0.0) or 0.0),
                P_Wpm=float(getattr(cb, "P_Wpm", getattr(cb, "Pn_Wpm", 0.0)) or 0.0),
                total_W=float(getattr(cb, "total_W", 0.0) or 0.0),
            )
        )

    buses: List[ReportBus] = []

    schedule = []
    tier_edges = {}

    if solve:
        thermal = solve["global"]  # ← FIX HERE
        graph = solve["graph"]
        tier_edges = solve["tier_edges"]

        from heatcalc.reports.bus_schedule import build_bus_schedule, build_joint_schedule
        schedule = build_bus_schedule(graph, thermal)
        joint_schedule = build_joint_schedule(graph, thermal)

        tier_edge_ids = {e.id for e in tier_edges.get(t, [])}

        for row in schedule:
            if row.bus_id not in tier_edge_ids:
                continue

            buses.append(
                ReportBus(
                    name=f"Bus {row.bus_id}",
                    width_mm=row.width_mm,
                    thickness_mm=row.thickness_mm,
                    parallel_bars=row.bars,
                    length_m=row.length_m,
                    current_A=row.I_max_A,
                    total_W=row.P_total_W,
                    T_max_C=row.T_max_C,
                    T_min_C=row.T_min_C,
                )
            )

        tier_joint_rows = []

        for jr in joint_schedule:
            if jr.joint_id not in tier_edge_ids:
                continue
            tier_joint_rows.append(jr)

    return ReportTier(
        tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
        width_mm=wmm,
        height_mm=hmm,
        depth_mm=dmm,
        components=comps,
        cables=cabs,
        buses=buses,
        joints=tier_joint_rows,  # ✅ clean
        loads=[],  # placeholder for later
    )

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
        ip_rating_n=g(m,"ip_rating_n"),
    )


def _safe_float(obj: Any, name: str, default: float) -> float:
    try:
        v = getattr(obj, name)
        return default if v is None else float(v)
    except Exception:
        return float(default)


def _safe_bool(obj: Any, name: str, default: bool) -> bool:
    try:
        v = getattr(obj, name)
        return default if v is None else bool(v)
    except Exception:
        return bool(default)

def build_tier_compliance_results(
    tiers,
    global_sol,
    tier_thermals,
    ambient_C,
    tier_edges,
    graph,
):
    """
    Build 61439 compliance results per tier.
    """

    results = []

    for th in tier_thermals:
        tier = next(t for t in tiers if str(getattr(t, "name")) == th.tag)
        tier_res = {
            "T_top": th.T_top,
            "limit_C": th.max_C,
        }

        comp = evaluate_tier_compliance(
            tier=tier,
            global_sol=global_sol,
            tier_res=tier_res,
            ambient_C=ambient_C,
            tier_edges=tier_edges,
            graph=graph,
        )

        results.append(comp)

    return results


def export_project_report(
    project: Any,
    switchboard_tab: Any,
    curvefit_tab: Any,
    out_pdf: Path,
    *,
    ambient_C: Optional[float] = None,
    header_logo_path: Optional[Path] = None,
    footer_image_path: Optional[Path] = None,
    iec60890_checklist=None,
    selected_tier_tags: Optional[List[str]] = None,
) -> Path:
    """
    Export a PDF report.

    IMPORTANT:
    - All thermal results (including airflow) come exclusively from calc_tier_iec60890().
    - No airflow.py usage is permitted.
    """
    meta = _meta_from_project(project)

    scene: QGraphicsScene = getattr(switchboard_tab, "scene", None)
    if scene is None:
        raise RuntimeError("SwitchboardTab.scene is not available")

    # -----------------------------------------
    # REQUIRE SOLVE RESULTS (NEW)
    # -----------------------------------------
    solve = getattr(switchboard_tab, "last_solve_result", None)

    if not solve:
        QMessageBox.warning(
            None,
            "No Results",
            "Please run the thermal solve before exporting the report."
        )
        return out_pdf

    # Refresh curve_no + wall_mounted etc from the latest geometry before reporting.
    if hasattr(switchboard_tab, "_recompute_all_curves"):
        try:
            switchboard_tab._recompute_all_curves()
        except Exception:
            pass

    # All tiers for adjacency / touching checks.
    all_tiers = _scene_tiers(scene)

    # Selected tiers for report content.
    report_tier_items = list(all_tiers)
    if selected_tier_tags:
        selected_set = {str(s).strip() for s in selected_tier_tags if str(s).strip()}
        if selected_set:
            report_tier_items = [
                t for t in report_tier_items
                if str(getattr(t, "name", getattr(t, "tag", ""))).strip() in selected_set
            ]

    report_tier_items.sort(
        key=lambda t: _natural_tier_key(str(getattr(t, "name", getattr(t, "tag", ""))))
    )

    report_tiers = [_map_tier_item(t, solve) for t in report_tier_items]
    total_w = sum(t.heat_w for t in report_tiers)
    totals = {"heat_total_w": round(total_w, 3)}

    enclosure_type = ""
    try:
        enclosure_type = getattr(project.meta, "enclosure_type", "") or ""
    except Exception:
        pass

    # Curve points (optional) for the report appendix/plots.
    xs, ys = None, None
    for meth in ("export_curve_points", "get_curve_points"):
        if hasattr(curvefit_tab, meth):
            try:
                xs, ys = getattr(curvefit_tab, meth)()
                break
            except Exception:
                pass

    # IEC 60890 / temperature results per tier.
    tier_thermals: list[TierThermal] = []

    if ambient_C is not None:

        # ---------------- Project-wide meta ----------------
        ambient = float(ambient_C)
        project_altitude_m = float(getattr(project.meta, "altitude_m", 0.0))
        ip_rating_n = int(getattr(project.meta, "ip_rating_n", 0))
        solar_dt = float(getattr(project.meta, "solar_delta_K", 0.0)) \
            if getattr(project.meta, "solar_enabled", False) else 0.0

        # Louvre definition (same source as live calc)
        louvre_def = None
        try:
            louvre_def = switchboard_tab.get_louvre_definition()
        except Exception:
            pass

        blocked: List[Tuple[str, List[str], float]] = []  # (tier_name, blockers, delta_allow_K)
        for t in report_tier_items:

            # ---------------- Vent areas via louvre model ----------------
            inlet_area_cm2 = 0.0
            vent_test_area_cm2 = None

            if louvre_def:
                inlet_area_cm2 = tier_effective_inlet_area_cm2(
                    tier=t,
                    louvre_def=louvre_def,
                    ip_rating_n=ip_rating_n,
                )

                vent_test_area_cm2 = tier_max_effective_inlet_area_cm2(
                    tier=t,
                    louvre_def=louvre_def,
                    ip_rating_n=ip_rating_n,
                )

            # ---------------- IEC 60890 calculation ----------------
            res = calc_tier_iec60890(
                tier=t,
                tiers=all_tiers,  # full geometry context
                wall_mounted=bool(getattr(t, "wall_mounted", False)),
                inlet_area_cm2=float(inlet_area_cm2),
                ambient_C=ambient,
                altitude_m=project_altitude_m,
                ip_rating_n=ip_rating_n,
                vent_test_area_cm2=vent_test_area_cm2,
                solar_delta_K=solar_dt,
            )

            # ------------------------------------------------------------
            # HARD BLOCK: thermally infeasible tiers
            # ------------------------------------------------------------
            if not res.get("cooling_possible", True):
                tier_name = str(getattr(t, "name", getattr(t, "tag", "Tier")))
                blockers = list(res.get("thermal_blockers", []) or [])
                delta_allow_K = float(res.get("delta_allow_K", 0.0) or 0.0)
                blocked.append((tier_name, blockers, delta_allow_K))
                continue  # don't append TierThermal for a blocked tier

            # ---------------- Normalise into TierThermal ----------------
            tier_thermals.append(
                TierThermal(
                    tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),

                    Ae=float(res.get("Ae", 0.0)),
                    P_W=float(res.get("P", 0.0)),

                    k=float(res.get("k", 0.0)),
                    c=float(res.get("c", 0.0)),
                    x=float(res.get("x", 0.0)),
                    f=res.get("f"),
                    g=res.get("g"),

                    vent=bool(res.get("ventilated", False)),
                    curve=int(res.get("curve_no", 1)),
                    ambient_C=float(res.get("ambient_C", ambient)),

                    dt_mid=float(res.get("dt_mid", 0.0)),
                    dt_top=float(res.get("dt_top", 0.0)),
                    dt_075=res.get("dt_075"),

                    T_mid=float(res.get("T_mid", 0.0)),
                    T_top=float(res.get("T_top", 0.0)),
                    T_075=res.get("T_075"),

                    max_C=float(res.get("limit_C")),
                    compliant_mid=bool(res.get("compliant_mid", False)),
                    compliant_top=bool(res.get("compliant_top", False)),

                    airflow_m3h=res.get("airflow_m3h"),
                    P_material_W=res.get("P_material"),
                    P_cooling_W=res.get("P_cooling"),
                    vent_recommended=bool(res.get("vent_recommended", False)),
                    inlet_area_cm2=float(res.get("inlet_area_cm2", inlet_area_cm2)),
                    P_890=float(res.get("P_890", 0.0)),
                    solar_dt=float(res.get("solar_dt",0.0)),

                    dims_m=_dims_m_from_tier(t),
                    surfaces=res.get("surfaces"),
                    figures_used=res.get("figures_used", []),

                    h_partitions_enabled=bool(getattr(t, "h_partitions_enabled", False)),
                    h_partitions_count=int(getattr(t, "h_partitions_count", 1)),
                )
            )

    if blocked:
        REASON_MAP = {
            "AMBIENT": "Ambient temperature ≥ allowable limit",
            "SOLAR": "Solar temperature rise ≥ allowable limit",
        }

        msg = "The report cannot be generated.\n\n"
        msg += "The following tiers are thermally infeasible:\n\n"

        for name, reasons, deltaK in blocked:
            if reasons:
                reason_txt = ", ".join(REASON_MAP.get(r, r) for r in reasons)
            else:
                reason_txt = "External conditions exceed limits"
            msg += f"• {name}  ({reason_txt}, ΔT_allow={deltaK:.1f} K)\n"

        msg += (
            "\n\nExternal conditions alone exceed allowable limits.\n"
            "IEC 60890 mitigation (ventilation or cooling) is not possible.\n\n"
            "Revise ambient assumptions, solar exposure,\n"
            "or enclosure design before generating a report."
        )

        QMessageBox.critical(None, "Report Blocked", msg)
        return None

    out_pdf = Path(out_pdf)
    out_pdf = Path(out_pdf)

    # ---------------------------------------------------------
    # BUILD 61439 COMPLIANCE RESULTS
    # ---------------------------------------------------------
    tier_compliance_results = build_tier_compliance_results(
        tiers=report_tier_items,
        global_sol=solve["global"],
        tier_thermals=tier_thermals,
        ambient_C=ambient_C,
        tier_edges=solve["tier_edges"],
        graph=solve["graph"],
    )

    # ---------------------------------------------------------
    # EXPORT REPORT
    # ---------------------------------------------------------
    return export_simple_report(
        out_pdf=out_pdf,
        meta=meta,
        enclosure_type=enclosure_type,
        tiers=report_tiers,
        totals=totals,
        scene=scene,
        curve_xs=xs,
        curve_ys=ys,
        ambient_C=ambient_C,
        tier_thermals=tier_thermals if tier_thermals else None,
        tier_compliance_results=tier_compliance_results,
        header_logo_path=header_logo_path,
        footer_image_path=footer_image_path,
        iec60890_checklist=iec60890_checklist,
    )