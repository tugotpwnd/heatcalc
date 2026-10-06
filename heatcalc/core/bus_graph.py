from __future__ import annotations

from collections import defaultdict
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
    face_to_face_dim: str = "thickness"
    gap_to_wall_mm: float = 50.0
    orientation_to_wall: str = "width"

    # ---- joint metadata ----
    is_joint: bool = False
    joint_spec: Optional[BusbarJointSpec] = None

    # ---- UI linkage ----
    ui_item: object | None = None       # BusLineItem for bus edges
    ui_join_item: object | None = None  # BusJoinItem for explicit joint edges

    # ---- physical-run metadata ----
    physical_run_id: int | None = None
    physical_run_length_m: float | None = None
    physical_run_s0_m: float | None = None
    physical_run_s1_m: float | None = None

    @property
    def geom_tuple(self) -> tuple[float, float, int]:
        return (
            float(self.width_mm),
            float(self.thickness_mm),
            int(self.bars_in_parallel),
        )

    def geom_distance_to(self, other_geom_tuple: tuple[float, float, int] | None) -> float:
        if other_geom_tuple is None:
            return float("inf")
        return (
            abs(float(self.width_mm) - float(other_geom_tuple[0]))
            + abs(float(self.thickness_mm) - float(other_geom_tuple[1]))
            + 1000.0 * abs(int(self.bars_in_parallel) - int(other_geom_tuple[2]))
        )

    def _shared_node(self, other: "Edge") -> bool:
        return (
            self.u == other.u or
            self.u == other.v or
            self.v == other.u or
            self.v == other.v
        )

    def shared_endpoints(self, other: "Edge") -> List[int]:
        out = []
        if self.u == other.u or self.v == other.u:
            out.append(other.u)
        if self.u == other.v or self.v == other.v:
            out.append(other.v)
        return out


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
    owner_tier: object | None = None


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

    def get_edge_convection_mode(self, edge: Edge) -> str:
        u_p = self.nodes[edge.u].p
        v_p = self.nodes[edge.v].p
        dx = abs(u_p.x() - v_p.x())
        dy = abs(u_p.y() - v_p.y())
        return "vertical" if dy > dx else "horizontal"

    def get_joint_side_edges(self, joint_edge_id: int):
        """
        Classify non-joint graph edges incident to the explicit joint edge into:
            - host side candidates
            - other side candidates
        """
        ej = self.edges[int(joint_edge_id)]
        joint_spec = ej.joint_spec
        host_geom_mm = None
        other_geom_mm = None

        if joint_spec:
            host_geom_mm = (
                float(getattr(joint_spec, "host_width_mm", 0.0)),
                float(getattr(joint_spec, "host_thickness_mm", 0.0)),
                int(getattr(joint_spec, "host_bars", 1))
            )
            other_geom_mm = (
                float(getattr(joint_spec, "other_width_mm", 0.0)),
                float(getattr(joint_spec, "other_thickness_mm", 0.0)),
                int(getattr(joint_spec, "other_bars", 1))
            )

        incident = [
            e for e in self.edges.values()
            if e.id != ej.id
            and not e.is_joint
            and e._shared_node(ej)
        ]

        host_ui_item_id = getattr(joint_spec, "_host_ui_item_id", None)

        host_edges = []
        other_edges = []
        unknown_edges = []

        for e in incident:
            same_host = False
            same_other = False

            if host_ui_item_id is not None and e.ui_item is not None:
                same_host = (id(e.ui_item) == host_ui_item_id)

            eg = e.geom_tuple

            if host_geom_mm is not None and eg == host_geom_mm:
                same_host = True if not same_other else same_host

            if other_geom_mm is not None and eg == other_geom_mm:
                same_other = True if not same_host else same_other

            if same_host and not same_other:
                host_edges.append(e)
            elif same_other and not same_host:
                other_edges.append(e)
            else:
                unknown_edges.append(e)

        if host_ui_item_id is not None:
            other_edges.extend(unknown_edges)
            unknown_edges = []

        for e in unknown_edges:
            d_host = e.geom_distance_to(host_geom_mm)
            d_other = e.geom_distance_to(other_geom_mm)

            if d_host < d_other:
                host_edges.append(e)
            elif d_other < d_host:
                other_edges.append(e)
            else:
                if not host_edges:
                    host_edges.append(e)
                elif not other_edges:
                    other_edges.append(e)
                else:
                    shared_nodes = e.shared_endpoints(ej)
                    if len(shared_nodes) == 1:
                        host_edges.append(e)
                    else:
                        host_edges.append(e) # final fallback

        return host_edges, other_edges

    def get_edge_lower_upper_nodes(self, edge: Edge) -> tuple[int, int]:
        u_p = self.nodes[edge.u].p
        v_p = self.nodes[edge.v].p
        if u_p.y() > v_p.y(): # Screen Y: higher value is lower position
            return edge.u, edge.v
        else:
            return edge.v, edge.u

    def get_downward_vertical_run_from_node(self, current_node_id: int, visited_edge_ids=None) -> float:
        """
        Return the longest contiguous VERTICAL run below current_node by walking
        downward through the graph.
        """
        if visited_edge_ids is None:
            visited_edge_ids = set()

        best_run = 0.0

        incident = [
            e for e in self.edges.values()
            if (e.u == current_node_id or e.v == current_node_id)
            and e.id not in visited_edge_ids
        ]

        for e in incident:
            if self.get_edge_convection_mode(e) != "vertical":
                continue

            lower_node, upper_node = self.get_edge_lower_upper_nodes(e)

            if current_node_id != upper_node:
                continue

            visited_now = set(visited_edge_ids)
            visited_now.add(e.id)

            next_node = lower_node
            tail = self.get_downward_vertical_run_from_node(next_node, visited_now)
            run = float(e.length_m) + tail

            if run > best_run:
                best_run = run

        return float(best_run)


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



