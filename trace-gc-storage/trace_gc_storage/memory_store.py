import uuid
import time
import copy
import threading
from trace_gc_storage.protocol import AppendResult, CompactionRecord
from trace_gc_storage.errors import (
    UnknownContextError,
    ExpiredContextError,
    ContextPurgedError,
    ReceiptNotFoundError,
    IdempotencyConflictError
)
from trace_gc_storage.canonical import payload_hash
from trace_gc.events import validate_event

class MemoryStore:
    def __init__(self):
        self._contexts = {}
        self._compactions = {}
        self._idempotency = {}  # (context_id, request_id) -> (AppendResult, payload_hash)
        self._locks = {}
        self._locks_lock = threading.Lock()

    def _get_lock(self, context_id: str) -> threading.Lock:
        with self._locks_lock:
            if context_id not in self._locks:
                self._locks[context_id] = threading.Lock()
            return self._locks[context_id]

    def _get_context(self, context_id: str) -> dict:
        if context_id not in self._contexts:
            raise UnknownContextError(context_id)
        ctx = self._contexts[context_id]
        if ctx["status"] == "purged":
            raise ContextPurgedError(context_id)
        return ctx

    def create(self, context_id: str | None = None) -> str:
        """Create a new context with a unique ID.
        
        If a context_id is provided and already exists, this method behaves as a
        silent no-op, returning the existing ID without resetting or overwriting its data.
        """
        cid = context_id or str(uuid.uuid4())
        if cid not in self._contexts:
            now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._contexts[cid] = {
                "events": [],
                "event_ids": set(),
                "status": "active",
                "latest_sequence": 0,
                "created_at": now_str,
                "last_accessed_at": now_str,
                "metadata": {}
            }
            self._compactions[cid] = []
        return cid

    def append(self, context_id: str, events: list[dict], request_id: str | None = None) -> AppendResult:
        # Thread locking for serialization, wrapping context status checks to prevent status check races
        lock = self._get_lock(context_id)
        with lock:
            if context_id not in self._contexts:
                raise UnknownContextError(context_id)
            ctx = self._contexts[context_id]
            if ctx["status"] == "purged":
                raise ContextPurgedError(context_id)
            if ctx["status"] in {"expired", "purge_eligible"}:
                raise ExpiredContextError(context_id)

            # Idempotency check
            if request_id is not None:
                key = (context_id, request_id)
                if key in self._idempotency:
                    orig_result, orig_hash = self._idempotency[key]
                    curr_hash = payload_hash(events)
                    if curr_hash == orig_hash:
                        res = copy.deepcopy(orig_result)
                        res["replayed"] = True
                        return res
                    else:
                        raise IdempotencyConflictError(context_id, request_id)

            # Validation stage (Atomic transaction - no sequence allocation on fail)
            validated_events = []
            incoming_ids = set()
            for ev in events:
                # 1. Schema check
                validated_ev = copy.deepcopy(validate_event(ev))
                # 2. Event ID uniqueness
                ev_id = validated_ev.get("id")
                if ev_id in ctx["event_ids"] or ev_id in incoming_ids:
                    raise ValueError(f"Duplicate event ID within context: {ev_id}")
                incoming_ids.add(ev_id)
                validated_events.append(validated_ev)

            # Mutate state (Commit stage)
            first_seq = ctx["latest_sequence"] + 1
            last_seq = ctx["latest_sequence"] + len(validated_events)
            
            for idx, validated_ev in enumerate(validated_events):
                assigned_seq = first_seq + idx
                validated_ev["sequence"] = assigned_seq
                ctx["events"].append(validated_ev)
                ctx["event_ids"].add(validated_ev["id"])

            ctx["latest_sequence"] = last_seq
            now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ctx["last_accessed_at"] = now_str

            result: AppendResult = {
                "context_id": context_id,
                "request_id": request_id,
                "first_sequence": first_seq if validated_events else None,
                "last_sequence": last_seq if validated_events else None,
                "event_count": len(validated_events),
                "replayed": False,
                "payload_hash": payload_hash(events)
            }

            if request_id is not None:
                self._idempotency[(context_id, request_id)] = (result, result["payload_hash"])

            return result

    def load_events(self, context_id: str) -> list[dict]:
        ctx = self._get_context(context_id)
        return copy.deepcopy(ctx["events"])

    def save_compaction(self, context_id: str, result: CompactionRecord) -> None:
        self._get_context(context_id)
        self._compactions[context_id].append(copy.deepcopy(result))

    def get_latest_compaction(self, context_id: str) -> CompactionRecord | None:
        ctx = self._get_context(context_id)
        records = self._compactions.get(context_id, [])
        if not records:
            return None
        latest_record = max(records, key=lambda r: r["snapshot_sequence"])
        record_copy = copy.deepcopy(latest_record)
        record_copy["latest_sequence_at_read"] = ctx["latest_sequence"]
        record_copy["stale"] = record_copy["snapshot_sequence"] < ctx["latest_sequence"]
        return record_copy

    def get_receipt(self, context_id: str, node_id: str) -> dict:
        ctx = self._get_context(context_id)
        for ev in ctx["events"]:
            if ev.get("id") == node_id:
                return copy.deepcopy(ev)
        raise ReceiptNotFoundError(context_id, node_id)

    def delete(self, context_id: str) -> None:
        lock = self._get_lock(context_id)
        with lock:
            if context_id not in self._contexts:
                raise UnknownContextError(context_id)
            self._contexts.pop(context_id, None)
            self._compactions.pop(context_id, None)
            keys_to_remove = [k for k in self._idempotency if k[0] == context_id]
            for k in keys_to_remove:
                self._idempotency.pop(k, None)

    def exists(self, context_id: str) -> bool:
        return context_id in self._contexts

    def touch(self, context_id: str) -> None:
        lock = self._get_lock(context_id)
        with lock:
            ctx = self._get_context(context_id)
            ctx["last_accessed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def get_metadata(self, context_id: str) -> dict:
        ctx = self._get_context(context_id)
        res = copy.deepcopy(ctx["metadata"])
        res.update({
            "status": ctx["status"],
            "created_at": ctx["created_at"],
            "last_accessed_at": ctx["last_accessed_at"],
            "latest_sequence": ctx["latest_sequence"]
        })
        return res

    # Lifecycle methods for test/administration use
    def expire(self, context_id: str) -> None:
        lock = self._get_lock(context_id)
        with lock:
            if context_id not in self._contexts:
                raise UnknownContextError(context_id)
            if self._contexts[context_id]["status"] == "purged":
                raise ContextPurgedError(context_id)
            self._contexts[context_id]["status"] = "expired"
            self._contexts[context_id]["last_accessed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def mark_purge_eligible(self, context_id: str) -> None:
        lock = self._get_lock(context_id)
        with lock:
            if context_id not in self._contexts:
                raise UnknownContextError(context_id)
            if self._contexts[context_id]["status"] == "purged":
                raise ContextPurgedError(context_id)
            self._contexts[context_id]["status"] = "purge_eligible"
            self._contexts[context_id]["last_accessed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def purge(self, context_id: str) -> None:
        lock = self._get_lock(context_id)
        with lock:
            if context_id not in self._contexts:
                raise UnknownContextError(context_id)
            if self._contexts[context_id]["status"] == "purged":
                return
            ctx = self._contexts[context_id]
            ctx["events"] = []
            ctx["event_ids"] = set()
            ctx["status"] = "purged"
            ctx["last_accessed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._compactions[context_id] = []
            keys_to_remove = [k for k in self._idempotency if k[0] == context_id]
            for k in keys_to_remove:
                self._idempotency.pop(k, None)
