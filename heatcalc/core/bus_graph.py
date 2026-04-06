from dataclasses import dataclass
from typing import Dict, List, Optional
from PyQt5.QtCore import QPointF
import math

from win32comext.shell.demos.servers.shell_view import debug

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
    face_to_face_dim: str = "thickness"
    gap_to_wall_mm: float = 50.0
    orientation_to_wall: str = "width"

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

def _edge_shares_joint(edge: Edge, joint_edge: Edge) -> bool:
    return (
        edge.u == joint_edge.u or
        edge.u == joint_edge.v or
        edge.v == joint_edge.u or
        edge.v == joint_edge.v
    )


def _shared_joint_endpoints(edge: Edge, joint_edge: Edge) -> list[int]:
    out = []
    if edge.u == joint_edge.u or edge.v == joint_edge.u:
        out.append(joint_edge.u)
    if edge.u == joint_edge.v or edge.v == joint_edge.v:
        out.append(joint_edge.v)
    return out


def _geom_tuple(edge: Edge) -> tuple[float, float, int]:
    return (
        float(edge.width_mm),
        float(edge.thickness_mm),
        int(edge.bars_in_parallel),
    )

def _infer_joint_edge_geometry(edges: Dict[int, Edge], debug: bool = False) -> None:
    """
    Resolve joint host-bar and other-bar geometry.

    Design intent
    -------------
    - The explicit joint edge already inherits the geometry of the bus edge that
      was split to create it. That is the HOST bar and should be preserved.
    - The OTHER bar should be any non-joint bus edge connected to the joint that
      does not belong to that same host bus item.
    - If no foreign bus is found, mirror the host geometry.

    This is much more reliable than:
      - taking min(neighbour geometry)
      - picking the 2nd unique geometry by dictionary order
    """

    for ej in edges.values():
        if not ej.is_joint:
            continue

        js = ej.joint_spec

        neighbours = [
            e for e in edges.values()
            if e.id != ej.id
            and not e.is_joint
            and _edge_shares_joint(e, ej)
        ]

        # -------------------------------------------------
        # HOST geometry = geometry of the bus edge that was
        # split to create this explicit joint edge.
        # -------------------------------------------------
        host_w = getattr(js, "host_bar_width_mm", None) if js is not None else None
        host_t = getattr(js, "host_bar_thickness_mm", None) if js is not None else None
        host_n = getattr(js, "host_bar_count", None) if js is not None else None

        if host_w is None:
            host_w = ej.width_mm or 100.0
        if host_t is None:
            host_t = ej.thickness_mm or 10.0
        if host_n is None:
            host_n = ej.bars_in_parallel or 1

        ej.width_mm = float(host_w)
        ej.thickness_mm = float(host_t)
        ej.bars_in_parallel = max(1, int(host_n))

        host_ui_item_id = getattr(js, "_host_ui_item_id", None) if js is not None else None

        host_neighbours = []
        other_candidates = []

        for e in neighbours:
            same_host = False

            # Best discriminator: same original BusLineItem
            if host_ui_item_id is not None and getattr(e, "ui_item", None) is not None:
                same_host = (id(e.ui_item) == host_ui_item_id)

            # Fallback if ui identity is unavailable
            elif _geom_tuple(e) == _geom_tuple(ej):
                same_host = True

            if same_host:
                host_neighbours.append(e)
            else:
                other_candidates.append(e)

        # -------------------------------------------------
        # Select OTHER bar
        # -------------------------------------------------
        if other_candidates:
            # Prefer the candidate attached at the joint endpoint that has the
            # strongest non-host presence. This helps midpoint/end-point cases.
            endpoint_other_counts = {
                ej.u: 0,
                ej.v: 0,
            }

            for e in other_candidates:
                for n in _shared_joint_endpoints(e, ej):
                    endpoint_other_counts[n] += 1

            def _sort_key(edge: Edge):
                shared_nodes = _shared_joint_endpoints(edge, ej)
                endpoint_score = 0
                if shared_nodes:
                    endpoint_score = max(endpoint_other_counts.get(n, 0) for n in shared_nodes)
                # Prefer stronger endpoint match first, then shorter edge, then id
                return (-endpoint_score, float(edge.length_m), int(edge.id))

            other = sorted(other_candidates, key=_sort_key)[0]
            other_w = float(other.width_mm)
            other_t = float(other.thickness_mm)
            other_n = max(1, int(other.bars_in_parallel))
        else:
            # No foreign bar found -> mirror host geometry
            other = None
            other_w = float(ej.width_mm)
            other_t = float(ej.thickness_mm)
            other_n = max(1, int(ej.bars_in_parallel))

        if js is not None:
            # persist resolved geometry on the spec for downstream solvers
            js.host_bar_width_mm = float(ej.width_mm)
            js.host_bar_thickness_mm = float(ej.thickness_mm)
            js.host_bar_count = int(ej.bars_in_parallel)

            js.other_bar_width_mm = float(other_w)
            js.other_bar_thickness_mm = float(other_t)
            js.other_bar_count = int(other_n)

        if debug:
            print("\n[JOINT GEOMETRY DEBUG]")
            print(f"joint_edge_id          = {ej.id}")
            print(f"joint_nodes            = ({ej.u}, {ej.v})")
            print(f"host_geom_mm           = ({ej.width_mm:.3f}, {ej.thickness_mm:.3f}, {ej.bars_in_parallel})")
            print(f"host_ui_item_id        = {host_ui_item_id}")

            print("neighbours:")
            for e in neighbours:
                shared = _shared_joint_endpoints(e, ej)
                print(
                    f"  edge={e.id} | shared_nodes={shared} | "
                    f"geom=({e.width_mm:.3f}, {e.thickness_mm:.3f}, {e.bars_in_parallel}) | "
                    f"ui_item_id={id(e.ui_item) if getattr(e, 'ui_item', None) is not None else None}"
                )

            print("host_neighbours:")
            for e in host_neighbours:
                print(
                    f"  edge={e.id} | geom=({e.width_mm:.3f}, {e.thickness_mm:.3f}, {e.bars_in_parallel})"
                )

            print("other_candidates:")
            for e in other_candidates:
                shared = _shared_joint_endpoints(e, ej)
                print(
                    f"  edge={e.id} | shared_nodes={shared} | "
                    f"geom=({e.width_mm:.3f}, {e.thickness_mm:.3f}, {e.bars_in_parallel})"
                )

            print(
                f"resolved_other_geom_mm = ({other_w:.3f}, {other_t:.3f}, {other_n})"
            )
            print(
                f"selected_other_edge_id = {other.id if other is not None else None}"
            )

