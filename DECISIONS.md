# Architecture Decisions & Invariants

This document outlines the strict design contracts and invariants that govern **TraceGC**'s storage, concurrency, serialization, and protocol integration layers in Phase 2b.

## Invariants

1.  **Core trace-gc remains zero-dependency.**
    *   *Verification*: Checked by `tests/test_dependencies.py` which verifies that importing `trace_gc` does not pull in any external third-party packages.
2.  **Storage and protocol adapters live in separate packages.**
    *   *Verification*: Verified by repository project structure layout and presence of isolated packages (`trace-gc-storage`, `trace-gc-mcp`, `trace-gc-langgraph`).
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
11. **trace_gc imports and runs in a bare virtualenv with zero extras.**
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
