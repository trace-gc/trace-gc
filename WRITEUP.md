# Technical Writeup: Context-GC Compaction Library

This document provides a comprehensive technical overview of Context-GC, a framework-agnostic, zero-dependency Python library designed for deterministic, receipt-preserving context compaction in stateful AI agent workflows.

---

## 1. Introduction: The Problem of Context Bloat in Agent Loops

As AI agents transition from simple single-turn query-response systems to long-running, autonomous workflows, they generate extensive execution traces. These traces contain tool calls, tool results, intermediate decisions, variable updates, and error-handling branches. In a standard agent loop, these events are appended to the system prompt to maintain historical context. However, this naive approach leads directly to **context bloat**, raising three severe challenges:

1.  **API Costs and Latency Escalation**: LLM pricing is billed per token. A linear accumulation of execution history results in quadratic cost growth over long sessions. Simultaneously, processing massive context windows increases time-to-first-token (TTFT) latency, degrading the user experience.
2.  **Attention Dilution**: Long-context models, while capable of processing large inputs, suffer from "needle in a haystack" retrieval degradation. Relevant state variables, final decisions, and key identifiers are easily lost when buried under hundreds of lines of obsolete tool results.
3.  **Non-Determinism and Context Poisoning**: The naive solution to context bloat is periodically calling a model to summarize past history (AI-driven summarization). However, LLM-based summarizers are non-deterministic, slow, expensive, and prone to hallucination. An LLM summarizer might inadvertently drop a critical identifier, misreport a state variable, or inject false context, leading to agent failure or loops.

Context-GC solves these problems by modeling the agent's history as a directed multigraph and applying deterministic graph-pruning algorithms. It prunes redundant and obsolete data with **zero extra LLM calls**, ensuring complete determinism, zero latency overhead, and 100% accurate receipt-based state recovery.

---

## 2. Core Architecture: Deterministic Graph-Based Pruning

Instead of treating the history log as a flat text stream, Context-GC structures the trace as a directed multigraph: the **StateGraph**. In the StateGraph, each historical event represents a node, and directed edges represent semantic or temporal dependencies.

The compaction process operates via a linear pipeline that executes four deterministic stages on the graph, followed by receipt generation and final prompt rendering.

```text
                                [Raw Event Stream] 
                                        │
                                        ▼
                                   StateGraph
                                        │
                                        ▼
                            1. Dead-Branch Sweeper (DFS)
                                        │
                                        ▼
                            2. Override Engine (Supersedes)
                                        │
                                        ▼
                            3. Deduplication Engine (Redundancy)
                                        │
                                        ▼
                            4. Topological Sampler (Cycle Collapse)
                                        │
                                        ▼
                             5. Receipt Stub Generation
                                        │
                                        ▼
                            [Rendered Prompt Prefix]
```

### The Recovery Guarantee: The Receipts Model

Pruned nodes are never deleted from memory. When the override engine or dead-branch sweeper decides a node is no longer needed in the active context, the node is marked as `pruned`. It is replaced in the final rendered prompt by a lightweight, deterministic **receipt stub**:
```text
[RECEIPT <node_id>]
```
This stub informs the downstream LLM that an event occurred and provides its unique identifier. If the agent needs to inspect the inputs, arguments, or raw result of that pruned step, it calls `get_receipt(graph, node_id)` to retrieve the complete original dictionary. This preserves spatial context while dramatically reducing prompt size.

---

## 3. Deep Dive into the Compaction Pipeline

Here we detail the implementation and algorithmic design of each pipeline stage.

### Graph Construction (`_build_state_graph`)

The graph nodes are validated dicts containing the core fields: `id`, `type`, `timestamp`, `parent_id`.
A directed edge of type `sequence` is added from `parent_id` to `id`. This links chronological execution, preserving the logical tree structure of the agent's work.

### Stage 1: Dead-Branch Sweeper (DFS)

When an agent hits a dead-end (e.g., a tool call fails or a plan is rejected), it generates an `abandon` event. The `abandon` event contains a `ref_to` list pointing to the root nodes of the branches to discard.