def extract_graph(scene, px_to_m=0.001, debug=False):

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
            face_to_face_dim=getattr(seg["spec"], "face_to_face_dim", "thickness"),
            gap_to_wall_mm=getattr(seg["spec"], "gap_to_wall_mm", 50.0),
            orientation_to_wall=getattr(seg["spec"], "orientation_to_wall", "width"),
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

                parent_bus = item.parentItem()

                if isinstance(parent_bus, BusLineItem):
                    # ---- TRUE HOST (from UI ownership) ----
                    spec._host_ui_item_id = id(parent_bus)
                    spec.host_bar_width_mm = float(parent_bus.spec.width_mm)
                    spec.host_bar_thickness_mm = float(parent_bus.spec.thickness_mm)
                    spec.host_bar_count = int(parent_bus.spec.bars_in_parallel)

                    if debug:
                        print("\n[JOIN HOST RESOLUTION]")
                        print(f"JOIN item id        = {id(item)}")
                        print(f"PARENT BUS id       = {id(parent_bus)}")
                        print(f"PARENT WIDTH mm     = {parent_bus.spec.width_mm}")
                        print(f"PARENT THICKNESS mm = {parent_bus.spec.thickness_mm}")

                else:
                    # ---- FALLBACK (shouldn't happen, but shouldn't crash) ----
                    spec._host_ui_item_id = id(best_edge.ui_item) if best_edge.ui_item else None

                    spec.host_bar_width_mm = float(best_edge.width_mm)
                    spec.host_bar_thickness_mm = float(best_edge.thickness_mm)
                    spec.host_bar_count = int(best_edge.bars_in_parallel)

                    if debug:
                        print("\n[JOIN HOST FALLBACK]")
                        print("There was an error resolving host bus for join")

                spec._host_edge_id_before_joint_split = int(best_edge.id)

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
                    face_to_face_dim=e.face_to_face_dim,
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
                width_mm=float(getattr(j.spec, "host_bar_width_mm", e.width_mm)),
                thickness_mm=float(getattr(j.spec, "host_bar_thickness_mm", e.thickness_mm)),
                bars_in_parallel=max(1, int(getattr(j.spec, "host_bar_count", e.bars_in_parallel))),
                face_to_face_dim=e.face_to_face_dim,
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
                face_to_face_dim=e.face_to_face_dim,
                is_joint=False,
                joint_spec=None,
                ui_item=e.ui_item,
                ui_join_item=None,
            )
            new_edge_id += 1

    edges = new_edges

    # refine explicit joint geometry from connected bus edges
    _infer_joint_edge_geometry(edges, debug=False)

    return Graph(
        nodes=nodes,
        edges=edges,
        loads=loads,
        joins=joins,
        source_node=source_node,
        source_ui_item=source_ui_item,
    )