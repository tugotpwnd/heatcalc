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
    ui_items: List[object] = None  # UI items associated with this node

    def __post_init__(self):
        if self.ui_items is None:
            self.ui_items = []


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

    # ---- UI linkage ----
    ui_item: object | None = None       # BusLineItem for bus edges
    ui_join_item: object | None = None  # BusJoinItem for explicit joint edges

@dataclass
class Load:
    node: int
    I_A: float
    ui_item: object | None = None


@dataclass
class Join:
    edge_id: int
    x_m: float
    spec: BusbarJointSpec
    ui_item: object | None = None


@dataclass
class Graph:
    nodes: Dict[int, Node]
    edges: Dict[int, Edge]
    loads: List[Load]
    joins: List[Join]
    source_node: Optional[int]
    source_ui_item: object | None = None

    # edges that share a node but belong to different tiers
    # used to inject copper conduction between tiers
    cross_tier_links: List[tuple[int, int]] = None


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

def _debug_cross_tier_candidates(edges: Dict[int, Edge], nodes: Dict[int, Node]) -> None:
    """
    Debug report showing whether edges from different tiers:
      1. share node ids
      2. merely touch geometrically
      3. are completely separate
    """

    print("\n================ GRAPH CROSS-TIER DEBUG ================")

    edge_list = list(edges.values())
    found = 0

    for i in range(len(edge_list)):
        ea = edge_list[i]
        if ea.is_joint:
            continue

        for j in range(i + 1, len(edge_list)):
            eb = edge_list[j]
            if eb.is_joint:
                continue

            if ea.tier is eb.tier:
                continue

            a0 = nodes[ea.u].p
            a1 = nodes[ea.v].p
            b0 = nodes[eb.u].p
            b1 = nodes[eb.v].p

            share_id = (
                ea.u == eb.u or
                ea.u == eb.v or
                ea.v == eb.u or
                ea.v == eb.v
            )

            dists = [
                ("ea.u / eb.u", _dist(a0, b0)),
                ("ea.u / eb.v", _dist(a0, b1)),
                ("ea.v / eb.u", _dist(a1, b0)),
                ("ea.v / eb.v", _dist(a1, b1)),
            ]
            best_name, best_dist = min(dists, key=lambda x: x[1])

            # only report pairs that look like candidate crossings
            if share_id or best_dist < 1.0:
                found += 1
                print(
                    f"Pair E{ea.id} (Tier {id(ea.tier)}) <-> "
                    f"E{eb.id} (Tier {id(eb.tier)}) | "
                    f"share_id={share_id} | closest={best_name} | d={best_dist:.4f}px"
                )
                print(
                    f"    E{ea.id}: u={ea.u}@({a0.x():.2f},{a0.y():.2f})  "
                    f"v={ea.v}@({a1.x():.2f},{a1.y():.2f})"
                )
                print(
                    f"    E{eb.id}: u={eb.u}@({b0.x():.2f},{b0.y():.2f})  "
                    f"v={eb.v}@({b1.x():.2f},{b1.y():.2f})"
                )

    if found == 0:
        print("No near cross-tier candidate pairs found.")

def _detect_cross_tier_links(edges: Dict[int, Edge], nodes: Dict[int, Node]) -> List[tuple[int, int]]:
    """
    Detect edges from different tiers that share a node id.

    These are true topology-level cross-tier links.
    """
    links = []

    print("\n================ DETECT CROSS-TIER LINKS ================")

    edge_list = list(edges.values())

    for i in range(len(edge_list)):
        ea = edge_list[i]
        if ea.is_joint:
            continue

        for j in range(i + 1, len(edge_list)):
            eb = edge_list[j]
            if eb.is_joint:
                continue

            if ea.tier is eb.tier:
                continue

            shared = (
                ea.u == eb.u or
                ea.u == eb.v or
                ea.v == eb.u or
                ea.v == eb.v
            )

            if shared:
                links.append((ea.id, eb.id))
                print(
                    f"LINK: E{ea.id} <-> E{eb.id} | "
                    f"nodes: ({ea.u},{ea.v}) <-> ({eb.u},{eb.v})"
                )

    print(f"Total cross-tier links detected: {len(links)}")
    return links

