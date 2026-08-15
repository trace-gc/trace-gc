# Architecture Decisions & Invariants

This document outlines the strict design contracts and invariants that govern **TraceGC**'s storage, concurrency, serialization, and protocol integration layers in Phase 2b.

## Invariants

1.  **Core tracegc remains zero-dependency.**
    *   *Verification*: Checked by `tests/test_dependencies.py` which verifies that importing `tracegc` does not pull in any external third-party packages.
2.  **Storage and protocol adapters live in separate packages.**
    *   *Verification*: Verified by repository project structure layout and presence of isolated packages (`tracegc-storage`, `tracegc-mcp`, `tracegc-langgraph`).
3.  **MCP calls use explicit context_id handles.**
    *   *Verification*: Verified by API schema tests in `tests/test_mcp_protocol.py`.
4.  **context_append is idempotent.**
    *   *Verification*: Verified by idempotency checks in `tests/test_idempotency.py`.
5.  **Same request_id + different canonical payload = conflict.**
    *   *Verification*: Verified by conflict trigger tests in `tests/test_idempotency.py`.
6.  **Canonical serialization rejects NaN/Infinity (allow_nan=False).**
    *   *Verification*: Verified by ValueError checks on serialization payloads in `tests/test_canonicalization.py`.
7.  **Expiration never implies deletion.**
    *   *Verification*: Verified by verifying data persistence post-expiration in `tests/test_lifecycle.py`.
8.  **Receipts remain recoverable until explicit purge.**
    *   *Verification*: Verified by `get_receipt` lookup tests on expired-but-not-purged contexts in `tests/test_lifecycle.py`.
9.  **Core pruning decisions remain deterministic.**
    *   *Verification*: Verified by differential properties tests running multiple random seeds in `tests/test_properties.py`.
10. **Persisted event/result formats carry schema_version 1.**
    *   *Verification*: Verified by schema validation checks in `tests/test_schemas.py`.
11. **tracegc imports and runs in a bare virtualenv with zero extras.**
    *   *Verification*: Checked by bare imports verification in `tests/test_dependencies.py`.
12. **Appends to one context are serialized.**
    *   *Verification*: Tested by concurrent append stress tests in `tests/test_concurrency.py`.
13. **Every compaction reads a consistent committed snapshot.**
    *   *Verification*: Tested by snapshot reads verification in `tests/test_storage_consistency.py`.
14. **A compaction records snapshot_sequence and may become stale.**
    *   *Verification*: Tested by staleness detection checks in `tests/test_storage_consistency.py`.
15. **get_receipt on a purged context raises ContextPurgedError.**
    *   *Verification*: Verified by purged context error assertions in `tests/test_lifecycle.py`.
16. **context_inspect has a versioned response schema.**
    *   *Verification*: Checked by inspecting API response structure checks in `tests/test_schemas.py`.
17. **context_id is not an authorization boundary.**
    *   *Verification*: Documented and verified in architectural and threat model documents.
18. **AppendResult exposes committed sequence boundaries and replay status.**
    *   *Verification*: Checked by validating return schemas of append calls in `tests/test_storage_appends.py`.
19. **Committed event sequences are contiguous; rolled-back transactions consume no committed sequence.**
    *   *Verification*: Tested by contiguous sequence number assertion checks under transaction rollbacks in `tests/test_concurrency.py`.
20. **A protected event cannot be pruned unless an explicit override policy is active.**
    *   *Verification*: Verified by retention policy logic test assertions in `tests/test_retention_policy.py`.
21. **MCP protocol metadata and TraceGC result_schema_version are independent versioning layers.**
    *   *Verification*: Documented and verified in architectural and threat model documents.
22. **Semantic extraction must never silently drop content (no-silent-loss invariant).**
    *   *Background*: A Phase 3 audit of `tracegc/semantic.py` found that `extract_semantic_events()` was
        silently discarding lines that matched no Tier 2 rule and segments whose `extract_fn` raised an
        exception. Both paths produced no event and consumed the input text, causing invisible data loss.
    *   *Fix (2026-08-12)*: Both the block-level exception handler and the per-line no-match branch now
        emit a neutral `text_chunk` event carrying `source_text` and `content` equal to the original
        line/segment text verbatim. Blank lines are still skipped (they carry no meaningful content).
        This "fail-closed" contract means any content that enters `extract_semantic_events()` must
        appear in the returned event list — either as a structured event or as a `text_chunk` fallback.
    *   *Verification*: Tested by `tests/test_semantic_extraction.py::test_no_silent_loss_on_unmatched_content`
        (mixed matched/unmatched block) and confirmed by the reproducibility and disabled-fallback tests
        added in the same commit.

