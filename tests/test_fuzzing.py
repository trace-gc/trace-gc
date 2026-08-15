import pytest
import time
from tracegc.compactor import compact_events
from tracegc.events import validate_event

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


def test_duplicate_event_id_raises():
    """BUG-1 regression: duplicate event IDs must raise ValueError, not silently overwrite."""
    from tracegc.graph import StateGraph
    graph = StateGraph()
    graph.add_node({"id": "e1", "type": "decision", "timestamp": 100, "content": "first"})
    with pytest.raises(ValueError, match="Duplicate event id"):
        graph.add_node({"id": "e1", "type": "decision", "timestamp": 200, "content": "second"})


def test_compact_events_duplicate_id_raises():
    """BUG-1 regression: compact_events() must raise on duplicate IDs (not produce corrupt output)."""
    events = [
        {"id": "e1", "type": "decision", "timestamp": 100, "parent_id": None, "content": "first"},
        {"id": "e1", "type": "decision", "timestamp": 200, "parent_id": None, "content": "second"},
    ]
    with pytest.raises(ValueError, match="Duplicate event id"):
        compact_events(events)


def test_100k_events_stress_test():
    """WS6: 100K-event linear chain must complete without crash and within memory bounds.
    
    Measures wall-clock time and peak memory. Asserts:
    - No stack overflow (BUG-2 regression)
    - All 100K events survive (no pruning in a clean chain)
    - Completes in < 60s wall-clock (generous bound, not an optimization target)
    - Peak memory < 2GB (generous sanity guard)
    """
    import time
    import tracemalloc

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

    tracemalloc.start()
    t0 = time.perf_counter()
    result = compact_events(events)
    dt = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    retained = len(result["compact_events"])
    pruned = len(result["pruned_ids"])
    receipts = len(result["receipts"])
    peak_mb = peak / (1024 * 1024)

    print(f"\n100K stress: {dt:.2f}s  peak={peak_mb:.1f}MB  retained={retained}  pruned={pruned}  receipts={receipts}")

    assert retained == n, f"Expected {n} retained events, got {retained}"
    assert pruned == 0, f"Expected 0 pruned events, got {pruned}"
    assert dt < 60.0, f"100K events took too long: {dt:.2f}s"
    assert peak_mb < 2048.0, f"Peak memory exceeded 2GB: {peak_mb:.1f}MB"


def test_100k_events_mixed_pruning_stress_test():
    """WS6: 100K events with mixed pruning triggers (abandons, overrides, duplicate tool calls).
    
    Validates performance, memory limits, and correctness of pruning counts at scale.
    """
    import time
    import tracemalloc

    events = []
    
    # 1. Base active linear chain: 48,000 decisions
    events.append({"id": "e0", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Root"})
    for i in range(1, 48000):
        events.append({
            "id": f"e{i}",
            "type": "decision",
            "timestamp": 1000 + i,
            "parent_id": f"e{i-1}",
            "content": f"Step {i}",
        })

    # Last active event ID to attach other events to
    last_active_id = f"e{47999}"

    # 2. Overrides: 10,000 writes to the same key 'x'
    # Keeping the default mode (prune_referenced_values=True), all older 9,999 writes should be pruned, and 1 survives.
    for i in range(10000):
        events.append({
            "id": f"ov_{i}",
            "type": "set_var",
            "timestamp": 100000 + i,
            "parent_id": last_active_id,
            "key": "x",
            "value": i
        })

    # 3. Duplicate tool calls: 20,000 tool calls + 20,000 tool results (40,000 events)
    # Triggers tool-call deduplication. Only 1 pair of each unique signature survives.
    # We will make 10,000 distinct call signatures, each duplicated exactly once.
    # Therefore, 10,000 calls and 10,000 results will be pruned.
    for i in range(10000):
        # First occurrence (will survive)
        events.append({
            "id": f"tc_first_{i}",
            "type": "tool_call",
            "timestamp": 200000 + i * 2,
            "parent_id": last_active_id,
            "tool_name": f"tool_{i}",
            "arguments": {"arg": i}
        })
        events.append({
            "id": f"tr_first_{i}",
            "type": "tool_result",
            "timestamp": 200000 + i * 2 + 1,
            "parent_id": f"tc_first_{i}",
            "call_id": f"tc_first_{i}",
            "result": f"res_{i}"
        })
        # Second occurrence (will be pruned)
        events.append({
            "id": f"tc_second_{i}",
            "type": "tool_call",
            "timestamp": 300000 + i * 2,
            "parent_id": last_active_id,
            "tool_name": f"tool_{i}",
            "arguments": {"arg": i}
        })
        events.append({
            "id": f"tr_second_{i}",
            "type": "tool_result",
            "timestamp": 300000 + i * 2 + 1,
            "parent_id": f"tc_second_{i}",
            "call_id": f"tc_second_{i}",
            "result": f"res_{i}"
        })

    # 4. Abandoned branch: 2,000 nodes total
    # We branch at ab_start_0 -> ab_node_1 -> ... -> ab_node_1999 (all chained sequentially)
    # Then we add an abandon event targeting ab_start_0.
    # The dead-branch sweeper will prune all 2,000 nodes in the sub-branch.
    events.append({
        "id": "ab_start_0",
        "type": "decision",
        "timestamp": 400000,
        "parent_id": last_active_id,
        "content": "abandon start"
    })
    for i in range(1, 1999):
        events.append({
            "id": f"ab_node_{i}",
            "type": "decision",
            "timestamp": 400000 + i,
            "parent_id": f"ab_node_{i-1}" if i > 1 else "ab_start_0",
            "content": f"abandon step {i}"
        })
    # The abandon event itself:
    events.append({
        "id": "ab_trigger",
        "type": "abandon",
        "timestamp": 500000,
        "parent_id": last_active_id,
        "ref_to": ["ab_start_0"]
    })

    # Total events: 48,000 (decisions) + 10,000 (overrides) + 40,000 (dedups) + 2,000 (abandon branch) = 100,000 events
    assert len(events) == 100000

    tracemalloc.start()
    t0 = time.perf_counter()
    result = compact_events(events)
    dt = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    retained = len(result["compact_events"])
    pruned = len(result["pruned_ids"])
    receipts = len(result["receipts"])
    peak_mb = peak / (1024 * 1024)

    # Let's count how many are pruned of each type for verification:
    # 9,999 (superseded set_var) + 20,000 (duplicate tool_calls/results) + 1,999 (abandoned chain) = 31,998 pruned.
    # Let's check: ab_trigger itself has parent_id=last_active_id, which is NOT in the abandoned sub-branch.
    # However, ab_trigger is not targeted by any abandon event, so it survives.
    # So total pruned must be exactly 31,998.
    
    print(f"\n100K mixed stress: {dt:.2f}s  peak={peak_mb:.1f}MB  retained={retained}  pruned={pruned}  receipts={receipts}")

    assert pruned == 31998, f"Expected 31998 pruned events, got {pruned}"
    assert retained == 100000 - 31998, f"Expected {100000 - 31998} retained events, got {retained}"
    assert dt < 60.0, f"100K events took too long: {dt:.2f}s"
    assert peak_mb < 2048.0, f"Peak memory exceeded 2GB: {peak_mb:.1f}MB"

