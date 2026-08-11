import sqlite3
import time
import json
import uuid
import copy
import threading
from context_gc_storage.protocol import AppendResult, CompactionRecord
from context_gc_storage.errors import (
    UnknownContextError,
    ExpiredContextError,
    ContextPurgedError,
    ReceiptNotFoundError,
    IdempotencyConflictError
)
from context_gc_storage.canonical import payload_hash
from context_gc.events import validate_event

class SQLiteStore:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._local = threading.local()
        # Setup schema immediately
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.db_path)
            conn.isolation_level = None  # Autocommit mode
            conn.execute("PRAGMA busy_timeout = 5000")
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            delattr(self._local, "conn")

    def _init_db(self) -> None:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            curr_ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if curr_ver == 0:
                conn.execute("""
                CREATE TABLE contexts (
                    context_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    expires_at TEXT NULL,
                    status TEXT NOT NULL,
                    event_schema_version INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                """)
                conn.execute("""
                CREATE TABLE context_sequences (
                    context_id TEXT PRIMARY KEY,
                    latest_sequence INTEGER NOT NULL DEFAULT 0
                );
                """)
                conn.execute("""
                CREATE TABLE events (
                    context_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    pruned INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (context_id, event_id)
                );
                """)
                conn.execute("CREATE INDEX idx_events_seq ON events(context_id, sequence);")
                conn.execute("""
                CREATE TABLE append_requests (
                    context_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (context_id, request_id)
                );
                """)
                conn.execute("""
                CREATE TABLE compactions (
                    context_id TEXT NOT NULL,
                    compaction_id TEXT NOT NULL,
                    snapshot_sequence INTEGER NOT NULL,
                    result_schema_version INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (context_id, compaction_id)
                );
                """)
                conn.execute("CREATE INDEX idx_compactions_seq ON compactions(context_id, snapshot_sequence);")
                conn.execute("PRAGMA user_version = 1")
                conn.execute("COMMIT")
            else:
                conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _get_context(self, conn, context_id: str) -> dict:
        row = conn.execute("SELECT status, created_at, last_accessed_at, metadata_json FROM contexts WHERE context_id = ?", (context_id,)).fetchone()
        if row is None:
            raise UnknownContextError(context_id)
        status, created_at, last_accessed, metadata_json = row
        if status == "purged":
            raise ContextPurgedError(context_id)
        return {
            "status": status,
            "created_at": created_at,
            "last_accessed_at": last_accessed,
            "metadata": json.loads(metadata_json)
        }

    def create(self, context_id: str | None = None) -> str:
        """Create a new context.
        
        If a context_id is provided and already exists, this method behaves as a
        silent no-op, returning the existing ID without resetting or overwriting its data.
        """
        cid = context_id or str(uuid.uuid4())
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT context_id FROM contexts WHERE context_id = ?", (cid,)).fetchone()
            if row is None:
                now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                conn.execute("""
                    INSERT INTO contexts (context_id, created_at, updated_at, last_accessed_at, expires_at, status, event_schema_version, metadata_json)
                    VALUES (?, ?, ?, ?, NULL, 'active', 1, '{}')
                """, (cid, now_str, now_str, now_str))
                conn.execute("""
                    INSERT INTO context_sequences (context_id, latest_sequence)
                    VALUES (?, 0)
                """, (cid,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return cid

    def append(self, context_id: str, events: list[dict], request_id: str | None = None) -> AppendResult:
        conn = self.conn
        
        # 1. Validation stage (no database write or locks yet)
        validated_events = []
        incoming_ids = set()
        for ev in events:
            # Schema check
            validated_ev = copy.deepcopy(validate_event(ev))
            ev_id = validated_ev.get("id")
            if ev_id in incoming_ids:
                raise ValueError(f"Duplicate event ID within context: {ev_id}")
            incoming_ids.add(ev_id)
            validated_events.append(validated_ev)

        # 2. Transaction stage
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Check context existence & status
            row = conn.execute("SELECT status FROM contexts WHERE context_id = ?", (context_id,)).fetchone()
            if row is None:
                raise UnknownContextError(context_id)
            status, = row
            if status == "purged":
                raise ContextPurgedError(context_id)
            if status in {"expired", "purge_eligible"}:
                raise ExpiredContextError(context_id)

            # Idempotency check
            if request_id is not None:
                req_row = conn.execute(
                    "SELECT response_json, payload_hash FROM append_requests WHERE context_id = ? AND request_id = ?",
                    (context_id, request_id)
                ).fetchone()
                if req_row is not None:
                    response_json, orig_hash = req_row
                    curr_hash = payload_hash(events)
                    if curr_hash == orig_hash:
                        res = json.loads(response_json)
                        res["replayed"] = True
                        conn.execute("COMMIT")
                        return res
                    else:
                        raise IdempotencyConflictError(context_id, request_id)

            # Duplicate ID check against committed events
            if validated_events:
                placeholders = ",".join("?" for _ in validated_events)
                ids_to_check = [ev.get("id") for ev in validated_events]
                dup_row = conn.execute(
                    f"SELECT event_id FROM events WHERE context_id = ? AND event_id IN ({placeholders})",
                    [context_id] + ids_to_check
                ).fetchone()
                if dup_row is not None:
                    raise ValueError(f"Duplicate event ID within context: {dup_row[0]}")

            # SELECT latest_sequence (locks sequence row)
            seq_row = conn.execute(
                "SELECT latest_sequence FROM context_sequences WHERE context_id = ?",
                (context_id,)
            ).fetchone()
            latest_seq = seq_row[0] if seq_row else 0

            first_seq = latest_seq + 1
            last_seq = latest_seq + len(validated_events)

            # Insert events
            for idx, ev in enumerate(validated_events):
                assigned_seq = first_seq + idx
                ev["sequence"] = assigned_seq
                conn.execute(
                    """
                    INSERT INTO events (context_id, event_id, sequence, event_type, timestamp, payload_json, pruned)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (context_id, ev["id"], assigned_seq, ev["type"], ev["timestamp"], json.dumps(ev))
                )

            # Update context_sequences
            conn.execute(
                "UPDATE context_sequences SET latest_sequence = ? WHERE context_id = ?",
                (last_seq, context_id)
            )

            # Update last_accessed_at
            now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "UPDATE contexts SET last_accessed_at = ? WHERE context_id = ?",
                (now_str, context_id)
            )

            # Build result
            result: AppendResult = {
                "context_id": context_id,
                "request_id": request_id,
                "first_sequence": first_seq if validated_events else None,
                "last_sequence": last_seq if validated_events else None,
                "event_count": len(validated_events),
                "replayed": False,
                "payload_hash": payload_hash(events)
            }

            # Insert append_requests
            if request_id is not None:
                conn.execute(
                    """
                    INSERT INTO append_requests (context_id, request_id, response_json, payload_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (context_id, request_id, json.dumps(result), result["payload_hash"], now_str)
                )

            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def load_events(self, context_id: str) -> list[dict]:
        conn = self.conn
        conn.execute("BEGIN")
        try:
            self._get_context(conn, context_id)
            cursor = conn.execute(
                "SELECT payload_json FROM events WHERE context_id = ? ORDER BY sequence",
                (context_id,)
            )
            events = []
            for row in cursor.fetchall():
                events.append(json.loads(row[0]))
            conn.execute("COMMIT")
            return events
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def save_compaction(self, context_id: str, result: CompactionRecord) -> None:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._get_context(conn, context_id)
            conn.execute(
                """
                INSERT INTO compactions (context_id, compaction_id, snapshot_sequence, result_schema_version, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    context_id,
                    result["compaction_id"],
                    result["snapshot_sequence"],
                    result["result_schema_version"],
                    result["result_json"],
                    result["created_at"]
                )
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_latest_compaction(self, context_id: str) -> CompactionRecord | None:
        conn = self.conn
        conn.execute("BEGIN")
        try:
            self._get_context(conn, context_id)
            seq_row = conn.execute(
                "SELECT latest_sequence FROM context_sequences WHERE context_id = ?",
                (context_id,)
            ).fetchone()
            latest_seq = seq_row[0] if seq_row else 0

            row = conn.execute(
                """
                SELECT compaction_id, snapshot_sequence, result_schema_version, result_json, created_at
                FROM compactions WHERE context_id = ?
                ORDER BY snapshot_sequence DESC LIMIT 1
                """,
                (context_id,)
            ).fetchone()
            
            if row is None:
                conn.execute("COMMIT")
                return None
                
            compaction_id, snapshot_sequence, result_schema_version, result_json, created_at = row
            
            record: CompactionRecord = {
                "context_id": context_id,
                "compaction_id": compaction_id,
                "snapshot_sequence": snapshot_sequence,
                "latest_sequence_at_read": latest_seq,
                "stale": snapshot_sequence < latest_seq,
                "result_schema_version": result_schema_version,
                "result_json": result_json,
                "created_at": created_at
            }
            conn.execute("COMMIT")
            return record
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_receipt(self, context_id: str, node_id: str) -> dict:
        conn = self.conn
        conn.execute("BEGIN")
        try:
            self._get_context(conn, context_id)
            row = conn.execute(
                "SELECT payload_json FROM events WHERE context_id = ? AND event_id = ?",
                (context_id, node_id)
            ).fetchone()
            if row is None:
                raise ReceiptNotFoundError(context_id, node_id)
            event = json.loads(row[0])
            conn.execute("COMMIT")
            return event
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def delete(self, context_id: str) -> None:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT context_id FROM contexts WHERE context_id = ?", (context_id,)).fetchone()
            if row is None:
                raise UnknownContextError(context_id)
            conn.execute("DELETE FROM contexts WHERE context_id = ?", (context_id,))
            conn.execute("DELETE FROM context_sequences WHERE context_id = ?", (context_id,))
            conn.execute("DELETE FROM events WHERE context_id = ?", (context_id,))
            conn.execute("DELETE FROM compactions WHERE context_id = ?", (context_id,))
            conn.execute("DELETE FROM append_requests WHERE context_id = ?", (context_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def exists(self, context_id: str) -> bool:
        conn = self.conn
        row = conn.execute("SELECT context_id FROM contexts WHERE context_id = ?", (context_id,)).fetchone()
        return row is not None

    def touch(self, context_id: str) -> None:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._get_context(conn, context_id)
            now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "UPDATE contexts SET last_accessed_at = ? WHERE context_id = ?",
                (now_str, context_id)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_metadata(self, context_id: str) -> dict:
        conn = self.conn
        conn.execute("BEGIN")
        try:
            ctx_data = self._get_context(conn, context_id)
            seq_row = conn.execute(
                "SELECT latest_sequence FROM context_sequences WHERE context_id = ?",
                (context_id,)
            ).fetchone()
            latest_seq = seq_row[0] if seq_row else 0
            
            res = copy.deepcopy(ctx_data["metadata"])
            res.update({
                "status": ctx_data["status"],
                "created_at": ctx_data["created_at"],
                "last_accessed_at": ctx_data["last_accessed_at"],
                "latest_sequence": latest_seq
            })
            conn.execute("COMMIT")
            return res
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def expire(self, context_id: str) -> None:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT status FROM contexts WHERE context_id = ?", (context_id,)).fetchone()
            if row is None:
                raise UnknownContextError(context_id)
            if row[0] == "purged":
                raise ContextPurgedError(context_id)
            now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "UPDATE contexts SET status = 'expired', last_accessed_at = ? WHERE context_id = ?",
                (now_str, context_id)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def mark_purge_eligible(self, context_id: str) -> None:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT status FROM contexts WHERE context_id = ?", (context_id,)).fetchone()
            if row is None:
                raise UnknownContextError(context_id)
            if row[0] == "purged":
                raise ContextPurgedError(context_id)
            now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "UPDATE contexts SET status = 'purge_eligible', last_accessed_at = ? WHERE context_id = ?",
                (now_str, context_id)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def purge(self, context_id: str) -> None:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT status FROM contexts WHERE context_id = ?", (context_id,)).fetchone()
            if row is None:
                raise UnknownContextError(context_id)
            if row[0] == "purged":
                conn.execute("COMMIT")
                return
            
            now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "UPDATE contexts SET status = 'purged', last_accessed_at = ? WHERE context_id = ?",
                (now_str, context_id)
            )
            
            # Deletes all associated events, compactions, and append_requests (real deletion)
            conn.execute("DELETE FROM events WHERE context_id = ?", (context_id,))
            conn.execute("DELETE FROM compactions WHERE context_id = ?", (context_id,))
            conn.execute("DELETE FROM append_requests WHERE context_id = ?", (context_id,))
            
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
