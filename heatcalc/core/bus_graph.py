from dataclasses import dataclass
from typing import Dict, List, Optional
from PyQt5.QtCore import QPointF
import math

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


@dataclass
class Load:
    node: int
    I_A: float


@dataclass
class Join:
    node: int
    R_contact_20_uohm: float


@dataclass
class Graph:
    nodes: Dict[int, Node]
    edges: Dict[int, Edge]
    loads: List[Load]
    joins: List[Join]
    source_node: Optional[int]


def _dist(a: QPointF, b: QPointF):
    return math.hypot(a.x() - b.x(), a.y() - b.y())


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

        key = (round(p.x(), 3), round(p.y(), 3))

        if key not in node_lookup:
            nonlocal node_id
            node_lookup[key] = node_id
            nodes[node_id] = Node(node_id, p)
            node_id += 1

        return node_lookup[key]

    for bl in bus_lines:

        line = bl.line()

        a = bl.mapToScene(line.p1())
        b = bl.mapToScene(line.p2())

        na = get_node(a)
        nb = get_node(b)

        L = _dist(a, b)

        edges[edge_id] = Edge(
            id=edge_id,
            u=na,
            v=nb,
            length_m=L * px_to_m,
            spec=bl.spec,
            tier=bl.parentItem(),
        )

        edge_id += 1

    source_node = None

    for item in scene.items():

        if item.type() == BUS_SOURCE_TYPE:

            p = item.center()

            best = min(nodes.values(), key=lambda n: _dist(p, n.p))

            source_node = best.id

    for item in scene.items():

        if isinstance(item, BusLoadItem):

            p = item.center()

            best = min(nodes.values(), key=lambda n: _dist(p, n.p))

            loads.append(Load(best.id, item.I_load_A))

    for item in scene.items():

        if isinstance(item, BusJoinItem):

            p = item.center()

            best = min(nodes.values(), key=lambda n: _dist(p, n.p))

            joins.append(Join(best.id, item.R_contact_20_uohm))

    return Graph(nodes, edges, loads, joins, source_node)