def _edge_direction_unit(nodes: Dict[int, Node], edge: Edge) -> tuple[float, float]:
    a = nodes[edge.u].p
    b = nodes[edge.v].p
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    L = math.hypot(dx, dy)
    if L <= 1e-12:
        return 0.0, 0.0
    return dx / L, dy / L


def _edges_are_collinear_at_node(
    nodes: Dict[int, Node],
    e1: Edge,
    e2: Edge,
    node_id: int,
    angle_tol_deg: float = 3.0,
) -> bool:
    if node_id not in (e1.u, e1.v) or node_id not in (e2.u, e2.v):
        return False

    p_node = nodes[node_id].p
    p1_other = nodes[e1.v if e1.u == node_id else e1.u].p
    p2_other = nodes[e2.v if e2.u == node_id else e2.u].p

    v1x = p1_other.x() - p_node.x()
    v1y = p1_other.y() - p_node.y()
    v2x = p2_other.x() - p_node.x()
    v2y = p2_other.y() - p_node.y()

    L1 = math.hypot(v1x, v1y)
    L2 = math.hypot(v2x, v2y)
    if L1 <= 1e-12 or L2 <= 1e-12:
        return False

    dot = (v1x * v2x + v1y * v2y) / (L1 * L2)
    dot = max(-1.0, min(1.0, dot))
    angle_deg = math.degrees(math.acos(dot))
    return abs(angle_deg - 180.0) <= angle_tol_deg


def _assign_physical_runs(nodes: Dict[int, Node], edges: Dict[int, Edge]) -> None:
    non_joint_edges = [e for e in edges.values() if not e.is_joint]
    if not non_joint_edges:
        return

    parent = {e.id: e.id for e in non_joint_edges}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    node_to_non_joint = defaultdict(list)
    for e in non_joint_edges:
        node_to_non_joint[e.u].append(e)
        node_to_non_joint[e.v].append(e)

    for node_id, incident in node_to_non_joint.items():
        if len(incident) != 2:
            continue
        e1, e2 = incident
        if e1.tier is not e2.tier:
            continue
        if e1.geom_tuple != e2.geom_tuple:
            continue
        if not _edges_are_collinear_at_node(nodes, e1, e2, node_id):
            continue
        union(e1.id, e2.id)

    groups = defaultdict(list)
    for e in non_joint_edges:
        groups[find(e.id)].append(e)

    next_run_id = 0
    for _, comp_edges in groups.items():
        comp_ids = {e.id for e in comp_edges}
        adjacency = defaultdict(list)
        for e in comp_edges:
            adjacency[e.u].append(e)
            adjacency[e.v].append(e)

        endpoints = [
            nid for nid, inc in adjacency.items()
            if len([e for e in inc if e.id in comp_ids]) == 1
        ]

        if len(comp_edges) == 1:
            e = comp_edges[0]
            e.physical_run_id = next_run_id
            e.physical_run_length_m = float(e.length_m)
            e.physical_run_s0_m = 0.0
            e.physical_run_s1_m = float(e.length_m)
            next_run_id += 1
            continue

        if len(endpoints) != 2:
            for e in comp_edges:
                e.physical_run_id = next_run_id
                e.physical_run_length_m = float(e.length_m)
                e.physical_run_s0_m = 0.0
                e.physical_run_s1_m = float(e.length_m)
                next_run_id += 1
            continue

        current_node = endpoints[0]
        prev_edge_id = None
        s_cursor = 0.0
        visited = set()

        while True:
            candidates = [e for e in adjacency[current_node] if e.id in comp_ids and e.id not in visited]
            if not candidates:
                break
            edge = candidates[0] if prev_edge_id is None else next((e for e in candidates if e.id != prev_edge_id), candidates[0])
            visited.add(edge.id)
            edge.physical_run_id = next_run_id
            edge.physical_run_length_m = None

            if edge.u == current_node:
                edge.physical_run_s0_m = float(s_cursor)
                edge.physical_run_s1_m = float(s_cursor + edge.length_m)
                current_node = edge.v
            else:
                edge.physical_run_s0_m = float(s_cursor + edge.length_m)
                edge.physical_run_s1_m = float(s_cursor)
                current_node = edge.u

            s_cursor += float(edge.length_m)
            prev_edge_id = edge.id

        total_len = float(s_cursor)
        for e in comp_edges:
            e.physical_run_length_m = total_len

        next_run_id += 1

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


