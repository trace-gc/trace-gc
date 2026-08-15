import os
import pytest
from tracegc.events import load_events_from_json
from tracegc.compactor import compact_events

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_trace.json")

def test_end_to_end_compactor():
    assert os.path.exists(FIXTURE_PATH), f"Fixture not found at {FIXTURE_PATH}"
    events = load_events_from_json(FIXTURE_PATH)
    result = compact_events(events)

    # Basic validations
    assert "prompt" in result
    assert "tokens_before" in result
    assert "tokens_after" in result
    assert "receipts" in result
    assert "pruned_ids" in result
    assert "compact_events" in result
    assert "graph" in result

    # Check token reduction
    assert result["tokens_after"] < result["tokens_before"]
    
    # Receipts check
    receipt_ids = [r["id"] for r in result["receipts"]]
    assert len(receipt_ids) > 0
    # Ensure all receipt dicts have an 'id'
    for r in result["receipts"]:
        assert "id" in r
        assert "type" in r
        assert r["type"] == "receipt"

    # Compacted prompt checks
    prompt = result["prompt"]
    assert "[RECEIPT a01]" in prompt
    assert "[RECEIPT e007]" in prompt
    # Make sure we didn't double-render a receipt
    assert prompt.count("[RECEIPT a01]") == 1

    # Verify pruned ids
    assert "a01" in result["pruned_ids"]
    assert "e007" in result["pruned_ids"]
