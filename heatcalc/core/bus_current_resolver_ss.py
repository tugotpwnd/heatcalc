from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List
from collections import defaultdict, deque

from heatcalc.core.bus_graph_extractor_ss import GEdge, GLoad


@dataclass
class CurrentSolveResult:
    node_downstream_A: Dict[int, float]
    edge_I_A: Dict[int, float]
    topo_order: List[int]


def resolve_edge_currents(
    nodes: Dict[int, object],
    edges: Dict[int, GEdge],
    loads: List[GLoad],
    source_node: int,
) -> CurrentSolveResult:
    """
    Resolve currents in a radial bus network.

    Steps:
    1) Build undirected adjacency
    2) BFS from source to orient edges
    3) Detect loops
    4) Accumulate downstream load
    """

    # -------------------------
    # Build undirected adjacency
    # -------------------------

    adj = defaultdict(list)

    for eid, e in edges.items():
        adj[e.u].append((eid, e.v))
        adj[e.v].append((eid, e.u))

    # -------------------------
    # BFS orientation
    # -------------------------

    visited = set()
    parent = {}
    oriented_edges = {}

    q = deque([source_node])
    visited.add(source_node)

    while q:
        u = q.popleft()

        for eid, v in adj[u]:

            if v not in visited:
                visited.add(v)
                parent[v] = u
                oriented_edges[eid] = (u, v)
                q.append(v)

            else:
                # detect back edge (cycle)
                if parent.get(u) != v and parent.get(v) != u:
                    raise ValueError("Bus network contains a loop. Radial topology required.")

    if len(visited) != len(nodes):
        raise ValueError("Bus network is disconnected from source.")

    # -------------------------
    # Build directed adjacency
    # -------------------------

    out_edges = defaultdict(list)
    in_edges = defaultdict(list)

    for eid, (u, v) in oriented_edges.items():
        out_edges[u].append(eid)
        in_edges[v].append(eid)

    # -------------------------
    # Topological order (BFS order works)
    # -------------------------

    topo = []
    q = deque([source_node])

    seen = set([source_node])

    while q:
        n = q.popleft()
        topo.append(n)

        for eid in out_edges.get(n, []):
            v = oriented_edges[eid][1]

            if v not in seen:
                seen.add(v)
                q.append(v)

    # -------------------------
    # Node load demand
    # -------------------------

    demand = {nid: 0.0 for nid in nodes.keys()}

    for ld in loads:
        demand[ld.node] += float(ld.I_A)

    # -------------------------
    # Downstream accumulation
    # -------------------------

    downstream = dict(demand)

    for n in reversed(topo):

        for eid in in_edges.get(n, []):
            u, v = oriented_edges[eid]
            downstream[u] += downstream[v]

    # -------------------------
    # Edge currents
    # -------------------------

    edge_I = {}

    for eid, (u, v) in oriented_edges.items():
        edge_I[eid] = downstream[v]

    return CurrentSolveResult(
        node_downstream_A=downstream,
        edge_I_A=edge_I,
        topo_order=topo,
    )