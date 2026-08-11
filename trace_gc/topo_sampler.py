# trace_gc/topo_sampler.py
"""Topological sampler – collapses strongly connected components (cycles).

Identifies cycles (strongly connected components) via Tarjan's algorithm and 
collapses them into a single deterministic cluster receipt node.
"""

from __future__ import annotations

import hashlib
from typing import List, Dict, Set

from .graph import StateGraph


def _deterministic_cluster_id(member_ids: List[str]) -> str:
    """Return a deterministic cluster ID from sorted member IDs using SHA-256."""
    sorted_ids = sorted(member_ids)
    joined = ",".join(sorted_ids).encode()
    digest = hashlib.sha256(joined).hexdigest()
    return f"cluster_{digest[:12]}"


def collapse_cycles(graph: StateGraph) -> List[str]:
    """Detect cycles, collapse each component into a cluster receipt, and mark members as pruned."""
    import sys
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, len(graph.nodes) + 100))
    try:
        index = 0
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        stack: List[str] = []
        on_stack: Set[str] = set()
        sccs: List[Set[str]] = []

        def strongconnect(v: str) -> None:
            nonlocal index
            indices[v] = index
            lowlink[v] = index
            index += 1
            stack.append(v)
            on_stack.add(v)
            for w in graph.get_children(v, edge_types=["sequence"]):
                if w not in indices:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])
            # If v is a root node, pop the stack and generate an SCC
            if lowlink[v] == indices[v]:
                component: Set[str] = set()
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    component.add(w)
                    if w == v:
                        break
                if len(component) > 1:
                    sccs.append(component)

        for node_id in list(graph.nodes):
            if node_id not in indices:
                strongconnect(node_id)
    finally:
        sys.setrecursionlimit(old_limit)

    receipt_ids: List[str] = []
    for component in sccs:
        member_ids = sorted(component)
        receipt_id = _deterministic_cluster_id(member_ids)
        receipt_node = {
            "id": receipt_id,
            "type": "receipt",
            "timestamp": min(graph.nodes[mid]["timestamp"] for mid in member_ids),
            "cluster_members": member_ids,
        }
        graph.add_node(receipt_node)
        receipt_ids.append(receipt_id)
        # Connect receipt to the earliest member (by timestamp) to preserve order
        earliest = min(member_ids, key=lambda mid: graph.nodes[mid]["timestamp"])
        graph.add_edge(receipt_id, earliest, "sequence")
        # Mark all original members as pruned and clean reverse lookup cache
        for nid in component:
            graph.prune_reasons[nid] = "collapsed into cycle cluster"
            graph.mark_pruned(nid)
            if nid in graph._rev:
                del graph._rev[nid]
        # Remove edges where both src and dst are inside the component once
        graph.edges = [
            (src, dst, typ)
            for (src, dst, typ) in graph.edges
            if not (src in component and dst in component)
        ]
        # After removal, rebuild reverse and forward lookup caches for remaining edges
        graph._rev.clear()
        graph._forward.clear()
        for src, dst, typ in graph.edges:
            graph._rev[dst].append((src, typ))
            graph._forward[src].append((dst, typ))
    return receipt_ids