## Entry 23 — Event ID Uniqueness Invariant (BUG-1 Fix, Phase 1 Hardening)

**Decision**: `StateGraph.add_node()` now raises `ValueError("Duplicate event id: {id}")` when the same ID is submitted twice, rather than silently overwriting the earlier node.

**Rationale**: Silent overwrite creates undefined pruning behavior — existing edges still reference the original node's position, but the node payload is replaced. An infrastructure-grade library must reject malformed input predictably. All callers generating event IDs are responsible for uniqueness; the graph enforces the invariant at insertion time.

**Breaking change**: No — valid traces have unique IDs. Any trace that previously relied on silent overwrite was already producing corrupt compaction output.

---

## Entry 24 — Iterative Tarjan's SCC (BUG-2 Fix, Phase 1 Hardening)

**Decision**: `collapse_cycles()` in `topo_sampler.py` was rewritten from recursive to iterative Tarjan's SCC using an explicit DFS stack. The `sys.setrecursionlimit` workaround was removed.

**Rationale**: The recursive implementation stack-overflowed on traces with linear chains deeper than Python's C-stack limit (roughly 2K–10K frames depending on platform). At 100K events — a realistic production scale — the crash was confirmed. The iterative implementation is O(N+E) and handles arbitrarily large graphs. Output is identical: same SCC groupings, same cluster ID derivation via SHA-256 of sorted member IDs.

**Breaking change**: None — pure implementation change, identical behavior.

---

## Entry 25 — Receipt Timestamp and Mutation Isolation (GAP-4/GAP-5 Fix, Phase 1 Hardening)

**Decision**: 
1. `mark_pruned()` now copies the original event's `timestamp` into the receipt stub so `collect_receipts()` sorts by actual event time rather than treating all stubs as `timestamp=0`.
2. `mark_pruned()` no longer mutates `event["pruned"] = True` on the original dict in `graph.nodes`. The `pruned=True` flag now appears only on copies returned by `get_receipt()`.
3. `get_receipt()` returns a `dict(original)` shallow copy with `pruned=True` added — not a reference to the graph's internal dict.

**Rationale**: Mutation of caller-held event dicts is an invisible side-effect that violates caller expectations. Receipt ordering by missing timestamp was silently wrong. Both are now fixed without API breakage.

**Breaking change**: Code that previously relied on `graph.nodes[node_id]["pruned"] is True` will no longer see `pruned=True` on the bare node. Use `get_receipt()` instead, which always returns a copy with `pruned=True`.

---

## Entry 26 — prune_referenced_values Override Engine Flag (BUG-3 Design Decision, Phase 1 Hardening)

**Decision**: `apply_overrides()` and `compact_events()` now accept a `prune_referenced_values: bool = True` parameter.

- **`True` (default, context-only mode)**: An older `set_var` value is pruned regardless of whether an active `tool_call`'s arguments still reference it. This was the previous behavior and remains the default because it produces maximum compaction and the LLM can re-derive values from surviving `tool_call` events.
- **`False` (replay-safe mode)**: An older `set_var` value is retained if any surviving `tool_call` event's `arguments` dict contains the same key with the same value. Use this when the compacted trace must be replayable without recomputing variable values from tool history.

**Rationale**: The override engine's previous behavior (prune all older values unconditionally) was correct for context-window compaction but unsafe for replay. Making the behavior explicit and opt-out-able via a documented flag is better than a silent "TODO".

**Breaking change**: None — default preserves current behavior.

---

## Entry 27 — Branch Rejoining Fix (GAP-3, Phase 1 Hardening)

**Decision**: `sweep_dead_branches()` now performs a fixpoint pass after its initial DFS to remove from the prune set any node that has at least one sequence parent NOT in the prune set.

**Rationale**: Before this fix, a node reachable from both an abandoned branch and an active branch would be incorrectly pruned. The DFS from abandoned targets would add it to `to_prune` without checking whether an active path also reaches it. The fixpoint correctly implements the invariant: a node is swept only if ALL of its sequence parents are also swept (or it is a direct `ref_to` target of an abandon event).

**Scope**: This scenario (a node with two parents, one abandoned and one active) is rare in practice because the standard event model gives each node at most one `parent_id`. It can occur when `add_edge()` is called directly or when a graph is constructed programmatically.

**Breaking change**: None for standard usage. Traces where a node was previously (incorrectly) pruned due to rejoining will now correctly retain it.
