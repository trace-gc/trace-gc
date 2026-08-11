import pytest
from trace_gc.graph import StateGraph
from trace_gc.override_engine import apply_overrides

def test_apply_overrides():
    graph = StateGraph()
    graph.add_node({"id": "v1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1})
    graph.add_node({"id": "v2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2})
    graph.add_node({"id": "v3", "type": "set_var", "timestamp": 150, "key": "x", "value": 3})
    graph.add_node({"id": "v4", "type": "set_var", "timestamp": 300, "key": "y", "value": 4})

    pruned = apply_overrides(graph)
    # v2 is the newest for key "x" (timestamp 200 > 150 > 100)
    # v1 (100) and v3 (150) should be pruned
    assert set(pruned) == {"v1", "v3"}
    assert "v1" in graph.pruned
    assert "v3" in graph.pruned
    assert "v2" not in graph.pruned
    assert "v4" not in graph.pruned

    # Supersedes edges should go from newest to older
    edges = [(src, dst, typ) for src, dst, typ in graph.edges if typ == "supersedes"]
    assert set(edges) == {
        ("v2", "v1", "supersedes"),
        ("v2", "v3", "supersedes")
    }
