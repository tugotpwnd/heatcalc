import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

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

    # --------------------------------
    # map node id → matrix index
    # --------------------------------

    node_ids = list(graph.nodes.keys())
    node_index = {nid: i for i, nid in enumerate(node_ids)}

    n = len(node_ids)

    if n == 0:
        raise RuntimeError("Graph has no nodes")

    if graph.source_node is None:
        raise RuntimeError("Network has no source")

    A = lil_matrix((n, n))
    b = np.zeros(n)

    # --------------------------------
    # build KCL matrix
    # --------------------------------

    for edge in graph.edges.values():

        u = node_index[edge.u]
        v = node_index[edge.v]

        #todo resolve laplacian for real model?
        A[u, u] += 1
        A[v, v] += 1
        A[u, v] -= 1
        A[v, u] -= 1

    # --------------------------------
    # loads
    # --------------------------------

    for load in graph.loads:

        if load.node not in node_index:
            continue

        i = node_index[load.node]
        b[i] -= load.I_A

    # --------------------------------
    # reference node
    # --------------------------------

    src = node_index[graph.source_node]

    A.rows[src] = []
    A.data[src] = []

    A[src, src] = 1
    b[src] = 0

    # --------------------------------
    # solve
    # --------------------------------

    V = spsolve(A.tocsr(), b)

    if not np.all(np.isfinite(V)):
        raise RuntimeError("Current solver produced NaN (network likely disconnected)")

    # --------------------------------
    # compute edge currents
    # --------------------------------

    for edge in graph.edges.values():

        u = node_index[edge.u]
        v = node_index[edge.v]

        edge.I_A = abs(V[u] - V[v])