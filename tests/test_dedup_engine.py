import pytest
from trace_gc.graph import StateGraph
from trace_gc.dedup_engine import deduplicate_tool_calls
from trace_gc.receipts import get_receipt

def test_dedup_identical_tool_calls():
    graph = StateGraph()
    
    # Define 3 identical tool calls and their results
    events = [
        {"id": "tc1", "type": "tool_call", "timestamp": 100, "tool_name": "read_file", "arguments": {"path": "main.py"}},
        {"id": "tr1", "type": "tool_result", "timestamp": 101, "call_id": "tc1", "result": "print('hello')"},
        
        {"id": "tc2", "type": "tool_call", "timestamp": 200, "tool_name": "read_file", "arguments": {"path": "main.py"}},
        {"id": "tr2", "type": "tool_result", "timestamp": 201, "call_id": "tc2", "result": "print('hello')"},
        
        {"id": "tc3", "type": "tool_call", "timestamp": 300, "tool_name": "read_file", "arguments": {"path": "main.py"}},
        {"id": "tr3", "type": "tool_result", "timestamp": 301, "call_id": "tc3", "result": "print('hello')"},
    ]
    for ev in events:
        graph.add_node(ev)
        
    pruned = deduplicate_tool_calls(graph)
    
    # Only tc1 and tr1 should survive. tc2, tr2, tc3, tr3 should be pruned.
    assert set(pruned) == {"tc2", "tr2", "tc3", "tr3"}
    assert "tc1" not in graph.pruned
    assert "tr1" not in graph.pruned
    
    for nid in pruned:
        assert nid in graph.pruned
        # Verify a valid, resolvable receipt exists
        assert nid in graph.receipts
        recovered = get_receipt(graph, nid)
        assert recovered["id"] == nid
        
    # Check that supersedes edges are created from surviving nodes to duplicate nodes
    supersedes_edges = [(src, dst) for src, dst, typ in graph.edges if typ == "supersedes"]
    assert ("tc1", "tc2") in supersedes_edges
    assert ("tc1", "tc3") in supersedes_edges
    assert ("tr1", "tr2") in supersedes_edges
    assert ("tr1", "tr3") in supersedes_edges


def test_no_dedup_differing_args():
    graph = StateGraph()
    
    # 2 tool calls with different arguments
    events = [
        {"id": "tc1", "type": "tool_call", "timestamp": 100, "tool_name": "read_file", "arguments": {"path": "main.py"}},
        {"id": "tr1", "type": "tool_result", "timestamp": 101, "call_id": "tc1", "result": "print('hello')"},
        
        {"id": "tc2", "type": "tool_call", "timestamp": 200, "tool_name": "read_file", "arguments": {"path": "app.py"}},
        {"id": "tr2", "type": "tool_result", "timestamp": 201, "call_id": "tc2", "result": "print('hello')"},
    ]
    for ev in events:
        graph.add_node(ev)
        
    pruned = deduplicate_tool_calls(graph)
    
    # None should be pruned
    assert len(pruned) == 0
    assert len(graph.pruned) == 0


def test_no_dedup_differing_results():
    graph = StateGraph()
    
    # 2 tool calls with same arguments but different results
    events = [
        {"id": "tc1", "type": "tool_call", "timestamp": 100, "tool_name": "read_file", "arguments": {"path": "main.py"}},
        {"id": "tr1", "type": "tool_result", "timestamp": 101, "call_id": "tc1", "result": "print('hello')"},
        
        {"id": "tc2", "type": "tool_call", "timestamp": 200, "tool_name": "read_file", "arguments": {"path": "main.py"}},
        {"id": "tr2", "type": "tool_result", "timestamp": 201, "call_id": "tc2", "result": "modified content"},
    ]
    for ev in events:
        graph.add_node(ev)
        
    pruned = deduplicate_tool_calls(graph)
    
    assert len(pruned) == 0


def test_non_json_serializable_args_fallback():
    """Tool calls with non-JSON-serializable arguments use str() fallback for dedup key."""
    class CustomObj:
        def __repr__(self):
            return "CustomObj()"

    graph = StateGraph()
    obj = CustomObj()
    # Two identical calls with a non-serializable arg object (same repr -> same dedup key)
    events = [
        {"id": "tc1", "type": "tool_call", "timestamp": 100, "tool_name": "run", "arguments": obj},
        {"id": "tr1", "type": "tool_result", "timestamp": 101, "call_id": "tc1", "result": "x"},
        {"id": "tc2", "type": "tool_call", "timestamp": 200, "tool_name": "run", "arguments": obj},
        {"id": "tr2", "type": "tool_result", "timestamp": 201, "call_id": "tc2", "result": "x"},
    ]
    for ev in events:
        graph.add_node(ev)

    pruned = deduplicate_tool_calls(graph)
    # tc1/tr1 survive; tc2/tr2 are duplicates
    assert set(pruned) == {"tc2", "tr2"}


def test_orphan_tool_call_not_deduplicated():
    """A tool_call with no matching tool_result (orphan) is never deduplicated."""
    graph = StateGraph()
    graph.add_node({"id": "tc1", "type": "tool_call", "timestamp": 100, "tool_name": "run", "arguments": {}})
    graph.add_node({"id": "tc2", "type": "tool_call", "timestamp": 200, "tool_name": "run", "arguments": {}})
    # No tool_result nodes at all

    pruned = deduplicate_tool_calls(graph)
    assert len(pruned) == 0
    assert "tc1" not in graph.pruned
    assert "tc2" not in graph.pruned


def test_tool_call_with_pre_pruned_result_not_deduplicated():
    """A tool_call whose result was already pruned by a prior stage is skipped for dedup."""
    graph = StateGraph()
    graph.add_node({"id": "tc1", "type": "tool_call", "timestamp": 100, "tool_name": "run", "arguments": {}})
    graph.add_node({"id": "tr1", "type": "tool_result", "timestamp": 101, "call_id": "tc1", "result": "ok"})
    graph.add_node({"id": "tc2", "type": "tool_call", "timestamp": 200, "tool_name": "run", "arguments": {}})
    graph.add_node({"id": "tr2", "type": "tool_result", "timestamp": 201, "call_id": "tc2", "result": "ok"})

    # Pre-prune tr1 (as if dead-branch sweeper pruned it)
    graph.mark_pruned("tr1")

    pruned = deduplicate_tool_calls(graph)
    # tc1/tr1 are not eligible (tr1 is pre-pruned); tc2/tr2 must not be deduplicated
    assert "tc2" not in graph.pruned
    assert "tr2" not in graph.pruned
