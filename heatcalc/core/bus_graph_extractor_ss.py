# heatcalc/core/bus_graph_extractor_ss.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import math
from PyQt5.QtCore import QPointF

from heatcalc.ui.bus_items import BusLineItem, BusLoadItem, BusJoinItem, BusSpecUI
from heatcalc.ui.bus_items import BUS_SOURCE_TYPE
from heatcalc.ui.tier_item import TierItem


# ----------------- graph data -----------------

@dataclass(frozen=True)
class GNode:
    id: int
    p: QPointF


@dataclass
class GEdge:
    id: int
    u: int
    v: int
    length_m: float
    spec: BusSpecUI
    ui_item: BusLineItem
    tier: TierItem | None   # <-- NEW


@dataclass
class GLoad:
    id: int
    node: int
    I_A: float


@dataclass
class GJoin:
    id: int
    node: int
    R_contact_20_uohm: float


@dataclass
class GraphExtractResult:
    nodes: Dict[int, GNode]
    edges: Dict[int, GEdge]
    loads: List[GLoad]
    joins: List[GJoin]
    source_node: Optional[int]


# ----------------- helpers -----------------

def _pt_key(p: QPointF, tol: float) -> Tuple[int, int]:
    return (int(round(p.x() / tol)), int(round(p.y() / tol)))


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _project_point_to_segment(p: QPointF, a: QPointF, b: QPointF) -> Tuple[float, QPointF]:

    ax, ay = a.x(), a.y()
    bx, by = b.x(), b.y()
    px, py = p.x(), p.y()

    vx, vy = (bx - ax), (by - ay)
    wx, wy = (px - ax), (py - ay)

    vv = vx * vx + vy * vy

    if vv <= 1e-12:
        return 0.0, QPointF(ax, ay)

    s = (wx * vx + wy * vy) / vv

    s_clamped = max(0.0, min(1.0, s))

    proj = QPointF(ax + s_clamped * vx, ay + s_clamped * vy)

    return s_clamped, proj


# -----------------------------------------------------------
# Extraction
# -----------------------------------------------------------

