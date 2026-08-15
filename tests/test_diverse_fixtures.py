# tests/test_diverse_fixtures.py
"""Diverse fixtures integration tests.

Applies compaction to structural fixtures representing a research agent trace
and a customer support agent trace, asserting probe preservation, pruning,
and token reduction.
"""

from __future__ import annotations

import os
import json
import pytest
from tracegc import TraceGC, compact_events
from tracegc.events import load_events_from_json
from tracegc.receipts import get_receipt

FIXTURE_RESEARCH_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "research_agent_trace.json")
FIXTURE_SUPPORT_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "support_agent_trace.json")


def test_research_agent_trace():
    """Verify research_agent_trace.json compaction and semantic probes."""
    assert os.path.exists(FIXTURE_RESEARCH_PATH)
    events = load_events_from_json(FIXTURE_RESEARCH_PATH)
    
    result = compact_events(events)
    prompt = result["prompt"]
    compact_ids = [ev["id"] for ev in result["compact_events"]]
    
    # Verify no errors and token reduction
    assert result["tokens_after"] < result["tokens_before"]
    
    # 1. Recall Probe:
    # Final conclusion must survive
    assert "Conclusion: Severe drought" in prompt
    assert "r11" in compact_ids
    # Larnaca core findings must survive
    assert "Larnaca Salt Lake" in prompt
    # Abandoned branch nodes must be pruned
    assert "ra02" not in compact_ids
    assert "ra05" not in compact_ids
    # Abandoned texts must not leak into prompt
    assert "RS 20.212" not in prompt
    assert "Mycenaean style" not in prompt
    
    # 2. Continuation / Override Probe:
    # Final hypothesis and source override must survive
    assert "current_hypothesis = Climate-induced drought" in prompt
    assert "best_source = Larnaca Lake pollen core data" in prompt
    # Obsolete values must be pruned
    assert "current_hypothesis = System collapse" not in prompt
    assert "best_source = Ramses III" not in prompt
    assert "r02" in result["pruned_ids"]
    assert "r03" in result["pruned_ids"]
    
    # Print metrics
    print(f"\n--- Research Agent Trace Compaction Metrics ---")
    print(f"Tokens Before: {result['tokens_before']}")
    print(f"Tokens After:  {result['tokens_after']}")
    print(f"Pruned Nodes:  {len(result['pruned_ids'])}")
    print(f"Remaining:     {len(result['compact_events'])}")


def test_support_agent_trace():
    """Verify support_agent_trace.json compaction and semantic probes."""
    assert os.path.exists(FIXTURE_SUPPORT_PATH)
    
    client = TraceGC()
    events = load_events_from_json(FIXTURE_SUPPORT_PATH)
    for ev in events:
        client.add_event(ev)
        
    result = client.compact()
    prompt = result["prompt"]
    compact_ids = [ev["id"] for ev in result["compact_events"]]
    
    # Verify no errors and token reduction
    assert result["tokens_after"] < result["tokens_before"]
    
    # 1. Decision / Rationale Probe:
    # Pivot decision must survive
    assert "Legacy CRM is deprecated. Pivoting to modern customer account service." in prompt
    assert "s02" in compact_ids
    # Abandoned legacy CRM path must be pruned
    assert "sa01" not in compact_ids
    assert "sa02" not in compact_ids
    assert "sa03" not in compact_ids
    assert "lookup_legacy_ticket" not in prompt
    assert "Querying legacy CRM" not in prompt
    
    # 2. Artifact-Tracking Probe:
    # Ticket and refund IDs must survive and appear in prompt (via active references)
    assert "tkt_5531" in prompt
    assert "ref_9041" in prompt
    # The actual producer of ticket_id (sa03) was pruned
    assert "sa03" in result["pruned_ids"]
    # Recover artifact metadata of pruned node via get_receipt
    recovered = client.get_receipt("sa03")
    assert recovered["id"] == "sa03"
    assert recovered["type"] == "tool_result"
    assert recovered["result"] == {"ticket_id": "tkt_5531"}
    assert recovered.get("pruned") is True
    
    # Print metrics
    print(f"\n--- Support Agent Trace Compaction Metrics ---")
    print(f"Tokens Before: {result['tokens_before']}")
    print(f"Tokens After:  {result['tokens_after']}")
    print(f"Pruned Nodes:  {len(result['pruned_ids'])}")
    print(f"Remaining:     {len(result['compact_events'])}")
