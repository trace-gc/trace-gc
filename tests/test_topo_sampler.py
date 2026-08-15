import pytest
import hashlib
from tracegc.graph import StateGraph
from tracegc.topo_sampler import collapse_cycles, _deterministic_cluster_id

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


def test_iterative_tarjan_10k_cycle():
    """BUG-2 regression: a 10K-node cycle must not stack-overflow with the iterative Tarjan implementation."""
    import time
    graph = StateGraph()
    n = 10_000
    for i in range(n):
        graph.add_node({"id": f"c{i}", "type": "decision", "timestamp": i})
    # Create a single large cycle: c0->c1->...->c(n-1)->c0
    for i in range(n - 1):
        graph.add_edge(f"c{i}", f"c{i+1}", "sequence")
    graph.add_edge(f"c{n-1}", "c0", "sequence")

    t0 = time.perf_counter()
    receipt_ids = collapse_cycles(graph)
    dt = time.perf_counter() - t0

    # All n members must be pruned and collapsed into one cluster
    assert len(receipt_ids) == 1
    cluster_id = receipt_ids[0]
    assert cluster_id in graph.nodes
    assert len(graph.pruned) == n
    print(f"10K cycle collapsed in {dt:.3f}s")


def test_iterative_tarjan_100k_linear_chain():
    """BUG-2 regression: a 100K linear chain (no cycles) must complete without stack overflow."""
    import time
    from tracegc.compactor import compact_events
    n = 100_000
    events = [{"id": "e0", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Root"}]
    for i in range(1, n):
        events.append({
            "id": f"e{i}",
            "type": "decision",
            "timestamp": 1000 + i,
            "parent_id": f"e{i-1}",
            "content": f"Step {i}",
        })
    t0 = time.perf_counter()
    result = compact_events(events)
    dt = time.perf_counter() - t0
    assert len(result["compact_events"]) == n, "All events must survive (no cycles, no abandons)"
    assert len(result["pruned_ids"]) == 0
    print(f"100K linear chain completed in {dt:.3f}s")
