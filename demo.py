# demo.py
"""Interactive CLI demo for TraceGC.

Simulates agent execution traces in real-time, appends events to TraceGC,
compacts history, and prints a detailed analysis using dynamic graph‑based
pruned-reason classification.
"""

from __future__ import annotations

import os
import json
import time
from trace_gc import TraceGC

FIXTURE_SUPPORT = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "support_agent_trace.json")
FIXTURE_RESEARCH = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "research_agent_trace.json")


def get_prune_reason(graph: TraceGC, node_id: str) -> str:
    """Classify the reason why a node was pruned based on graph properties."""
    # 1. Identify if it is part of a collapsed cycle
    clustered_nodes = set()
    for node in graph.graph.nodes.values():
        if node.get("type") == "receipt" and "cluster_members" in node:
            clustered_nodes.update(node["cluster_members"])
    if node_id in clustered_nodes:
        return "Collapsed as part of a repeating sequence cycle"

    event = graph.graph.nodes.get(node_id)
    if event is None:
        return "Pruned - reason not classified"

    # 2. Check for incoming supersedes edges
    has_supersedes = False
    for src, typ in graph.graph._rev.get(node_id, []):
        if typ == "supersedes":
            has_supersedes = True
            break

    if has_supersedes:
        if event.get("type") == "set_var":
            return "Overridden by a newer variable assignment"
        elif event.get("type") in ("tool_call", "tool_result"):
            return "Deduplicated duplicate execution"

    # 3. Check if descendant of an abandon target
    abandon_targets = []
    for ev in graph.graph.nodes.values():
        if ev.get("type") == "abandon":
            abandon_targets.extend(ev.get("ref_to", []))

    is_abandoned = False
    for tgt in abandon_targets:
        if tgt == node_id:
            is_abandoned = True
            break
        # Query sequence ancestors of the node
        ancestors = graph.graph.get_ancestors(node_id, edge_types=["sequence"])
        if tgt in ancestors:
            is_abandoned = True
            break

    if is_abandoned:
        return "Abandoned dead-end execution branch (pruned by DFS)"

    # 4. Fallback check for abandon node itself
    if event.get("type") == "abandon":
        return "Abandon instruction metadata (omitted from prompt)"

    return "Pruned - reason not classified"


def run_demo_for(fixture_path: str, title: str):
    print("=" * 80)
    print(f"  CONTEXT-GC DEMO: {title.upper()}")
    print("=" * 80)
    
    if not os.path.exists(fixture_path):
        print(f"Error: Fixture file not found at {fixture_path}")
        return

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "events" in data:
        events = data["events"]
    else:
        events = data

    client = TraceGC()
    
    print("\n--- Simulating Agent Execution (Ingesting Events) ---")
    for idx, ev in enumerate(events):
        client.add_event(ev)
        
        etype = ev["type"]
        eid = ev["id"]
        if etype == "decision":
            detail = f"Decision: {ev['content']}"
        elif etype == "set_var":
            detail = f"Set Var: {ev['key']} = {ev['value']}"
        elif etype == "tool_call":
            detail = f"Tool Call: {ev['tool_name']}({ev['arguments']})"
        elif etype == "tool_result":
            detail = f"Tool Result: {ev['result']}"
        elif etype == "abandon":
            detail = f"Abandon Path: {ev['ref_to']}"
        else:
            detail = str(ev)
            
        print(f"[{idx+1:02d}] Added event {eid:<6} | {detail[:70]}")
        time.sleep(0.01)  # Quick simulate pacing

    print("\n" + "=" * 80)
    print("                    RUNNING DETERMINISTIC GRAPH COMPACTION")
    print("=" * 80)
    
    start_time = time.perf_counter()
    result = client.compact()
    elapsed = (time.perf_counter() - start_time) * 1000.0

    print("\n--- Compaction Metrics ---")
    tokens_before = result["tokens_before"]
    tokens_after = result["tokens_after"]
    pct_reduction = ((tokens_before - tokens_after) / tokens_before) * 100.0
    pruned_count = len(result["pruned_ids"])
    
    print(f"Total Input Events:  {len(events)}")
    print(f"Surviving Events:     {len(result['compact_events'])}")
    print(f"Pruned Nodes Count:   {pruned_count}")
    print(f"Tokens Before:        {tokens_before}")
    print(f"Tokens After:         {tokens_after}")
    print(f"Token Size Reduction: {pct_reduction:.1f}%")
    print(f"Execution Latency:    {elapsed:.3f}ms")
    
    print("\n--- Pruned Node Breakdown (Dynamically Classified) ---")
    pruned_set = set(result["pruned_ids"])
    for ev in events:
        eid = ev["id"]
        if eid in pruned_set:
            reason = get_prune_reason(client, eid)
            print(f"- Node {eid:<6} ({ev['type']:<11}) | Reason: {reason}")

    # Artifact check specifically for support trace
    if "support" in fixture_path:
        print("\n--- Recovering Pruned Artifact Metadata ---")
        ticket_node_id = "sa03"
        if ticket_node_id in pruned_set:
            recovered = client.get_receipt(ticket_node_id)
            print(f"Successfully resolved pruned node '{ticket_node_id}' metadata via get_receipt():")
            print(f"  Result payload: {recovered['result']}")
            print(f"  Pruned status:  {recovered.get('pruned')}")

    print("\n" + "=" * 80)
    print("                       FINAL COMPACTED PROMPT PREFIX")
    print("=" * 80)
    print(result["prompt"])
    print("=" * 80 + "\n\n")


def run_demo():
    run_demo_for(FIXTURE_SUPPORT, "Customer Support Agent Trace")
    run_demo_for(FIXTURE_RESEARCH, "Research Agent Trace")


if __name__ == "__main__":
    run_demo()
