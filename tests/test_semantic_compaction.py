import pytest
import json
from trace_gc import compact_events
from trace_gc.events import validate_event
from trace_gc.semantic import extract_semantic_events
from trace_gc.receipts import get_receipt


def test_proposed_active_confirmed_extraction():
    # 1. Natural language defaults to PROPOSED
    events_raw = [
        {"id": "e1", "type": "text_chunk", "timestamp": 100, "content": "I will use PostgreSQL."},
        {"id": "e2", "type": "text_chunk", "timestamp": 110, "content": "I am considering Redis."},
    ]
    # Extract semantic events
    extracted = []
    for ev in events_raw:
        extracted.extend(extract_semantic_events(ev["content"], ev["id"], ev["timestamp"]))

    assert len(extracted) == 2
    assert extracted[0]["value"] == "postgresql"
    assert extracted[0]["status"] == "PROPOSED"
    assert extracted[1]["value"] == "redis"
    assert extracted[1]["status"] == "PROPOSED"


def test_active_confirmation_policy():
    # 2. ACTIVE requires execution evidence, otherwise demoted to PROPOSED
    # Case A: Natural language configured PostgreSQL, but NO execution evidence -> PROPOSED
    events_no_evidence = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "postgresql", "status": "ACTIVE", "source_text": "Configured PostgreSQL."},
    ]
    res_no = compact_events(events_no_evidence)
    # The status in graph should be demoted to PROPOSED
    assert res_no["graph"].decision_status["e1"] == "PROPOSED"

    # Case B: With execution evidence -> ACTIVE preserved
    events_with_evidence = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "postgresql", "status": "ACTIVE", "source_text": "Configured PostgreSQL."},
        {"id": "tc1", "type": "command_run", "timestamp": 110, "parent_id": "e1", "command": "pg_ctl status", "exit_code": 0},
    ]
    res_with = compact_events(events_with_evidence)
    assert res_with["graph"].decision_status["e1"] == "ACTIVE"


def test_confirmed_status_parsing():
    # 3. Connection verified successfully -> CONFIRMED
    events = [
        {"id": "e1", "type": "text_chunk", "timestamp": 100, "content": "PostgreSQL connection verified successfully."},
    ]
    extracted = extract_semantic_events(events[0]["content"], "e1", 100)
    assert len(extracted) == 1
    assert extracted[0]["value"] == "postgresql"
    assert extracted[0]["status"] == "CONFIRMED"


def test_failed_and_abandoned_states():
    # 4 & 5. FAILED and ABANDONED states
    events_raw = [
        {"id": "e1", "type": "text_chunk", "timestamp": 100, "content": "redis configuration failed"},
        {"id": "e2", "type": "text_chunk", "timestamp": 110, "content": "abandoned postgresql"},
    ]
    extracted = []
    for ev in events_raw:
        extracted.extend(extract_semantic_events(ev["content"], ev["id"], ev["timestamp"]))

    assert extracted[0]["value"] == "redis"
    assert extracted[0]["status"] == "FAILED"
    assert extracted[1]["value"] == "postgresql"
    assert extracted[1]["status"] == "ABANDONED"


def test_superseded_state_pruning():
    # 6. Superseded state pruning
    # Redis fails, switch to PostgreSQL which connection is verified
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "redis", "status": "FAILED", "source_text": "redis configuration failed"},
        {"id": "e2", "type": "set_var", "timestamp": 110, "parent_id": "e1", "key": "database", "value": "postgresql", "status": "CONFIRMED", "source_text": "PostgreSQL connection verified"},
    ]
    res = compact_events(events)
    # redis is marked SUPERSEDED and pruned
    assert "e1" in res["pruned_ids"]
    assert "e2" not in res["pruned_ids"]
    
    # Verify receipt recovery of the superseded redis
    recovered = get_receipt(res["graph"], "e1")
    assert recovered["status"] == "SUPERSEDED"


def test_semantic_duplicate_pruning():
    # 7. Semantic duplicates are pruned, keeping the first and recording provenance
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "postgresql", "status": "CONFIRMED", "source_text": "Verified Postgres."},
        {"id": "e2", "type": "set_var", "timestamp": 110, "parent_id": "e1", "key": "database", "value": "postgresql", "status": "CONFIRMED", "source_text": "Postgres connection verified successfully."},
    ]
    res = compact_events(events)
    # e2 is pruned as duplicate
    assert "e2" in res["pruned_ids"]
    assert "e1" not in res["pruned_ids"]

    # Provenance list contains info of the merged duplicate
    surviving = res["compact_events"][0]
    assert "provenance" in surviving
    assert "normalized_sources" in surviving["provenance"]
    assert surviving["provenance"]["normalized_sources"][0]["id"] == "e2"


