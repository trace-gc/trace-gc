# trace_gc/analysis.py
"""Analysis module for querying decision-lifecycle states and information value across trace nodes."""

from __future__ import annotations

from typing import Dict, List, Set, Optional, Any
from .graph import StateGraph
from .retention_policy import is_protected
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


def analyze_retained_events(
    graph: StateGraph,
    tracked_decision_keys: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Perform an information value analysis on the trace compaction graph.

    Reports the semantic meaning, dependency state, and retention rationale
    for each event in the graph.
    """
    tracked_keys = (
        tracked_decision_keys
        if tracked_decision_keys is not None
        else {"database"}
    )

    # 1. Pre-calculate active reference targets
    active_referenced_ids = set()
    for nid, node in graph.nodes.items():
        if nid in graph.pruned:
            continue
        for ref in node.get("ref_to", []):
            active_referenced_ids.add(ref)

    analysis = []

    for node_id, event in graph.nodes.items():
        ev_type = event.get("type", "unknown")

        # Derive semantic meaning
        if ev_type == "set_var":
            meaning = f"variable assignment: {event.get('key')} = {event.get('value')}"
        elif ev_type == "tool_call":
            meaning = f"tool call: {event.get('tool_name')}({event.get('arguments')})"
        elif ev_type == "tool_result":
            meaning = f"tool result for call {event.get('call_id')}"
        elif ev_type == "command_run":
            meaning = f"shell command run: {event.get('command')}"
        elif ev_type == "test_run":
            meaning = f"test suite execution: {event.get('test_names')}"
        elif ev_type == "decision":
            meaning = f"agent decision: {event.get('content')}"
        elif ev_type == "verification":
            meaning = f"verification assertion: {event.get('content')}"
        else:
            meaning = f"{ev_type}: {event.get('content', event.get('message', ''))}"

        retained = node_id not in graph.pruned

        # Calculate dependencies (what active nodes reference this event)
        dependents = []
        for nid, node in graph.nodes.items():
            if nid in graph.pruned:
                continue
            if node_id in node.get("ref_to", []):
                dependents.append(nid)

        # Reason retained or pruned
        if retained:
            if is_protected(event):
                reason_retained = "protected by critical retention policy"
            elif dependents:
                reason_retained = f"referenced by active dependency nodes: {dependents}"
            elif ev_type == "set_var" and event.get("key") in tracked_keys and event.get("status") in {"ACTIVE", "CONFIRMED"}:
                reason_retained = f"current active {event.get('key')} state decision"
            elif ev_type in {"command_run", "test_run"} and event.get("exit_code") == 0:
                reason_retained = "successful execution evidence of current state"
            elif ev_type == "verification" and event.get("passed") is True:
                reason_retained = "passed verification evidence of current state"
            else:
                reason_retained = "neutral active narrative/event context"
        else:
            reason_retained = graph.prune_reasons.get(node_id, "pruned as obsolete override or dead branch")

        # Potentially removable checks
        potentially_removable = False
        reason_not_removed = "n/a (already pruned)"

        if retained:
            if ev_type in {"tool_result", "command_run"} and event.get("exit_code") == 0:
                # Successful execution evidence is potentially removable if we only care about state
                potentially_removable = True
                reason_not_removed = "retained as successful execution evidence justifying current state"
            elif ev_type == "tool_call" and event.get("tool_name") in {"view_file", "read_file"}:
                potentially_removable = True
                reason_not_removed = "retained file read (evidence); can be removed if subsequent edits prove it obsolete"
            elif ev_type == "verification" and event.get("passed") is True:
                potentially_removable = True
                reason_not_removed = "retained passed verification result (evidence)"
            elif ev_type == "decision":
                # Intermediate reasoning is potentially removable if fully superseded
                potentially_removable = True
                reason_not_removed = "retained intermediate agent reasoning/conversation"
            else:
                reason_not_removed = "required to define active state configuration/dependencies"

        analysis.append({
            "event_id": node_id,
            "semantic_meaning": meaning,
            "current_state": event.get("status", "ACTIVE" if retained else "PRUNED"),
            "dependencies": dependents,
            "retained": retained,
            "reason_retained": reason_retained,
            "potentially_removable": potentially_removable,
            "reason_not_removed": reason_not_removed
        })

    return analysis
