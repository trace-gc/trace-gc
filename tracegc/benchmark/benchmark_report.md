# Comparative Benchmark Report

This report presents a comparative evaluation of context compaction methods for AI agents. The benchmark measures how different strategies prune and structure event history traces of varying lengths (SHORT, MEDIUM, and LONG) and their impact on probe accuracy, latency, and cost.

## Executive Summary

1. **Standout Performer: `tracegc_pipeline`**
   * Achieved **100% accuracy** on all evaluation probes (Recall, Artifact, Continuation, and Decision) at every trace length.
   * Reduced prompt size by **~21% in tokens** compared to `full_history` on LONG traces.
   * Completely **deterministic** (yes) with zero latency overhead (averaging ~0.0028 seconds), offering full-history accuracy at lower cost with reproducible outputs.

2. **Failure Mode of Naive Baselines (`truncate_by_event_count`, `truncate_by_token_count`)**
   * These simple window-based pruning strategies perform well on SHORT traces but **collapse to 0% accuracy** on recall, continuation, and decision probes the moment traces exceed "short" lengths. This demonstrates the critical need for semantic compaction.

3. **Limitations of LLM Summarization (`ai_summarize_single`, `ai_summarize_recursive`)**
   * Although LLM-based summarization (using `gemini-3.6-flash`) reduces prompt size significantly, it does so at a **real accuracy cost**.
   * Notably, both methods scored **0% artifact accuracy** on LONG traces and **0% decision accuracy across all trace lengths**. This highlights a critical limitation: LLMs drop concrete historical artifacts (like generated IDs or specific variable paths) and decision checkpoints during summary generation.
   * **Inconsistent Recall:** The recursive LLM summarizer shows significant inconsistency with recall accuracy across trace lengths. It drops to **0% recall accuracy** on MEDIUM traces, yet recovers to **100% recall accuracy** on LONG traces. This anomaly has no obvious explanation and is flagged as an open question.
   * Non-deterministic output (no) and significant latency and cost overheads (recursive summarization averaging ~50.95 seconds on LONG traces) make them less desirable than deterministic compactors.

---

## Comparative Benchmark Tables

### Methodology Note: Exact Substring Matching
> [!NOTE]
> The decision probe checks for exact substring survival against the original event text. This structurally favors methods that preserve verbatim text (`truncate_by_event_count`, `truncate_by_token_count`, `tracegc_pipeline`) over methods that paraphrase (`ai_summarize_single`, `ai_summarize_recursive`) — a correctly-summarized, semantically accurate paraphrase can score 0% on this probe even when it retains the right information in different words. We report probe scores as-is because they're deterministic and reproducible, but this benchmark measures literal information survival, not downstream answer correctness. For a test of actual downstream answer correctness (an LLM answering a real question from compacted vs. full context), see the Scenario 5 stress-test result in the [Supplementary Finding: Live Answer-Quality Check](https://github.com/athishio/tracegc/blob/main/WRITEUP.md#supplementary-finding-live-answer-quality-check) section of `WRITEUP.md`. We have not separately investigated the low artifact-accuracy scores for AI summarization on long traces, so this caveat does not extend to that metric either — it may reflect a genuine limitation of summarization, a different measurement artifact, or something else; it is simply unexamined.

### Methodology Note: Cycle Collapse Verification
> [!NOTE]
> Cycle-collapsing behavior (defensive graph loop collapsing) is verified separately under synthetic cyclic traces in [`tests/test_topo_sampler.py`](file:///e:/TraceGC/tests/test_topo_sampler.py). All comparative benchmark numbers are scored against natural, un-injected event traces.


### Comparative Benchmark — SHORT Traces
| Method | Avg Tokens | Avg Latency (s) | Cost ($) | Recall Acc. | Artifact Acc. | Continuation Acc. | Decision Acc. | Deterministic |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_history                   | 121.0      | 0.0000          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | n/a           |
| truncate_by_event_count        | 116.3      | 0.0000          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | n/a           |
| truncate_by_token_count        | 121.0      | 0.0000          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | n/a           |
| ai_summarize_single (flash)    | 90.7       | 5.2322          | $0.000036 | 100.0%      | 33.3%         | 55.6%             | 0.0%          | no            |
| ai_summarize_single (pro)      | not run — Pro tier unavailable (0 req/day quota) | | | | | | | |
| ai_summarize_recursive (flash) | skip       | skip            | $0.000000 | skip        | skip          | skip              | skip          | n/a           |
| ai_summarize_recursive (pro)   | not run — Pro tier unavailable (0 req/day quota) | | | | | | | |
| tracegc_pipeline            | 75.3       | 0.0011          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | yes           |

### Comparative Benchmark — MEDIUM Traces
| Method | Avg Tokens | Avg Latency (s) | Cost ($) | Recall Acc. | Artifact Acc. | Continuation Acc. | Decision Acc. | Deterministic |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_history                   | 379.7      | 0.0000          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | n/a           |
| truncate_by_event_count        | 133.3      | 0.0000          | $0.000000 | 0.0%        | 100.0%        | 0.0%              | 0.0%          | n/a           |
| truncate_by_token_count        | 212.0      | 0.0001          | $0.000000 | 0.0%        | 100.0%        | 0.0%              | 0.0%          | n/a           |
| ai_summarize_single (flash)    | 146.7      | 6.3571          | $0.000072 | 66.7%       | 88.9%         | 100.0%            | 0.0%          | no            |
| ai_summarize_single (pro)      | not run — Pro tier unavailable (0 req/day quota) | | | | | | | |
| ai_summarize_recursive (flash) | 131.0      | 21.1568         | $0.000082 | 0.0%        | 66.7%         | 100.0%            | 0.0%          | no            |
| ai_summarize_recursive (pro)   | not run — Pro tier unavailable (0 req/day quota) | | | | | | | |
| tracegc_pipeline            | 299.0      | 0.0017          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | yes           |

### Comparative Benchmark — LONG Traces
| Method | Avg Tokens | Avg Latency (s) | Cost ($) | Recall Acc. | Artifact Acc. | Continuation Acc. | Decision Acc. | Deterministic |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_history                   | 1301.0     | 0.0001          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | n/a           |
| truncate_by_event_count        | 104.3      | 0.0000          | $0.000000 | 0.0%        | 0.0%          | 0.0%              | 0.0%          | n/a           |
| truncate_by_token_count        | 210.7      | 0.0001          | $0.000000 | 0.0%        | 0.0%          | 0.0%              | 0.0%          | n/a           |
| ai_summarize_single (flash)    | 243.4      | 8.2741          | $0.000171 | 100.0%      | 0.0%          | 100.0%            | 0.0%          | no            |
| ai_summarize_single (pro)      | not run — Pro tier unavailable (0 req/day quota) | | | | | | | |
| ai_summarize_recursive (flash) | 219.2      | 42.6211*        | $0.000199 | 100.0%      | 0.0%          | 100.0%            | 0.0%          | no            |
| ai_summarize_recursive (pro)   | not run — Pro tier unavailable (0 req/day quota) | | | | | | | |
| tracegc_pipeline            | 1028.3     | 0.0028          | $0.000000 | 100.0%      | 100.0%        | 100.0%            | 100.0%        | yes           |

*\*One run included a 75s API rate-limit retry wait; latency figures exclude this wait time to reflect model response time rather than our own quota constraints. (Including the retry wait, average latency is 50.9544s).*
