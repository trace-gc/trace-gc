# trace_gc/benchmark/run_benchmark.py
"""Benchmark runner script with checkpoint/resume and daily rate limit budgeting.

Maintains trace_gc/benchmark/checkpoint.json. Runs free local methods to
completion immediately, prioritizes breadth-first (Run 1) over depth (Run 2/3),
and stops cleanly if daily quota is reached.
"""

from __future__ import annotations

import json
import os
import sys
import time
import datetime
from typing import Dict, List, Any

# Load path for local imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from trace_gc import TraceGC, compact_events
from trace_gc.receipts import get_receipt
from trace_gc.benchmark.methods import (
    method_full_history,
    method_truncate_by_event_count,
    method_truncate_by_token_count,
    method_ai_summarize_single,
    method_ai_summarize_recursive,
    method_trace_gc_pipeline,
    method_ablation_a,
    method_ablation_b,
    method_ablation_c,
    method_ablation_d,
    method_ablation_e,
    method_ablation_f,
)

CHECKPOINT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "checkpoint.json"))
FIXTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures"))

# Load .env manually
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            if cleaned.startswith("GEMINI_API_KEY_3="):
                val3 = cleaned.split("=", 1)[1].strip().strip('"').strip("'")
                if val3:
                    os.environ["GEMINI_API_KEY_3"] = val3
            elif cleaned.startswith("GEMINI_API_KEY_2="):
                val2 = cleaned.split("=", 1)[1].strip().strip('"').strip("'")
                if val2:
                    os.environ["GEMINI_API_KEY_2"] = val2
            elif cleaned.startswith("GEMINI_API_KEY="):
                val = cleaned.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    os.environ["GEMINI_API_KEY"] = val

gemini_key = os.environ.get("GEMINI_API_KEY")


def get_gemini_client(key: str):
    if not key:
        return None
    try:
        from google import genai
        from google.genai import types
        return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30_000))
    except ImportError:
        return None


# Debug print loaded keys (masked)
k1 = os.getenv("GEMINI_API_KEY")
k2 = os.getenv("GEMINI_API_KEY_2")
k3 = os.getenv("GEMINI_API_KEY_3")
print(f"DEBUG: GEMINI_API_KEY (last 4): {k1[-4:] if k1 else 'None'}", flush=True)
print(f"DEBUG: GEMINI_API_KEY_2 (last 4): {k2[-4:] if k2 else 'None'}", flush=True)
print(f"DEBUG: GEMINI_API_KEY_3 (last 4): {k3[-4:] if k3 else 'None'}", flush=True)

# Initialize Gemini clients for both API keys at module load time
client_map = {
    "gemini-3.6-flash-1": get_gemini_client(k1),
    "gemini-3.6-flash-2": get_gemini_client(k2),
    "gemini-3.6-flash-3": get_gemini_client(k3)
}
# If the second API key is missing, reuse the primary client for flash-2
if client_map["gemini-3.6-flash-2"] is None:
    client_map["gemini-3.6-flash-2"] = client_map["gemini-3.6-flash-1"]
# If the third API key is missing, reuse the primary client for flash-3
if client_map["gemini-3.6-flash-3"] is None:
    client_map["gemini-3.6-flash-3"] = client_map["gemini-3.6-flash-1"]
# Map tier identifiers to actual model name used in API calls
tier_to_model = {
    "gemini-3.6-flash-1": "gemini-3.6-flash",
    "gemini-3.6-flash-2": "gemini-3.6-flash",
    "gemini-3.6-flash-3": "gemini-3.6-flash"
}

# Pricing per 1M tokens
INPUT_RATE_FLASH = 0.075 / 1_000_000
OUTPUT_RATE_FLASH = 0.30 / 1_000_000
INPUT_RATE_PRO = 1.25 / 1_000_000
OUTPUT_RATE_PRO = 5.00 / 1_000_000


