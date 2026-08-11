# trace_gc/benchmark/methods.py
"""Compaction methods used in the comparative benchmark.

Defines the six compaction strategies: full_history, event-count truncation,
token-count truncation, single AI summary, recursive AI summary, and
trace-gc deterministic pipeline. Includes robust rate-limit retry logic.
"""

from __future__ import annotations

import time
from typing import List, Dict, Any, Tuple
from trace_gc import compact_events
from trace_gc.compactor import _render_event


def generate_with_retry(gemini_client: Any, model: str, contents: str, config: dict) -> Any:
    """Send content generation request to Gemini with exponential backoff on 429 errors."""
    from google.genai.errors import APIError
    
    max_retries = 5
    for attempt in range(max_retries):
        try:
            return gemini_client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except APIError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                if "quota exceeded" in str(e).lower() and ("limit: 20" in str(e).lower() or "daily" in str(e).lower() or "perday" in str(e).lower()):
                    raise RuntimeError(f"DAILY_QUOTA_EXCEEDED: {e}")
                print(f"\n[429 Rate Limit] Hit limit on {model}: {e}. Waiting 75s for window reset (attempt {attempt+1}/{max_retries})...", flush=True)
                time.sleep(75)
            elif "503" in str(e) or "UNAVAILABLE" in str(e):
                print(f"\n[503 Unavailable] Hit unavailable on {model}: {e}. Waiting 20s before retry (attempt {attempt+1}/{max_retries})...", flush=True)
                time.sleep(20)
            else:
                raise e
    raise RuntimeError(f"Exceeded maximum retries for Gemini API due to rate limits on model {model}.")


def method_full_history(events: List[Dict[str, Any]]) -> Tuple[str, int, float]:
    """Control method: preserves the entire event log history with no pruning."""
    start = time.perf_counter()
    strings = [_render_event(ev) for ev in events]
    prompt = "\n".join(filter(None, strings))
    tokens = len(prompt) // 4
    elapsed = time.perf_counter() - start
    return prompt, tokens, elapsed


def method_truncate_by_event_count(
    events: List[Dict[str, Any]], limit: int = 15
) -> Tuple[str, int, float]:
    """Truncates the event log by keeping only the last N events."""
    start = time.perf_counter()
    truncated = events[-limit:] if len(events) > limit else events
    strings = [_render_event(ev) for ev in truncated]
    prompt = "\n".join(filter(None, strings))
    tokens = len(prompt) // 4
    elapsed = time.perf_counter() - start
    return prompt, tokens, elapsed


def method_truncate_by_token_count(
    events: List[Dict[str, Any]], token_limit: int = 200
) -> Tuple[str, int, float]:
    """Truncates the event log by keeping only the last N tokens' worth of events."""
    start = time.perf_counter()
    selected = []
    current_tokens = 0
    for ev in reversed(events):
        rendered = _render_event(ev)
        if not rendered:
            continue
        ev_tokens = len(rendered) // 4
        if current_tokens + ev_tokens > token_limit:
            if not selected:
                selected.append(ev)
            break
        selected.append(ev)
        current_tokens += ev_tokens
    selected.reverse()
    strings = [_render_event(ev) for ev in selected]
    prompt = "\n".join(filter(None, strings))
    tokens = len(prompt) // 4
    elapsed = time.perf_counter() - start
    return prompt, tokens, elapsed


def method_ai_summarize_single(
    events: List[Dict[str, Any]], gemini_client: Any, model: str = "gemini-2.5-flash"
) -> Tuple[str, int, float]:
    """Summarizes the full uncompacted event log history in a single Gemini call."""
    start = time.perf_counter()
    full_prompt, _, _ = method_full_history(events)
    
    system_prompt = (
        "You are an expert context summarizer for AI agents. "
        "Your task is to summarize the following event history log into a dense, compact text representation "
        "retaining all active variables, successful outcomes, final decisions, and key details (like file paths or generated IDs). "
        "Do NOT include details from abandoned branches, failed attempts, or overridden/superseded variables. "
        "Ensure the output is clean and formatted for another AI model to continue the task."
    )
    
    response = generate_with_retry(
        gemini_client=gemini_client,
        model=model,
        contents=full_prompt,
        config={
            "system_instruction": system_prompt,
            "temperature": 0.0,
        }
    )
    summary = response.text.strip()
    tokens = len(summary) // 4
    elapsed = time.perf_counter() - start
    return summary, tokens, elapsed


def method_ai_summarize_recursive(
    events: List[Dict[str, Any]], 
    gemini_client: Any, 
    model: str = "gemini-2.5-flash", 
    threshold: int = 50, 
    chunk_size: int = 25
) -> Tuple[str, int, float]:
    """Summarizes the event log recursively by chunking if size exceeds a threshold."""
    start = time.perf_counter()
    if len(events) <= threshold:
        # Skipped on short/medium traces below threshold
        return "", 0, 0.0
        
    chunks = [events[i:i + chunk_size] for i in range(0, len(events), chunk_size)]
    chunk_summaries = []
    
    for idx, chunk in enumerate(chunks):
        chunk_prompt, _, _ = method_full_history(chunk)
        system_prompt = (
            f"Summarize part {idx+1}/{len(chunks)} of the event log history. "
            "Extract only key facts, variables, final decisions, and active task progress."
        )
        response = generate_with_retry(
            gemini_client=gemini_client,
            model=model,
            contents=chunk_prompt,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.0,
            }
        )
        chunk_summaries.append(response.text.strip())
        
    combined_prompt = "\n\n=== Part Summary ===\n\n".join(chunk_summaries)
    system_prompt = (
        "Consolidate these part summaries into a single dense, compact, unified history representation. "
        "Retain all active variables, successful outcomes, final decisions, and key details. "
        "Discard any references to failed, overridden, or abandoned paths."
    )
    response = generate_with_retry(
        gemini_client=gemini_client,
        model=model,
        contents=combined_prompt,
        config={
            "system_instruction": system_prompt,
            "temperature": 0.0,
        }
    )
    summary = response.text.strip()
    tokens = len(summary) // 4
    elapsed = time.perf_counter() - start
    return summary, tokens, elapsed


def method_trace_gc_pipeline(events: List[Dict[str, Any]]) -> Tuple[str, int, float]:
    """TraceGC deterministic pipeline: applies DFS sweeper, overrides, dedup, and SCC collapse."""
    start = time.perf_counter()
    result = compact_events(events)
    elapsed = time.perf_counter() - start
    return result["prompt"], result["tokens_after"], elapsed
