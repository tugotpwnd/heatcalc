from dataclasses import dataclass
from typing import Dict, List, Optional
from PyQt5.QtCore import QPointF
import math

from heatcalc.core.models import BusbarJointSpec
from heatcalc.ui.bus_items import BusLineItem, BusLoadItem, BusJoinItem
from heatcalc.ui.bus_items import BUS_SOURCE_TYPE
from heatcalc.ui.tier_item import TierItem


@dataclass
class Node:
    id: int
    p: QPointF


@dataclass
class Edge:
    id: int
    u: int
    v: int
    length_m: float
    spec: object
    tier: TierItem | None
    I_A: float = 0.0

    # ---- geometry carried explicitly by every edge ----
    width_mm: float = 0.0
    thickness_mm: float = 0.0
    bars_in_parallel: int = 1

    # ---- joint metadata ----
    is_joint: bool = False
    joint_spec: Optional[BusbarJointSpec] = None

@dataclass
class Load:
    node: int
    I_A: float


@dataclass
class Join:
    edge_id: int
    x_m: float
    spec: BusbarJointSpec


@dataclass
class Graph:
    nodes: Dict[int, Node]
    edges: Dict[int, Edge]
    loads: List[Load]
    joins: List[Join]
    source_node: Optional[int]


def _dist(a: QPointF, b: QPointF):
    return math.hypot(a.x() - b.x(), a.y() - b.y())

def _point_on_segment(p, a, b, tol=0.25):
    """Return True if point p lies on segment a-b (pixel tolerance)."""

    ax, ay = a.x(), a.y()
    bx, by = b.x(), b.y()
    px, py = p.x(), p.y()

    # segment length
    L = math.hypot(bx - ax, by - ay)
    if L == 0:
        return False

    # projection factor
    t = ((px-ax)*(bx-ax) + (py-ay)*(by-ay)) / (L*L)

    if t < 0 or t > 1:
        return False

    # closest point
    cx = ax + t*(bx-ax)
    cy = ay + t*(by-ay)

    d = math.hypot(px-cx, py-cy)

    return d < tol

def _point_at_distance(a: QPointF, b: QPointF, x_m: float, total_len_m: float) -> QPointF:
    if total_len_m <= 1e-12:
        return QPointF(a)

    r = max(0.0, min(1.0, x_m / total_len_m))
    return QPointF(
        a.x() + r * (b.x() - a.x()),
        a.y() + r * (b.y() - a.y()),
    )


def _infer_joint_edge_geometry(edges: Dict[int, Edge]) -> None:
    """
    For each joint edge:

    1. Determine the bus geometry the joint lives on
       (smallest width/thickness of neighbouring bus segments)

    2. Determine the geometry of the *other* bus entering the joint
       (used for clamped-edge joints).
    """

    for ej in edges.values():

        if not ej.is_joint:
            continue

        neighbours = []

        for e in edges.values():

            if e.id == ej.id:
                continue

            if e.is_joint:
                continue

            shared = (
                e.u == ej.u or e.u == ej.v or
                e.v == ej.u or e.v == ej.v
            )

            if shared:
                neighbours.append(e)

        if not neighbours:
            # fallback
            ej.width_mm = ej.width_mm or 100.0
            ej.thickness_mm = ej.thickness_mm or 10.0
            ej.bars_in_parallel = max(1, ej.bars_in_parallel)
            continue

        # -------------------------------------------------
        # Primary bus (the one the joint lies on)
        # -------------------------------------------------

        ej.width_mm = min(e.width_mm for e in neighbours)
        ej.thickness_mm = min(e.thickness_mm for e in neighbours)
        ej.bars_in_parallel = max(1, min(e.bars_in_parallel for e in neighbours))

        # -------------------------------------------------
        # Determine the "other bar" geometry
        # -------------------------------------------------

        if ej.joint_spec is not None:

            # find the neighbour with different geometry
            other_candidates = [
                e for e in neighbours
                if (
                    e.width_mm != ej.width_mm or
                    e.thickness_mm != ej.thickness_mm
                )
            ]

            if other_candidates:
                other = other_candidates[0]

                ej.joint_spec.other_bar_width_mm = other.width_mm
                ej.joint_spec.other_bar_thickness_mm = other.thickness_mm
                ej.joint_spec.other_bar_count = other.bars_in_parallel

