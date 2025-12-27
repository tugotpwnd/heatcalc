# reports/export_api.py
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple
import re

from PyQt5.QtWidgets import QGraphicsScene

from ..ui.tier_item import TierItem
from ..core.iec60890_calc import calc_tier_iec60890

from .simple_report import (
    export_simple_report,
    ProjectMeta as ReportMeta,
    TierRow as ReportTier,
    ComponentRow as ReportComponent,
    CableRow as ReportCable,
    TierThermal,
)

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


def _map_tier_item(t: TierItem) -> ReportTier:
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

    return ReportTier(
        tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
        width_mm=wmm,
        height_mm=hmm,
        depth_mm=dmm,
        components=comps,
        cables=cabs,
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


def export_project_report(
    project: Any,
    switchboard_tab: Any,
    curvefit_tab: Any,
    out_pdf: Path,
    *,
    ambient_C: Optional[float] = None,
    inlet_area_cm2: float = 300.0,
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

    report_tiers = [_map_tier_item(t) for t in report_tier_items]
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
    tier_thermals: List[TierThermal] = []
    if ambient_C is not None:
        allow_mat = _safe_bool(getattr(project, "meta", object()), "allow_material_dissipation", False)
        k_m2K = _safe_float(getattr(project, "meta", object()), "enclosure_k_W_m2K", 0.0)
        enc_mat = getattr(getattr(project, "meta", object()), "enclosure_material", None)
        default_vent_area_cm2 = _safe_float(getattr(project, "meta", object()), "default_vent_area_cm2", 0.0)

        for t in report_tier_items:
            # Prefer per-tier vent size when present; otherwise fall back to the report/default inlet area.
            if getattr(t, "is_ventilated", False):
                tier_inlet_cm2 = float(getattr(t, "vent_area_for_iec", lambda: 0.0)() or 0.0)
            else:
                tier_inlet_cm2 = 0.0  # no inlet area

            res = calc_tier_iec60890(
                tier=t,
                tiers=all_tiers,  # IMPORTANT: full model for touching checks
                wall_mounted=bool(getattr(t, "wall_mounted", False)),
                inlet_area_cm2=float(tier_inlet_cm2),
                ambient_C=float(ambient_C),
                enclosure_k_W_m2K=float(k_m2K),
                allow_material_dissipation=bool(allow_mat),
                default_vent_area_cm2=default_vent_area_cm2
            )

            tier_thermals.append(
                TierThermal(
                    tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
                    Ae=float(res.get("Ae", 0.0)),
                    P_W=float(res.get("P", 0.0)),
                    k=float(res.get("k", 0.0)),
                    c=float(res.get("c", 0.0)),
                    x=float(res.get("x", 0.0)),
                    f=res.get("f", None),
                    g=res.get("g", None),
                    vent=bool(res.get("ventilated", False)),
                    curve=int(getattr(t, "curve_no", 1) or 1),
                    ambient_C=float(res.get("ambient_C", ambient_C)),

                    dt_mid=float(res.get("dt_mid", 0.0)),
                    dt_top=float(res.get("dt_top", 0.0)),
                    dt_075=res.get("dt_075", None),

                    T_mid=float(res.get("T_mid", 0.0)),
                    T_top=float(res.get("T_top", 0.0)),
                    T_075=res.get("T_075", None),

                    max_C=float(res.get("limit_C", getattr(t, "max_temp_C", 70))),
                    compliant_mid=bool(res.get("compliant_mid", False)),
                    compliant_top=bool(res.get("compliant_top", False)),

                    # New: IEC calc now returns airflow and dissipation split directly.
                    airflow_m3h=res.get("airflow_m3h", None),
                    P_material_W=res.get("P_material", None),
                    P_cooling_W=res.get("P_cooling", None),
                    vent_recommended=bool(res.get("vent_recommended", False)),
                    inlet_area_cm2=float(res.get("inlet_area_cm2", tier_inlet_cm2)),

                    naturally_vented=bool(getattr(t, "is_ventilated", False)),
                    natural_vent_area_cm2=float(getattr(t, "vent_area_cm2", 0.0) or 0.0),
                    natural_vent_label=getattr(t, "vent_label", None),

                    enclosure_material=str(enc_mat) if enc_mat else None,
                    enclosure_k=float(res.get("enclosure_k_W_m2K", k_m2K)),
                    allow_material_dissipation=bool(res.get("allow_material_dissipation", allow_mat)),

                    dims_m=_dims_m_from_tier(t),
                    surfaces=res.get("surfaces", None),
                    figures_used=res.get("figures_used", []),
                )
            )

    out_pdf = Path(out_pdf)
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
        header_logo_path=header_logo_path,
        footer_image_path=footer_image_path,
        iec60890_checklist=iec60890_checklist,
    )
