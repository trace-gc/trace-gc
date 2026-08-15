# tracegc_mcp/server.py
"""Model Context Protocol (MCP) server integration for TraceGC context compaction."""

from __future__ import annotations

from typing import Any, Dict, Optional, List
from tracegc.api import TraceGC


class TraceGCMCPServer:
    """In-memory MCP server backing TraceGC session compaction."""

    def __init__(self) -> None:
        self.sessions: Dict[str, TraceGC] = {}

    def _get_session(self, session_id: str = "default") -> TraceGC:
        if session_id not in self.sessions:
            self.sessions[session_id] = TraceGC()
        return self.sessions[session_id]

    def add_event(self, event: Dict[str, Any], session_id: str = "default") -> Dict[str, Any]:
        """Appends an event to a TraceGC session."""
        session = self._get_session(session_id)
        session.add_event(event)
        return {"status": "ok", "session_id": session_id, "event_id": event.get("id")}

    def compact(self, session_id: str = "default") -> Dict[str, Any]:
        """Runs the compaction pipeline for a session and returns serializable output."""
        session = self._get_session(session_id)
        res = session.compact()
        return {
            "session_id": session_id,
            "prompt": res.get("prompt", ""),
            "tokens_before": res.get("tokens_before", 0),
            "tokens_after": res.get("tokens_after", 0),
            "pruned_ids": res.get("pruned_ids", []),
            "receipts": res.get("receipts", []),
            "compact_events": res.get("compact_events", []),
        }

    def get_receipt(
        self, session_id: str = "default", node_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Returns pruning receipt(s) for a session."""
        session = self._get_session(session_id)
        if node_id:
            try:
                receipt = session.get_receipt(node_id)
                return {"session_id": session_id, "node_id": node_id, "receipt": receipt}
            except (KeyError, ValueError) as e:
                return {"session_id": session_id, "node_id": node_id, "error": str(e)}
        else:
            res = session.compact()
            return {"session_id": session_id, "receipts": res.get("receipts", [])}


# Default global server instance for convenient tool handler calls
_default_server = TraceGCMCPServer()


def add_event(event: Dict[str, Any], session_id: str = "default") -> Dict[str, Any]:
    """MCP tool wrapper: appends an event to a TraceGC session."""
    return _default_server.add_event(event, session_id=session_id)


def compact(session_id: str = "default") -> Dict[str, Any]:
    """MCP tool wrapper: compacts a TraceGC session."""
    return _default_server.compact(session_id=session_id)


def get_receipt(
    session_id: str = "default", node_id: Optional[str] = None
) -> Dict[str, Any]:
    """MCP tool wrapper: retrieves receipt(s) for a TraceGC session."""
    return _default_server.get_receipt(session_id=session_id, node_id=node_id)
