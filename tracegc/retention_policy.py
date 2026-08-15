# tracegc/retention_policy.py
"""Retention policy module for TraceGC.

Determines if an event is protected from pruning.
"""

from __future__ import annotations


def is_protected(event: dict) -> bool:
    """Return True if the event is protected from pruning.

    LIMITATION: 'retain_until' (task_end/session_end) currently behaves as permanent protection (no expiration mechanism exists in Phase 1).
    """
    importance = event.get("importance")
    retain_until = event.get("retain_until")
    if importance == "critical":
        return True
    if retain_until in {"task_end", "session_end"}:
        return True
    return False
