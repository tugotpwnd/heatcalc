# reports/export_api.py
from __future__ import annotations
from pathlib import Path
from typing import Any, List, Optional, Tuple
import re

from PyQt5.QtWidgets import QGraphicsScene

from ..core.airflow import required_airflow_with_wall_loss
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

    meta = _meta_from_project(project)

    scene: QGraphicsScene = getattr(switchboard_tab, "scene", None)
    if scene is None:
        raise RuntimeError("SwitchboardTab.scene is not available")

    # IMPORTANT: refresh curve_no + wall_mounted from the latest geometry
    if hasattr(switchboard_tab, "_recompute_all_curves"):
        try:
            switchboard_tab._recompute_all_curves()
        except Exception:
            pass

    # All tiers for adjacency / thermal calculation truth
    all_tiers = _scene_tiers(scene)

    # Selected tiers for report content only
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

    enclosure = ""
    try:
        enclosure = getattr(project.meta, "enclosure_type", "") or ""
    except Exception:
        pass

    xs, ys = None, None
    for meth in ("export_curve_points", "get_curve_points"):
        if hasattr(curvefit_tab, meth):
            try:
                xs, ys = getattr(curvefit_tab, meth)()
                break
            except Exception:
                pass

    tier_thermals: List[TierThermal] = []
    if ambient_C is not None:
        for t in report_tier_items:
            res = calc_tier_iec60890(
                tier=t,
                tiers=all_tiers,  # IMPORTANT: use full model for touching checks
                wall_mounted=bool(getattr(t, "wall_mounted", False)),
                inlet_area_cm2=inlet_area_cm2,
                ambient_C=float(ambient_C),
            )

            wall = required_airflow_with_wall_loss(
                P_W=res["P"],
                amb_C=ambient_C,
                max_internal_C=res["limit_C"],
                enclosure_area_m2=res["Ae"],
                allow_wall_dissipation=project.meta.allow_material_dissipation,
                k_W_per_m2K=project.meta.enclosure_k_W_m2K,
            )

            tier_thermals.append(
                TierThermal(
                    tag=str(getattr(t, "name", getattr(t, "tag", "Tier"))),
                    Ae=res["Ae"],
                    P_W=res["P"],
                    k=res["k"],
                    c=res["c"],
                    x=res["x"],
                    f=res["f"],
                    g=res["g"],
                    vent=t.is_ventilated,
                    curve=t.curve_no,
                    ambient_C=res["ambient_C"],

                    dt_mid=res["dt_mid"],
                    dt_top=res["dt_top"],
                    dt_075=res.get("dt_075"),

                    T_mid=res["T_mid"],
                    T_top=res["T_top"],
                    T_075=res.get("T_075"),

                    max_C=res["limit_C"],
                    compliant_mid=res["compliant_mid"],
                    compliant_top=res["compliant_top"],

                    enclosure_material=project.meta.enclosure_material,
                    enclosure_k=project.meta.enclosure_k_W_m2K,

                    q_walls_W=wall.q_walls_W,
                    q_fans_W=wall.q_fans_W,
                    airflow_m3h=wall.airflow_m3h,

                    dims_m=_dims_m_from_tier(t),
                    surfaces=res["surfaces"],
                    figures_used=res.get("figures_used", []),
                )
            )

    out_pdf = Path(out_pdf)
    return export_simple_report(
        out_pdf=out_pdf,
        meta=meta,
        enclosure_type=enclosure,
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
