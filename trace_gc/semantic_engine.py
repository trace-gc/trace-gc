# trace_gc/semantic_engine.py
"""Semantic Engine – decision lifecycle state machine (PROPOSED -> ACTIVE -> CONFIRMED -> SUPERSEDED).

Tracks decision lifecycle status transitions on graph nodes for configured decision keys.
"""

from __future__ import annotations

from typing import Dict, List, Set, Optional
from .graph import StateGraph


def update_decision_lifecycle_status(
    graph: StateGraph,
    tracked_decision_keys: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """Process decision lifecycle status transitions (PROPOSED -> ACTIVE -> CONFIRMED -> SUPERSEDED).

    Parameters
    ----------
    graph:
        The ``StateGraph`` containing trace nodes to evaluate.
    tracked_decision_keys:
        Set of variable keys to receive decision-lifecycle status treatment.
        Defaults to ``{"database"}`` if ``None``.

    Returns
    -------
    Dict[str, str]
        Mapping of node IDs to their resolved status string.
    """
    tracked_keys = (
        tracked_decision_keys
        if tracked_decision_keys is not None
        else {"database"}
    )

    statuses: Dict[str, str] = {}
    key_to_set_vars: Dict[str, List[dict]] = {}

    # Gather all set_var nodes for tracked keys
    for node_id, event in graph.nodes.items():
        if event.get("type") == "set_var":
            key = event.get("key")
            if key in tracked_keys:
                key_to_set_vars.setdefault(key, []).append(event)
                # Rule 1: Default status is PROPOSED
                statuses[node_id] = "PROPOSED"

    # Process status transitions per tracked key
    for key, events in key_to_set_vars.items():
        # Sort by timestamp ascending; newest is last.
        events.sort(key=lambda e: e["timestamp"])
        newest = events[-1]
        newest_id = newest["id"]

        # Rule 3: Older set_var events for this tracked key become SUPERSEDED
        for older in events[:-1]:
            older_id = older["id"]
            statuses[older_id] = "SUPERSEDED"
            older["status"] = "SUPERSEDED"

        # Check for ACTIVE / CONFIRMED confirmation by downstream tool_call or tool_result
        for node_id, event in graph.nodes.items():
            if event.get("type") in ("tool_call", "tool_result", "decision"):
                # Rule 2: References newest set_var key/value -> ACTIVE or CONFIRMED
                args = event.get("arguments", {})
                res = event.get("result")
                content = event.get("content", "")

                newest_val = newest.get("value")
                is_referenced = (
                    (isinstance(args, dict) and args.get(key) == newest_val) or
                    (isinstance(res, str) and str(newest_val) in res) or
                    (isinstance(content, str) and str(newest_val) in content)
                )

                if is_referenced:
                    if event.get("type") == "tool_result":
                        statuses[newest_id] = "CONFIRMED"
                        newest["status"] = "CONFIRMED"
                    elif statuses.get(newest_id) != "CONFIRMED":
                        statuses[newest_id] = "ACTIVE"
                        newest["status"] = "ACTIVE"

        if newest_id not in statuses or statuses[newest_id] == "PROPOSED":
            statuses[newest_id] = "PROPOSED"
            newest["status"] = "PROPOSED"

    return statuses