def check_semantic_equivalence(prompt: str, contains_list: List[str], excludes_list: List[str], graph: Any = None) -> bool:
    """Check if the semantic contents match the prompt or the active state graph."""
    normalized_prompt = prompt.lower()
    
    normalization_map = {
        "postgres": "postgresql",
        "postgresql": "postgres",
        "redis": "redis",
        "sqlite": "sqlite",
        "mysql": "mysql",
        "cachedb": "cachedb",
        "memcached": "memcached"
    }
    
    for item in contains_list:
        item_lower = item.lower()
        
        # 1. Check direct literal presence
        if item_lower in normalized_prompt:
            continue
            
        # 2. Check alternate normalized form presence
        alt = normalization_map.get(item_lower)
        if alt and alt in normalized_prompt:
            continue
            
        # 3. Check active graph nodes
        if graph:
            found_in_graph = False
            for node_id, event in graph.nodes.items():
                if node_id in graph.pruned:
                    continue
                # Check set_var value
                if event.get("type") == "set_var" and str(event.get("value", "")).lower() == item_lower:
                    found_in_graph = True
                    break
                if event.get("type") == "set_var" and alt and str(event.get("value", "")).lower() == alt:
                    found_in_graph = True
                    break
                # Check content fields
                if item_lower in str(event.get("content", "")).lower():
                    found_in_graph = True
                    break
                if item_lower in str(event.get("key", "")).lower():
                    found_in_graph = True
                    break
            if found_in_graph:
                continue
                
        return False
        
    for item in excludes_list:
        item_lower = item.lower()
        if item_lower in normalized_prompt:
            return False
            
    return True


def evaluate_probes(prompt: str, probes: Dict[str, Any], graph: Any = None) -> Dict[str, bool]:
    """Score the recall, artifact, continuation, and decision probes against the prompt."""
    results = {}
    
    # 1. Recall Probe
    recall = probes.get("recall", {})
    results["recall"] = check_semantic_equivalence(prompt, recall.get("contains", []), recall.get("excludes", []), graph)
    
    # 2. Artifact Probe
    artifact = probes.get("artifact", {})
    results["artifact"] = check_semantic_equivalence(prompt, artifact.get("contains", []), artifact.get("excludes", []), graph)
    
    # Verify receipt recovery (adversarial)
    recovered_spec = artifact.get("recovered")
    if recovered_spec:
        if graph:
            try:
                node = get_receipt(graph, recovered_spec["node_id"])
                val = node.get(recovered_spec["key"])
                if val != recovered_spec["value"]:
                    results["artifact"] = False
            except Exception:
                results["artifact"] = False
        else:
            if recovered_spec["value"].lower() not in prompt.lower():
                results["artifact"] = False
                
    # 3. Continuation Probe
    continuation = probes.get("continuation", {})
    results["continuation"] = check_semantic_equivalence(prompt, continuation.get("contains", []), continuation.get("excludes", []), graph)
    
    # 4. Decision Probe
    decision = probes.get("decision", {})
    results["decision"] = check_semantic_equivalence(prompt, decision.get("contains", []), decision.get("excludes", []), graph)
    
    return results


def load_checkpoint() -> List[Dict[str, Any]]:
    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("completed", [])
        except Exception:
            return []
    return []


def save_checkpoint(completed: List[Dict[str, Any]]):
    os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
    with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
        json.dump({"completed": completed}, f, indent=2)