The Dead-Branch Sweeper performs a Depth-First Search (DFS) starting from each root node in `ref_to`, recursively traversing child nodes along `sequence` edges. All visited descendant nodes are marked as `pruned`. This cleanly eliminates full subtrees representing abandoned plans, failed regex parsers, or temporary diagnostic loops.

### Stage 2: Override Engine

Agents frequently update state variables. For example, a search agent updates a variable named `best_source` or `current_hypothesis` multiple times. 

The Override Engine inspects all `set_var` events. For each unique `key`, it traverses the active sequence path to identify which `set_var` nodes are logically superseded by newer updates. A directed edge of type `supersedes` is added from the newer `set_var` node to the older one. The older node is marked as `pruned`. Only the most recent, active value for each variable is retained in the prompt.

### Stage 3: Deduplication Engine

If an agent runs the same tool call with the same arguments multiple times and receives the identical output, the duplicate steps are redundant.

The Deduplication Engine identifies identical `tool_call` and `tool_result` event pairs. It retains the first occurrence (determined by timestamp) and marks all subsequent duplicates as `pruned`. It inserts a `supersedes` edge from the original to the duplicate, ensuring that any receipt lookups for the duplicate resolve correctly to the original.

### Stage 4: Topological Sampler (Cycle Collapse)

The Topological Sampler runs Tarjan's strongly connected components (SCC) algorithm to detect sequence cycles in the graph. When an SCC with a size greater than 1 is found, the sampler collapses it into a single receipt node. The intra-SCC edges are removed, and a sequence edge is added from the receipt node to the earliest member of the cycle.

#### **Rigorous Finding: Unreachability through Standard API**
During testing, an audit of the graph traversal code revealed a structural invariant: **cycles can never form under standard public API usage**.
Because `ContextGC.add_event()` validates that an event's `parent_id` already exists in the graph:
```python
if parent not in self.graph.nodes:
    raise ValueError("parent_id not found in graph — events must be added in dependency order")
```
and because `parent_id` is a single-value string, the sequence graph is built as a strict tree/DAG. Time flows forwards, meaning a later event cannot be the parent of an earlier event. 

Thus, `collapse_cycles` acts strictly as **defensive infrastructure**. It exists to protect the compaction compiler from infinite loops if a graph is manually constructed out-of-order, or populated from non-linear external data sources.

---

## 4. Verification and Rigor: The Quest for Correctness

A deterministic context compiler must guarantee that compaction never corrupts state or drops critical information. To verify this, we implemented a multi-layered correctness suite.

### Property-Based Testing with Hypothesis

Using the `hypothesis` library, we defined property tests to assert invariants over randomly generated execution trees:
*   **Topological Invariant**: Compaction must always produce a Directed Acyclic Graph (DAG) that is topologically sortable.
*   **Pruning Invariant**: If a node is pruned, its original payload must still be retrievable via `get_receipt`.
*   **Size Invariant**: Compacted prompts must always be smaller than or equal to the uncompacted prompt.

### Schema Fuzzing

We wrote fuzzing scripts to feed randomly generated events (violating data types, containing empty fields, or containing malformed timestamps) into `validate_event()`. This verified that the schema validation engine successfully blocks invalid inputs before they reach the graph builder, guaranteeing that compile-time graph construction is safe.

### The Semantic Probe Suite

We implemented four key semantic probe tests in `tests/test_probes.py` to target specific failure modes:
1.  **Recall Probe**: Verifies that final successful variables survive while obsolete attempts (e.g., failed algorithms) are pruned.
2.  **Artifact-Tracking Probe**: Verifies that generated resource identifiers (like temp files) that are referenced downstream survive compaction.
3.  **Continuation Probe**: Verifies that active plan steps and overridden variables are resolved correctly, allowing the agent to resume mid-task.
4.  **Decision Probe**: Tightens assertions to verify that when failed approach A is abandoned in favor of approach B, the rationale for the choice survives while A's tool details are pruned.

---

## 5. Validation on Diverse Fixtures

To demonstrate that the pipeline generalizes beyond our initial demo story, we validated it on two structurally distinct agentic workflows.

### Fixture 1: Research Agent Trace (`research_agent_trace.json`)

