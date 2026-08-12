# Trace-GC Core Semantics Specification (v0.1)

This specification defines the core data model, graph traversal rules, pruning algorithms, and invariants of Trace-GC. It is designed to be language-independent so that the Trace-GC engine can be implemented consistently across different platforms.

---

## 1. Event Model & Schema

Every node in the state graph is defined by an **Event**. An event is represented as a structured dictionary/object containing both generic fields and type-specific fields.

### Generic Fields
All events MUST contain the following fields:
*   `id` (string): A unique, non-empty identifier for the event.
*   `type` (string): One of the supported event types listed below.
*   `timestamp` (integer): A non-negative millisecond-precision integer representing the chronological time of the event.

Optionally, any event MAY contain:
*   `parent_id` (string | null): The unique identifier of the logical predecessor in sequence order.
*   `importance` (string): One of `critical`, `task`, `session`, `temporary`, `debug`.
*   `retain_until` (string | null): One of `task_end`, `session_end`, `null`.
*   `tags` (list of strings): User-defined metadata tags.

### Unique ID Invariant
*   **Duplicate Detection:** Event IDs must be strictly unique within a trace. The ingestion engine MUST raise an error (e.g., `ValueError`) immediately upon encountering a duplicate `id`. Silently overwriting existing events or allowing duplicate IDs in the graph is forbidden.

### Type-Specific Schemas
The Trace-GC engine supports the following event types and requires specific fields for each:

| Event Type | Required Fields | Description |
| :--- | :--- | :--- |
| `set_var` | `key` (str), `value` (any) | Declares or overwrites a state variable. |
| `tool_call` | `tool_name` (str), `arguments` (any) | Initiates an external tool execution. |
| `tool_result` | `call_id` (str), `result` (any) | Captures the output of a `tool_call`. `call_id` must match the tool call's `id`. |
| `abandon` | `ref_to` (list of str) | Marks one or more target events (and their descendants) as abandoned. |
| `decision` | `content` (str) | Represents a prompt, agent reasoning step, or plan choice. |
| `file_read` | `path` (str) | Captures a file read operation. |
| `file_edit` | `path` (str), `diff_hash` (str) | Captures a file modification hash. |
| `command_run` | `command` (str), `exit_code` (int) | Captures command line execution and exit status. |
| `test_run` | `test_names` (list of str), `exit_code` (int), `passed_count` (int), `failed_count` (int) | Captures test suite results. |
| `build_run` | `exit_code` (int) | Captures build/compiler exit status. |
| `git_diff` | `diff_hash` (str), `files_changed` (list of str) | Captures version control diff state. |
| `git_commit` | `commit_hash` (str), `message` (str) | Captures a git commit event. |
| `error` | `message` (str) | Captures runtime failures, warnings, or exceptions. |
| `artifact_created`| `artifact_type` (str), `path` (str) | Tracks the production of an artifact file or directory. |
| `requirement` | `content` (str) | Declares a user or design requirement. |
| `constraint` | `content` (str) | Declares a hard system or behavioral constraint. |
| `verification` | `content` (str), `passed` (bool) | Asserts verification/testing outcome. |
| `text_chunk` | `content` (str) | A generic text message fallback format (used when parsing fails). |

---

## 2. Parent & Dependency Semantics

Dependencies between events are modeled as directed edges in a multi-graph. There are three types of edges:
1.  `sequence`: Directed edges indicating logical chronology (parent/child relationships).
2.  `supersedes`: Added during override pruning to connect newer variable writes/deduplicated calls to their older predecessors.
3.  `abandons`: Links an `abandon` event to its target node.

### Sequence Edge Construction
A `sequence` edge is added from `A` to `B` (written `A -> B`) if and only if `B.parent_id == A.id`.
*   **Referential Integrity:** If `B.parent_id` is defined, the event with ID `B.parent_id` must exist in the graph. Ingesting a node with a non-existent parent ID must raise an error.
*   **Forward References:** Sequence dependency resolution is list-order independent. If an event `B` appears before `A` in the ingestion array but references `A` as its parent, the engine must still create the `sequence` edge `A -> B` successfully without raising a referential integrity error, provided both nodes exist in the final input set.

---

## 3. Branch & Abandon Semantics

Pruning of dead or discarded branches is triggered by `abandon` events.

### Dead-Branch Sweep Algorithm
Given a graph $G = (V, E)$, the engine sweeps dead branches through the following procedure:
1.  Identify all `abandon` events in $V$.
2.  For each target event ID $t$ specified in an abandon event's `ref_to` list:
    *   Perform a Depth-First Search (DFS) or Breadth-First Search (BFS) following only `sequence` edges in the forward direction starting from $t$.
    *   Collect all reachable nodes into a candidate prune set $P$.
3.  **Branch-Rejoining Invariant:**
    A candidate node $c \in P$ MUST NOT be pruned if it is reachable from any active root node in the graph via a path of `sequence` edges that does not pass through any abandoned nodes.
    *   *Implementation:* A node $c$ is removed from the prune set $P$ if it has at least one sequence parent that is not in $P$ (excluding nodes that are directly targeted in the `ref_to` list of an abandon event). This check must run iteratively until no further nodes can be removed from $P$ (a fixpoint pass).
4.  All remaining nodes in the pruned candidate set $P$ are marked as pruned. The abandon events that triggered the sweep are also marked as pruned if they lie within the pruned branches.

### Active Paths
An event is considered "active" if it is not marked as pruned by the dead-branch sweep or any subsequent engine stages. The logical root of the graph (nodes with no sequence parents) and all nodes reachable from them that do not descend from an abandoned target constitute the active set.

---

## 4. Variable & State Override Semantics

State variables declared via `set_var` events are pruned when they are superseded by newer writes to the same key.

