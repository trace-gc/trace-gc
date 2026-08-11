# trace_gc/override_engine.py
"""Override engine – identifies and prunes superseded ``set_var`` updates.

Retains only the latest set_var event for each key (by timestamp) among surviving 
nodes and marks all preceding writes as pruned.
"""

from __future__ import annotations

from collections import defaultdict
from typing import List

from .graph import StateGraph
from .retention_policy import is_protected


def apply_overrides(graph: StateGraph) -> List[str]:
    """Detect and prune superseded ``set_var`` events, returning the pruned IDs."""
    # Group ``set_var`` events by their ``key``
    key_to_events: dict[str, List[dict]] = defaultdict(list)
    for node_id, event in graph.nodes.items():
        if node_id in graph.pruned:
            continue
        if event.get("type") == "set_var" and event.get("key") is not None:
            key_to_events[event["key"]].append(event)

    pruned_ids: List[str] = []
    for key, events in key_to_events.items():
        # Sort by timestamp ascending; newest is last
        events.sort(key=lambda e: e["timestamp"])
        newest = events[-1]
        newest_id = newest["id"]
        # Older events are superseded
        for older in events[:-1]:
            older_id = older["id"]
            # Add supersedes edge from newest -> older
            graph.add_edge(newest_id, older_id, "supersedes")
            
            # Store what would have pruned it
            reason = f"superseded by {newest_id}"
            graph.prune_reasons[older_id] = reason

            if is_protected(older):
                graph.protected.add(older_id)
                if older.get("importance") == "critical":
                    graph.protected_reasons[older_id] = "importance=critical"
                elif older.get("retain_until") in {"task_end", "session_end"}:
                    graph.protected_reasons[older_id] = f"retain_until={older['retain_until']}"
            else:
                # Mark older as pruned
                graph.mark_pruned(older_id)
                pruned_ids.append(older_id)
    return pruned_ids
