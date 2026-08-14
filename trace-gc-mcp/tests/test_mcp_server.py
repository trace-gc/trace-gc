import pytest
from trace_gc_mcp import TraceGCMCPServer, add_event, compact, get_receipt


def test_mcp_server_end_to_end():
    """Verify add_event -> compact -> get_receipt works end-to-end via TraceGCMCPServer."""
    server = TraceGCMCPServer()
    session_id = "test_session_1"

    # 1. Add events (including an overridden set_var)
    res1 = server.add_event(
        {"id": "e1", "type": "set_var", "timestamp": 100, "key": "model", "value": "v1"},
        session_id=session_id,
    )
    assert res1["status"] == "ok"
    assert res1["event_id"] == "e1"

    res2 = server.add_event(
        {"id": "e2", "type": "set_var", "timestamp": 200, "key": "model", "value": "v2"},
        session_id=session_id,
    )
    assert res2["status"] == "ok"

    # 2. Compact session
    comp_res = server.compact(session_id=session_id)
    assert comp_res["session_id"] == session_id
    assert "e1" in comp_res["pruned_ids"]
    assert "e2" not in comp_res["pruned_ids"]

    # 3. Retrieve receipt
    rcpt_res = server.get_receipt(session_id=session_id, node_id="e1")
    assert rcpt_res["session_id"] == session_id
    assert rcpt_res["node_id"] == "e1"
    assert rcpt_res["receipt"]["pruned"] is True
    assert rcpt_res["receipt"]["key"] == "model"
    assert rcpt_res["receipt"]["value"] == "v1"


def test_mcp_module_level_helpers():
    """Verify global tool wrapper functions work end-to-end."""
    session_id = "global_session"
    add_event({"id": "ev1", "type": "set_var", "timestamp": 10, "key": "x", "value": 1}, session_id=session_id)
    add_event({"id": "ev2", "type": "set_var", "timestamp": 20, "key": "x", "value": 2}, session_id=session_id)

    comp = compact(session_id=session_id)
    assert "ev1" in comp["pruned_ids"]

    receipt = get_receipt(session_id=session_id, node_id="ev1")
    assert receipt["receipt"]["value"] == 1
