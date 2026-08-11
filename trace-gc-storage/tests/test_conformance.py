import pytest
import time
from trace_gc_storage.memory_store import MemoryStore
from trace_gc_storage.sqlite_store import SQLiteStore
from trace_gc_storage.errors import (
    UnknownContextError,
    ExpiredContextError,
    ContextPurgedError,
    ReceiptNotFoundError,
    IdempotencyConflictError
)

@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield MemoryStore()
    else:
        db_file = tmp_path / "test.db"
        s = SQLiteStore(str(db_file))
        yield s
        s.close()

def test_create_and_exists(store):
    cid = store.create()
    assert cid is not None
    assert store.exists(cid)
    assert not store.exists("nonexistent")

def test_create_already_exists_noop(store):
    cid = store.create("custom-id")
    store.append(cid, [{"id": "e1", "type": "decision", "timestamp": 100, "content": "dec 1"}])
    assert len(store.load_events(cid)) == 1

    # Re-create should be a silent no-op and preserve data
    cid2 = store.create("custom-id")
    assert cid2 == "custom-id"
    assert len(store.load_events(cid)) == 1

def test_idempotency_replay_and_conflict(store):
    cid = store.create()

    events_1 = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "dec 1"}]
    events_2 = [{"id": "e2", "type": "decision", "timestamp": 101, "content": "dec 2"}]

    # First append
    res1 = store.append(cid, events_1, request_id="req1")
    assert res1["event_count"] == 1
    assert res1["replayed"] is False
    assert res1["first_sequence"] == 1
    assert res1["last_sequence"] == 1

    # Replay of first append (same request_id, same payload)
    res2 = store.append(cid, events_1, request_id="req1")
    assert res2["event_count"] == 1
    assert res2["replayed"] is True
    assert res2["first_sequence"] == 1
    assert res2["last_sequence"] == 1

    # Conflict append (same request_id, different payload)
    with pytest.raises(IdempotencyConflictError):
        store.append(cid, events_2, request_id="req1")

def test_sequence_contiguity(store):
    cid = store.create()

    events_1 = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "dec 1"}]
    events_2 = [
        {"id": "e2", "type": "decision", "timestamp": 101, "content": "dec 2"},
        {"id": "e3", "type": "decision", "timestamp": 102, "content": "dec 3"}
    ]

    res1 = store.append(cid, events_1)
    res2 = store.append(cid, events_2)

    assert res1["first_sequence"] == 1
    assert res1["last_sequence"] == 1
    assert res2["first_sequence"] == 2
    assert res2["last_sequence"] == 3

    # Check committed sequences inside context
    committed = store.load_events(cid)
    assert len(committed) == 3
    assert [ev["sequence"] for ev in committed] == [1, 2, 3]

def test_duplicate_event_id_rejection(store):
    cid = store.create()

    events_1 = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "dec 1"}]
    events_2 = [{"id": "e1", "type": "decision", "timestamp": 101, "content": "dup dec 1"}]

    store.append(cid, events_1)
    
    # Duplicate ID from prior commit -> must reject whole batch
    with pytest.raises(ValueError, match="Duplicate event ID within context"):
        store.append(cid, events_2)

    # Validate atomicity: no new events or sequence increment
    assert len(store.load_events(cid)) == 1

def test_compaction_staleness(store):
    cid = store.create()

    events_1 = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "dec 1"}]
    store.append(cid, events_1)

    record = {
        "context_id": cid,
        "compaction_id": "c1",
        "snapshot_sequence": 1,
        "latest_sequence_at_read": 1,
        "stale": False,
        "result_schema_version": 1,
        "result_json": "{}",
        "created_at": "2026-08-09T00:00:00Z"
    }

    store.save_compaction(cid, record)
    comp = store.get_latest_compaction(cid)
    assert comp is not None
    assert comp["stale"] is False

    # Append new event
    events_2 = [{"id": "e2", "type": "decision", "timestamp": 101, "content": "dec 2"}]
    store.append(cid, events_2)

    # Now compaction is stale
    comp2 = store.get_latest_compaction(cid)
    assert comp2["stale"] is True

    # Save fresh compaction at N+1
    record_fresh = {
        "context_id": cid,
        "compaction_id": "c2",
        "snapshot_sequence": 2,
        "latest_sequence_at_read": 2,
        "stale": False,
        "result_schema_version": 1,
        "result_json": "{}",
        "created_at": "2026-08-09T00:01:00Z"
    }
    store.save_compaction(cid, record_fresh)
    comp3 = store.get_latest_compaction(cid)
    assert comp3["stale"] is False

def test_lifecycle_and_reads(store):
    cid = store.create()

    events_1 = [{"id": "e1", "type": "decision", "timestamp": 100, "content": "dec 1"}]
    store.append(cid, events_1)

    # 1. Expire context
    store.expire(cid)
    
    # Appends are blocked
    with pytest.raises(ExpiredContextError):
        store.append(cid, [{"id": "e2", "type": "decision", "timestamp": 101, "content": "dec 2"}])

    # Reads are allowed
    assert len(store.load_events(cid)) == 1
    assert store.get_receipt(cid, "e1")["id"] == "e1"
    assert store.get_metadata(cid)["status"] == "expired"

    # 2. Mark purge eligible
    store.mark_purge_eligible(cid)
    
    # Appends still blocked
    with pytest.raises(ExpiredContextError):
        store.append(cid, [{"id": "e2", "type": "decision", "timestamp": 101, "content": "dec 2"}])

    # Reads still allowed
    assert len(store.load_events(cid)) == 1
    assert store.get_receipt(cid, "e1")["id"] == "e1"
    assert store.get_metadata(cid)["status"] == "purge_eligible"

    # 3. Purge context
    store.purge(cid)

    # Any read or write raises ContextPurgedError
    with pytest.raises(ContextPurgedError):
        store.append(cid, events_1)
    with pytest.raises(ContextPurgedError):
        store.load_events(cid)
    with pytest.raises(ContextPurgedError):
        store.get_receipt(cid, "e1")
    with pytest.raises(ContextPurgedError):
        store.get_metadata(cid)

def test_unknown_context_error(store):
    cid = "unknown-id"

    with pytest.raises(UnknownContextError):
        store.append(cid, [])
    with pytest.raises(UnknownContextError):
        store.load_events(cid)
    with pytest.raises(UnknownContextError):
        store.get_receipt(cid, "e1")
    with pytest.raises(UnknownContextError):
        store.save_compaction(cid, {})
    with pytest.raises(UnknownContextError):
        store.get_metadata(cid)
    with pytest.raises(UnknownContextError):
        store.touch(cid)

def test_touch_and_metadata(store):
    cid = store.create()

    meta1 = store.get_metadata(cid)
    assert meta1["status"] == "active"
    assert meta1["latest_sequence"] == 0

    t1 = meta1["last_accessed_at"]
    
    time.sleep(1)
    store.touch(cid)
    
    meta2 = store.get_metadata(cid)
    assert meta2["last_accessed_at"] != t1
