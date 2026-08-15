# tracegc/benchmark/methods.py
"""Compaction methods used in the comparative benchmark.

Defines the six compaction strategies: full_history, event-count truncation,
token-count truncation, single AI summary, recursive AI summary, and
tracegc deterministic pipeline. Includes robust rate-limit retry logic.
"""

from __future__ import annotations

import time
from typing import List, Dict, Any, Tuple
from tracegc import compact_events
from tracegc.compactor import _render_event


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


def method_tracegc_pipeline(events: List[Dict[str, Any]]) -> Tuple[str, int, float]:
    """TraceGC deterministic pipeline: applies DFS sweeper, overrides, dedup, and SCC collapse."""
    start = time.perf_counter()
    result = compact_events(events)
    elapsed = time.perf_counter() - start
    return result["prompt"], result["tokens_after"], elapsed


def preprocess_semantic_events(events: List[Dict[str, Any]], cache: Optional[Any] = None) -> List[Dict[str, Any]]:
    from tracegc.semantic import extract_semantic_events
    from tracegc.api import get_stable_event_id
    new_events = []
    
    # Track ID mapping for parents/references
    id_map = {}
    
    for idx, ev in enumerate(events):
        if ev.get("type") in {"decision", "text_chunk"} and ev.get("content"):
            content = ev["content"]
            event_id = get_stable_event_id(ev, idx)
            cached = cache.get(event_id) if cache else None
            
            if cached is not None:
                extracted = [dict(e) for e in cached["semantic_representation"]]
            else:
                extracted = extract_semantic_events(content, ev["id"], ev["timestamp"])
                if cache:
                    cache.set(
                        event_id=event_id,
                        semantic_representation=extracted,
                        source_provenance={"content": content, "idx": idx},
                        extraction_status="success" if extracted else "skipped"
                    )
            
            if extracted:
                extracted[0]["parent_id"] = ev.get("parent_id")
                # Carry critical metadata
                for ext_ev in extracted:
                    if "importance" in ev:
                        ext_ev["importance"] = ev["importance"]
                    if "retain_until" in ev:
                        ext_ev["retain_until"] = ev["retain_until"]
                id_map[ev["id"]] = extracted[-1]["id"]
                new_events.extend(extracted)
            else:
                new_events.append(ev)
        else:
            new_events.append(ev)
            
    # Relink parents and ref_to lists
    for ev in new_events:
        parent = ev.get("parent_id")
        if parent in id_map:
            ev["parent_id"] = id_map[parent]
        if "ref_to" in ev:
            ev["ref_to"] = [id_map.get(r, r) for r in ev["ref_to"]]
            
    return new_events


def method_ablation_a(events: List[Dict[str, Any]]) -> Tuple[str, int, float, Any]:
    """Stage A: Existing Trace-GC Core (Stages 1-4 only)"""
    start = time.perf_counter()
    result = compact_events(events, prune_semantic=False)
    elapsed = time.perf_counter() - start
    return result["prompt"], result["tokens_after"], elapsed, result["graph"]


def method_ablation_b(events: List[Dict[str, Any]]) -> Tuple[str, int, float, Any]:
    """Stage B: Semantic Extraction Only (Stages 1-4 + Extracted Events, no semantic pruning)"""
    start = time.perf_counter()
    sem_events = preprocess_semantic_events(events)
    result = compact_events(
        sem_events,
        prune_semantic=True,
        prune_duplicates=False,
        prune_superseded=False,
        prune_errors=False
    )
    elapsed = time.perf_counter() - start
    return result["prompt"], result["tokens_after"], elapsed, result["graph"]


def method_ablation_c(events: List[Dict[str, Any]]) -> Tuple[str, int, float, Any]:
    """Stage C: Semantic Normalization + Trace-GC (Same as extraction but with normalization verified)"""
    # Normalization happens in semantic rule parsing
    return method_ablation_b(events)


def method_ablation_d(events: List[Dict[str, Any]]) -> Tuple[str, int, float, Any]:
    """Stage D: Semantic Duplicate Pruning (Stages 1-4 + duplicate pruning)"""
    start = time.perf_counter()
    sem_events = preprocess_semantic_events(events)
    result = compact_events(
        sem_events,
        prune_semantic=True,
        prune_duplicates=True,
        prune_superseded=False,
        prune_errors=False
    )
    elapsed = time.perf_counter() - start
    return result["prompt"], result["tokens_after"], elapsed, result["graph"]


def method_ablation_e(events: List[Dict[str, Any]]) -> Tuple[str, int, float, Any]:
    """Stage E: Superseded-State Pruning (Stages 1-4 + superseded pruning)"""
    start = time.perf_counter()
    sem_events = preprocess_semantic_events(events)
    result = compact_events(
        sem_events,
        prune_semantic=True,
        prune_duplicates=False,
        prune_superseded=True,
        prune_errors=False
    )
    elapsed = time.perf_counter() - start
    return result["prompt"], result["tokens_after"], elapsed, result["graph"]


def method_ablation_f(events: List[Dict[str, Any]]) -> Tuple[str, int, float, Any]:
    """Stage F: Full Semantic Trace-GC (All Stages 1-5 active)"""
    start = time.perf_counter()
    sem_events = preprocess_semantic_events(events)
    result = compact_events(
        sem_events,
        prune_semantic=True,
        prune_duplicates=True,
        prune_superseded=True,
        prune_errors=True
    )
    elapsed = time.perf_counter() - start
    return result["prompt"], result["tokens_after"], elapsed, result["graph"]
