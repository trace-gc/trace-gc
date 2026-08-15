import pytest
import json
from hypothesis import given, strategies as st, settings
from tracegc.graph import StateGraph
from tracegc.compactor import compact_events, _render_event
from tracegc.receipts import get_receipt

@st.composite
def event_trace_strategy(draw):
    """Generates a chronologically valid list of event dictionaries for property tests."""
    num_events = draw(st.integers(min_value=5, max_value=40))
    events = []
    event_ids = []
    tool_call_ids_without_result = []
    keys_used = []
    
    for i in range(num_events):
        event_id = f"e{i:03d}"
        timestamp = 1000 + i * 10
        
        # Decide parent_id (must be an existing ID or None to respect chronology)
        parent_id = None
        if event_ids and draw(st.booleans()):
            parent_id = draw(st.sampled_from(event_ids))
            
        # Choose a type
        allowed_types = ["set_var", "tool_call", "decision"]
        if tool_call_ids_without_result:
            allowed_types.append("tool_result")
        if event_ids:
            allowed_types.append("abandon")
            
        etype = draw(st.sampled_from(allowed_types))
        
        event = {
            "id": event_id,
            "type": etype,
            "timestamp": timestamp,
            "parent_id": parent_id,
        }
        
        if etype == "set_var":
            key = draw(st.sampled_from(["x", "y", "z"]))
            val = draw(st.integers(min_value=1, max_value=100))
            event["key"] = key
            event["value"] = val
            keys_used.append(key)
            
        elif etype == "tool_call":
            tool_name = draw(st.sampled_from(["read", "write", "search"]))
            args = {"param": draw(st.integers(min_value=1, max_value=10))}
            event["tool_name"] = tool_name
            event["arguments"] = args
            tool_call_ids_without_result.append(event_id)
            
        elif etype == "tool_result":
            call_id = draw(st.sampled_from(tool_call_ids_without_result))
            tool_call_ids_without_result.remove(call_id)
            event["call_id"] = call_id
            event["result"] = f"Result of {call_id}"
            
        elif etype == "abandon":
            num_ref = draw(st.integers(min_value=1, max_value=min(3, len(event_ids))))
            ref_to = draw(st.lists(st.sampled_from(event_ids), min_size=num_ref, max_size=num_ref, unique=True))
            event["ref_to"] = ref_to
            
        elif etype == "decision":
            event["content"] = f"Decision at step {i}"
            
        events.append(event)
        event_ids.append(event_id)
        
    # Introduce random sequence cycles (length 3 loop)
    if len(events) >= 5 and draw(st.booleans()):
        start_idx = draw(st.integers(min_value=0, max_value=len(events) - 4))
        id0 = events[start_idx]["id"]
        id1 = events[start_idx+1]["id"]
        id2 = events[start_idx+2]["id"]
        events[start_idx]["parent_id"] = id2
        events[start_idx+1]["parent_id"] = id0
        events[start_idx+2]["parent_id"] = id1

    return events


@given(event_trace_strategy())
def test_compact_events_invariants(events):
    # Run the compaction pipeline
    result1 = compact_events(events)
    result2 = compact_events(events)
    
    prompt = result1["prompt"]
    compact_events_list = result1["compact_events"]
    graph = result1["graph"]
    
    # 1. Determinism check: Running twice produces identical results
    assert result1["prompt"].encode("utf-8") == result2["prompt"].encode("utf-8")
    assert [e["id"] for e in result1["compact_events"]] == [e["id"] for e in result2["compact_events"]]
    assert result1["tokens_before"] == result2["tokens_before"]
    assert result1["tokens_after"] == result2["tokens_after"]
    assert result1["pruned_ids"] == result2["pruned_ids"]
    assert result1["receipts"] == result2["receipts"]
    
    # 2. No pruned node appears in final compacted/rendered prompt output
    # Validate that no surviving event is in graph.pruned
    assert all(ev["id"] not in graph.pruned for ev in compact_events_list)
            
    # 3. Every node in graph.pruned has a corresponding entry in graph.receipts and is resolvable
    for node_id in graph.pruned:
        assert node_id in graph.receipts, f"Pruned node {node_id} is missing receipt"
        recovered = get_receipt(graph, node_id)
        assert recovered["id"] == node_id
        assert recovered.get("pruned") is True, f"Recovered node {node_id} is not marked pruned"

    # 4. Topological order is never violated for sequence dependencies
    # If there is a sequence edge U -> V between surviving nodes, U must precede V
    live_ids = [ev["id"] for ev in compact_events_list]
    id_to_pos = {nid: idx for idx, nid in enumerate(live_ids)}
    
    for src, dst, typ in graph.edges:
        if typ == "sequence" and src in id_to_pos and dst in id_to_pos:
            assert id_to_pos[src] < id_to_pos[dst], f"Sequence dependency violation: {src} must precede {dst}"


@given(event_trace_strategy())
@settings(max_examples=50)
def test_unrelated_active_branch_never_pruned(events):
    """Property: events on an active (non-abandoned) branch are never in graph.pruned.
    
    Builds a minimal active branch (two decision events with no abandonment)
    and appends it to the generated trace. Verifies that none of the active-branch
    events appear in the pruned set after compaction.
    """
    from tracegc.compactor import compact_events
    # Append a guaranteed-active branch of 2 events
    max_ts = max((e["timestamp"] for e in events), default=0)
    active_events = [
        {"id": "_active_root", "type": "decision", "timestamp": max_ts + 1000,
         "parent_id": None, "content": "active root"},
        {"id": "_active_child", "type": "decision", "timestamp": max_ts + 1001,
         "parent_id": "_active_root", "content": "active child"},
    ]
    combined = events + active_events
    result = compact_events(combined)
    graph = result["graph"]
    # The active branch events must not be pruned
    assert "_active_root" not in graph.pruned, "Active root was incorrectly pruned"
    assert "_active_child" not in graph.pruned, "Active child was incorrectly pruned"


@given(
    st.lists(
        st.fixed_dictionaries({
            "id": st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))),
            "type": st.just("decision"),
            "timestamp": st.integers(min_value=0, max_value=10000),
            "parent_id": st.none(),
            "content": st.text(min_size=1, max_size=20),
        }),
        min_size=2,
        max_size=5,
    )
)
def test_invalid_trace_duplicate_id_raises_value_error(raw_events):
    """Property: a trace with duplicate event IDs raises ValueError specifically.
    
    Confirms that the pipeline fails predictably (ValueError) rather than
    producing corrupt output or raising an unrelated exception type.
    """
    from tracegc.compactor import compact_events
    # Force a duplicate by copying the first event
    dup_event = dict(raw_events[0])
    dup_event["timestamp"] = raw_events[0]["timestamp"] + 1  # different data, same ID
    events_with_dup = [raw_events[0], dup_event]  # two events with the same ID
    with pytest.raises(ValueError, match="Duplicate event id"):
        compact_events(events_with_dup)
