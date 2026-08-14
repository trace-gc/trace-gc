# trace_gc/receipts.py
"""Receipt handling utilities.

Provides functions to collect compaction receipts and retrieve the full original
event payloads for pruned nodes.
"""

from __future__ import annotations

from typing import List, Dict

from .graph import StateGraph


def collect_receipts(graph: StateGraph) -> List[Dict]:
    """Return a list of all pruned receipt stubs from the graph, sorted by event timestamp."""
    receipts = list(graph.receipts.values())
    receipts.sort(key=lambda r: r.get("timestamp", 0))
    return receipts


def get_receipt(graph: StateGraph, node_id: str) -> dict:
    """Return the original event dictionary for a pruned node, with ``pruned=True`` added.

    Returns a *copy* of the original event dict — the copy has ``pruned=True``
    appended and any derived ``decision_status`` updated. The original dict in
    ``graph.nodes`` is never mutated.

    Raises ``KeyError`` if *node_id* is not in ``graph.nodes``.
    """
    result = graph.get_node_with_status(node_id)
    if node_id in graph.pruned:
        result["pruned"] = True
    return result
