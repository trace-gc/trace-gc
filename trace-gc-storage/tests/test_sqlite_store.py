import pytest
import threading
import time
import os
from trace_gc_storage.sqlite_store import SQLiteStore
from trace_gc_storage.errors import ContextPurgedError, ExpiredContextError

def test_sqlite_concurrency(tmp_path):
    db_file = str(tmp_path / "concurrency.db")
    
    # Initialize the store/database on main thread
    init_store = SQLiteStore(db_file)
    cid = init_store.create()
    init_store.close()

    num_threads = 5
    events_per_thread = 20

    def worker(thread_idx):
        # Each worker thread gets its own connection/store instance
        store = SQLiteStore(db_file)
        try:
            for i in range(events_per_thread):
                ev_id = f"t{thread_idx}-e{i}"
                store.append(cid, [{"id": ev_id, "type": "decision", "timestamp": 100, "content": f"t{thread_idx} {i}"}])
        finally:
            store.close()

    threads = []
    for t_idx in range(num_threads):
        t = threading.Thread(target=worker, args=(t_idx,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Re-open on main thread to check results
    verify_store = SQLiteStore(db_file)
    try:
        committed = verify_store.load_events(cid)
        total_events = num_threads * events_per_thread
        assert len(committed) == total_events
        
        sequences = sorted([ev["sequence"] for ev in committed])
        assert sequences == list(range(1, total_events + 1))
    finally:
        verify_store.close()

def test_sqlite_restart_recovery(tmp_path):
    db_file = str(tmp_path / "recovery.db")

    # Instance 1: write data
    store1 = SQLiteStore(db_file)
    cid = store1.create()
    store1.append(cid, [{"id": "e1", "type": "decision", "timestamp": 100, "content": "hello"}])
    
    record = {
        "context_id": cid,
        "compaction_id": "comp-1",
        "snapshot_sequence": 1,
        "latest_sequence_at_read": 1,
        "stale": False,
        "result_schema_version": 1,
        "result_json": "{}",
        "created_at": "2026-08-09T00:00:00Z"
    }
    store1.save_compaction(cid, record)
    store1.close()

    # Instance 2: read same database file
    store2 = SQLiteStore(db_file)
    try:
        events = store2.load_events(cid)
        assert len(events) == 1
        assert events[0]["id"] == "e1"
        assert events[0]["content"] == "hello"

        receipt = store2.get_receipt(cid, "e1")
        assert receipt["content"] == "hello"

        comp = store2.get_latest_compaction(cid)
        assert comp is not None
        assert comp["compaction_id"] == "comp-1"
    finally:
        store2.close()

def test_sqlite_simulated_rollback(tmp_path):
    db_file = str(tmp_path / "rollback.db")
    store = SQLiteStore(db_file)
    cid = store.create()

    # Successful append 1 (sequence 1)
    res1 = store.append(cid, [{"id": "e1", "type": "decision", "timestamp": 100, "content": "dec 1"}])
    assert res1["last_sequence"] == 1

    # Simulated failing append (invalid field type in schema validation)
    # The validation happens before write transaction starts, which is also rolled back.
    # To test actual database transaction rollback, we can attempt to insert duplicate ID
    # or invalid payload schema. Let's append a list where the second event is invalid,
    # raising ValueError.
    invalid_batch = [
        {"id": "e2", "type": "decision", "timestamp": 101, "content": "valid"},
        {"id": "e3", "type": "command_run", "timestamp": 102, "command": "", "exit_code": 0}  # empty command raises ValueError
    ]

    with pytest.raises(ValueError):
        store.append(cid, invalid_batch)

    # Let's also test a database constraint failure rollback, like duplicate event_id:
    duplicate_batch = [
        {"id": "e1", "type": "decision", "timestamp": 103, "content": "dup"}  # e1 already exists
    ]
    with pytest.raises(ValueError, match="Duplicate event ID within context"):
        store.append(cid, duplicate_batch)

    # Verify context sequence count and latest_sequence are unchanged (still 1)
    meta = store.get_metadata(cid)
    assert meta["latest_sequence"] == 1

    # Verify that a subsequent successful append starts at sequence 2 (no gap)
    res2 = store.append(cid, [{"id": "e4", "type": "decision", "timestamp": 104, "content": "dec 4"}])
    assert res2["first_sequence"] == 2
    assert res2["last_sequence"] == 2

    # Load and confirm sequences inside database are contiguous: 1, 2
    events = store.load_events(cid)
    assert [ev["sequence"] for ev in events] == [1, 2]
    
    store.close()

def test_sqlite_status_check_race(tmp_path):
    db_file = str(tmp_path / "race.db")
    store = SQLiteStore(db_file)
    cid = store.create()

    errors = []
    successes = []

    def worker_append():
        thread_store = SQLiteStore(db_file)
        try:
            for i in range(100):
                thread_store.append(cid, [{"id": f"race-e{i}", "type": "decision", "timestamp": 100 + i, "content": "race"}])
                successes.append(i)
                time.sleep(0.001)
        except ContextPurgedError:
            errors.append("purged")
        finally:
            thread_store.close()

    def worker_purge():
        thread_store = SQLiteStore(db_file)
        try:
            time.sleep(0.02)  # Let some appends run
            thread_store.purge(cid)
        finally:
            thread_store.close()

    t1 = threading.Thread(target=worker_append)
    t2 = threading.Thread(target=worker_purge)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    # Verify that once the purge committed, subsequent appends raised ContextPurgedError
    assert "purged" in errors
    
    # After purge, all read methods raise ContextPurgedError
    with pytest.raises(ContextPurgedError):
        store.load_events(cid)
        
    store.close()

def test_sqlite_wal_read_concurrency(tmp_path):
    db_file = str(tmp_path / "wal_concurrency.db")
    store = SQLiteStore(db_file)
    cid_read = store.create()
    cid_write = store.create()
    
    # Write one event to cid_read so there is something to select
    store.append(cid_read, [{"id": "e1", "type": "decision", "timestamp": 100, "content": "read value"}])
    
    read_started = threading.Event()
    write_finished = threading.Event()
    read_finished = threading.Event()
    errors = []

    def reader_thread():
        thread_store = SQLiteStore(db_file)
        try:
            conn = thread_store.conn
            conn.execute("BEGIN")
            rows = conn.execute("SELECT payload_json FROM events WHERE context_id = ?", (cid_read,)).fetchall()
            assert len(rows) == 1
            
            read_started.set()
            time.sleep(0.5)
            
            conn.execute("COMMIT")
            read_finished.set()
        except Exception as e:
            errors.append(e)
        finally:
            thread_store.close()

    def writer_thread():
        read_started.wait()
        thread_store = SQLiteStore(db_file)
        try:
            t0 = time.time()
            thread_store.append(cid_write, [{"id": "e2", "type": "decision", "timestamp": 200, "content": "write value"}])
            write_time = time.time() - t0
            
            assert write_time < 0.2
            assert not read_finished.is_set()
            write_finished.set()
        except Exception as e:
            errors.append(e)
        finally:
            thread_store.close()

    t_read = threading.Thread(target=reader_thread)
    t_write = threading.Thread(target=writer_thread)

    t_read.start()
    t_write.start()

    t_read.join()
    t_write.join()

    assert not errors
    assert write_finished.is_set()
    assert read_finished.is_set()
    
    store.close()