def extract_graph(scene, px_to_m=0.001):

    nodes = {}
    edges = {}
    loads = []
    joins = []

    node_id = 0
    edge_id = 0

    bus_lines = [i for i in scene.items() if isinstance(i, BusLineItem)]

    node_lookup = {}
    NODE_SNAP_TOL = 0.5  # pixels

    def get_node(p, ui_item=None):

        # search for existing node within tolerance
        for nid, node in nodes.items():
            if _dist(node.p, p) <= NODE_SNAP_TOL:
                if ui_item is not None and ui_item not in node.ui_items:
                    node.ui_items.append(ui_item)
                return nid

        # create new node
        nonlocal node_id
        nid = node_id
        nodes[nid] = Node(nid, p)

        if ui_item is not None:
            nodes[nid].ui_items.append(ui_item)

        node_id += 1
        return nid

    # -------------------------------------------------
    # Collect raw segments
    # -------------------------------------------------

    segments = []

    for bl in bus_lines:

        line = bl.line()

        a = bl.mapToScene(line.p1())
        b = bl.mapToScene(line.p2())

        tier = None
        obj = bl.parentItem()
        while obj is not None:
            if isinstance(obj, TierItem):
                tier = obj
                break
            if hasattr(obj, "parentItem"):
                obj = obj.parentItem()
            else:
                break

        segments.append({
            "a": a,
            "b": b,
            "spec": bl.spec,
            "tier": tier,
            "ui_item": bl,
        })

    # -------------------------------------------------
    # Detect endpoint-on-segment intersections
    # -------------------------------------------------

    split_points_by_segment = {}

    for i, sa in enumerate(segments):

        for j, sb in enumerate(segments):

            if i == j:
                continue

            for p in [sa["a"], sa["b"]]:

                if _point_on_segment(p, sb["a"], sb["b"]):

                    # ignore if already an endpoint
                    if _dist(p, sb["a"]) < 0.5 or _dist(p, sb["b"]) < 0.5:
                        continue

                    split_points_by_segment.setdefault(j, []).append(p)

    # -------------------------------------------------
    # Split segments at ALL intersection points
    # -------------------------------------------------

    new_segments = []

    for idx, seg in enumerate(segments):

        split_pts = split_points_by_segment.get(idx)

        if not split_pts:
            new_segments.append(seg)
            continue

        a = seg["a"]
        b = seg["b"]

        ax, ay = a.x(), a.y()
        bx, by = b.x(), b.y()

        dx = bx - ax
        dy = by - ay

        L2 = dx * dx + dy * dy

        if L2 == 0:
            new_segments.append(seg)
            continue

        # parameterise split points along the segment
        t_points = []

        for p in split_pts:
            t = ((p.x() - ax) * dx + (p.y() - ay) * dy) / L2
            t = max(0.0, min(1.0, t))

            t_points.append((t, p))

        # deduplicate using rounded t
        dedup = {}
        for t, p in t_points:
            dedup[round(t, 6)] = p

        ordered = sorted((t, p) for t, p in dedup.items())

        chain = [a] + [p for _, p in ordered] + [b]

        for p0, p1 in zip(chain[:-1], chain[1:]):

            if _dist(p0, p1) <= 1e-9:
                continue

            new_segments.append({
                "a": p0,
                "b": p1,
                "spec": seg["spec"],
                "tier": seg["tier"],
                "ui_item": seg["ui_item"],
            })

    segments = new_segments
    # -------------------------------------------------
    # Build nodes and edges
    # -------------------------------------------------

    for seg in segments:

        a = seg["a"]
        b = seg["b"]

        na = get_node(a, seg["ui_item"])
        nb = get_node(b, seg["ui_item"])

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
            ui_item=seg.get("ui_item"),
            ui_join_item=None,
        )
        edge_id += 1

    # -------------------------------------------------
    # Source
    # -------------------------------------------------

    source_node = None
    source_ui_item = None

    for item in scene.items():

        if item.type() == BUS_SOURCE_TYPE:

            p = item.center()

            best = min(nodes.values(), key=lambda n: _dist(p, n.p))

            source_node = best.id
            source_ui_item = item
            if item not in best.ui_items:
                best.ui_items.append(item)

    # -------------------------------------------------
    # Loads
    # -------------------------------------------------

    for item in scene.items():

        if isinstance(item, BusLoadItem):

            p = item.center()

            best = min(nodes.values(), key=lambda n: _dist(p, n.p))

            loads.append(Load(best.id, item.I_load_A, ui_item=item))
            if item not in best.ui_items:
                best.ui_items.append(item)

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
                        ui_item=item,
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
                n1 = get_node(p1, e.ui_item)

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
                    ui_item=e.ui_item,
                    ui_join_item=None,
                )
                start_node = n1
                new_edge_id += 1

            # ---- explicit joint edge ----
            pj0 = _point_at_distance(a_pt, b_pt, x0, e.length_m)
            pj1 = _point_at_distance(a_pt, b_pt, x1, e.length_m)

            nj0 = start_node
            nj1 = get_node(pj1, e.ui_item)

            joint_len = x1 - x0

            joint_edge_id = new_edge_id
            new_edges[new_edge_id] = Edge(
                id=new_edge_id,
                u=nj0,
                v=nj1,
                length_m=joint_len,
                spec=j.spec,
                tier=e.tier,
                width_mm=e.width_mm,
                thickness_mm=e.thickness_mm,
                bars_in_parallel=e.bars_in_parallel,
                is_joint=True,
                joint_spec=j.spec,
                ui_item=None,
                ui_join_item=j.ui_item,
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
                ui_item=e.ui_item,
                ui_join_item=None,
            )
            new_edge_id += 1

    edges = new_edges

    # refine explicit joint geometry from connected bus edges
    _infer_joint_edge_geometry(edges)

    # graph-level debug: are cross-tier buses truly sharing nodes?
    _debug_cross_tier_candidates(edges, nodes)

    # detect copper continuity between tiers
    cross_tier_links = _detect_cross_tier_links(edges, nodes)

    return Graph(
        nodes=nodes,
        edges=edges,
        loads=loads,
        joins=joins,
        source_node=source_node,
        source_ui_item=source_ui_item,
        cross_tier_links=cross_tier_links,
    )