### Override Engine Rules
1.  Group all active (non-pruned) `set_var` events by their `key`.
2.  Sort events in each group chronologically by `timestamp` ascending.
3.  The event with the highest timestamp is marked as the "newest" write.
4.  **Stable-Sort Tiebreak:** If two `set_var` events for the same key share the identical timestamp, the event that appeared *later in the input list* is selected as the newer write.
5.  All preceding `set_var` events in the sorted list are candidate superseded nodes.
6.  For each candidate superseded node, add a `supersedes` edge from the newest node to the superseded node.

### Replay Safety Configuration
Pruning behavior is governed by the configuration option `prune_referenced_values` (boolean):
*   **`prune_referenced_values = True` (Default / Context-only Mode):**
    All superseded `set_var` events are pruned unconditionally. This yields maximum token savings.
*   **`prune_referenced_values = False` (Replay-safe Mode):**
    An older superseded `set_var` event is **retained** (not pruned) if its `value` is referenced as a value in the `arguments` dictionary of any active (non-pruned) `tool_call` event in the trace. This ensures that the trace remains executable in execution replays.

---

## 5. Deduplication Semantics

Redundant tool invocations are pruned via exact deduplication.

### Equivalence Definition
Two tool calls are equivalent if and only if they share:
1.  The same `tool_name`.
2.  Identical arguments (as serialized deterministically, sorting dictionary keys).
3.  An identical tool result payload (from a successful associated `tool_result` event).

### Deduplication Rules
1.  Only `tool_call` events that have a corresponding, non-pruned `tool_result` event are eligible for deduplication. Orphan tool calls (without results) are skipped.
2.  Group eligible tool calls by their (`tool_name`, serialized arguments, serialized result) signature.
3.  Sort each group chronologically by `timestamp` ascending.
4.  The earliest tool call and its corresponding result are retained.
5.  All subsequent duplicate tool calls and results in the group are marked as pruned.
6.  Add a `supersedes` edge from the surviving tool call to the pruned duplicate tool calls, and from the surviving tool result to the pruned duplicate tool results.

---

## 6. Cycle & SCC Semantics

Traces containing dependency loops (cycles) are compacted by collapsing cycles into a single representational node.

### Cycle Collapse Algorithm
1.  Identify Strongly Connected Components (SCCs) in the sequence graph using Tarjan's algorithm.
2.  To support arbitrary trace sizes (e.g. 100K+ events) without stack overflow, the SCC search MUST be implemented **iteratively** (using an explicit stack) rather than recursively.
3.  For each component containing more than one node:
    *   Prune all member nodes of the component.
    *   Derive a deterministic **Cluster ID** from the sorted list of member event IDs:
        $$\text{Cluster ID} = \text{"cluster\_"} + \text{SHA256}(\text{sorted\_member\_ids})[0..12]$$
    *   Create a new `receipt` node with the derived Cluster ID and a timestamp equal to the minimum timestamp among all member nodes.
    *   Add a `sequence` edge from the new cluster receipt node to the earliest member node (by timestamp).
    *   Remove all sequence edges that connect nodes internal to the cycle. External edges connecting to or from cycle members are updated to reference the new cluster receipt node.

---

## 7. Determinism Guarantees

The compacted trace output is guaranteed to be 100% deterministic given the same inputs and configuration.

### Topological Sort Ordering
When rendering the compacted prompt or reconstructing messages, surviving nodes are ordered using a topological sort of the remaining `sequence` edges.
*   **Tie-breaking:** If the topological sort queue contains multiple nodes with no remaining active incoming sequence edges, they are dequeued in ascending order of their `timestamp`.
*   **Timestamp Ties:** If multiple nodes share the identical timestamp, they are dequeued in lexicographical order of their event `id` strings. This ensures determinism under all conditions.

---

## 8. Receipt Semantics & Recovery

Every pruned event is preserved as a recoverable receipt.

### Receipt Stub Structure
A receipt stub is a lightweight dictionary created when a node is marked pruned:
```json
{
  "id": "event_id",
  "type": "receipt",
  "target_id": "event_id",
  "status": "pruned",
  "timestamp": 123456789
}
```
*   The `timestamp` in the receipt stub MUST copy the original event's timestamp.
*   The list of receipts returned by the engine is sorted in ascending chronological order of their event `timestamp` fields.

### Recovery Guarantee
*   Given the compacted output and the final state graph, the caller can retrieve the **exact, unmutated original event payload** for any pruned node ID.
*   **Mutation-Isolation:** Marking a node as pruned or recovering it must never mutate the caller's original event dictionary in-place. The recovered event payload must be returned as a copy containing the additional field `"pruned": true` to distinguish it from surviving active events.

---

## 9. Compaction Invariants

The Trace-GC engine guarantees the following invariants across all pipelines:

1.  **No Invention:** Compaction never invents or synthesizes new event types (with the sole exception of cluster receipt nodes representing cycles).
2.  **Referential Validity:** Retained events never reference pruned events as sequence parents; any sequence edge pointing to a pruned node is bridged or collapsed.
3.  **Recoverability:** Every pruned node is recoverable in its original shape using the state graph and receipt stubs.
4.  **Isomorphism:** Identical event arrays processed under the same configuration produce byte-for-byte identical output prompts, lists of surviving events, and receipt lists.
5.  **Isolation:** Pruning an abandoned branch or variable override never affects active, unrelated branches.
6.  **Fail-Fast:** Invalid traces (e.g. duplicate IDs, missing required schema fields) fail predictably at ingestion time with `ValueError`.

---

## 10. Versioning & Stability

This document defines version **0.1** of the Trace-GC core specification. The interface and invariants specified here are subject to refinement and will be incremented semantically as advanced features (such as incremental garbage collection or specialized LLM extraction levels) are integrated.
