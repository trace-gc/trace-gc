# context_gc/__init__.py
"""Top level package for the deterministic context compaction library.

Exports the public API so users can simply do::

    from context_gc import StateGraph, compact_events, ContextGCMiddleware
"""

from .graph import StateGraph
from .compactor import compact_events
from .middleware import (
    ContextGCMiddleware,
    call_anthropic_with_compaction,
    call_openai_with_compaction,
)
from .api import ContextGC
