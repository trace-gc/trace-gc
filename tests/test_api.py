import pytest
from tracegc import TraceGC

def test_tracegc_incremental():
    client = TraceGC()
    
    # 16 events added incrementally
    events = [
        {"id": "e001", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Start config"},
        {"id": "e002", "type": "set_var", "timestamp": 1010, "parent_id": "e001", "key": "x", "value": 10},
        {"id": "e003", "type": "set_var", "timestamp": 1020, "parent_id": "e002", "key": "y", "value": 20},
        {"id": "e004", "type": "set_var", "timestamp": 1030, "parent_id": "e003", "key": "x", "value": 15},  # overrides x=10
        {"id": "e005", "type": "decision", "timestamp": 1040, "parent_id": "e004", "content": "Start attempt 1"},
        {"id": "tc01", "type": "tool_call", "timestamp": 1050, "parent_id": "e005", "tool_name": "read", "arguments": {"file": "a.txt"}},
        {"id": "tr01", "type": "tool_result", "timestamp": 1060, "parent_id": "tc01", "call_id": "tc01", "result": "content a"},
        {"id": "e006", "type": "set_var", "timestamp": 1070, "parent_id": "tr01", "key": "z", "value": 30},
        {"id": "e007", "type": "decision", "timestamp": 1080, "parent_id": "e006", "content": "Attempt 1 failed"},
        {"id": "ab01", "type": "abandon", "timestamp": 1090, "parent_id": "e007", "ref_to": ["e005"]},  # abandons attempt 1
        {"id": "e008", "type": "decision", "timestamp": 1100, "parent_id": "e004", "content": "Start attempt 2"},
        {"id": "tc02", "type": "tool_call", "timestamp": 1110, "parent_id": "e008", "tool_name": "list", "arguments": {"dir": "src"}},
        {"id": "tr02", "type": "tool_result", "timestamp": 1120, "parent_id": "tc02", "call_id": "tc02", "result": ["main.py"]},
        {"id": "tc03", "type": "tool_call", "timestamp": 1130, "parent_id": "tr02", "tool_name": "list", "arguments": {"dir": "src"}},  # duplicate tool call!
        {"id": "tr03", "type": "tool_result", "timestamp": 1140, "parent_id": "tc03", "call_id": "tc03", "result": ["main.py"]},  # duplicate tool result!
        {"id": "e009", "type": "decision", "timestamp": 1150, "parent_id": "tr03", "content": "Success config"}
    ]
    
    for ev in events:
        client.add_event(ev)
        
    assert len(client.events) == 16
    
    # Run compaction
    result = client.compact()
    
    # Check shape
    assert "prompt" in result
    assert "tokens_before" in result
    assert "tokens_after" in result
    assert "receipts" in result
    assert "pruned_ids" in result
    assert "compact_events" in result
    
    # Check tokens are reduced
    assert result["tokens_after"] < result["tokens_before"]
    
    # Check pruned IDs contain expected overridden, abandoned, and deduplicated nodes
    pruned = result["pruned_ids"]
    assert "e002" in pruned  # Overridden x=10
    assert "e005" in pruned  # Abandoned branch nodes
    assert "tc01" in pruned
    assert "tr01" in pruned
    assert "e006" in pruned
    assert "e007" in pruned
    assert "ab01" in pruned
    assert "tc03" in pruned  # Deduplicated tool call
    assert "tr03" in pruned  # Deduplicated tool result
    
    # Check prompt has receipts but not raw pruned contents
    prompt = result["prompt"]
    assert "x = 10" not in prompt
    assert "x = 15" in prompt
    assert "content a" not in prompt
    assert "[RECEIPT e002]" in prompt
    
    # Verify receipts are resolvable through get_receipt wrapper
    for pid in pruned:
        rcpt = client.get_receipt(pid)
        assert rcpt["id"] == pid
        assert rcpt.get("pruned") is True


def test_tracegc_nonexistent_parent_raises():
    client = TraceGC()
    client.add_event({"id": "e001", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Root"})
    
    with pytest.raises(ValueError) as excinfo:
        client.add_event({
            "id": "e002",
            "type": "decision",
            "timestamp": 1010,
            "parent_id": "nonexistent_parent",
            "content": "Child"
        })
    assert "parent_id 'nonexistent_parent' not found in graph — events must be added in dependency order" in str(excinfo.value)


def test_tracegc_sqlite_store_persistence(tmp_path):
    """Verify TraceGC client using SQLiteStore persists events and compactions across new client instances."""
    from tracegc_storage import SQLiteStore

    db_file = str(tmp_path / "agent_persistence.db")
    ctx_id = "sess_sqlite_test"

    # Instance 1: Create client, add events, and compact
    store1 = SQLiteStore(db_file)
    client1 = TraceGC(store=store1, context_id=ctx_id)
    client1.add_event({"id": "v1", "type": "set_var", "timestamp": 100, "key": "k", "value": 1})
    client1.add_event({"id": "v2", "type": "set_var", "timestamp": 200, "key": "k", "value": 2})

    res1 = client1.compact()
    assert "v1" in res1["pruned_ids"]
    assert "v2" not in res1["pruned_ids"]

    # Instance 2: New client instance pointed at same DB file and context_id
    store2 = SQLiteStore(db_file)
    client2 = TraceGC(store=store2, context_id=ctx_id)
    res2 = client2.compact()

    assert "v1" in res2["pruned_ids"]
    assert len(res2["compact_events"]) == 1
    assert res2["compact_events"][0]["value"] == 2

    # Receipt retrieval on fresh instance
    rcpt = client2.get_receipt("v1")
    assert rcpt["id"] == "v1"
    assert rcpt["pruned"] is True
    assert rcpt["value"] == 1

