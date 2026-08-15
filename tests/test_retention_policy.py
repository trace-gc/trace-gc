"""Tests for event retention policy logic.

For a demonstration trace showing the 'importance=critical' protection feature,
see the fixture: tests/fixtures/retention_policy_demo.json.
"""

import pytest
from tracegc.graph import StateGraph
from tracegc.events import validate_event
from tracegc.override_engine import apply_overrides
from tracegc.dead_branch_sweeper import sweep_dead_branches
from tracegc.dedup_engine import deduplicate_tool_calls


def test_override_protection():
    # Construct a state graph with set_var events where one would be overridden but is marked critical
    graph = StateGraph()
    e1 = validate_event({"id": "e1", "type": "set_var", "timestamp": 1000, "key": "refill_rate", "value": 5, "importance": "critical"})
    e2 = validate_event({"id": "e2", "type": "set_var", "timestamp": 1010, "key": "refill_rate", "value": 10})
    graph.add_node(e1)
    graph.add_node(e2)

    pruned = apply_overrides(graph)
    assert "e1" not in pruned
    assert "e1" not in graph.pruned
    assert "e1" in graph.protected
    assert graph.protected_reasons["e1"] == "importance=critical"
    assert graph.prune_reasons["e1"] == "superseded by e2"


def test_dead_branch_sweeper_protection():
    # Construct a state graph where a node inside an abandoned branch is protected via retain_until
    graph = StateGraph()
    e1 = validate_event({"id": "e1", "type": "decision", "timestamp": 1000, "content": "Starting"})
    e2 = validate_event({"id": "e2", "type": "set_var", "timestamp": 1010, "parent_id": "e1", "key": "x", "value": 8, "retain_until": "task_end"})
    e3 = validate_event({"id": "ab1", "type": "abandon", "timestamp": 1020, "parent_id": "e2", "ref_to": ["e2"]})
    graph.add_node(e1)
    graph.add_node(e2)
    graph.add_node(e3)
    graph.add_edge("e1", "e2", "sequence")
    graph.add_edge("e2", "ab1", "sequence")

    pruned = sweep_dead_branches(graph)
    assert "e2" not in pruned
    assert "e2" not in graph.pruned
    assert "e2" in graph.protected
    assert graph.protected_reasons["e2"] == "retain_until=task_end"
    assert graph.prune_reasons["e2"] == "abandoned by ab1"


def test_dedup_protection():
    # Construct a state graph with duplicate tool calls where one of the duplicates is protected
    graph = StateGraph()
    # Tool result 1
    tc1 = validate_event({"id": "tc1", "type": "tool_call", "timestamp": 1000, "tool_name": "get_x", "arguments": {}})
    tr1 = validate_event({"id": "tr1", "type": "tool_result", "timestamp": 1010, "call_id": "tc1", "result": 42})
    # Duplicate tool result 2 (protected)
    tc2 = validate_event({"id": "tc2", "type": "tool_call", "timestamp": 1020, "tool_name": "get_x", "arguments": {}, "importance": "critical"})
    tr2 = validate_event({"id": "tr2", "type": "tool_result", "timestamp": 1030, "call_id": "tc2", "result": 42})

    graph.add_node(tc1)
    graph.add_node(tr1)
    graph.add_node(tc2)
    graph.add_node(tr2)

    pruned = deduplicate_tool_calls(graph)
    assert "tc2" not in pruned
    assert "tc2" not in graph.pruned
    assert "tc2" in graph.protected
    assert graph.protected_reasons["tc2"] == "importance=critical"
    assert graph.prune_reasons["tc2"] == "duplicate of tc1"

    # Since tr2 is not protected, it gets pruned
    assert "tr2" in pruned
    assert "tr2" in graph.pruned
    assert graph.prune_reasons["tr2"] == "duplicate of tr1"
