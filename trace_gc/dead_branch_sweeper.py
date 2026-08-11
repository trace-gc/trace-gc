# trace_gc/dead_branch_sweeper.py
"""Dead‑branch sweeper – prunes abandoned execution paths.

Walks sequence edges starting from abandon event targets to prune entire abandoned sub-branches.
"""

from __future__ import annotations

from typing import List, Set

from .graph import StateGraph
from .retention_policy import is_protected


def sweep_dead_branches(graph: StateGraph) -> List[str]:
    """Prune nodes and their descendants targeted by abandon events, returning pruned IDs."""
    to_prune: Set[str] = set()
    
    # Identify all abandon events and map target IDs to triggering abandon IDs.
    abandon_reasons: dict[str, str] = {}
    for ev in graph.nodes.values():
        if ev.get("type") == "abandon":
            for tgt in ev.get("ref_to", []):
                abandon_reasons[tgt] = ev["id"]

    # Depth‑first walk following *sequence* edges only.
    descendant_reasons: dict[str, str] = {}
    def dfs(start_id: str, trigger_id: str) -> None:
        stack = [start_id]
        while stack:
            cur = stack.pop()
            if cur in to_prune:
                continue
            to_prune.add(cur)
            descendant_reasons[cur] = trigger_id
            for child in graph.get_children(cur, edge_types=["sequence"]):
                stack.append(child)

    for tgt, trigger_id in abandon_reasons.items():
        if tgt in graph.nodes:
            dfs(tgt, trigger_id)

    pruned_ids: List[str] = []
    for node_id in to_prune:
        trigger_id = descendant_reasons[node_id]
        event = graph.nodes.get(node_id)
        
        if event and event.get("type") == "abandon":
            reason = "abandon event pruned alongside its own target branch"
        else:
            reason = f"abandoned by {trigger_id}"
            
        graph.prune_reasons[node_id] = reason

        if event and is_protected(event):
            graph.protected.add(node_id)
            if event.get("importance") == "critical":
                graph.protected_reasons[node_id] = "importance=critical"
            elif event.get("retain_until") in {"task_end", "session_end"}:
                graph.protected_reasons[node_id] = f"retain_until={event['retain_until']}"
        else:
            graph.mark_pruned(node_id)
            pruned_ids.append(node_id)
            
    return pruned_ids