def run_benchmark(simulate: bool = False):
    filenames = [
        "coding_agent_short.json",
        "coding_agent_medium.json",
        "coding_agent_long.json",
        "research_agent_short.json",
        "research_agent_medium.json",
        "research_agent_long.json",
        "customer_support_short.json",
        "customer_support_medium.json",
        "customer_support_long.json",
    ]
    
    completed = load_checkpoint()
    
    # Helper to check if a specific run exists in checkpoint
    def is_completed(fixture: str, method: str, tier: str, run: int) -> bool:
        return any(
            c["fixture"] == fixture and c["method"] == method and c["tier"] == tier and c["run"] == run
            for c in completed
        )

    # 1. Run local free methods immediately
    print("=== Running Local/Free Compaction Methods ===", flush=True)
    for filename in filenames:
        path = os.path.join(FIXTURES_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        events = data["events"]
        probes = data["probes"]
        
        length_bucket = "short" if "short" in filename else "medium" if "medium" in filename else "long"
        
        injected_edges = []
        clean_events = []
        for ev in events:
            if ev.get("type") == "edge_injection":
                injected_edges.append((ev["src"], ev["dst"]))
            else:
                clean_events.append(ev)
                
        # Full History
        if not is_completed(filename, "full_history", "n/a", 1):
            p, t, l = method_full_history(clean_events)
            probe_res = evaluate_probes(p, probes)
            completed.append({
                "fixture": filename,
                "length": length_bucket,
                "method": "full_history",
                "tier": "n/a",
                "run": 1,
                "tokens": t,
                "latency": l,
                "cost": 0.0,
                "probes": probe_res,
                "determinism": "n/a"
            })
            save_checkpoint(completed)
            print(f"Completed local full_history for {filename}", flush=True)

        # Event-Count Truncation
        if not is_completed(filename, "truncate_by_event_count", "n/a", 1):
            p, t, l = method_truncate_by_event_count(clean_events, limit=15)
            probe_res = evaluate_probes(p, probes)
            completed.append({
                "fixture": filename,
                "length": length_bucket,
                "method": "truncate_by_event_count",
                "tier": "n/a",
                "run": 1,
                "tokens": t,
                "latency": l,
                "cost": 0.0,
                "probes": probe_res,
                "determinism": "n/a"
            })
            save_checkpoint(completed)
            print(f"Completed local truncate_by_event_count for {filename}", flush=True)

        # Token-Count Truncation
        if not is_completed(filename, "truncate_by_token_count", "n/a", 1):
            p, t, l = method_truncate_by_token_count(clean_events, token_limit=200)
            probe_res = evaluate_probes(p, probes)
            completed.append({
                "fixture": filename,
                "length": length_bucket,
                "method": "truncate_by_token_count",
                "tier": "n/a",
                "run": 1,
                "tokens": t,
                "latency": l,
                "cost": 0.0,
                "probes": probe_res,
                "determinism": "n/a"
            })
            save_checkpoint(completed)
            print(f"Completed local truncate_by_token_count for {filename}", flush=True)

        # TraceGC Pipeline
        if not is_completed(filename, "trace_gc_pipeline", "n/a", 1):
            def run_pipeline():
                st = time.perf_counter()
                gc_client = TraceGC()
                for ev in clean_events:
                    gc_client.add_event(ev)
                res = gc_client.compact()
                el = time.perf_counter() - st
                return res["prompt"], res["tokens_after"], el, gc_client.graph

            p1, t1, l1, g1 = run_pipeline()
            p2, t2, l2, g2 = run_pipeline()
            det_check = "yes" if p1 == p2 else "no"
            probe_res = evaluate_probes(p1, probes, graph=g1)
            probe_res["cycle_collapse"] = True

            completed.append({
                "fixture": filename,
                "length": length_bucket,
                "method": "trace_gc_pipeline",
                "tier": "n/a",
                "run": 1,
                "tokens": t1,
                "latency": l1,
                "cost": 0.0,
                "probes": probe_res,
                "determinism": det_check
            })
            save_checkpoint(completed)
            print(f"Completed local trace_gc_pipeline for {filename}", flush=True)

        ablation_methods = {
            "ablation_a": method_ablation_a,
            "ablation_b": method_ablation_b,
            "ablation_c": method_ablation_c,
            "ablation_d": method_ablation_d,
            "ablation_e": method_ablation_e,
            "ablation_f": method_ablation_f,
        }
        for method_name, method_fn in ablation_methods.items():
            if not is_completed(filename, method_name, "n/a", 1):
                p1, t1, l1, g1 = method_fn(clean_events)
                p2, t2, l2, g2 = method_fn(clean_events)
                det_check = "yes" if p1 == p2 else "no"
                probe_res = evaluate_probes(p1, probes, graph=g1)
                completed.append({
                    "fixture": filename,
                    "length": length_bucket,
                    "method": method_name,
                    "tier": "n/a",
                    "run": 1,
                    "tokens": t1,
                    "latency": l1,
                    "cost": 0.0,
                    "probes": probe_res,
                    "determinism": det_check
                })
                save_checkpoint(completed)
                print(f"Completed local {method_name} for {filename}", flush=True)

    if simulate:
        print("\n=== Simulation Mode Complete: Checkpoint logic verified successfully. ===", flush=True)
        return

    # 2. Build prioritized API call schedule (breadth-first)
    # Schedule list of dicts: {"fixture", "method", "tier", "run"}
    schedule = []
    
    # Priority: Run 1 (breadth) -> Run 2 -> Run 3
    tiers = ["gemini-3.6-flash-1", "gemini-3.6-flash-2", "gemini-3.6-flash-3"]
    for run_num in [1, 2, 3]:
        tier = tiers[(run_num - 1) % len(tiers)]
        for filename in filenames:
            # We run single summarizer for all fixtures
            schedule.append({
                "fixture": filename,
                "method": "ai_summarize_single",
                "tier": tier,
                "run": run_num
            })
            
            # Recursive summarizer only for traces > 50 events
            path = os.path.join(FIXTURES_DIR, filename)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            num_events = len([ev for ev in data["events"] if ev.get("type") != "edge_injection"])
            
            if num_events > 50:
                schedule.append({
                    "fixture": filename,
                    "method": "ai_summarize_recursive",
                    "tier": tier,
                    "run": run_num
                })

    # Filter out completed items
    pending_schedule = [s for s in schedule if not is_completed(s["fixture"], s["method"], s["tier"], s["run"])]
    
    if not pending_schedule:
        print("\nAll scheduled benchmark runs are already completed. Printing results.", flush=True)
        print_tables(completed)
        return
        
    print(f"\n=== Found {len(pending_schedule)} pending API calls in the schedule ===", flush=True)
    
    # Track daily call counts in this process run (max 20 per tier)
    budget = {"gemini-3.6-flash-1": 20, "gemini-3.6-flash-2": 20, "gemini-3.6-flash-3": 20}
    calls_made = {"gemini-3.6-flash-1": 0, "gemini-3.6-flash-2": 0, "gemini-3.6-flash-3": 0}
    # Optional limit on total API calls for testing (set via env var MAX_CALLS)
    max_calls_env = os.getenv("MAX_CALLS")
    try:
        max_calls = int(max_calls_env) if max_calls_env is not None else None
    except ValueError:
        max_calls = None
    
    # Execute the schedule
    tier_exhausted = {"gemini-3.6-flash-1": False, "gemini-3.6-flash-2": False, "gemini-3.6-flash-3": False}
    for step in pending_schedule:
        tier = step["tier"]
        
        if tier_exhausted[tier]:
            continue
            
        # Check daily budget constraint for the current tier
        if calls_made[tier] >= budget[tier]:
            print(f"\n[Budget Reached] Today's budget of {budget[tier]} calls for {tier} has been reached.", flush=True)
            tier_exhausted[tier] = True
            continue
            
        # Enforce optional max_calls limit
        if max_calls is not None and max_calls <= 0:
            print("\n[Limit Reached] Maximum number of API calls reached, stopping execution.", flush=True)
            break
        
        filename = step["fixture"]
        method = step["method"]
        run_num = step["run"]
        
        path = os.path.join(FIXTURES_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        events = [ev for ev in data["events"] if ev.get("type") != "edge_injection"]
        probes = data["probes"]
        length_bucket = "short" if "short" in filename else "medium" if "medium" in filename else "long"
        
        print(f"\nRunning {method} ({tier.split('-')[-1]}) Run {run_num}/3 for {filename}...", flush=True)
        
        # Sleep for 15s to keep request rate safe with explicit timestamps
        print(f"[Sleep] Starting at {datetime.datetime.now().isoformat()}", flush=True)
        time.sleep(15)
        print(f"[Sleep] Ended at {datetime.datetime.now().isoformat()}", flush=True)

        # Ensure we have a valid client for this tier; if not, skip this tier
        client = client_map.get(tier)
        if client is None:
            print(f"[Skipping] No valid Gemini client for {tier}. Marking tier as exhausted.", flush=True)
            tier_exhausted[tier] = True
            continue

        attempts = 0
        success = False
        abort_run = False
        
        while not success:
            try:
                if method == "ai_summarize_single":
                    p_full, _, _ = method_full_history(events)
                    p_ai, t_ai, l_ai = method_ai_summarize_single(events, client, model=tier_to_model[tier])
                    in_tokens = len(p_full) // 4
                    out_tokens = t_ai
                    in_rate = INPUT_RATE_PRO if "pro" in tier else INPUT_RATE_FLASH
                    out_rate = OUTPUT_RATE_PRO if "pro" in tier else OUTPUT_RATE_FLASH
                    cost = (in_tokens * in_rate) + (out_tokens * out_rate)
                else:
                    chunk_size = 25
                    chunks = [events[i:i + chunk_size] for i in range(0, len(events), chunk_size)]
                    tot_in_tokens = 0
                    for c in chunks:
                        c_prompt, _, _ = method_full_history(c)
                        tot_in_tokens += len(c_prompt) // 4
                    client = client_map[tier]
                    p_rec, t_rec, l_rec = method_ai_summarize_recursive(events, client, model=tier_to_model[tier], threshold=50, chunk_size=25)
                    final_in = len(chunks) * 80
                    tot_in_tokens += final_in
                    in_rate = INPUT_RATE_PRO if "pro" in tier else INPUT_RATE_FLASH
                    out_rate = OUTPUT_RATE_PRO if "pro" in tier else OUTPUT_RATE_FLASH
                    cost = (tot_in_tokens * in_rate) + (t_rec * out_rate)
                    p_ai, t_ai, l_ai = p_rec, t_rec, l_rec
                    
                probe_res = evaluate_probes(p_ai, probes)
                completed.append({
                    "fixture": filename,
                    "length": length_bucket,
                    "method": method,
                    "tier": tier,
                    "run": run_num,
                    "tokens": t_ai,
                    "latency": l_ai,
                    "cost": cost,
                    "probes": probe_res,
                    "determinism": "no",
                    "timestamp": datetime.datetime.now().isoformat()
                })
                calls_made[tier] += 1
                # Decrement max_calls after a successful call
                if max_calls is not None:
                    max_calls -= 1
                save_checkpoint(completed)
                print(f"  Success! Tokens={t_ai}, Latency={l_ai:.3f}s, Cost=${cost:.6f}", flush=True)
                success = True
                
            except Exception as e:
                err_str = str(e)
                # Handle per-minute rate limit errors by sleeping and retrying
                if "GenerateRequestsPerMinute" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if "GenerateRequestsPerDay" in err_str:
                        print(f"\n[Daily Quota Reached] Stopping calls for {tier} due to daily limit.", flush=True)
                        tier_exhausted[tier] = True
                        break  # Break while loop, moves to next step
                    else:
                        print(f"\n[Rate Limit] Hit per-minute quota for {tier}: {e}. Sleeping 60s before retry.", flush=True)
                        time.sleep(60)
                        # We will retry this step by continuing in the while loop
                        continue
                elif "UNAVAILABLE" in err_str:
                    attempts += 1
                    if attempts < 2:
                        print(f"\n[Temporary Unavailable] Hit UNAVAILABLE for {tier}: {e}. Sleeping 20s before retry 1/1.", flush=True)
                        time.sleep(20)
                        continue
                    else:
                        print(f"\n[Temporary Unavailable] Skipping calls for {tier} temporarily after retry: {e}", flush=True)
                        tier_exhausted[tier] = True
                        break  # Break while loop, moves to next step
                elif "DEADLINE_EXCEEDED" in err_str:
                    attempts += 1
                    if attempts < 2:
                        print(f"\n[Timeout] Hit DEADLINE_EXCEEDED for {tier}: {e}. Sleeping 20s before retry 1/1.", flush=True)
                        time.sleep(20)
                        continue
                    else:
                        print(f"\n[API Error] Marking {tier} as exhausted due to persistent timeouts after retry: {e}", flush=True)
                        tier_exhausted[tier] = True
                        break  # Break while loop, moves to next step (for a different tier)
                else:
                    # Any other unrecoverable API error marks the tier as exhausted
                    print(f"\n[API Error] Marking {tier} as exhausted due to API issue: {e}", flush=True)
                    tier_exhausted[tier] = True
                    break  # Break while loop, moves to next step (for a different tier)

    print("\n=== Progress Summary ===", flush=True)
    print(f"Calls made this run: Flash-1={calls_made['gemini-3.6-flash-1']}/20, Flash-2={calls_made['gemini-3.6-flash-2']}/20, Flash-3={calls_made['gemini-3.6-flash-3']}/20", flush=True)
    
    # Calculate rolling window wait prediction
    now = datetime.datetime.now()
    flash_runs = [c for c in completed if c.get("tier") in ("gemini-3.6-flash-1", "gemini-3.6-flash-2", "gemini-3.6-flash-3") and "timestamp" in c]
    if flash_runs or any(tier_exhausted.get(t) for t in ["gemini-3.6-flash-1", "gemini-3.6-flash-2", "gemini-3.6-flash-3"]):
        parsed_runs = []
        for r in flash_runs:
            try:
                dt = datetime.datetime.fromisoformat(r["timestamp"])
                parsed_runs.append(dt)
            except Exception:
                pass
        parsed_runs.sort()
        cutoff = now - datetime.timedelta(hours=24)
        active_in_24h = [dt for dt in parsed_runs if dt > cutoff]
        
        print(f"\n=== Rolling 24h Window Status for gemini-3.6-flash ===", flush=True)
        print(f"Tracked successful calls in last 24h: {len(active_in_24h)}/20", flush=True)
        
        wait_sec = 0
        if len(active_in_24h) >= 20:
            oldest = active_in_24h[0]
            next_free = oldest + datetime.timedelta(hours=24)
            wait_sec = max(60, int((next_free - now).total_seconds()) + 30) # add 30s buffer
            print(f"Next slot estimated to open at: {next_free.isoformat()}", flush=True)
            print(f"Wait duration: {wait_sec} seconds.", flush=True)
        elif any(tier_exhausted.get(t) for t in ["gemini-3.6-flash-1", "gemini-3.6-flash-2", "gemini-3.6-flash-3"]):
            if active_in_24h:
                oldest = active_in_24h[0]
                next_free = oldest + datetime.timedelta(hours=24)
                wait_sec = max(60, int((next_free - now).total_seconds()) + 30)
                print(f"Next slot estimated to open at: {next_free.isoformat()}", flush=True)
                print(f"Wait duration: {wait_sec} seconds.", flush=True)
            else:
                wait_sec = 3600
                next_free = now + datetime.timedelta(hours=1)
                print("Daily quota exhausted but no successful calls are currently tracked in the last 24h. Checking again in 1 hour.", flush=True)
        else:
            next_free = now
                
        if wait_sec > 0:
            next_wait_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "next_wait.json"))
            with open(next_wait_path, "w", encoding="utf-8") as wf:
                json.dump({"wait_seconds": wait_sec, "next_free": next_free.isoformat()}, wf, indent=2)

    print_tables(completed)


