import pytest
from trace_gc.graph import StateGraph
from trace_gc.dead_branch_sweeper import sweep_dead_branches

def test_sweep_dead_branches():
    graph = StateGraph()
    # Create two branches:
    # branch 1: e1 -> e2 -> e3 (abandoned at e2)
    # branch 2: e1 -> e4 -> e5 (surviving)
    graph.add_node({"id": "e1", "type": "decision", "timestamp": 100})
    graph.add_node({"id": "e2", "type": "decision", "timestamp": 200, "parent_id": "e1"})
    graph.add_node({"id": "e3", "type": "tool_call", "timestamp": 300, "parent_id": "e2"})
    graph.add_node({"id": "e4", "type": "decision", "timestamp": 400, "parent_id": "e1"})
    graph.add_node({"id": "e5", "type": "tool_call", "timestamp": 500, "parent_id": "e4"})
    graph.add_node({"id": "ab1", "type": "abandon", "timestamp": 600, "parent_id": "e3", "ref_to": ["e2"]})

    graph.add_edge("e1", "e2", "sequence")
    graph.add_edge("e2", "e3", "sequence")
    graph.add_edge("e1", "e4", "sequence")
    graph.add_edge("e4", "e5", "sequence")
    graph.add_edge("e3", "ab1", "sequence")

    pruned = sweep_dead_branches(graph)
    # ab1 abandons e2, so e2 and all its sequence descendants (e3, ab1) should be pruned.
    assert "e2" in graph.pruned
    assert "e3" in graph.pruned
    assert "ab1" in graph.pruned
    assert "e1" not in graph.pruned
    assert "e4" not in graph.pruned
    assert "e5" not in graph.pruned

    # Assert reasons
    assert graph.prune_reasons["e2"] == "abandoned by ab1"
    assert graph.prune_reasons["e3"] == "abandoned by ab1"
    assert graph.prune_reasons["ab1"] == "abandon event pruned alongside its own target branch"