def test_semantic_normalization():
    # 8. PostgreSQL, postgres, PostgreSQL all normalize to postgresql
    texts = [
        "Use Postgres.",
        "Switch database to PostgreSQL.",
        "Move to postgresql"
    ]
    for text in texts:
        extracted = extract_semantic_events(text, "test", 100)
        assert len(extracted) == 1
        assert extracted[0]["value"] == "postgresql"


def test_provenance_preservation():
    # 9. Provenance preservation
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "postgresql", "status": "CONFIRMED", "source_text": "postgres verified"},
    ]
    res = compact_events(events)
    assert res["compact_events"][0]["source_text"] == "postgres verified"


def test_receipt_recovery():
    # 10. Receipt recovery retains all metadata and marks copies as pruned
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "redis", "status": "FAILED", "source_text": "redis failed"},
        {"id": "e2", "type": "set_var", "timestamp": 110, "parent_id": "e1", "key": "database", "value": "postgresql", "status": "CONFIRMED", "source_text": "postgres verified"},
    ]
    res = compact_events(events)
    recovered = get_receipt(res["graph"], "e1")
    assert recovered["pruned"] is True
    assert recovered["value"] == "redis"
    # Internal graph node not mutated
    assert "pruned" not in res["graph"].nodes["e1"]


def test_resolved_error_pruning():
    # 11. Resolved error pruning
    # test command fails first, then succeeds later
    events = [
        {"id": "e1", "type": "command_run", "timestamp": 100, "parent_id": None, "command": "pytest tests/a.py", "exit_code": 1},
        {"id": "e2", "type": "command_run", "timestamp": 110, "parent_id": "e1", "command": "pytest tests/a.py", "exit_code": 0},
    ]
    res = compact_events(events)
    assert "e1" in res["pruned_ids"]
    assert "e2" not in res["pruned_ids"]


def test_ambiguous_and_low_confidence_retention():
    # 12 & 13. Ambiguous statements and low-confidence extractions are kept
    events_raw = [
        {"id": "e1", "type": "text_chunk", "timestamp": 100, "content": "I am thinking about lunch today."},
    ]
    extracted = extract_semantic_events(events_raw[0]["content"], "e1", 100)
    assert len(extracted) == 1
    assert extracted[0]["type"] == "text_chunk"  # Fallback to text_chunk
    assert extracted[0]["content"] == "I am thinking about lunch today."


def test_false_pruning_prevention():
    # 14. Prevent pruning if referenced by active nodes or constraints
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "redis", "status": "FAILED", "source_text": "redis failed"},
        # Constraint explicitly references e1
        {"id": "c1", "type": "constraint", "timestamp": 105, "parent_id": "e1", "ref_to": ["e1"], "content": "Do not use redis ever again"},
        {"id": "e2", "type": "set_var", "timestamp": 110, "parent_id": "c1", "key": "database", "value": "postgresql", "status": "CONFIRMED", "source_text": "postgres verified"},
    ]
    res = compact_events(events)
    # e1 should NOT be pruned because it is referenced in ref_to of c1!
    assert "e1" not in res["pruned_ids"]


def test_determinism_and_serialization_roundtrip():
    # 15 & 16. Deterministic output and serialization round-trip
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "parent_id": None, "key": "database", "value": "redis", "status": "FAILED", "source_text": "redis failed"},
        {"id": "e2", "type": "set_var", "timestamp": 110, "parent_id": "e1", "key": "database", "value": "postgresql", "status": "CONFIRMED", "source_text": "postgres verified"},
    ]
    res1 = compact_events(events)
    res2 = compact_events(events)
    
    assert res1["prompt"] == res2["prompt"]
    assert res1["pruned_ids"] == res2["pruned_ids"]

    # Serialization check
    serialized = json.dumps(events)
    deserialized = json.loads(serialized)
    res_serialized = compact_events(deserialized)
    assert res1["prompt"] == res_serialized["prompt"]
    assert res1["pruned_ids"] == res_serialized["pruned_ids"]


