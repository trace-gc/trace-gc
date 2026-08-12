# trace_gc/compactor.py
"""Compaction orchestrator – builds the graph, runs pruning stages, and renders a prompt.

The public entry point is :func:`compact_events` which accepts a list of event
records (as produced by :func:`load_events_from_json` in ``events.py``) and
returns a dictionary containing the compacted prompt, token metrics, receipts,
and the list of surviving event dictionaries.
"""

from __future__ import annotations

from typing import List, Dict, Any, Set

from .events import validate_event
from .graph import StateGraph
from .override_engine import apply_overrides
from .dead_branch_sweeper import sweep_dead_branches
from .dedup_engine import deduplicate_tool_calls
from .topo_sampler import collapse_cycles
from .receipts import collect_receipts

# ---------------------------------------------------------------------------
# Rendering helpers – turn an event dict into a human‑readable string
# ---------------------------------------------------------------------------
def _render_event(event: Dict[str, Any]) -> str:
    etype = event.get("type")
    if etype == "set_var":
        return f"{event.get('key')} = {event.get('value')}"
    if etype in {"decision", "text_chunk"}:
        return event.get("content", "")
    if etype == "tool_call":
        return f"CALL {event.get('tool_name')}({event.get('arguments')})"
    if etype == "tool_result":
        return f"RESULT {event.get('result')}"
    if etype == "receipt":
        # Simple stub representation – can be enriched later
        return f"[RECEIPT {event.get('id')}]"
    
    # Coding agent event types
    if etype == "file_read":
        return f"READ {event.get('path')}"
    if etype == "file_edit":
        dh = event.get('diff_hash', '')
        return f"EDIT {event.get('path')} (diff {dh[:8]})"
    if etype == "command_run":
        return f"RUN {event.get('command')} -> exit {event.get('exit_code')}"
    if etype == "test_run":
        names = event.get('test_names', [])
        if len(names) > 3:
            names_str = ", ".join(names[:3]) + f", +{len(names) - 3} more"
        else:
            names_str = ", ".join(names)
        return f"TEST {names_str} — {event.get('passed_count')} passed, {event.get('failed_count')} failed"
    if etype == "build_run":
        return f"BUILD -> exit {event.get('exit_code')}"
    if etype == "git_diff":
        dh = event.get('diff_hash', '')
        files = ", ".join(event.get('files_changed', []))
        return f"DIFF (diff {dh[:8]}) on {files}"
    if etype == "git_commit":
        ch = event.get('commit_hash', '')
        return f"COMMIT {ch[:8]}: {event.get('message')}"
    if etype == "error":
        msg = f"ERROR: {event.get('message')}"
        rel = event.get('related_to')
        if rel:
            msg += f" (related to {rel})"
        return msg
    if etype == "artifact_created":
        return f"ARTIFACT {event.get('artifact_type')} created at {event.get('path')}"
    if etype == "requirement":
        return f"REQUIREMENT: {event.get('content')}"
    if etype == "constraint":
        return f"CONSTRAINT: {event.get('content')}"
    if etype == "verification":
        passed_str = "passed" if event.get('passed') else "failed"
        return f"VERIFICATION: {event.get('content')} — {passed_str}"

    # ``abandon`` events are not rendered in the final prompt
    return ""

# ---------------------------------------------------------------------------
# Graph construction – respects explicit parent_id relationships
# ---------------------------------------------------------------------------
def _build_state_graph(events: List[Dict[str, Any]]) -> StateGraph:
    """Create a :class:`StateGraph` from *events* using ``parent_id`` edges.

    The function validates events, adds each as a node, and then creates a
    ``sequence`` edge from ``parent_id`` -> ``id`` whenever ``parent_id`` is not
    ``None``.  This mirrors the logical flow of the original trace and enables the
    dead‑branch sweeper to prune only true descendants.
    """
    graph = StateGraph()
    for ev in events:
        ev = validate_event(ev)
        graph.add_node(ev)
    # Add explicit sequence edges based on parent_id
    for ev in events:
        parent = ev.get("parent_id")
        if parent:
            if parent not in graph.nodes:
                raise ValueError(
                    f"Event {ev['id']!r} references a non-existent parent_id {parent!r}"
                )
            graph.add_edge(parent, ev["id"], "sequence")
    return graph