def _owning_tier_from_item(item) -> TierItem | None:
    obj = item
    while obj is not None:
        if isinstance(obj, TierItem):
            return obj
        if hasattr(obj, "parentItem"):
            obj = obj.parentItem()
        else:
            break
    return None


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

    NODE_SNAP_TOL = 0.5  # pixels
    MAX_EDGE_LEN_M = 0.050  # thermal/UI discretisation target

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
    # Add nominal thermal discretisation along every bus
    # -------------------------------------------------
    discretised_segments = []

    for seg in segments:
        a = seg["a"]
        b = seg["b"]

        L_px = _dist(a, b)
        L_m = L_px * px_to_m

        if L_m <= MAX_EDGE_LEN_M + 1e-12:
            discretised_segments.append(seg)
            continue

        n_parts = max(1, int(math.ceil(L_m / MAX_EDGE_LEN_M)))
        chain = [
            QPointF(
                a.x() + (k / n_parts) * (b.x() - a.x()),
                a.y() + (k / n_parts) * (b.y() - a.y()),
            )
            for k in range(n_parts + 1)
        ]

        for p0, p1 in zip(chain[:-1], chain[1:]):
            if _dist(p0, p1) <= 1e-9:
                continue

            discretised_segments.append({
                "a": p0,
                "b": p1,
                "spec": seg["spec"],
                "tier": seg["tier"],
                "ui_item": seg["ui_item"],
            })

    segments = discretised_segments

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
            if hasattr(item, "set_node_id"):
                item.set_node_id(best.id)
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
            if hasattr(item, "set_node_id"):
                item.set_node_id(best.id)
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
            parent_bus = item.parentItem()
            candidate_edges = [
                e for e in edges.values()
                if isinstance(parent_bus, BusLineItem) and e.ui_item is parent_bus
            ]
            if not candidate_edges:
                candidate_edges = list(edges.values())

            for e in candidate_edges:
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
                owner_tier = (
                    _owning_tier_from_item(parent_bus)
                    if isinstance(parent_bus, BusLineItem)
                    else None
                )
                if owner_tier is None:
                    owner_tier = best_edge.tier

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

                # Persist host-edge metadata needed later for joint convection modelling.
                # x_m is measured along the ORIGINAL host edge, from edge.u toward edge.v.
                spec.host_edge_total_length_m = float(best_edge.length_m)
                spec.host_joint_center_x_m = float(x_m)

                # These are filled more precisely after the explicit joint interval [x0, x1]
                # is created during edge splitting.
                spec.joint_x0_m = None
                spec.joint_x1_m = None
                spec.host_vertical_run_below_m = None

                joins.append(
                    Join(
                        edge_id=best_edge.id,
                        x_m=x_m,
                        spec=spec,
                        ui_item=item,
                        owner_tier=owner_tier,
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

            host_is_vertical = abs(b_pt.y() - a_pt.y()) > abs(b_pt.x() - a_pt.x())

            j.spec.host_edge_total_length_m = float(e.length_m)
            j.spec.joint_x0_m = float(x0)
            j.spec.joint_x1_m = float(x1)

            if host_is_vertical:
                if a_pt.y() > b_pt.y():
                    run_below_m = x0
                else:
                    run_below_m = e.length_m - x1
                j.spec.host_vertical_run_below_m = float(max(run_below_m, 1e-12))
            else:
                j.spec.host_vertical_run_below_m = None

            # ---- pre-joint bus segment ----
            pre_len = x0 - prev_x
            if pre_len > 1e-9:
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
                    gap_to_wall_mm=e.gap_to_wall_mm,
                    orientation_to_wall=e.orientation_to_wall,
                    is_joint=False,
                    joint_spec=None,
                    ui_item=e.ui_item,
                    ui_join_item=None,
                )
                start_node = n1
                new_edge_id += 1

            # ---- explicit joint edge ----
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
                gap_to_wall_mm=e.gap_to_wall_mm,
                orientation_to_wall=e.orientation_to_wall,
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
                gap_to_wall_mm=e.gap_to_wall_mm,
                orientation_to_wall=e.orientation_to_wall,
                is_joint=False,
                joint_spec=None,
                ui_item=e.ui_item,
                ui_join_item=None,
            )
            new_edge_id += 1

    edges = new_edges

    # refine explicit joint geometry from connected bus edges
    _infer_joint_edge_geometry(edges, debug=False)

    # annotate contiguous physical copper runs for thermal characteristic length
    _assign_physical_runs(nodes, edges)

    return Graph(
        nodes=nodes,
        edges=edges,
        loads=loads,
        joins=joins,
        source_node=source_node,
        source_ui_item=source_ui_item,
    )