Simulates a research agent investigating the cause of the Bronze Age Collapse.
*   **Structure**: Sets a `current_hypothesis` and a `best_source` variable, performs library searches, pursues an abandoned research path (Assyrian pottery migrations), and pivots to Larnaca Salt Lake climate proxy core data.
*   **Assertions**: Checks that the final conclusion survives, the Larnaca core data survives, the obsolete hypothesis variables are overridden, and the abandoned branch details do not leak.
*   **Metrics**:
    *   *Tokens Before*: 463
    *   *Tokens After*: 241
    *   *Pruned Nodes*: 10
    *   *Surviving Nodes*: 9

### Fixture 2: Customer Support Agent Trace (`support_agent_trace.json`)

Simulates a support agent processing a refund ticket.
*   **Structure**: Querying a legacy CRM is attempted and fails, leading to an abandoned branch. The agent pivots to a modern customer service tool, retrieves account details, issues a refund, updates the ticket status variable, and sends a confirmation email.
*   **Assertions**: Checks that the legacy CRM attempt is pruned. Verifies that the ticket ID (`tkt_5531`) is successfully recovered via `get_receipt("sa03")` even though its producing tool result node was pruned as part of the abandoned legacy branch.
*   **Metrics**:
    *   *Tokens Before*: 227
    *   *Tokens After*: 196
    *   *Pruned Nodes*: 5
    *   *Surviving Nodes*: 12

---

## 6. Benchmark Results

### Methodology Note: Exact Substring Matching
> [!NOTE]
> The decision probe checks for exact substring survival against the original event text. This structurally favors methods that preserve verbatim text (`truncate_by_event_count`, `truncate_by_token_count`, `context_gc_pipeline`) over methods that paraphrase (`ai_summarize_single`, `ai_summarize_recursive`) — a correctly-summarized, semantically accurate paraphrase can score 0% on this probe even when it retains the right information in different words. We report probe scores as-is because they're deterministic and reproducible, but this benchmark measures literal information survival, not downstream answer correctness. For a test of actual downstream answer correctness (an LLM answering a real question from compacted vs. full context), see the Scenario 5 stress-test result in the [Supplementary Finding: Live Answer-Quality Check](#supplementary-finding-live-answer-quality-check) section. We have not separately investigated the low artifact-accuracy scores for AI summarization on long traces, so this caveat does not extend to that metric either — it may reflect a genuine limitation of summarization, a different measurement artifact, or something else; it is simply unexamined.

To move beyond a single hand-tuned demo, Context-GC was benchmarked
against three alternatives — full history (no compaction), naive
truncation, and AI-driven summarization — across 9 fixtures (3 agent
types × 3 trace lengths), using real live API calls against Gemini 3.6
Flash for the summarization baselines (99 calls total, ~$0.0046 spent).

The full comparison table is in the README; the short version is this:
truncation produces the smallest output but destroys information
indiscriminately (0% recall/decision accuracy on longer traces).
AI-summarization does better on recall but consistently loses the
*reasoning* behind an agent's decisions — 0% decision accuracy across
every trace length tested — while adding real cost, multi-second
latency, and non-determinism.

**Context-GC was the only method to score 100% across all four semantic
probes — recall, artifact-tracking, continuation, and decision — on
every trace length, with zero cost, sub-millisecond latency, and full
determinism.** Its compression ratio is more conservative than the
alternatives by design: the receipts model means nothing is ever
permanently lost, only deferred behind a resolvable pointer.

One methodology bug is worth documenting honestly, since it's a good
example of the kind of verification this project has leaned on
throughout: a synthetic cycle injected into three medium-length fixtures
was initially being silently discarded before compaction ran, because it
was added to a live graph object that `compact()` rebuilds from scratch
internally. The benchmark runner's cycle-detection check was also
looking in the wrong internal data structure. Both were fixed — the
cycle injection now happens inside the actual graph-construction step
`compact()` calls, and the check was corrected to look in the right
place. The corrected numbers (reflecting real cycle-collapse behavior
for the first time) are what's reported above.

Two things are disclosed rather than hidden: Gemini Pro-tier comparison
could not be run (0 req/day quota available), so all summarization
figures are Flash-tier only; and one anomaly — `ai_summarize_recursive`
scoring 0% recall on medium traces but 100% on long traces — remains
unexplained and is reported as an open question rather than papered
over.

