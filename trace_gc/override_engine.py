# trace_gc/override_engine.py
"""Override engine – identifies and prunes superseded ``set_var`` updates.

Retains only the latest set_var event for each key (by timestamp) among surviving
nodes and marks all preceding writes as pruned.

Behavior modes
--------------
``prune_referenced_values=True`` (default)
    Pruning is context-only.  An older set_var value is removed from the
    compacted output even if an active tool_call's arguments still reference
    that key with that exact value.  This is safe for LLM context-window
    compaction where the LLM re-derives context from the surviving events.

``prune_referenced_values=False`` (replay-safe mode)
    An older set_var value is **retained** if any active (non-pruned)
    tool_call event's ``arguments`` dict contains the same key with the
    same value.  Use this mode when the compacted trace must be replayable
    without recomputing variable values from tool_call history.

The tiebreak rule when two set_var events share the same timestamp:
    ``list.sort()`` is a stable sort, so the event appearing *later in the
    input list* is treated as "newest" and retained.  This is deterministic
    given a fixed input order and is documented here rather than left implicit.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, List

from .graph import StateGraph
from .retention_policy import is_protected


def _is_value_referenced_by_active_tool_call(
    graph: StateGraph, key: str, value: Any
) -> bool:
    """Return True if any surviving tool_call event's arguments contain key=value."""
    for node_id, event in graph.nodes.items():
        if node_id in graph.pruned:
            continue
        if event.get("type") == "tool_call":
            args = event.get("arguments", {})
            if isinstance(args, dict) and args.get(key) == value:
                return True
    return False


def apply_overrides(
    graph: StateGraph,
    prune_referenced_values: bool = True,
    tracked_decision_keys: set[str] | None = None,
) -> List[str]:
    """Detect and prune superseded/overridden ``set_var`` events, returning the pruned IDs.

    Parameters
    ----------
    graph:
        The ``StateGraph`` to mutate in-place.
    prune_referenced_values:
        When ``True`` (default), an older set_var value is pruned regardless
        of whether any active tool_call still references it (context-only mode).
        When ``False``, older values are retained if they are still referenced
        by an active tool_call's arguments (replay-safe mode).
    tracked_decision_keys:
        Optional set of variable keys to receive decision-lifecycle treatment
        ("superseded by ..."). If ``None`` (default), all ``set_var`` keys receive
        decision-lifecycle treatment, matching pre-refactor behavior. When passed
        explicitly (e.g. ``{"auth_provider"}``), listed keys receive decision-lifecycle
        treatment ("superseded by ...") while unlisted keys still undergo normal
        keep-latest override pruning ("overridden by ...").
    """
    # Group ``set_var`` events by their ``key``
    key_to_events: dict[str, List[dict]] = defaultdict(list)
    for node_id, event in graph.nodes.items():
        if node_id in graph.pruned:
            continue
        if event.get("type") == "set_var" and event.get("key") is not None and event.get("key") != "database":
            key_to_events[event["key"]].append(event)

    pruned_ids: List[str] = []
    for key, events in key_to_events.items():
        # Sort by timestamp ascending; newest is last.
        # Stable sort: same-timestamp events keep their input-list order.
        events.sort(key=lambda e: e["timestamp"])
        newest = events[-1]
        newest_id = newest["id"]
        is_decision_tracked = (
            key in tracked_decision_keys
            if tracked_decision_keys is not None
            else True
        )

        # Older events are superseded or overridden
        for older in events[:-1]:
            older_id = older["id"]

            # Replay-safe mode: retain if any active tool_call still references
            # this key with this exact value.
            if not prune_referenced_values and _is_value_referenced_by_active_tool_call(
                graph, key, older.get("value")
            ):
                continue  # keep this older value; skip pruning it

            if is_decision_tracked:
                edge_type = "supersedes"
                reason = f"superseded by {newest_id}"
            else:
                edge_type = "overridden"
                reason = f"overridden by {newest_id}"

            # Add edge from newest -> older
            graph.add_edge(newest_id, older_id, edge_type)
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
