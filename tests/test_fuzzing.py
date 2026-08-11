import pytest
import time
from trace_gc.compactor import compact_events
from trace_gc.events import validate_event

def test_missing_required_fields():
    # Missing 'timestamp' and 'key'/'value' for set_var
    malformed = {"id": "e1", "type": "set_var"}
    with pytest.raises(ValueError) as excinfo:
        validate_event(malformed)
    assert "Missing required fields" in str(excinfo.value)


def test_invalid_type_value():
    # Invalid event type
    malformed = {"id": "e1", "type": "invalid_type", "timestamp": 100}
    with pytest.raises(ValueError) as excinfo:
        validate_event(malformed)
    assert "Unsupported event type" in str(excinfo.value)


def test_nonexistent_parent_id():
    # parent_id references non-existent ID
    events = [
        {"id": "e001", "type": "decision", "timestamp": 100, "parent_id": None, "content": "Start"},
        {"id": "e002", "type": "decision", "timestamp": 200, "parent_id": "non_existent_id", "content": "Invalid parent"}
    ]
    with pytest.raises(ValueError) as excinfo:
        compact_events(events)
    assert "references a non-existent parent_id" in str(excinfo.value)
    assert "e002" in str(excinfo.value)
    assert "non_existent_id" in str(excinfo.value)


def test_deeply_nested_parent_id_chain():
    # Create 800 events chained sequentially to stress recursion limits
    events = []
    events.append({"id": "e0", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Root"})
    for i in range(1, 800):
        events.append({
            "id": f"e{i}",
            "type": "decision",
            "timestamp": 1000 + i,
            "parent_id": f"e{i-1}",
            "content": f"Node {i}"
        })
    
    # Should run successfully without blowing the recursion limit
    result = compact_events(events)
    assert len(result["compact_events"]) == 800


def test_huge_trace_performance_and_linearity():
    # Benchmark performance on 1,000 and 10,000 events
    def make_chain(size):
        events = []
        events.append({"id": "e0", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Root"})
        for i in range(1, size):
            events.append({
                "id": f"e{i}",
                "type": "decision",
                "timestamp": 1000 + i,
                "parent_id": f"e{i-1}",
                "content": f"Node {i}"
            })
        return events

    trace_small = make_chain(1000)
    trace_large = make_chain(10000)

    # Warmup
    compact_events(trace_small)

    t0 = time.perf_counter()
    compact_events(trace_small)
    dt_small = time.perf_counter() - t0

    t0 = time.perf_counter()
    compact_events(trace_large)
    dt_large = time.perf_counter() - t0

    print(f"1,000 events took:  {dt_small:.4f}s")
    print(f"10,000 events took: {dt_large:.4f}s")

    # Verify that execution time for 10,000 events is reasonable (less than 5.0 seconds)
    # and scaling is approximately linear or O(N log N).
    assert dt_large < 5.0, f"10k trace took too long: {dt_large:.4f}s"
    
    # The ratio of large to small shouldn't be extremely high (e.g. not quadratic O(N^2), so ratio should be < 20x for 10x size increase)
    if dt_small > 0.001:  # Avoid division by very small numbers
        ratio = dt_large / dt_small
        assert ratio < 20.0, f"Performance scaling is worse than O(N log N), ratio: {ratio:.1f}"