### Supplementary Finding: Live Answer-Quality Check

*   **Overview**: This is a small, separate semantic check (separate from the main benchmark) covering a single specific scenario: an abandoned execution branch containing a stale variable overwrite (e.g. `refill_rate` initially set to 5, temporarily overwritten to 8 in an abandoned path attempt, and then correctly resumed at 5).
*   **API Calls**: A total of 28 live API calls were made to Claude 3.5 Sonnet and Gemini 2.5 Flash across 4 separate runs, comparing uncompacted and compacted prompts on the identical target question. 
*   **Result**: 
    *   **Uncompacted Prompts**: Answered correctly **0/14** times (the model consistently latched onto the abandoned, noise-laden value `8`).
    *   **Compacted Prompts**: Answered correctly **14/14** times (the model successfully extracted the active value `5` due to the aborted branch being pruned).
*   **Scope & Limitation**: The other 4 scenarios defined in `scripts/run_answer_quality.py` were never run. Therefore, this finding is a narrow, illustrative data point specifically showing benefits in "stale abandoned-branch value" cases and should not be generalized beyond this case.
*   **Total Cost**: ~$0.0038 across all 28 calls.

---

## 7. Limitations and Future Work

While Context-GC provides a powerful, deterministic alternative to lossy summarization, it has explicit structural limitations:

1.  **Structured Events Only**: Compaction relies entirely on typed event metadata. If an agent records its history as a single freeform text log, Context-GC cannot reconstruct the dependency graph.
2.  **No Incremental Recomputation**: Calling `.compact()` rebuilds the entire state graph from scratch. For very long traces, this incurs linear processing overhead on each call. Future versions will implement true incremental compilation where updates are applied to the active graph in-place.
3.  **DAG Invariant**: The topological sampler requires sequence edges to be acyclic. While cycle collapse handles cycles, any sequence relationships that cannot be resolved topologically will block prompt rendering.

---

## 8. Prior Art and Comparison

Managing long-context window limits and token costs in agentic systems is an active engineering and research domain. Existing approaches include:

*   **Content-Level Compression (Headroom)**: Compresses the content of individual messages or tool outputs as they arrive (routing JSON, logs, or text to specialized per-type compressors, including the trained ML-based compressor *Kompress*) while leaving the historical conversation structure untouched to maximize provider KV-cache hits.
*   **OS-Inspired Memory Architectures**: Frameworks (such as MemGPT/Letta) that treat context management analogously to operating system paging, moving data between virtual memory (context) and disk (archival storage).
*   **Graph-based Memory Systems & Knowledge Graphs**: Tools (like Cognee) that structure agent experiences as entity-relation networks rather than linear logs.
*   **Hosted Memory & Vector Databases**: SaaS platforms and databases that offer retrieval-augmented generation (RAG) and search workflows over raw text memories.
*   **AI-Driven Summarization**: Naive LLM calls that periodically summarize history logs into shorter paragraphs.

### Headroom vs. Context-GC

A primary architectural distinction exists between **Headroom** and **Context-GC**:
*   **Headroom** compresses the *content* of individual messages/tool-outputs as they arrive—routing JSON/code/logs/text to per-type compressors (one of which, *Kompress*, uses a trained ML model, not pure determinism), and explicitly leaves prior conversation history untouched to preserve provider KV-cache hits. Headroom decides what to keep small on the way in.
*   **Context-GC** solves a different layer: given an agent's already-accumulated structured event history, it identifies which parts are now dead (superseded, abandoned, or cyclical) and structurally removes them. Context-GC decides what should still exist at all once it is already there.

The two approaches are complementary rather than competing: Headroom shrinks new incoming tool outputs, while Context-GC prunes stale state from history. Furthermore, Context-GC's entire pipeline has zero ML/AI models anywhere, including in the pruning logic itself, whereas Headroom's is deterministic for some content types but uses a trained model for general text. Additionally, Headroom's memory-layer deduplication explicitly relies on an LLM call to judge whether two facts should be merged ('LLM-Mediated Dedup'), whereas Context-GC's deduplication is exact-match on tool name, arguments, and result — fully deterministic, with no model call anywhere in the decision.