# ---------------------------------------------------------------------------
# Main entry point – run pipeline, render, and compute token metrics
# ---------------------------------------------------------------------------
def compact_events(events: List[Dict[str, Any]], prune_referenced_values: bool = True) -> Dict[str, Any]:
    """Run the full deterministic compaction pipeline and produce a prompt.

    Returns a mapping with the following keys:

    - ``prompt`` – the rendered compacted context string.
    - ``tokens_before`` – estimated token count of the original (unpruned) prompt.
    - ``tokens_after`` – token count after compaction.
    - ``receipts`` – list of receipt stub dictionaries.
    - ``pruned_ids`` – list of IDs that were removed.
    - ``compact_events`` – list of surviving event dictionaries (ordered).
    - ``graph`` – the ``StateGraph`` instance used during compaction, allowing
      callers to recover original events via ``get_receipt``.
    """
    # -------------------------------------------------------------------
    # Build initial graph and run pruning stages
    # -------------------------------------------------------------------
    graph = _build_state_graph(events)
    sweep_dead_branches(graph)      # stage 1
    apply_overrides(graph, prune_referenced_values=prune_referenced_values)   # stage 2
    deduplicate_tool_calls(graph)   # stage 3
    collapse_cycles(graph)          # stage 4

    pruned_ids = sorted(list(graph.pruned))
    receipts = collect_receipts(graph)

    # -------------------------------------------------------------------
    # Determine ordering for rendering – topological order of *sequence* edges
    # -------------------------------------------------------------------
    ordered_node_ids = graph.topological_sort(edge_type="sequence")
    live_node_ids = [nid for nid in ordered_node_ids if nid not in graph.pruned]

    # -------------------------------------------------------------------
    # Render the original (pre‑pruning) prompt for token baseline
    # -------------------------------------------------------------------
    original_strings = [_render_event(ev) for ev in events]
    original_text = "\n".join(filter(None, original_strings))
    tokens_before = len(original_text) // 4

    # Identify pruned nodes that are part of a collapsed cycle
    clustered_nodes: Set[str] = set()
    for node in graph.nodes.values():
        if node.get("type") == "receipt" and "cluster_members" in node:
            clustered_nodes.update(node["cluster_members"])

    # Pre-index outgoing edges by source for O(N + E) linear lookup
    from collections import defaultdict
    edges_by_src = defaultdict(list)
    for src, dst, typ in graph.edges:
        edges_by_src[src].append((dst, typ))

    # -------------------------------------------------------------------
    # Render the compacted prompt, inserting receipt stubs inline when a live
    # node has edges to pruned nodes (e.g., supersedes edges). Deduplicate
    # receipts so each pruned node contributes at most one line.
    # -------------------------------------------------------------------
    rendered_parts: List[str] = []
    seen_receipts: Set[str] = set()
    for nid in live_node_ids:
        node = graph.nodes[nid]
        rendered = _render_event(node)
        if rendered:
            rendered_parts.append(rendered)
        # Check outgoing edges for references to pruned nodes
        for dst, typ in edges_by_src.get(nid, []):
            if dst in graph.pruned and dst not in seen_receipts:
                if dst in clustered_nodes:
                    continue
                receipt = graph.receipts.get(dst)
                if receipt:
                    rendered_parts.append(_render_event(receipt))
                    seen_receipts.add(dst)
    prompt = "\n".join(rendered_parts)
    tokens_after = len(prompt) // 4

    # Gather surviving event dictionaries (in the same order as rendering)
    live_events = [graph.nodes[nid] for nid in live_node_ids]

    return {
        "prompt": prompt,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "receipts": receipts,
        "pruned_ids": pruned_ids,
        "protected_ids": sorted(list(graph.protected)),
        "compact_events": live_events,
        "graph": graph,
    }
