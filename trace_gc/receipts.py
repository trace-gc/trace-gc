# trace_gc/receipts.py
"""Receipt handling utilities.

Provides functions to collect compaction receipts and retrieve the full original 
event payloads for pruned nodes.
"""

from __future__ import annotations

from typing import List, Dict

from .graph import StateGraph


def collect_receipts(graph: StateGraph) -> List[Dict]:
    """Return a list of all pruned receipts from the graph, sorted by timestamp."""
    receipts = list(graph.receipts.values())
    receipts.sort(key=lambda r: r.get("timestamp", 0))
    return receipts


def get_receipt(graph: StateGraph, node_id: str) -> dict:
    """Return the original event dictionary for a pruned node ID. Raises KeyError if node_id is not in graph.nodes."""
    if node_id not in graph.nodes:
        raise KeyError(f"Unknown node id: {node_id}")
    return graph.nodes[node_id]
