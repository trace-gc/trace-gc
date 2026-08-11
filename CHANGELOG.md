# Changelog

All notable changes to this project will be documented in this file.

## [0.4.0] - Unreleased

### Changed
- **License**: License changed from MIT to Apache 2.0, effective this version onward. All prior published versions (0.1.0 through 0.3.0) remain licensed under MIT; those terms do not change retroactively.

## [0.3.0] - 2026-08-08

### Added
- **Coding Agent Event Schema**: Added 12 new coding-specific event types: `file_read`, `file_edit`, `command_run`, `test_run`, `build_run`, `git_diff`, `git_commit`, `error`, `artifact_created`, `requirement`, `constraint`, and `verification`. Enforced non-empty validations on string fields.
- **Coding Prompt Rendering**: Added human-readable single-line formats for the new event types. Implemented test names list truncation formatting for `test_run` (>3 tests).
- **CLIexplain details**: Extended `explain` subcommand to output the target details of `error` events' `related_to` field, with safe lookup tolerance for missing/pruned IDs.

## [0.2.1] - 2026-08-08

### Changed
- **Benchmark Correction**: Removed synthetic cross-edge injection from the comparative benchmark runner for `context_gc_pipeline`, scoring all methods on natural traces. Corrected the medium-tier token count average to **299.0** tokens.
- **Cycle Collapse Note**: Added methodology notes indicating cycle collapse is verified separately in unit tests (`tests/test_topo_sampler.py`).
- **Docs**: Changed relative markdown links in `README.md` and `benchmark_report.md` to absolute GitHub paths to prevent broken links when rendered on PyPI.

## [0.2.0] - 2026-08-08

### Added
- **Retention Policy & Safety primitives**:
  - Optional event schema attributes: `importance` (`critical`, `task`, `session`, `temporary`, `debug`), `tags`, and `retain_until` (`task_end`, `session_end`, `None`).
  - Added `is_protected(event)` check inside override, dead-branch sweeper, and deduplication engines to prevent pruning of critical events.
  - Added `protected` tracking inside `StateGraph` along with audit/reason logs for compaction (`prune_reasons`, `protected_reasons`).
- **CLI Management Tool**:
  - `context-gc compact [--dry-run] <trace>`: Compacts events; dry-run mode displays prune/protect actions and token savings without outputting the prompt.
  - `context-gc explain <trace> <node_id>`: Displays full event data, status (pruned/protected/kept), and details of what would/did override or abandon the node.
  - `context-gc restore <trace> <node_id>`: Recovers and prints the original event payload of a pruned node.
  - `context-gc diff <trace>`: Displays a unified diff showing original vs compacted prompt.

## [0.1.1] - 2026-08-06

### Changed
- **Docs**: Added methodology caveat for the decision probe's exact-substring-matching limitation; corrected average latency figure for `ai_summarize_recursive` on long traces to exclude a rate-limit retry wait.

## [0.1.0] - 2026-07-31

### Added
- **Core Compaction Pipeline (Week 1)**:
  - `StateGraph` core graph model with optimized forward adjacency cache mapping and reverse lookup.
  - **Dead-Branch Sweeper (DFS)**: Prunes unsuccessful branches starting from `abandon` events.
  - **Override Engine**: Prunes overridden variable updates (`set_var` events) among surviving nodes.
  - **Deduplication Engine**: Deduplicates exact-duplicate tool call results.
  - **Topological Sampler**: Detects cycles and strongly connected components (SCCs) via Tarjan's algorithm and collapses them.
  - **Receipts**: Preserves original events of pruned nodes for recovery.
- **Packaging and PEP 621 Standard (Week 2 - In Progress)**:
  - Added `pyproject.toml` using PEP 621 format.
  - Core library remains completely dependency-free, with testing libraries configured as optional dependencies.
  - Added MIT `LICENSE` and initial packaging configurations.
