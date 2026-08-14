# trace_gc/analysis.py
"""Analysis module for querying decision-lifecycle states across trace nodes."""

from __future__ import annotations

from typing import Dict, List, Set, Optional
from .graph import StateGraph
from .semantic_engine import update_decision_lifecycle_status


def get_active_decisions(
    graph: StateGraph,
    tracked_decision_keys: Optional[Set[str]] = None,
) -> Dict[str, dict]:
    """Retrieve active or confirmed decision nodes from the graph.

    Parameters
    ----------
    graph:
        The ``StateGraph`` to analyze.
    tracked_decision_keys:
        Optional set of decision keys to track.

    Returns
    -------
    Dict[str, dict]
        Mapping of node_id -> event dict for nodes with status in {"ACTIVE", "CONFIRMED"}.
    """
    statuses = update_decision_lifecycle_status(graph, tracked_decision_keys=tracked_decision_keys)
    active_nodes: Dict[str, dict] = {}
    for node_id, status in statuses.items():
        if status in {"ACTIVE", "CONFIRMED"}:
            active_nodes[node_id] = graph.nodes[node_id]
    return active_nodes