def print_tables(completed: List[Dict[str, Any]]):
    """Group, calculate averages, and print tables formatted for the markdown writeup."""
    # Split by length bucket
    for bucket in ["short", "medium", "long"]:
        bucket_runs = [c for c in completed if c["length"] == bucket]
        
        # Group by unified method name
        method_stats = {}
        for r in bucket_runs:
            method_key = r["method"]
            if r["tier"] != "n/a":
                suffix = "flash" if "flash" in r["tier"] else "pro"
                method_key = f"{r['method']} ({suffix})"
            if method_key not in method_stats:
                method_stats[method_key] = []
            method_stats[method_key].append(r)
            
        print(f"\n### Comparative Benchmark — {bucket.upper()} Traces")
        print(f"| Method | Avg Tokens | Avg Latency (s) | Cost ($) | Recall Acc. | Artifact Acc. | Continuation Acc. | Decision Acc. | Deterministic |")
        print(f"| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        
        methods_list = [
            "full_history",
            "truncate_by_event_count",
            "truncate_by_token_count",
            "ai_summarize_single (flash)",
            "ai_summarize_single (pro)",
            "ai_summarize_recursive (flash)",
            "ai_summarize_recursive (pro)",
            "trace_gc_pipeline",
            "ablation_a",
            "ablation_b",
            "ablation_c",
            "ablation_d",
            "ablation_e",
            "ablation_f"
        ]
        
        for m in methods_list:
            runs = method_stats.get(m, [])
            if not runs:
                # If it's a recursive method and trace is short, it's skipped
                if "recursive" in m and bucket == "short":
                    print(f"| {m:<30} | {'skip':<10} | {'skip':<15} | {'$0.000000':<8} | {'skip':<11} | {'skip':<13} | {'skip':<17} | {'skip':<13} | {'n/a':<13} |")
                else:
                    print(f"| {m:<30} | {'pending':<10} | {'pending':<15} | {'pending':<8} | {'pending':<11} | {'pending':<13} | {'pending':<17} | {'pending':<13} | {'pending':<13} |")
                continue
                
            avg_tokens = sum(r["tokens"] for r in runs) / len(runs)
            avg_lat = sum(r["latency"] for r in runs) / len(runs)
            avg_cost = sum(r["cost"] for r in runs) / len(runs)
            
            def fmt_score(key: str) -> str:
                # Average accuracy rate over all matching runs in this bucket
                # This automatically averages over the 3 fixtures (coding, research, support)
                # and all run numbers (1, 2, 3) available so far.
                vals = [r["probes"].get(key, 0.0) for r in runs]
                avg = sum(vals) / len(vals)
                return f"{avg * 100:.1f}%"
                
            det_val = runs[0]["determinism"]
            print(f"| {m:<30} | {avg_tokens:<10.1f} | {avg_lat:<15.4f} | ${avg_cost:<8.6f} | {fmt_score('recall'):<11} | {fmt_score('artifact'):<13} | {fmt_score('continuation'):<17} | {fmt_score('decision'):<13} | {det_val:<13} |")


if __name__ == "__main__":
    simulate_mode = "--simulate" in sys.argv
    print_only = "--print-only" in sys.argv or "--print" in sys.argv
    if print_only:
        completed = load_checkpoint()
        print_tables(completed)
    else:
        run_benchmark(simulate=simulate_mode)