def test_semantic_non_equivalence_retention():
    # Test cases where semantic similarity does NOT mean equivalence
    # We should not prune different statements about postgresql
    from trace_gc.api import compact
    messages = [
        {"role": "user", "content": "Considering PostgreSQL."},
        {"role": "user", "content": "Do not use PostgreSQL."},
        {"role": "user", "content": "PostgreSQL failed."},
        {"role": "user", "content": "PostgreSQL is required by the customer."}
    ]
    res = compact(messages, semantic_extraction=True)
    # Since these are conflicting/negated/required, they shouldn't be pruned as simple duplicates.
    # Particularly "Do not use" and "required by the customer" must both survive!
    surviving_content = [m.get("content", "") for m in res.messages if isinstance(m, dict)]
    assert any("Do not use" in c for c in surviving_content)
    assert any("required" in c for c in surviving_content)


def test_semantic_incremental_cache():
    # Test incremental extraction cache lookup and version invalidation
    from trace_gc.api import SemanticCache, compact
    
    cache = SemanticCache(parser_version="1.0.0")
    messages = [
        "Setup postgresql connection",
        "Setup postgresql connection" # duplicate text
    ]
    
    # First run: empty cache, should populate
    res1 = compact(messages, semantic_extraction=True, cache=cache)
    assert len(cache.entries) > 0
    
    # Second run: should hit cache
    # Let's count calls by stubbing/wrapping extract_semantic_events or modifying the cached value
    # Let's modify the cached representation to prove it was loaded from cache!
    cached_key = list(cache.entries.keys())[0]
    cache.entries[cached_key]["semantic_representation"] = [
        {"id": "cached_e1", "type": "set_var", "key": "database", "value": "cached_db", "status": "ACTIVE"}
    ]
    
    res2 = compact(messages, semantic_extraction=True, cache=cache)
    # The active graph nodes should contain the cached technology choice cached_db
    active_values = [
        ev.get("value") for nid, ev in res2._graph.nodes.items()
        if nid not in res2._graph.pruned and ev.get("type") == "set_var"
    ]
    assert "cached_db" in active_values
    
    # Third run: invalid version, should re-parse and ignore cache
    cache.parser_version = "2.0.0"
    res3 = compact(messages, semantic_extraction=True, cache=cache)
    assert "cached_db" not in res3.messages[0]
    assert "postgresql" in res3.messages[0]


def test_obsolete_file_reads_pruning():
    # Test Rule 5C: obsolete file reads are pruned if a subsequent edit happens on the same file
    from trace_gc.compactor import compact_events
    events = [
        {"id": "e1", "type": "tool_call", "timestamp": 100, "parent_id": None, "tool_name": "read_file", "arguments": {"path": "src/app.py"}},
        {"id": "tr1", "type": "tool_result", "timestamp": 101, "parent_id": "e1", "call_id": "e1", "result": "def main(): pass"},
        {"id": "e2", "type": "tool_call", "timestamp": 110, "parent_id": "tr1", "tool_name": "write_file", "arguments": {"path": "src/app.py", "content": "def main(): print('hello')"}},
        {"id": "tr2", "type": "tool_result", "timestamp": 111, "parent_id": "e2", "call_id": "e2", "result": "success"}
    ]
    res = compact_events(events, prune_obsolete_reads=True)
    # The read call e1 and its result tr1 should be pruned!
    assert "e1" in res["pruned_ids"]
    assert "tr1" in res["pruned_ids"]
    assert "e2" not in res["pruned_ids"]
    assert "tr2" not in res["pruned_ids"]


def test_redundant_verifications_pruning():
    # Test Rule 5F: older successful verification runs are pruned if a newer success happens
    from trace_gc.compactor import compact_events
    events = [
        {"id": "e1", "type": "command_run", "timestamp": 100, "parent_id": None, "command": "pytest tests/a.py", "exit_code": 0},
        {"id": "tr1", "type": "tool_result", "timestamp": 101, "parent_id": "e1", "call_id": "e1", "result": "1 passed"},
        {"id": "e2", "type": "command_run", "timestamp": 110, "parent_id": "tr1", "command": "pytest tests/a.py", "exit_code": 0},
        {"id": "tr2", "type": "tool_result", "timestamp": 111, "parent_id": "e2", "call_id": "e2", "result": "1 passed"}
    ]
    res = compact_events(events, prune_redundant_verifications=True)
    # The older command run e1 and its result tr1 should be pruned!
    assert "e1" in res["pruned_ids"]
    assert "tr1" in res["pruned_ids"]
    assert "e2" not in res["pruned_ids"]
    assert "tr2" not in res["pruned_ids"]
