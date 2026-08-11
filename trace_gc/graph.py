# trace_gc/graph.py
"""StateGraph implementation – nodes, edges, and utility helpers.

The graph stores events as nodes and directed edges with an explicit ``edge_type``.  The
library distinguishes three edge types:

- ``sequence`` – the natural chronological ordering (parent/child relationship).
- ``supersedes`` – added by the ``override_engine`` to link a newer ``set_var`` to the
  older ones it overwrites.
- ``abandons`` – added by the ``dead_branch_sweeper`` to denote that a node was pruned
  because an ``abandon`` event references it.

All traversal helpers accept an optional ``edge_types`` filter so callers can restrict
behaviour to only ``sequence`` edges when required (e.g. dead‑branch pruning).
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, List, Tuple, Iterable, Set


class StateGraph:
    """A mutable directed multigraph for LLM event traces.

    Nodes are stored as ``id -> event`` mappings.  Edges are stored as a list of
    tuples ``(src, dst, edge_type)`` and a reverse‑lookup dictionary for fast
    ancestor queries.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, dict] = {}
        self.edges: List[Tuple[str, str, str]] = []  # (src, dst, type)
        self._rev: Dict[str, List[Tuple[str, str]]] = defaultdict(list)  # dst -> [(src, type)]
        self._forward: Dict[str, List[Tuple[str, str]]] = defaultdict(list)  # src -> [(dst, type)]
        self.pruned: Set[str] = set()
        self.protected: Set[str] = set()
        self.prune_reasons: Dict[str, str] = {}
        self.protected_reasons: Dict[str, str] = {}
        self.receipts: Dict[str, dict] = {}

    # ---------------------------------------------------------------------
    # Node / edge mutation helpers
    # ---------------------------------------------------------------------
    def add_node(self, event: dict) -> None:
        node_id = event["id"]
        self.nodes[node_id] = event

    def add_edge(self, src: str, dst: str, edge_type: str) -> None:
        self.edges.append((src, dst, edge_type))
        self._rev[dst].append((src, edge_type))
        self._forward[src].append((dst, edge_type))

    # ---------------------------------------------------------------------
    # Traversal utilities – optional ``edge_types`` filter parameter
    # ---------------------------------------------------------------------
    def get_children(self, node_id: str, edge_types: Iterable[str] | None = None) -> List[str]:
        if edge_types is None:
            edge_types = {"sequence", "supersedes", "abandons"}
        else:
            edge_types = set(edge_types)
        return [dst for dst, typ in self._forward.get(node_id, []) if typ in edge_types]

    def get_ancestors(self, node_id: str, edge_types: Iterable[str] | None = None) -> Set[str]:
        if edge_types is None:
            edge_types = {"sequence", "supersedes", "abandons"}
        else:
            edge_types = set(edge_types)
        visited: Set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for src, typ in self._rev.get(cur, []):
                if typ in edge_types and src not in visited:
                    visited.add(src)
                    stack.append(src)
        return visited

    # ---------------------------------------------------------------------
    # Indegree map and topological ordering (sequence edges only)
    # ---------------------------------------------------------------------
    def indegree_map(self, edge_type: str = "sequence") -> Dict[str, int]:
        indeg: Dict[str, int] = {node_id: 0 for node_id in self.nodes}
        for src, dst, typ in self.edges:
            if typ == edge_type:
                indeg[dst] = indeg.get(dst, 0) + 1
        return indeg

    def topological_sort(self, edge_type: str = "sequence") -> List[str]:
        indeg = self.indegree_map(edge_type)
        import heapq
        pq = []
        for n, d in indeg.items():
            if d == 0:
                ts = self.nodes[n].get("timestamp", 0)
                heapq.heappush(pq, (ts, n))
        order: List[str] = []
        while pq:
            _, node = heapq.heappop(pq)
            order.append(node)
            for child in self.get_children(node, edge_types=[edge_type]):
                indeg[child] -= 1
                if indeg[child] == 0:
                    ts = self.nodes[child].get("timestamp", 0)
                    heapq.heappush(pq, (ts, child))
        return order

    # ---------------------------------------------------------------------
    # Pruning helper – creates a receipt stub and marks the node as pruned.
    # ---------------------------------------------------------------------
    def mark_pruned(self, node_id: str) -> None:
        """Mark *node_id* as pruned and generate a minimal receipt stub.

        The receipt stub is stored in ``self.receipts`` for later consumption by the
        ``receipts`` module.
        """
        self.pruned.add(node_id)
        event = self.nodes.get(node_id)
        if event is not None:
            event["pruned"] = True
        # Create deterministic receipt stub with explicit id field.
        receipt = {"id": node_id, "type": "receipt", "target_id": node_id, "status": "pruned"}
        self.receipts[node_id] = receipt

    def __repr__(self) -> str:
        return f"StateGraph(nodes={len(self.nodes)}, edges={len(self.edges)})"
