# tracegc-mcp

Model Context Protocol (MCP) server integration for TraceGC context compaction.

## Usage Example

`tracegc-mcp` exposes three core MCP tools backed by TraceGC's deterministic compaction pipeline:

- `add_event(event: dict, session_id: str)` — Appends a structured event to an active session trace.
- `compact(session_id: str)` — Runs dead-branch sweeping, override pruning, deduplication, and cycle collapse for a session, returning the compacted prompt and metrics.
- `get_receipt(session_id: str, node_id: str)` — Retrieves full, unpruned event payload receipts for any pruned node.

```python
from tracegc_mcp import TraceGCMCPServer, add_event, compact, get_receipt

# 1. Initialize server instance
server = TraceGCMCPServer()

# 2. Add events to a session
server.add_event({
    "id": "e1",
    "type": "set_var",
    "timestamp": 100,
    "key": "database",
    "value": "postgres"
}, session_id="sess_100")

server.add_event({
    "id": "e2",
    "type": "set_var",
    "timestamp": 200,
    "key": "database",
    "value": "sqlite"
}, session_id="sess_100")

# 3. Compact the session trace
result = server.compact(session_id="sess_100")
print(result["prompt"])
# Output: "database = sqlite"

# 4. Retrieve receipt for the pruned event
receipt = server.get_receipt(session_id="sess_100", node_id="e1")
print(receipt["receipt"]["value"])
# Output: "postgres"
```

Module-level tool helpers (`add_event`, `compact`, `get_receipt`) are also provided for direct tool dispatching.
