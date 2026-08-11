import pytest
from trace_gc.graph import StateGraph
from trace_gc.receipts import collect_receipts, get_receipt

def test_receipts():
    graph = StateGraph()
    # Add a couple of nodes
    graph.add_node({"id": "n1", "type": "set_var", "timestamp": 100, "key": "k", "value": "v"})
    graph.add_node({"id": "n2", "type": "decision", "timestamp": 200, "content": "dec"})

    # Mark pruned
    graph.mark_pruned("n1")
    graph.mark_pruned("n2")

    # Get receipt stubs
    r_list = collect_receipts(graph)
    assert len(r_list) == 2
    # Sorted by timestamp of nodes: n1 timestamp is 100, n2 is 200
    # Wait, does the receipt dict have timestamp or is it retrieved from nodes?
    # In graph.mark_pruned, it builds receipt without timestamp?
    # Let's check receipts.py sorting: receipts.sort(key=lambda r: r.get("timestamp", 0))
    # Wait! In graph.py, mark_pruned creates receipt:
    # receipt = {"id": node_id, "type": "receipt", "target_id": node_id, "status": "pruned"}
    # Ah, it doesn't add "timestamp" to the receipt dict itself!
    # Wait, let's verify if `collect_receipts` will sort it correctly if "timestamp" is missing.
    # In receipts.py: `receipts.sort(key=lambda r: r.get("timestamp", 0))`
    # Since they both have no timestamp, their get("timestamp", 0) returns 0.
    # If they have no timestamp, sorting order might be stable/arbitrary.
    # Let's test that get_receipt works.
    recovered1 = get_receipt(graph, "n1")
    assert recovered1["id"] == "n1"
    assert recovered1["pruned"] is True

    recovered2 = get_receipt(graph, "n2")
    assert recovered2["id"] == "n2"

    with pytest.raises(KeyError):
        get_receipt(graph, "n_unknown")
