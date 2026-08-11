# trace_gc/api.py
"""Incremental-friendly API client for TraceGC.

Provides the class-based interface to manage and compact event traces incrementally.
"""

from __future__ import annotations

from typing import Dict, List, Any
from .events import validate_event
from .graph import StateGraph
from .compactor import compact_events
from .receipts import get_receipt as _get_receipt


class TraceGC:
    """Incremental API client for TraceGC.

    Manages a running history of events for an LLM agent, allowing events to be
    added one by one and compacted on demand.
    """

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.graph: StateGraph = StateGraph()

    def add_event(self, event: Dict[str, Any]) -> None:
        """Validate and append a single event to the history.

        Validates the event against the schema and updates the internal graph representation.
        """
        validated = validate_event(event)
        self.events.append(validated)
        self.graph.add_node(validated)
        
        parent = validated.get("parent_id")
        if parent:
            if parent not in self.graph.nodes:
                raise ValueError(
                    f"parent_id '{parent}' not found in graph — events must be added in dependency order"
                )
            self.graph.add_edge(parent, validated["id"], "sequence")

    def compact(self) -> Dict[str, Any]:
        """Runs the full compaction pipeline against all events added so far.

        Note: re-runs from scratch each call, not incremental — see README for details.
        """
        result = compact_events(self.events)
        self.graph = result["graph"]
        return result

    def get_receipt(self, node_id: str) -> Dict[str, Any]:
        """Retrieve the original event dict/receipt for a pruned node ID."""
        return _get_receipt(self.graph, node_id)
