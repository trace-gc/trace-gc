import pytest
from trace_gc_storage.canonical import canonical_payload, payload_hash

def test_key_ordering_invariance():
    event_a = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "hello"}]
    event_b = [{"content": "hello", "timestamp": 100, "type": "decision", "id": "e1"}]
    
    assert canonical_payload(event_a) == canonical_payload(event_b)
    assert payload_hash(event_a) == payload_hash(event_b)

def test_array_ordering_sensitivity():
    event_a = [
        {"id": "e1", "type": "decision", "timestamp": 100, "content": "hello"},
        {"id": "e2", "type": "decision", "timestamp": 200, "content": "world"}
    ]
    event_b = [
        {"id": "e2", "type": "decision", "timestamp": 200, "content": "world"},
        {"id": "e1", "type": "decision", "timestamp": 100, "content": "hello"}
    ]
    
    assert canonical_payload(event_a) != canonical_payload(event_b)
    assert payload_hash(event_a) != payload_hash(event_b)

def test_nan_infinity_value_error():
    # Top-level NaN
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_payload([{"id": "e1", "type": "set_var", "timestamp": 100, "key": "x", "value": float("nan")}])
        
    # Nested NaN
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_payload([{"id": "e1", "type": "tool_call", "timestamp": 100, "tool_name": "x", "arguments": {"nested": {"val": float("nan")}}}])

    # Infinity
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_payload([{"id": "e1", "type": "set_var", "timestamp": 100, "key": "x", "value": float("inf")}])

    # Negative Infinity
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_payload([{"id": "e1", "type": "set_var", "timestamp": 100, "key": "x", "value": float("-inf")}])

def test_unicode_preservation_and_stability():
    event_a = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "😊 testing unicode 🚀 \u03c0"}]
    event_b = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "😊 testing unicode 🚀 \u03c0"}]
    
    # Ensure bytes are identical and preserved without escaping
    bytes_a = canonical_payload(event_a)
    assert b"\\u" not in bytes_a
    assert "😊".encode("utf-8") in bytes_a
    
    assert bytes_a == canonical_payload(event_b)
    assert payload_hash(event_a) == payload_hash(event_b)

def test_empty_events_list():
    assert canonical_payload([]) == b"[]"
    assert payload_hash([]) == "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945" # sha256("[]")
