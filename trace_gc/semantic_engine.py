# trace_gc/semantic_engine.py
"""Semantic compaction engine for TraceGC.

Implements Stage 5: Semantic Pruning Engine. Processes semantic duplicates,
superseded decisions, and resolved errors while preserving provenance.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import List, Set, Dict, Any, Optional

from .graph import StateGraph
from .retention_policy import is_protected
from .override_engine import _is_value_referenced_by_active_tool_call


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
                graph.decision_status[node_id] = "PROPOSED"

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
            graph.decision_status[older_id] = "SUPERSEDED"

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
                        graph.decision_status[newest_id] = "CONFIRMED"
                    elif statuses.get(newest_id) != "CONFIRMED":
                        statuses[newest_id] = "ACTIVE"
                        graph.decision_status[newest_id] = "ACTIVE"

        if newest_id not in statuses or statuses[newest_id] == "PROPOSED":
            statuses[newest_id] = "PROPOSED"
            graph.decision_status[newest_id] = "PROPOSED"

    return statuses


def is_referenced_by_active_node(graph: StateGraph, node_id: str) -> bool:
    """Return True if node_id is referenced in the ref_to list of any active node."""
    for nid, node in graph.nodes.items():
        if nid in graph.pruned:
            continue
        if node_id in node.get("ref_to", []):
            return True
    return False


def _get_filepath_from_event(event: dict) -> Optional[str]:
    """Helper to extract file path/name from file_read/file_edit or tool_call events."""
    if event.get("type") in {"file_read", "file_edit"}:
        return event.get("path")
    if event.get("type") == "tool_call":
        tool_name = event.get("tool_name", "")
        if tool_name in {"view_file", "read_file", "write_file", "edit_file", "patch_file"}:
            args = event.get("arguments", {})
            if isinstance(args, dict):
                for k in ["path", "filename", "filepath", "file"]:
                    if k in args:
                        return args[k]
    return None


def apply_semantic_pruning(
    graph: StateGraph,
    prune_referenced_values: bool = True,
    prune_duplicates: bool = True,
    prune_superseded: bool = True,
    prune_errors: bool = True,
    prune_obsolete_reads: bool = True,
    prune_redundant_verifications: bool = True,
    tracked_decision_keys: Optional[Set[str]] = None,
) -> List[str]:
    """Identify and prune semantically obsolete nodes, returning their IDs."""
    tracked_keys = (
        tracked_decision_keys
        if tracked_decision_keys is not None
        else {"database"}
    )
    pruned_ids: List[str] = []

    # O(N) pre-calculation of all active reference targets in the graph
    active_referenced_ids: Set[str] = set()
    for nid, node in graph.nodes.items():
        if nid in graph.pruned:
            continue
        for ref in node.get("ref_to", []):
            active_referenced_ids.add(ref)

    # -------------------------------------------------------------------------
    # Rule 1: Confirmation Policy (Verify ACTIVE status has execution evidence)
    # -------------------------------------------------------------------------
    active_tech_choice_ids = []
    has_execution_evidence = False

    for node_id, event in graph.nodes.items():
        if node_id in graph.pruned:
            continue
        status = graph.decision_status.get(node_id, event.get("status"))
        if (
            event.get("type") == "set_var"
            and event.get("key") in tracked_keys
            and status == "ACTIVE"
        ):
            active_tech_choice_ids.append(node_id)
            if node_id not in graph.decision_status:
                graph.decision_status[node_id] = "ACTIVE"
        elif event.get("type") in {"command_run", "tool_call", "build_run", "verification"}:
            has_execution_evidence = True

    if not has_execution_evidence:
        for node_id in active_tech_choice_ids:
            graph.decision_status[node_id] = "PROPOSED"

    # -------------------------------------------------------------------------
    # Rule 2: Semantic Duplicate Pruning
    # -------------------------------------------------------------------------
    if prune_duplicates:
        tech_groups = defaultdict(list)
        for node_id, event in graph.nodes.items():
            if node_id in graph.pruned:
                continue
            if event.get("type") == "set_var" and event.get("key") in tracked_keys:
                val = event.get("value")
                status = graph.decision_status.get(node_id, event.get("status", "PROPOSED"))
                tech_groups[(val, status)].append(event)

        for (val, status), group in tech_groups.items():
            if len(group) <= 1:
                continue
            group.sort(key=lambda e: e["timestamp"])
            surviving = group[0]
            surviving_id = surviving["id"]

            if "provenance" not in surviving:
                surviving["provenance"] = {}
            if "normalized_sources" not in surviving["provenance"]:
                surviving["provenance"]["normalized_sources"] = []

            for dup in group[1:]:
                dup_id = dup["id"]
                if dup_id in active_referenced_ids:
                    continue
                if is_protected(dup):
                    graph.protected.add(dup_id)
                    graph.protected_reasons[dup_id] = "protected from duplicate pruning"
                    continue

                surviving["provenance"]["normalized_sources"].append({
                    "id": dup_id,
                    "source_text": dup.get("source_text", ""),
                    "timestamp": dup.get("timestamp")
                })

                graph.add_edge(surviving_id, dup_id, "supersedes")
                graph.prune_reasons[dup_id] = f"semantic duplicate of {surviving_id}"
                graph.mark_pruned(dup_id)
                pruned_ids.append(dup_id)

    # -------------------------------------------------------------------------
    # Rule 3: Superseded Decisions
    # -------------------------------------------------------------------------
    if prune_superseded:
        active_choices = []
        for node_id, event in graph.nodes.items():
            status = graph.decision_status.get(node_id, event.get("status"))
            # Check active status
            if (
                event.get("type") == "set_var"
                and event.get("key") in tracked_keys
                and status in {"ACTIVE", "CONFIRMED", None}
            ):
                # Only add if not pruned, or if it is the latest active choice
                active_choices.append(event)

        if active_choices:
            active_choices.sort(key=lambda e: e["timestamp"])
            latest_active = active_choices[-1]
            latest_val = latest_active["value"]
            latest_id = latest_active["id"]

            for node_id, event in list(graph.nodes.items()):
                if node_id == latest_id:
                    continue
                if event.get("type") == "set_var" and event.get("key") in tracked_keys:
                    if event.get("value") != latest_val:
                        if node_id in active_referenced_ids:
                            continue
                        if not prune_referenced_values and _is_value_referenced_by_active_tool_call(
                            graph, event.get("key"), event.get("value")
                        ):
                            continue

                        if is_protected(event):
                            graph.protected.add(node_id)
                            graph.protected_reasons[node_id] = "protected decision"
                            continue

                        graph.decision_status[node_id] = "SUPERSEDED"
                        graph.prune_reasons[node_id] = f"superseded by active {event.get('key')} {latest_val} (event {latest_id})"
                        if node_id not in graph.pruned:
                            graph.add_edge(latest_id, node_id, "supersedes")
                            graph.mark_pruned(node_id)
                            pruned_ids.append(node_id)

    # -------------------------------------------------------------------------
    # Rule 4: Resolved Error Pruning
    # -------------------------------------------------------------------------
    if prune_errors:
        errors = []
        successes = []

        for node_id, event in graph.nodes.items():
            if node_id in graph.pruned:
                continue
            if event.get("type") == "error":
                errors.append(event)
            elif event.get("type") == "command_run" and event.get("exit_code", 0) != 0:
                errors.append(event)
            elif event.get("type") == "test_run" and event.get("failed_count", 0) > 0:
                errors.append(event)

            elif event.get("type") == "command_run" and event.get("exit_code", 0) == 0:
                successes.append(event)
            elif event.get("type") == "test_run" and event.get("failed_count", 0) == 0:
                successes.append(event)
            elif event.get("type") == "verification" and event.get("passed") is True:
                successes.append(event)

        for err in errors:
            err_id = err["id"]
            resolver = None
            for succ in successes:
                if succ["timestamp"] > err["timestamp"]:
                    if err["type"] == "command_run" and succ["type"] == "command_run":
                        if err.get("command", "").split()[0] == succ.get("command", "").split()[0]:
                            resolver = succ
                            break
                    elif err["type"] == "test_run" and succ["type"] == "test_run":
                        if set(err.get("test_names", [])) == set(succ.get("test_names", [])):
                            resolver = succ
                            break
                    elif err["type"] == "error" and succ["type"] == "verification":
                        resolver = succ
                        break

            if resolver:
                if err_id in active_referenced_ids:
                    continue
                if is_protected(err):
                    graph.protected.add(err_id)
                    graph.protected_reasons[err_id] = "protected from resolved error pruning"
                    continue

                graph.add_edge(resolver["id"], err_id, "supersedes")
                graph.prune_reasons[err_id] = f"error resolved by {resolver['id']}"
                graph.mark_pruned(err_id)

                if err_id in graph.receipts:
                    graph.receipts[err_id]["pruning_rule"] = "resolved_error"
                    graph.receipts[err_id]["resolved_by"] = resolver["id"]
                    graph.receipts[err_id]["semantic_rep"] = {"type": err["type"], "status": "FAILED"}

                pruned_ids.append(err_id)

    # -------------------------------------------------------------------------
    # Rule 5C: Obsolete File Reads Compaction
    # -------------------------------------------------------------------------
    if prune_obsolete_reads:
        file_edits = []
        for node_id, event in graph.nodes.items():
            if node_id in graph.pruned:
                continue
            is_edit = (event.get("type") == "file_edit") or (
                event.get("type") == "tool_call" and event.get("tool_name") in {"write_file", "edit_file", "patch_file", "write_to_file"}
            )
            if is_edit:
                path = _get_filepath_from_event(event)
                if path:
                    file_edits.append({"id": node_id, "path": path, "timestamp": event["timestamp"]})

        for edit in file_edits:
            edit_path = edit["path"]
            edit_ts = edit["timestamp"]

            for node_id, event in list(graph.nodes.items()):
                if node_id in graph.pruned or node_id == edit["id"]:
                    continue
                is_read = (event.get("type") == "file_read") or (
                    event.get("type") == "tool_call" and event.get("tool_name") in {"view_file", "read_file"}
                )
                if is_read:
                    read_path = _get_filepath_from_event(event)
                    if read_path == edit_path and event["timestamp"] < edit_ts:
                        if node_id in active_referenced_ids:
                            continue
                        if is_protected(event):
                            continue

                        graph.prune_reasons[node_id] = f"file read of {read_path} obsolete after edit {edit['id']}"
                        graph.mark_pruned(node_id)
                        pruned_ids.append(node_id)

                        # Compact matching tool results
                        for res_id, res_ev in list(graph.nodes.items()):
                            if res_ev.get("type") == "tool_result" and res_ev.get("call_id") == node_id:
                                if res_id not in graph.pruned and res_id not in active_referenced_ids:
                                    graph.prune_reasons[res_id] = f"tool result for obsolete read {node_id}"
                                    graph.mark_pruned(res_id)
                                    pruned_ids.append(res_id)

    # -------------------------------------------------------------------------
    # Rule 5F: Redundant Verifications Compaction
    # -------------------------------------------------------------------------
    if prune_redundant_verifications:
        verifications = defaultdict(list)
        for node_id, event in graph.nodes.items():
            if node_id in graph.pruned:
                continue

            is_success_verification = False
            command_key = None

            if event.get("type") == "verification" and event.get("passed") is True:
                is_success_verification = True
                command_key = f"ver_{event.get('content', '')}"
            elif event.get("type") == "command_run" and event.get("exit_code") == 0:
                cmd = event.get("command", "").strip()
                if cmd.startswith("pytest") or cmd.startswith("npm test") or cmd.startswith("python"):
                    is_success_verification = True
                    command_key = f"cmd_{cmd}"
            elif event.get("type") == "test_run" and event.get("exit_code") == 0 and event.get("failed_count", 0) == 0:
                is_success_verification = True
                command_key = f"test_{tuple(sorted(event.get('test_names', [])))}"

            if is_success_verification and command_key:
                verifications[command_key].append(event)

        for command_key, group in verifications.items():
            if len(group) <= 1:
                continue
            group.sort(key=lambda e: e["timestamp"])
            latest_succ = group[-1]
            latest_id = latest_succ["id"]

            for old_succ in group[:-1]:
                old_id = old_succ["id"]
                if old_id in active_referenced_ids:
                    continue
                if is_protected(old_succ):
                    continue

                graph.prune_reasons[old_id] = f"redundant verification, superseded by latest success {latest_id}"
                graph.mark_pruned(old_id)
                pruned_ids.append(old_id)

                for res_id, res_ev in list(graph.nodes.items()):
                    if res_ev.get("type") == "tool_result" and res_ev.get("call_id") == old_id:
                        if res_id not in graph.pruned and res_id not in active_referenced_ids:
                            graph.prune_reasons[res_id] = f"tool result for redundant verification {old_id}"
                            graph.mark_pruned(res_id)
                            pruned_ids.append(res_id)

    return pruned_ids
