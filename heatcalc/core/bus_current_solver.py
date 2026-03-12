import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

def get_disconnected_items(graph):
    """
    Returns a set of UI items that are NOT connected to the source node.
    """
    if graph.source_node is None:
        # If there's no source, everything is "disconnected" in a sense,
        # but we handle this specifically in the UI.
        return set()

    visited = set()
    stack = [graph.source_node]

    adjacency = {}
    for e in graph.edges.values():
        adjacency.setdefault(e.u, []).append(e.v)
        adjacency.setdefault(e.v, []).append(e.u)

    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)

        for nxt in adjacency.get(n, []):
            stack.append(nxt)

    disconnected_ui_items = set()

    # Check nodes
    for nid, node in graph.nodes.items():
        if nid not in visited:
            for item in node.ui_items:
                disconnected_ui_items.add(item)

    # Check edges (BusLineItems)
    for e in graph.edges.values():
        if e.u not in visited or e.v not in visited:
            if e.ui_item:
                disconnected_ui_items.add(e.ui_item)
            if e.ui_join_item:
                disconnected_ui_items.add(e.ui_join_item)

    # Check loads
    for load in graph.loads:
        if load.node not in visited:
            if load.ui_item:
                disconnected_ui_items.add(load.ui_item)

    # Note: Joins are already mostly covered by edges (ui_join_item) or explicit ui_item in Join dataclass
    for j in graph.joins:
        # Check if the edge it belongs to is connected
        if j.edge_id not in graph.edges or graph.edges[j.edge_id].u not in visited:
             if j.ui_item:
                 disconnected_ui_items.add(j.ui_item)

    return disconnected_ui_items

def filter_graph_to_source_component(graph):

    if graph.source_node is None:
        raise RuntimeError("No source node defined")

    visited = set()
    stack = [graph.source_node]

    adjacency = {}

    for e in graph.edges.values():
        adjacency.setdefault(e.u, []).append(e.v)
        adjacency.setdefault(e.v, []).append(e.u)

    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)

        for nxt in adjacency.get(n, []):
            stack.append(nxt)

    graph.nodes = {k: v for k, v in graph.nodes.items() if k in visited}

    graph.edges = {
        k: e for k, e in graph.edges.items()
        if e.u in visited and e.v in visited
    }

    graph.loads = [l for l in graph.loads if l.node in visited]

    valid_edges = set(graph.edges.keys())

    graph.joins = [j for j in graph.joins if j.edge_id in valid_edges]

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve


def solve_currents(graph):

    if graph.source_node is None:
        raise RuntimeError("Network has no source")

    # ------------------------------
    # Build adjacency
    # ------------------------------

    adjacency = {}

    for e in graph.edges.values():
        adjacency.setdefault(e.u, []).append((e.v, e))
        adjacency.setdefault(e.v, []).append((e.u, e))

    # ------------------------------
    # Build spanning tree from source
    # ------------------------------

    parent = {}
    parent_edge = {}
    children = {nid: [] for nid in graph.nodes}

    stack = [graph.source_node]
    visited = set()

    while stack:

        n = stack.pop()

        if n in visited:
            continue

        visited.add(n)

        for nbr, edge in adjacency.get(n, []):

            if nbr in visited:
                continue

            parent[nbr] = n
            parent_edge[nbr] = edge

            children[n].append(nbr)

            stack.append(nbr)

    # ------------------------------
    # Node current demand
    # ------------------------------

    node_current = {nid: 0.0 for nid in graph.nodes}

    for load in graph.loads:
        if load.node in node_current:
            node_current[load.node] += load.I_A

    # ------------------------------
    # Post-order traversal
    # ------------------------------

    def accumulate(node):

        total = node_current[node]

        for child in children[node]:

            child_current = accumulate(child)

            edge = parent_edge[child]

            edge.I_A = abs(child_current)

            total += child_current

        return total

    accumulate(graph.source_node)