import pytest
import hashlib
from trace_gc.graph import StateGraph
from trace_gc.topo_sampler import collapse_cycles, _deterministic_cluster_id

def test_deterministic_cluster_id():
    members = ["c", "a", "b"]
    cid1 = _deterministic_cluster_id(members)
    cid2 = _deterministic_cluster_id(["b", "c", "a"])
    assert cid1 == cid2
    # Verify prefix and format
    assert cid1.startswith("cluster_")
    # Verify hash content (SHA-256 over "a,b,c")
    expected_hash = hashlib.sha256(b"a,b,c").hexdigest()[:12]
    assert cid1 == f"cluster_{expected_hash}"

def test_collapse_cycles():
    graph = StateGraph()
    # Create nodes: A -> B -> C -> A
    graph.add_node({"id": "A", "type": "decision", "timestamp": 100})
    graph.add_node({"id": "B", "type": "tool_call", "timestamp": 200})
    graph.add_node({"id": "C", "type": "tool_result", "timestamp": 300})
    graph.add_node({"id": "D", "type": "decision", "timestamp": 400}) # outside cycle

    graph.add_edge("A", "B", "sequence")
    graph.add_edge("B", "C", "sequence")
    graph.add_edge("C", "A", "sequence")
    graph.add_edge("C", "D", "sequence")

    pruned = collapse_cycles(graph)

    # A, B, C form an SCC and should be collapsed.
    expected_cid = _deterministic_cluster_id(["A", "B", "C"])
    assert expected_cid in pruned
    assert expected_cid in graph.nodes
    
    # Members should be marked pruned
    assert "A" in graph.pruned
    assert "B" in graph.pruned
    assert "C" in graph.pruned
    assert "D" not in graph.pruned

    # Intra-SCC edges should be removed
    sequence_edges = [(src, dst) for src, dst, typ in graph.edges if typ == "sequence"]
    assert ("A", "B") not in sequence_edges
    assert ("B", "C") not in sequence_edges
    assert ("C", "A") not in sequence_edges
    
    # Receipt node should connect to the earliest member (A)
    assert (expected_cid, "A") in sequence_edges
    # Edges to outside should remain
    assert ("C", "D") in sequence_edges