def extract_bus_graph_from_scene(
    scene,
    *,
    node_merge_tol_px: float = 1.0,
    px_to_m: float = 0.001
) -> GraphExtractResult:

    # ---------------------------------------------------------
    # 1. Collect tiers
    # ---------------------------------------------------------

    tiers = [i for i in scene.items() if isinstance(i, TierItem)]

    bus_lines: List[BusLineItem] = []
    loads_ui: List[BusLoadItem] = []
    joins_ui: List[BusJoinItem] = []

    # collect only items belonging to tiers
    for tier in tiers:

        for it in tier.bus_items():

            if isinstance(it, BusLineItem):
                bus_lines.append(it)

            elif isinstance(it, BusLoadItem):
                loads_ui.append(it)

            elif isinstance(it, BusJoinItem):
                joins_ui.append(it)

    # ---------------------------------------------------------
    # 2. Detect connection points (T-intersections)
    # ---------------------------------------------------------

    connection_points = []

    for i, L in enumerate(bus_lines):

        La, Lb = _bus_scene_endpoints(L)

        for p in (La, Lb):

            for j, other in enumerate(bus_lines):

                if i == j:
                    continue

                a, b = _bus_scene_endpoints(other)

                s, proj = _project_point_to_segment(p, a, b)

                if _dist(p, proj) <= node_merge_tol_px:

                    if s <= 1e-6 or s >= 1 - 1e-6:
                        continue

                    if _dist(p, a) <= node_merge_tol_px:
                        continue

                    if _dist(p, b) <= node_merge_tol_px:
                        continue

                    connection_points.append(p)

    unique = {}

    for p in connection_points:
        k = _pt_key(p, node_merge_tol_px)
        unique[k] = p

    intersection_points = list(unique.values())

    # ---------------------------------------------------------
    # 3. Build node map
    # ---------------------------------------------------------

    node_ids: Dict[Tuple[int, int], int] = {}
    nodes: Dict[int, GNode] = {}

    def get_node_id(p: QPointF) -> int:

        k = _pt_key(p, node_merge_tol_px)

        if k not in node_ids:
            nid = len(nodes)
            node_ids[k] = nid
            nodes[nid] = GNode(id=nid, p=p)

        return node_ids[k]

    # endpoints
    for bl in bus_lines:
        a, b = _bus_scene_endpoints(bl)
        get_node_id(a)
        get_node_id(b)

    # intersections
    for p in intersection_points:
        get_node_id(p)

    # ---------------------------------------------------------
    # 4. Join nodes
    # ---------------------------------------------------------

    pending_joins: List[Tuple[int, int, float]] = []

    for i, jn in enumerate(joins_ui):

        p = jn.center()

        best_dist = float("inf")
        best_proj = None

        for bl in bus_lines:

            a, b = _bus_scene_endpoints(bl)

            s, proj = _project_point_to_segment(p, a, b)

            d = _dist(p, proj)

            if d < best_dist:
                best_dist = d
                best_proj = proj

        if best_proj is None or best_dist > 15:
            continue

        nid = get_node_id(best_proj)

        pending_joins.append((i, nid, float(jn.R_contact_20_uohm)))

    # ---------------------------------------------------------
    # 5. Build edges
    # ---------------------------------------------------------

    edges: Dict[int, GEdge] = {}
    edge_id = 0

    for bl in bus_lines:

        a, b = _bus_scene_endpoints(bl)

        nodes_on_line = []

        for nid, node in nodes.items():

            s, proj = _project_point_to_segment(node.p, a, b)

            if _dist(node.p, proj) <= node_merge_tol_px + 1e-6:
                nodes_on_line.append((s, nid))

        if len(nodes_on_line) < 2:
            continue

        nodes_on_line = list(dict.fromkeys(nodes_on_line))
        nodes_on_line.sort(key=lambda x: x[0])

        tier = bl.parentItem()

        if isinstance(tier, TierItem):
            tier_ref = tier
        else:
            tier_ref = None

        for i in range(len(nodes_on_line) - 1):

            s0, n0 = nodes_on_line[i]
            s1, n1 = nodes_on_line[i + 1]

            p0 = nodes[n0].p
            p1 = nodes[n1].p

            L_px = _dist(p0, p1)

            if L_px < 1e-6:
                continue

            edges[edge_id] = GEdge(
                id=edge_id,
                u=n0,
                v=n1,
                length_m=L_px * px_to_m,
                spec=bl.spec,
                ui_item=bl,
                tier=tier_ref
            )

            edge_id += 1

    # ---------------------------------------------------------
    # 6. Source node
    # ---------------------------------------------------------

    source_node = None

    for item in scene.items():

        if item.type() == BUS_SOURCE_TYPE:

            pos = item.center()

            nid = find_nearest_node(nodes, pos)

            if source_node is not None:
                raise ValueError("Multiple bus sources detected")

            source_node = nid

    if source_node is None:
        raise ValueError("No bus source defined")

    # ---------------------------------------------------------
    # 7. Loads
    # ---------------------------------------------------------

    loads: List[GLoad] = []

    for i, ld in enumerate(loads_ui):

        p = ld.center()

        best_n = min(nodes.values(), key=lambda n: _dist(p, n.p))

        loads.append(
            GLoad(
                id=i,
                node=best_n.id,
                I_A=float(ld.I_load_A)
            )
        )

    # ---------------------------------------------------------
    # 8. Joins
    # ---------------------------------------------------------

    joins: List[GJoin] = []

    for jid, nid, R_uohm in pending_joins:

        joins.append(
            GJoin(
                id=jid,
                node=nid,
                R_contact_20_uohm=R_uohm
            )
        )

    return GraphExtractResult(
        nodes=nodes,
        edges=edges,
        loads=loads,
        joins=joins,
        source_node=source_node
    )


# -----------------------------------------------------------
# utilities
# -----------------------------------------------------------

def find_nearest_node(nodes: Dict[int, GNode], p: QPointF) -> int:

    best_node = None
    best_dist = float("inf")

    for nid, node in nodes.items():

        d = _dist(p, node.p)

        if d < best_dist:
            best_dist = d
            best_node = nid

    if best_node is None:
        raise ValueError("No nodes exist in graph")

    return best_node


def _bus_scene_endpoints(bl: BusLineItem) -> Tuple[QPointF, QPointF]:

    line = bl.line()

    a = bl.mapToScene(line.p1())
    b = bl.mapToScene(line.p2())

    return a, b