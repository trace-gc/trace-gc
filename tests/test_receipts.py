import pytest
import json
from tracegc.graph import StateGraph
from tracegc.receipts import collect_receipts, get_receipt
from tracegc.compactor import compact_events


def test_receipts_basic():
    """Single receipt: recoverable via get_receipt(), invalid ID raises KeyError."""
    graph = StateGraph()
    graph.add_node({"id": "n1", "type": "set_var", "timestamp": 100, "key": "k", "value": "v"})
    graph.add_node({"id": "n2", "type": "decision", "timestamp": 200, "content": "dec"})

    graph.mark_pruned("n1")
    graph.mark_pruned("n2")

    r_list = collect_receipts(graph)
    assert len(r_list) == 2

    recovered1 = get_receipt(graph, "n1")
    assert recovered1["id"] == "n1"
    assert recovered1["pruned"] is True

    recovered2 = get_receipt(graph, "n2")
    assert recovered2["id"] == "n2"
    assert recovered2["pruned"] is True

    with pytest.raises(KeyError):
        get_receipt(graph, "n_unknown")


def test_receipt_ordering_by_timestamp():
    """GAP-4 regression: collect_receipts() must sort by original event timestamp."""
    graph = StateGraph()
    # Add nodes out-of-timestamp order
    graph.add_node({"id": "late", "type": "decision", "timestamp": 500, "content": "late"})
    graph.add_node({"id": "early", "type": "decision", "timestamp": 100, "content": "early"})
    graph.add_node({"id": "mid", "type": "decision", "timestamp": 300, "content": "mid"})

    graph.mark_pruned("late")
    graph.mark_pruned("early")
    graph.mark_pruned("mid")

    receipts = collect_receipts(graph)
    ids = [r["id"] for r in receipts]
    assert ids == ["early", "mid", "late"], f"Expected chronological order, got {ids}"
    # Each stub must carry the original event's timestamp
    assert receipts[0]["timestamp"] == 100
    assert receipts[1]["timestamp"] == 300
    assert receipts[2]["timestamp"] == 500


def test_receipt_mutation_isolation():
    """GAP-5 regression: marking a node pruned must not mutate the original event dict."""
    original_event = {"id": "ev", "type": "decision", "timestamp": 100, "content": "hello"}
    graph = StateGraph()
    graph.add_node(original_event)
    graph.mark_pruned("ev")

    # Original dict must NOT have been mutated
    assert "pruned" not in original_event, (
        "mark_pruned() must not mutate the original event dict"
    )
    # get_receipt() must return a copy WITH pruned=True
    recovered = get_receipt(graph, "ev")
    assert recovered["pruned"] is True
    assert recovered is not original_event, "get_receipt must return a copy, not the original"


def test_receipt_copy_independence():
    """get_receipt() returns an independent copy — mutating it doesn't affect graph.nodes."""
    graph = StateGraph()
    graph.add_node({"id": "ev", "type": "decision", "timestamp": 100, "content": "hello"})
    graph.mark_pruned("ev")

    copy1 = get_receipt(graph, "ev")
    copy2 = get_receipt(graph, "ev")
    copy1["extra_field"] = "mutated"

    # Neither copy2 nor the original graph node should be affected
    assert "extra_field" not in copy2
    assert "extra_field" not in graph.nodes["ev"]


def test_many_receipts_all_recoverable():
    """100+ pruned events must all be individually recoverable with correct content."""
    n = 150
    events = [
        {"id": f"ev{i}", "type": "decision", "timestamp": i * 10, "content": f"step_{i}"}
        for i in range(n)
    ]
    result = compact_events(events)  # no pruning (no abandon/override), just builds graph
    graph = result["graph"]

    # Manually prune all nodes
    for ev in events:
        graph.mark_pruned(ev["id"])

    receipts = collect_receipts(graph)
    assert len(receipts) == n

    for i in range(n):
        recovered = get_receipt(graph, f"ev{i}")
        assert recovered["id"] == f"ev{i}"
        assert recovered["content"] == f"step_{i}"
        assert recovered["pruned"] is True


def test_receipt_json_round_trip():
    """Receipt stubs must be JSON-serializable for persistence."""
    graph = StateGraph()
    graph.add_node({"id": "ev", "type": "set_var", "timestamp": 42, "key": "x", "value": 7})
    graph.mark_pruned("ev")

    stubs = collect_receipts(graph)
    # Must not raise
    serialized = json.dumps(stubs)
    deserialized = json.loads(serialized)
    assert deserialized[0]["id"] == "ev"
    assert deserialized[0]["timestamp"] == 42