def extract_graph(scene, px_to_m=0.001):

    nodes = {}
    edges = {}
    loads = []
    joins = []

    node_id = 0
    edge_id = 0

    bus_lines = [i for i in scene.items() if isinstance(i, BusLineItem)]

    node_lookup = {}

    def get_node(p):

        key = (round(p.x(), 2), round(p.y(), 2))

        if key not in node_lookup:
            nonlocal node_id
            node_lookup[key] = node_id
            nodes[node_id] = Node(node_id, p)
            node_id += 1

        return node_lookup[key]

    # -------------------------------------------------
    # Collect raw segments
    # -------------------------------------------------

    segments = []

    for bl in bus_lines:

        line = bl.line()

        a = bl.mapToScene(line.p1())
        b = bl.mapToScene(line.p2())

        segments.append({
            "a": a,
            "b": b,
            "spec": bl.spec,
            "tier": bl.parentItem()
        })

    # -------------------------------------------------
    # Detect endpoint-on-segment intersections
    # -------------------------------------------------

    extra_points = []

    for i, sa in enumerate(segments):

        for j, sb in enumerate(segments):

            if i == j:
                continue

            for p in [sa["a"], sa["b"]]:

                if _point_on_segment(p, sb["a"], sb["b"]):

                    if _dist(p, sb["a"]) < 0.5 or _dist(p, sb["b"]) < 0.5:
                        continue

                    extra_points.append((j, p))

    # -------------------------------------------------
    # Split segments where intersections occur
    # -------------------------------------------------

    for seg_index, p in extra_points:

        seg = segments[seg_index]

        if seg is None:
            continue

        a = seg["a"]
        b = seg["b"]

        segments[seg_index] = None

        segments.append({
            "a": a,
            "b": p,
            "spec": seg["spec"],
            "tier": seg["tier"]
        })

        segments.append({
            "a": p,
            "b": b,
            "spec": seg["spec"],
            "tier": seg["tier"]
        })

    segments = [s for s in segments if s is not None]

    # -------------------------------------------------
    # Build nodes and edges
    # -------------------------------------------------

    for seg in segments:

        a = seg["a"]
        b = seg["b"]

        na = get_node(a)
        nb = get_node(b)

        L = _dist(a, b)

        edges[edge_id] = Edge(
            id=edge_id,
            u=na,
            v=nb,
            length_m=L * px_to_m,
            spec=seg["spec"],
            tier=seg["tier"],
            width_mm=seg["spec"].width_mm,
            thickness_mm=seg["spec"].thickness_mm,
            bars_in_parallel=seg["spec"].bars_in_parallel,
            is_joint=False,
            joint_spec=None,
        )

        edge_id += 1

    # -------------------------------------------------
    # Source
    # -------------------------------------------------

    source_node = None

    for item in scene.items():

        if item.type() == BUS_SOURCE_TYPE:

            p = item.center()

            best = min(nodes.values(), key=lambda n: _dist(p, n.p))

            source_node = best.id

    # -------------------------------------------------
    # Loads
    # -------------------------------------------------

    for item in scene.items():

        if isinstance(item, BusLoadItem):

            p = item.center()

            best = min(nodes.values(), key=lambda n: _dist(p, n.p))

            loads.append(Load(best.id, item.I_load_A))

    # -------------------------------------------------
    # Joins (placed along edges)
    # -------------------------------------------------

    for item in scene.items():

        if isinstance(item, BusJoinItem):

            p = item.center()

            best_edge = None
            best_t = None
            best_dist = 1e9

            for e in edges.values():

                a = nodes[e.u].p
                b = nodes[e.v].p

                ax, ay = a.x(), a.y()
                bx, by = b.x(), b.y()
                px, py = p.x(), p.y()

                dx = bx - ax
                dy = by - ay

                L2 = dx * dx + dy * dy
                if L2 == 0:
                    continue

                t = ((px - ax) * dx + (py - ay) * dy) / L2

                if t < 0 or t > 1:
                    continue

                cx = ax + t * dx
                cy = ay + t * dy

                d = math.hypot(px - cx, py - cy)

                if d < best_dist:
                    best_dist = d
                    best_edge = e
                    best_t = t

            if best_edge is not None:
                x_m = best_t * best_edge.length_m

                spec = BusbarJointSpec(**vars(item.spec))
                spec.x_m = x_m

                joins.append(
                    Join(
                        edge_id=best_edge.id,
                        x_m=x_m,
                        spec=spec,
                    )
                )

    # -------------------------------------------------
    # Split edges where joins occur, and insert explicit joint edges
    # -------------------------------------------------
    new_edges = {}
    new_edge_id = 0

    for e in edges.values():

        edge_joins = [j for j in joins if j.edge_id == e.id]

        if not edge_joins:
            e.id = new_edge_id
            new_edges[new_edge_id] = e
            new_edge_id += 1
            continue

        edge_joins.sort(key=lambda j: j.x_m)

        a_pt = nodes[e.u].p
        b_pt = nodes[e.v].p

        start_node = e.u
        prev_x = 0.0

        for j in edge_joins:

            overlap_m = max(float(j.spec.overlap_m), 1e-6)

            # proposed joint interval
            x0 = j.x_m - 0.5 * overlap_m
            x1 = j.x_m + 0.5 * overlap_m

            # clamp to segment bounds
            x0 = max(0.0, x0)
            x1 = min(e.length_m, x1)

            # enforce monotonic split
            x0 = max(prev_x, x0)

            # discard invalid or degenerate intervals
            if x1 <= x0:
                continue

            # if the overlap got crushed, force a tiny explicit joint edge
            if x1 - x0 < 1e-6:
                x0 = max(prev_x, j.x_m)
                x1 = min(e.length_m, x0 + 1e-6)

                if x1 <= x0:
                    continue

            # ---- pre-joint bus segment ----
            pre_len = x0 - prev_x
            if pre_len > 1e-9:
                p0 = _point_at_distance(a_pt, b_pt, prev_x, e.length_m)
                p1 = _point_at_distance(a_pt, b_pt, x0, e.length_m)

                n0 = start_node
                n1 = get_node(p1)

                new_edges[new_edge_id] = Edge(
                    id=new_edge_id,
                    u=n0,
                    v=n1,
                    length_m=pre_len,
                    spec=e.spec,
                    tier=e.tier,
                    width_mm=e.width_mm,
                    thickness_mm=e.thickness_mm,
                    bars_in_parallel=e.bars_in_parallel,
                    is_joint=False,
                    joint_spec=None,
                )
                start_node = n1
                new_edge_id += 1

            # ---- explicit joint edge ----
            pj0 = _point_at_distance(a_pt, b_pt, x0, e.length_m)
            pj1 = _point_at_distance(a_pt, b_pt, x1, e.length_m)

            nj0 = start_node
            nj1 = get_node(pj1)

            joint_len = x1 - x0

            joint_edge_id = new_edge_id
            new_edges[new_edge_id] = Edge(
                id=new_edge_id,
                u=nj0,
                v=nj1,
                length_m=joint_len,
                spec=j.spec,  # keep the joint spec here
                tier=e.tier,
                width_mm=e.width_mm,  # temporary, refined below
                thickness_mm=e.thickness_mm,
                bars_in_parallel=e.bars_in_parallel,
                is_joint=True,
                joint_spec=j.spec,
            )
            new_edge_id += 1

            # point the join object at the explicit joint edge
            j.edge_id = joint_edge_id

            start_node = nj1
            prev_x = x1

        # ---- final trailing bus segment ----
        final_len = e.length_m - prev_x
        if final_len > 1e-9:
            new_edges[new_edge_id] = Edge(
                id=new_edge_id,
                u=start_node,
                v=e.v,
                length_m=final_len,
                spec=e.spec,
                tier=e.tier,
                width_mm=e.width_mm,
                thickness_mm=e.thickness_mm,
                bars_in_parallel=e.bars_in_parallel,
                is_joint=False,
                joint_spec=None,
            )
            new_edge_id += 1

    edges = new_edges

    # refine explicit joint geometry from connected bus edges
    _infer_joint_edge_geometry(edges)

    return Graph(
        nodes=nodes,
        edges=edges,
        loads=loads,
        joins=joins,
        source_node=source_node,
    )
