import pytest
from trace_gc.graph import StateGraph
from trace_gc.override_engine import apply_overrides
from trace_gc.compactor import compact_events


def test_apply_overrides():
    """Baseline: 3 writes to same key — newest survives, older two pruned."""
    graph = StateGraph()
    graph.add_node({"id": "v1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1})
    graph.add_node({"id": "v2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2})
    graph.add_node({"id": "v3", "type": "set_var", "timestamp": 150, "key": "x", "value": 3})
    graph.add_node({"id": "v4", "type": "set_var", "timestamp": 300, "key": "y", "value": 4})

    pruned = apply_overrides(graph)
    # v2 is the newest for key "x" (timestamp 200 > 150 > 100)
    assert set(pruned) == {"v1", "v3"}
    assert "v1" in graph.pruned
    assert "v3" in graph.pruned
    assert "v2" not in graph.pruned
    assert "v4" not in graph.pruned

    edges = [(src, dst, typ) for src, dst, typ in graph.edges if typ == "supersedes"]
    assert set(edges) == {
        ("v2", "v1", "supersedes"),
        ("v2", "v3", "supersedes")
    }


def test_timestamp_tie_deterministic_tiebreak():
    """When two set_var events share a timestamp, the one appearing later in
    the input list is kept (stable sort tiebreak — deterministic for fixed input)."""
    graph = StateGraph()
    # Both at timestamp 100; "v2" appears later in input list
    graph.add_node({"id": "v1", "type": "set_var", "timestamp": 100, "key": "x", "value": "first"})
    graph.add_node({"id": "v2", "type": "set_var", "timestamp": 100, "key": "x", "value": "second"})

    pruned = apply_overrides(graph)
    # v2 appears later => it is "newest" after stable sort
    assert "v1" in graph.pruned
    assert "v2" not in graph.pruned

    # Running again with same graph (after clearing pruned) must give same result
    graph2 = StateGraph()
    graph2.add_node({"id": "v1", "type": "set_var", "timestamp": 100, "key": "x", "value": "first"})
    graph2.add_node({"id": "v2", "type": "set_var", "timestamp": 100, "key": "x", "value": "second"})
    pruned2 = apply_overrides(graph2)
    assert pruned2 == pruned


def test_set_var_after_abandon_surviving_override():
    """A set_var written after an abandon event on a fresh path is the newest and must survive."""
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "key": "mode", "value": "slow"},
        {"id": "ab1", "type": "abandon", "timestamp": 200, "ref_to": ["e1"]},
        {"id": "e2", "type": "set_var", "timestamp": 300, "key": "mode", "value": "fast"},
    ]
    result = compact_events(events)
    pruned_ids = result["pruned_ids"]
    live_ids = [e["id"] for e in result["compact_events"]]

    # e1 is pruned by the abandon (dead branch sweeper)
    assert "e1" in pruned_ids
    # e2 is the surviving set_var — it must not be pruned by override
    assert "e2" in live_ids
    assert "e2" not in pruned_ids


def test_prune_referenced_values_true_default():
    """Default mode (prune_referenced_values=True): older value pruned even if tool_call references it."""
    events = [
        # set x=1 (older)
        {"id": "sv1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1},
        # tool_call using x=1 in its arguments
        {"id": "tc1", "type": "tool_call", "timestamp": 150, "tool_name": "compute",
         "arguments": {"x": 1}},
        {"id": "tr1", "type": "tool_result", "timestamp": 160, "call_id": "tc1", "result": "ok"},
        # set x=2 (newer, supersedes sv1)
        {"id": "sv2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2},
    ]
    result = compact_events(events, prune_referenced_values=True)
    assert "sv1" in result["pruned_ids"], "Default mode must prune sv1 even though tc1 uses x=1"
    assert "sv2" not in result["pruned_ids"]


def test_prune_referenced_values_false_replay_safe():
    """Replay-safe mode (prune_referenced_values=False): older value retained if tool_call still references it."""
    events = [
        # set x=1 (older)
        {"id": "sv1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1},
        # active tool_call uses x=1
        {"id": "tc1", "type": "tool_call", "timestamp": 150, "tool_name": "compute",
         "arguments": {"x": 1}},
        {"id": "tr1", "type": "tool_result", "timestamp": 160, "call_id": "tc1", "result": "ok"},
        # set x=2 (newer)
        {"id": "sv2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2},
    ]
    result = compact_events(events, prune_referenced_values=False)
    # sv1 must be RETAINED because tc1 (active) has arguments["x"] == 1
    assert "sv1" not in result["pruned_ids"], (
        "Replay-safe mode must retain sv1 because tc1 references x=1"
    )
    assert "sv2" not in result["pruned_ids"]


def test_prune_referenced_values_false_unreferenced_still_pruned():
    """Replay-safe mode: an older value NOT referenced by any tool_call is still pruned."""
    events = [
        {"id": "sv1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1},
        {"id": "sv2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2},
        # No tool_call references x=1
    ]
    result = compact_events(events, prune_referenced_values=False)
    assert "sv1" in result["pruned_ids"], "Unreferenced older values must still be pruned in replay-safe mode"
    assert "sv2" not in result["pruned_ids"]
