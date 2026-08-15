# tracegc_mcp package init
"""Model Context Protocol adapters for TraceGC."""

from .server import TraceGCMCPServer, add_event, compact, get_receipt

__all__ = ["TraceGCMCPServer", "add_event", "compact", "get_receipt"]
