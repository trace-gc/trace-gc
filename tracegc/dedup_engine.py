# tracegc/dedup_engine.py
"""Deduplication engine – prunes duplicate tool calls."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import List, Dict, Any

from .graph import StateGraph
from .retention_policy import is_protected

logger = logging.getLogger(__name__)


def deduplicate_tool_calls(graph: StateGraph) -> List[str]:
    """Prunes duplicate tool calls (same tool_name, arguments, and result) by retaining only the earliest."""
    # 1. Map call_id to tool_result nodes (only surviving/not pruned results)
    tool_results: Dict[str, Dict[str, Any]] = {}
    for node_id, node in graph.nodes.items():
        if node_id in graph.pruned:
            continue
        if node.get("type") == "tool_result" and "call_id" in node:
            tool_results[node["call_id"]] = node

    # 2. Group active tool_call nodes by (tool_name, arguments, result)
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for node_id, node in graph.nodes.items():
        if node_id in graph.pruned:
            continue
        if node.get("type") == "tool_call":
            # Must have an associated surviving tool_result to be deduplicated
            res_node = tool_results.get(node_id)
            if res_node is None:
                continue
            
            # Serialize arguments and result to make them hashable
            try:
                args_str = json.dumps(node.get("arguments"), sort_keys=True)
                res_str = json.dumps(res_node.get("result"), sort_keys=True)
            except (TypeError, ValueError):
                # Fallback to string representation if not json serializable
                tool_name = node.get("tool_name", "unknown")
                logger.warning(
                    "Deduplication for tool '%s' (event '%s') may be unreliable due to non-JSON-serializable arguments",
                    tool_name,
                    node_id,
                )
                args_str = str(node.get("arguments"))
                res_str = str(res_node.get("result"))

            key = (node.get("tool_name"), args_str, res_str)
            groups[key].append(node)

    pruned_ids: List[str] = []
    for key, tcs in groups.items():
        if len(tcs) <= 1:
            continue
        # Sort by timestamp ascending
        tcs.sort(key=lambda x: x["timestamp"])
        surviving_tc = tcs[0]
        surviving_tc_id = surviving_tc["id"]
        surviving_tr_id = tool_results[surviving_tc_id]["id"]

        for dup_tc in tcs[1:]:
            dup_tc_id = dup_tc["id"]
            dup_tr = tool_results[dup_tc_id]
            dup_tr_id = dup_tr["id"]

            # Add supersedes edges
            graph.add_edge(surviving_tc_id, dup_tc_id, "supersedes")
            graph.add_edge(surviving_tr_id, dup_tr_id, "supersedes")

            # Track reasons
            tc_reason = f"duplicate of {surviving_tc_id}"
            tr_reason = f"duplicate of {surviving_tr_id}"
            graph.prune_reasons[dup_tc_id] = tc_reason
            graph.prune_reasons[dup_tr_id] = tr_reason

            # Handle duplicate tool call
            if is_protected(dup_tc):
                graph.protected.add(dup_tc_id)
                if dup_tc.get("importance") == "critical":
                    graph.protected_reasons[dup_tc_id] = "importance=critical"
                elif dup_tc.get("retain_until") in {"task_end", "session_end"}:
                    graph.protected_reasons[dup_tc_id] = f"retain_until={dup_tc['retain_until']}"
            else:
                graph.mark_pruned(dup_tc_id)
                pruned_ids.append(dup_tc_id)

            # Handle duplicate tool result
            if is_protected(dup_tr):
                graph.protected.add(dup_tr_id)
                if dup_tr.get("importance") == "critical":
                    graph.protected_reasons[dup_tr_id] = "importance=critical"
                elif dup_tr.get("retain_until") in {"task_end", "session_end"}:
                    graph.protected_reasons[dup_tr_id] = f"retain_until={dup_tr['retain_until']}"
            else:
                graph.mark_pruned(dup_tr_id)
                pruned_ids.append(dup_tr_id)

    return pruned_ids
