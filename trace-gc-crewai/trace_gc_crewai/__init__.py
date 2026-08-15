# trace_gc_crewai/__init__.py
"""TraceGC CrewAI Adapter.

Provides integration utilities to compact CrewAI execution context, step outputs,
and task histories.
"""

from .adapter import (
    compact_messages,
    TraceGCCrewCallback,
    create_step_callback,
    TraceGCCrewAdapter,
)

__all__ = [
    "compact_messages",
    "TraceGCCrewCallback",
    "create_step_callback",
    "TraceGCCrewAdapter",
]
