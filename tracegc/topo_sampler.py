# tracegc/topo_sampler.py
"""Topological sampler – collapses strongly connected components (cycles).

Identifies cycles (strongly connected components) via an iterative Tarjan's algorithm and
collects them into a single deterministic cluster receipt node. The iterative implementation
avoids Python recursion limits on large graphs.
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
    """Detect cycles via iterative Tarjan's SCC, collapse each into a cluster receipt, and mark members pruned.

    Uses an explicit DFS stack instead of Python recursion so arbitrarily large graphs
    (including linear chains of 100K+ nodes) are handled without stack overflow.
    Output is identical to the former recursive implementation: same SCC groupings,
    same cluster ID derivation via SHA-256 of sorted member IDs.
    """
    idx: int = 0
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    tarjan_stack: List[str] = []   # Tarjan's path stack
    on_stack: Set[str] = set()
    sccs: List[Set[str]] = []

    for start in list(graph.nodes):
        if start in indices:
            continue

        # Initialise the start node before entering the DFS loop
        indices[start] = idx
        lowlink[start] = idx
        idx += 1
        tarjan_stack.append(start)
        on_stack.add(start)

        # Each DFS frame: (node_id, children_iterator)
        # Using iter() + next() preserves iterator state across loop iterations.
        dfs_stack: List[tuple] = [
            (start, iter(graph.get_children(start, edge_types=["sequence"])))
        ]

        while dfs_stack:
            v, children = dfs_stack[-1]
            try:
                w = next(children)
                if w not in indices:
                    # Tree edge — initialise w and push a new frame
                    indices[w] = idx
                    lowlink[w] = idx
                    idx += 1
                    tarjan_stack.append(w)
                    on_stack.add(w)
                    dfs_stack.append(
                        (w, iter(graph.get_children(w, edge_types=["sequence"])))
                    )
                elif w in on_stack:
                    # Back edge — update lowlink of current node
                    lowlink[v] = min(lowlink[v], indices[w])
            except StopIteration:
                # All neighbours of v processed — pop this frame
                dfs_stack.pop()
                # Propagate lowlink to the parent frame
                if dfs_stack:
                    parent = dfs_stack[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                # Check whether v is the root of a completed SCC
                if lowlink[v] == indices[v]:
                    component: Set[str] = set()
                    while True:
                        w = tarjan_stack.pop()
                        on_stack.remove(w)
                        component.add(w)
                        if w == v:
                            break
                    if len(component) > 1:
                        sccs.append(component)

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
