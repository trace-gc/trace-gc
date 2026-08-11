import pytest
import threading
import time
from trace_gc_storage.memory_store import MemoryStore
from trace_gc_storage.errors import ContextPurgedError

def test_concurrency():
    store = MemoryStore()
    cid = store.create()

    num_threads = 10
    events_per_thread = 10

    def worker(thread_idx):
        for i in range(events_per_thread):
            ev_id = f"t{thread_idx}-e{i}"
            store.append(cid, [{"id": ev_id, "type": "decision", "timestamp": 100, "content": f"t{thread_idx} {i}"}])

    threads = []
    for t_idx in range(num_threads):
        t = threading.Thread(target=worker, args=(t_idx,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Verify that sequences assigned are exactly 1 to N with no duplicates or gaps
    committed = store.load_events(cid)
    total_events = num_threads * events_per_thread
    assert len(committed) == total_events
    
    sequences = sorted([ev["sequence"] for ev in committed])
    assert sequences == list(range(1, total_events + 1))

def test_status_check_race():
    store = MemoryStore()
    cid = store.create()

    errors = []
    successes = []

    def worker_append():
        try:
            for i in range(100):
                store.append(cid, [{"id": f"race-e{i}", "type": "decision", "timestamp": 100 + i, "content": "race"}])
                successes.append(i)
                time.sleep(0.001)
        except ContextPurgedError:
            errors.append("purged")

    def worker_purge():
        time.sleep(0.02)  # Let some appends run
        store.purge(cid)

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
