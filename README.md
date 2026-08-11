# Context-GC

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/athishio/context-gc/blob/main/demo/colab_demo.ipynb) [![Live Web App](https://img.shields.io/badge/Live-Web%20App-blueviolet)](https://context-gc-web.vercel.app)

Deterministic, receipt-preserving context compaction library for AI agents with no extra LLM calls.

* **Reduces token cost** by pruning obsolete event paths and redundant actions.
* **Avoids stale-context confusion** by removing overridden variables and dead-end attempts.
* **Zero added latency from AI calls** using a fully local, deterministic compaction engine.
* **Retention Policy Control** through optional metadata (`importance`, `tags`, `retain_until`) to protect critical events from being pruned.
* **CLI Auditing & Inspection** (`context-gc` command) to compact, dry-run, explain pruning decisions, diff prompts, and restore receipts.

## Interactive Demos

*   **Google Colab**: Try out the library and run code examples directly in the [Colab Notebook](https://colab.research.google.com/github/athishio/context-gc/blob/main/demo/colab_demo.ipynb).
*   **Web App**: Visualize the compaction behavior interactively in your browser at the [Context-GC Web Playground](https://context-gc-web.vercel.app).

## Description

Context-GC is a framework-agnostic, installable library combining deterministic graph-based pruning with recoverable receipts. While existing tools (such as Self-GC, ClawVM, Cognee, ContextNest, Headroom, and MemGPT/Letta) split these approaches across research papers, hosted SaaS products, client-side compressors, or LLM-based summarization routines, Context-GC ships as a simple, drop-in, zero-dependency Python library designed for developers building stateful agent workflows.

By modeling the agent's interaction history (execution traces) as a directed multigraph, Context-GC identifies and removes obsolete or superseded steps, dead execution branches, and cycles. When elements are pruned, Context-GC leaves behind lightweight, deterministic *receipt stubs* inline, allowing agents to preserve awareness of their history. Furthermore, the complete original content of any pruned step remains fully recoverable on-demand.

---

## Architecture

Context-GC processes execution traces through a linear compilation pipeline, transforming a raw timeline of structured events into a clean, compacted prompt prefix.

### Compaction Pipeline Flow

```text
┌───────┐      ┌───────┐      ┌─────────────────────────┐      ┌──────────────┐      ┌───────────────────┐
│ Trace │ ───► │ Graph │ ───► │    Override Engine +    │ ───► │ Topo Sampler │ ───► │ Compacted Prompt  │
└───────┘      └───────┘      │   Dead-Branch Sweeper   │      └──────────────┘      │  + Receipt Store  │
                              └─────────────────────────┘                            └───────────────────┘
```

### Entry Points

*   **`ContextGC` (Recommended for Agent Loops)**: An incremental-friendly wrapper class. It allows you to append events one by one as they happen (`add_event()`) and call `compact()` on demand. This is the recommended entry point for long-running agent loops where history grows step-by-step.
*   **`compact_events()` (Single-Shot)**: A low-level function that accepts a static list of event dictionaries and returns the compacted output in a single call. Best for post-mortem processing or batch compaction pipelines.

### The Receipts Model

To prevent context-compaction from causing permanent "memory loss," Context-GC employs a deterministic receipt recovery model. Pruned events are never discarded from memory; they are converted into lightweight inline receipt stubs (e.g., `[RECEIPT node_id]`). Callers can recover the complete, original event dictionary (including arguments, tool names, and return values) at any time by calling `get_receipt(graph, node_id)`.

---

## Installation

### For Local Development / From Source
Clone this repository and run an editable installation from the root directory:

```bash
pip install -e .
```

### From PyPI
Once published to PyPI, you can install the package directly:

```bash
pip install context-gc
```

---

## Quick Start (Incremental API)

The recommended interface for managing agent context is the `ContextGC` client. It allows you to append events step-by-step as they occur and run compaction on-demand:

```python
from context_gc import ContextGC

# 1. Initialize the client
client = ContextGC()

# 2. Append events incrementally as they occur
client.add_event({
    "id": "e001", 
    "type": "decision", 
    "timestamp": 1000, 
    "parent_id": None, 
    "content": "Start config"
})
client.add_event({
    "id": "e002", 
    "type": "set_var", 
    "timestamp": 1010, 
    "parent_id": "e001", 
    "key": "x", 
    "value": 10
})
client.add_event({
    "id": "e003", 
    "type": "set_var", 
    "timestamp": 1020, 
    "parent_id": "e002", 
    "key": "x", 
    "value": 20  # Supersedes x=10
})

# 3. Compact the context history on-demand
result = client.compact()

# The result dictionary contains:
# - 'prompt': The rendered prompt string with receipts (e.g. '[RECEIPT e002]\nx = 20')
# - 'tokens_before' / 'tokens_after': Token metrics before and after compaction
# - 'receipts': List of receipt node IDs generated
# - 'pruned_ids': List of all event IDs that were pruned
# - 'compact_events': List of surviving event dictionaries
# - 'graph': The internal StateGraph state
print(result["prompt"])
```

### Low-Level API (Single-Shot Compaction)
If you already have a full, pre-collected list of events upfront, you can use the lower-level single-shot function `compact_events()` directly:

```python
from context_gc import compact_events

events = [
    {"id": "e001", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Hello"},
    # ... other events ...
]
result = compact_events(events)
```

---

## LLM Middleware Adapters

Context-GC provides concrete integration helper functions for popular LLM provider libraries. These helper functions are optional (lazy-loaded inside the functions), so the core `context-gc` package remains completely dependency-free.

To use these adapters, ensure you install the corresponding package first:

```bash
# To use the Anthropic adapter
pip install anthropic

# To use the OpenAI adapter
pip install openai
```

### Usage Example

```python
from context_gc import ContextGC
from context_gc.middleware import call_openai_with_compaction

# Build and populate your context
client = ContextGC()
client.add_event({"id": "e1", "type": "set_var", "timestamp": 1000, "parent_id": None, "key": "x", "value": 10})
client.add_event({"id": "e2", "type": "set_var", "timestamp": 1010, "parent_id": "e1", "key": "x", "value": 20})

# Call the model; the adapter automatically handles compaction of history 
# and sends the compacted prompt as the system prefix.
res = call_openai_with_compaction(
    context_gc=client,
    model="gpt-4o-mini",
    user_message="Explain what value x holds.",
    api_key="your-openai-api-key"  # Optional, falls back to env var
)

print(res["response_text"])
print(res["metrics"]) # {input_tokens, output_tokens, tokens_before, tokens_after}
```

---

## Pruning Stages

Context-GC executes four deterministic stages to prune context:

1. **Dead-Branch Sweeper (DFS)**: Recursively traverses `sequence` edges starting from explicit `abandon` events to prune unsuccessful or aborted attempts. For example, if a sub-branch of tool calls and decisions is created but later abandoned, the sweeper marks the entire sub-branch as pruned.
2. **Override Engine (`supersedes` edges)**: Finds superseded state variables (like `set_var` events) and retains only the most recent update per key among surviving nodes. For example, if a state variable is set multiple times, intermediate values are pruned in favor of the latest value.
3. **Deduplication Engine**: Identifies duplicate tool call results (identical tool name, inputs, and outputs/results) and prunes redundant later identical executions, leaving a receipt pointing to the earliest surviving call.
4. **Topological Sampler (Cycle Collapse)**: A defensive/structural optimization stage. It identifies cycles and strongly connected components (SCCs) via Tarjan's algorithm and collapses them into single deterministic receipt nodes (e.g., collapsing repeating execution loops to leave a single receipt stub). Since the standard `add_event()` event-stream API enforces sequential dependency parent checks, cycles can never form in normal client usage. This stage exists as defensive infrastructure to safely handle graphs constructed by other means (e.g. direct `StateGraph` population from out-of-order logs or non-chronological sources).

---

## Receipts & Event Recovery

Pruned events are never permanently deleted from memory. Instead, they are flagged as pruned (`pruned=True`) on the `StateGraph` and represented by inline stubs (`[RECEIPT <node_id>]`). 

Callers can recover the complete original event payload at any time using `get_receipt(graph, node_id)`. For example, recovering `e002` returns the original superseded variable assignment dict:

```python
# Call get_receipt from the ContextGC client instance:
print(client.get_receipt("e002"))
# Returns:
# {'id': 'e002', 'type': 'set_var', 'timestamp': 1010, 'parent_id': 'e001', 'key': 'x', 'value': 10, 'pruned': True}
```

---

## Event Schema

Context-GC validates incoming events according to five structured types defined in `context_gc/events.py`:

*   **`set_var`**: Used to update state variables.
    *   *Required fields*: `id`, `type`, `timestamp`, `key`, `value`
*   **`tool_call`**: Represents a tool execution request.
    *   *Required fields*: `id`, `type`, `timestamp`, `tool_name`, `arguments`
*   **`tool_result`**: Captures the execution result of a tool.
    *   *Required fields*: `id`, `type`, `timestamp`, `call_id`, `result`
*   **`abandon`**: Denotes abandoning a path.
    *   *Required fields*: `id`, `type`, `timestamp`, `ref_to` (list of target node IDs to prune)
*   **`decision`**: Describes a transition logic or agent choice.
    *   *Required fields*: `id`, `type`, `timestamp`, `content`

*All events support an optional `parent_id` (string) field to map the sequential execution path.*

### Coding Agent Event Types (Schema v0.3.0+)

*   **`file_read`**: Reads a file payload.
    *   *Required fields*: `id`, `type`, `timestamp`, `path` (non-empty string)
*   **`file_edit`**: Edits a file.
    *   *Required fields*: `id`, `type`, `timestamp`, `path` (non-empty string), `diff_hash` (non-empty string hash of the change)
*   **`command_run`**: Runs a terminal command.
    *   *Required fields*: `id`, `type`, `timestamp`, `command` (non-empty string), `exit_code` (integer)
*   **`test_run`**: Runs test cases.
    *   *Required fields*: `id`, `type`, `timestamp`, `test_names` (list of strings), `exit_code` (integer), `passed_count` (integer), `failed_count` (integer)
*   **`build_run`**: Runs a build task.
    *   *Required fields*: `id`, `type`, `timestamp`, `exit_code` (integer)
*   **`git_diff`**: Shows repository diff.
    *   *Required fields*: `id`, `type`, `timestamp`, `diff_hash` (non-empty string), `files_changed` (list of strings)
*   **`git_commit`**: Creates a git commit.
    *   *Required fields*: `id`, `type`, `timestamp`, `commit_hash` (non-empty string), `message` (non-empty string)
*   **`error`**: Signals an error execution.
    *   *Required fields*: `id`, `type`, `timestamp`, `message` (non-empty string)
    *   *Optional field*: `related_to` (non-empty string ID of the causing event, or None)
*   **`artifact_created`**: Generates a file artifact.
    *   *Required fields*: `id`, `type`, `timestamp`, `artifact_type` (non-empty string), `path` (non-empty string)
*   **`requirement`**: Defines a system requirement.
    *   *Required fields*: `id`, `type`, `timestamp`, `content` (non-empty string)
*   **`constraint`**: Defines a system constraint.
    *   *Required fields*: `id`, `type`, `timestamp`, `content` (non-empty string)
*   **`verification`**: Asserts a verification check.
    *   *Required fields*: `id`, `type`, `timestamp`, `content` (non-empty string), `passed` (boolean)

---

## Prior Art / Related Work

The problem of managing long-context window limits and cost in agentic systems is an active area of research and engineering. Related approaches include:
*   **Content-Level Compression (Headroom)**: Compresses the content of individual messages or tool outputs as they arrive (routing JSON, logs, or text to specialized per-type compressors, including the trained ML-based compressor *Kompress*) while leaving the historical conversation structure untouched to maximize provider KV-cache hits.
*   **Graph-based Memory Systems & Knowledge Graphs**: Tools (like Cognee) that structure agent experiences as entity-relation networks rather than linear logs.
*   **OS-Inspired Memory Architectures**: Frameworks (such as MemGPT/Letta) that treat context management analogously to operating system paging, moving data between virtual memory and disk.
*   **Hosted Memory & Vector Databases**: SaaS platforms and databases that offer retrieval-augmented generation (RAG) and search workflows over raw text memories.
*   **AI-Driven Summarization**: Naive LLM calls that periodically summarize history logs into shorter paragraphs.

### Headroom vs. Context-GC

A primary architectural distinction exists between **Headroom** and **Context-GC**:
*   **Headroom** compresses the *content* of individual messages/tool-outputs as they arrive—routing JSON/code/logs/text to per-type compressors (one of which, *Kompress*, uses a trained ML model, not pure determinism), and explicitly leaves prior conversation history untouched to preserve provider KV-cache hits. Headroom decides what to keep small on the way in.
*   **Context-GC** solves a different layer: given an agent's already-accumulated structured event history, it identifies which parts are now dead (superseded, abandoned, or cyclical) and structurally removes them. Context-GC decides what should still exist at all once it is already there.

The two approaches are complementary rather than competing: Headroom shrinks new incoming tool outputs, while Context-GC prunes stale state from history. Furthermore, Context-GC's entire pipeline has zero ML/AI models anywhere, including in the pruning logic itself, whereas Headroom's is deterministic for some content types but uses a trained model for general text. Additionally, Headroom's memory-layer deduplication explicitly relies on an LLM call to judge whether two facts should be merged ('LLM-Mediated Dedup'), whereas Context-GC's deduplication is exact-match on tool name, arguments, and result — fully deterministic, with no model call anywhere in the decision.

### Context-GC's Niche

Context-GC does not compete with hosted retrieval systems or general-purpose cognitive architectures. Its niche is defined by:
1.  **Lightweight & Dependency-Free**: It is an offline, installable Python library with zero external package dependencies.
2.  **Structured Event-Only**: It does not parse natural language or make semantic assumptions; it operates deterministically on structured schemas (`set_var`, `tool_call`, etc.).
3.  **Receipt-Based Guarantee**: Unlike lossy summarization or truncation, pruned elements are replaced with inline receipt stubs that guarantee the original metadata remains fully recoverable on-demand.

## Benchmark Results

Context-GC was benchmarked against three alternative context-management
strategies — no compaction (`full_history`), naive truncation (by event
count and by token count), and AI-driven summarization (Gemini 3.6 Flash,
single-pass and recursive) — across 9 fixtures spanning three agent
types (coding, research, customer-support) and three trace lengths
(short, medium, long). Each method was scored on token output, latency,
determinism, and four semantic probes (recall, artifact-tracking,
continuation, decision) that check whether compaction silently lost
anything that mattered.

| Trace Size | Method | Tokens | Recall | Artifact | Continuation | Decision | Deterministic |
|---|---|---|---|---|---|---|---|
| Short | full_history | 121.0 | 100% | 100% | 100% | 100% | n/a |
| Short | truncate_by_event_count | 116.3 | 100% | 100% | 100% | 100% | n/a |
| Short | ai_summarize_single | 90.7 | 100% | 33.3% | 55.6% | 0.0% | No |
| Short | **context_gc_pipeline** | **75.3** | **100%** | **100%** | **100%** | **100%** | **Yes** |
| Medium | full_history | 379.7 | 100% | 100% | 100% | 100% | n/a |
| Medium | truncate_by_event_count | 133.3 | 0.0% | 100% | 0.0% | 0.0% | n/a |
| Medium | ai_summarize_single | 146.7 | 66.7% | 88.9% | 100% | 0.0% | No |
| Medium | ai_summarize_recursive | 131.0 | 0.0% | 66.7% | 100% | 0.0% | No |
| Medium | **context_gc_pipeline** | **299.0** | **100%** | **100%** | **100%** | **100%** | **Yes** |
| Long | full_history | 1301.0 | 100% | 100% | 100% | 100% | n/a |
| Long | truncate_by_event_count | 104.3 | 0.0% | 0.0% | 0.0% | 0.0% | n/a |
| Long | ai_summarize_single | 243.4 | 100% | 0.0% | 100% | 0.0% | No |
| Long | ai_summarize_recursive | 219.2 | 100% | 0.0% | 100% | 0.0% | No |
| Long | **context_gc_pipeline** | **1028.3** | **100%** | **100%** | **100%** | **100%** | **Yes** |

*(Truncated for brevity above — see [Comparative Benchmark Report](https://github.com/athishio/context-gc/blob/main/context_gc/benchmark/benchmark_report.md) for the full table including token-count truncation, average latencies, and per-tier breakdowns.)*

### Methodology Note: Exact Substring Matching
> [!NOTE]
> The decision probe checks for exact substring survival against the original event text. This structurally favors methods that preserve verbatim text (`truncate_by_event_count`, `truncate_by_token_count`, `context_gc_pipeline`) over methods that paraphrase (`ai_summarize_single`, `ai_summarize_recursive`) — a correctly-summarized, semantically accurate paraphrase can score 0% on this probe even when it retains the right information in different words. We report probe scores as-is because they're deterministic and reproducible, but this benchmark measures literal information survival, not downstream answer correctness. For a test of actual downstream answer correctness (an LLM answering a real question from compacted vs. full context), see the Scenario 5 stress-test result in the [Supplementary Finding: Live Answer-Quality Check](https://github.com/athishio/context-gc/blob/main/WRITEUP.md#supplementary-finding-live-answer-quality-check) section of `WRITEUP.md`. We have not separately investigated the low artifact-accuracy scores for AI summarization on long traces, so this caveat does not extend to that metric either — it may reflect a genuine limitation of summarization, a different measurement artifact, or something else; it is simply unexamined.

### What this actually shows

Naive truncation produces the smallest output by far, but it does so by
simply discarding whatever falls outside its window — recall and
decision accuracy collapse to 0% on medium and long traces. It compresses
by destroying information, not by understanding it.

AI-summarization compresses more aggressively than Context-GC on longer
traces and preserves recall reasonably well, but **decision accuracy is
0% across every single trace length** — the rationale behind an agent's
pivot from one approach to another is consistently lost in summarization.
It also costs real money (~$0.0046 total across 99 calls in this
benchmark), takes 4-50 seconds of added latency per call, and produces
different output on every run.

**Context-GC is the only method that scored 100% across all four probes
on every trace length tested.** Its token reduction is more conservative
than the alternatives — the tradeoff is deliberate: nothing is ever
truly discarded, and every pruned event remains recoverable via
`get_receipt()`. The pitch isn't "smallest possible output" — it's
"reduction with a correctness guarantee nothing else in this table has."

### Known limitations of this benchmark

- **Pro-tier comparison not run.** Gemini Pro was unavailable (0 req/day
  quota) in this environment; all AI-summarization figures are Flash-tier
  only.
- **Small sample size.** 3 runs per fixture/method combination — this
  reflects behavior on these specific trace structures, not a broad
  statistical distribution.
- **Unresolved anomaly**: `ai_summarize_recursive` scored 0% recall on
  medium traces but recovered to 100% on long traces. No clear
  architectural explanation was found; this is reported as-is rather
  than smoothed over.
- **Cycle Collapse Verification**: Cycle-collapsing behavior (defensive graph loop collapsing) is verified separately under synthetic cyclic traces in [`tests/test_topo_sampler.py`](file:///e:/Context-GC/tests/test_topo_sampler.py). All comparative benchmark numbers are scored against natural, un-injected event traces.

---

## Limitations

- **Structured Events Only**: Compaction operates purely on typed, structured event inputs. Context-GC does not parse freeform natural-language prose or try to semantic-check contradictions in plain text.
- **DAG Assumption**: The state graph must resolve to a Directed Acyclic Graph (DAG) after the cycle collapsing stage has executed to allow topological rendering.
- **API Compaction Performance**: Incremental compaction is not fully incremental under the hood; it re-runs the full compaction pipeline on each `.compact()` call. For very long traces, this means repeated execution overhead.
- **Retain Until Expiration**: The `retain_until` event metadata field currently has no dynamic expiration mechanism (e.g. tracking when a task or session actually ends). In Phase 1, it behaves identically to permanent protection.
- **Deduplication Scope**: Deduplication is intentionally scoped to `tool_call`/`tool_result` pairs only. Repeated `file_read`, `test_run`, `build_run`, or similar coding-agent events are NOT deduplicated, even if identical — re-reading a file or re-running tests after a change represents meaningful re-verification, not redundancy, and collapsing them would remove genuine reasoning-trace context.

---

## Development Setup

For local development across the package monorepo, you must install the packages in editable mode to allow correct module resolution (e.g. without manually managing `PYTHONPATH`).

1. Install the core `context-gc` package:
   ```bash
   pip install -e .
   ```
2. Install the `context-gc-storage` package:
   ```bash
   pip install -e ./context-gc-storage
   ```
3. Once functional implementations are added for `context-gc-mcp` and `context-gc-langgraph`, install them similarly:
   ```bash
   pip install -e ./context-gc-mcp
   ```
   ```bash
   pip install -e ./context-gc-langgraph
   ```

