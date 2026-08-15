# scripts/run_answer_quality.py
"""Answer quality comparison script.

Compares model responses using full uncompacted event traces versus
compacted event traces. Supports both Anthropic (Claude) and Google (Gemini).
Includes repeat run benchmarking and JSONL logging.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Dict, List, Any, Optional

# Adjust path to import local package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tracegc.events import validate_event
from tracegc.compactor import compact_events, _render_event


def get_claude_response(api_key: str, system_prompt: str, prompt: str) -> str:
    """Send a request to Anthropic API using standard urllib."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-3-5-sonnet-20240620",
        "max_tokens": 150,
        "temperature": 0.0,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            return res_body["content"][0]["text"].strip()
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8")
        raise RuntimeError(f"Anthropic API request failed: {e.code} - {error_msg}")
    except Exception as e:
        raise RuntimeError(f"Request error: {e}")


def get_gemini_response(client: Any, system_prompt: str, prompt_text: str) -> str:
    """Send a request to Google Gemini API using google-genai package with rate limit handling."""
    import time
    from google.genai import errors

    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt_text,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.0,
                }
            )
            return response.text.strip()
        except errors.APIError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                print(f"Rate limit hit. Waiting 70s for minute window reset (attempt {attempt+1}/{max_retries})...", flush=True)
                time.sleep(70)
            else:
                raise e
    raise RuntimeError("Exceeded maximum retries for Gemini API due to rate limits.")


# Define the 5 scenarios
SCENARIOS = [
    {
        "id": 1,
        "name": "Scenario 1: Simple Overwrite",
        "file": "tests/fixtures/sample_trace.json",
        "question": "What is the final bucket capacity value set for the token bucket rate limiter?",
        "ground_truth": "20",
    },
    {
        "id": 2,
        "name": "Scenario 2: Overwrite in Abandoned Branch (Stress Test)",
        "events": [
            {"id": "e001", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Initialize configuration"},
            {"id": "e002", "type": "set_var", "timestamp": 1010, "parent_id": "e001", "key": "x", "value": 10},
            {"id": "a01", "type": "decision", "timestamp": 1020, "parent_id": "e002", "content": "Attempting temporary overwrite"},
            {"id": "a02", "type": "set_var", "timestamp": 1030, "parent_id": "a01", "key": "x", "value": 100},
            {"id": "a03", "type": "decision", "timestamp": 1040, "parent_id": "a02", "content": "Tweak caused failure"},
            {"id": "ab01", "type": "abandon", "timestamp": 1050, "parent_id": "a03", "ref_to": ["a01"]},
            {"id": "e003", "type": "decision", "timestamp": 1060, "parent_id": "e002", "content": "Tweak abandoned. Resuming active run."}
        ],
        "question": "What is the final value of key 'x'?",
        "ground_truth": "10",
    },
    {
        "id": 3,
        "name": "Scenario 3: Redundant Tool Calls Deduplicated",
        "events": [
            {"id": "e001", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Checking active directory configuration"},
            {"id": "tc1", "type": "tool_call", "timestamp": 1010, "parent_id": "e001", "tool_name": "list_files", "arguments": {"dir": "src"}},
            {"id": "tr1", "type": "tool_result", "timestamp": 1020, "parent_id": "tc1", "call_id": "tc1", "result": ["main.py", "utils.py"]},
            {"id": "tc2", "type": "tool_call", "timestamp": 1030, "parent_id": "tr1", "tool_name": "list_files", "arguments": {"dir": "src"}},
            {"id": "tr2", "type": "tool_result", "timestamp": 1040, "parent_id": "tc2", "call_id": "tc2", "result": ["main.py", "utils.py"]},
            {"id": "e002", "type": "decision", "timestamp": 1050, "parent_id": "tr2", "content": "Confirmed directory has two files"}
        ],
        "question": "What files are in the 'src' directory?",
        "ground_truth": "main.py, utils.py",
    },
    {
        "id": 4,
        "name": "Scenario 4: Multi-Variables Overrides and Cycles",
        "file": "tests/fixtures/sample_trace_large.json",
        "question": "What are the final values of refill_rate and request_timeout?",
        "ground_truth": "refill_rate = 10, request_timeout = 200",
    },
    {
        "id": 5,
        "name": "Scenario 5: Multi-Stage Abandonment",
        "events": [
            {"id": "e001", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Starting system configuration"},
            {"id": "e002", "type": "set_var", "timestamp": 1010, "parent_id": "e001", "key": "refill_rate", "value": 5},
            {"id": "a01", "type": "decision", "timestamp": 1020, "parent_id": "e002", "content": "Attempting optimization"},
            {"id": "a02", "type": "set_var", "timestamp": 1030, "parent_id": "a01", "key": "refill_rate", "value": 8},
            {"id": "ab01", "type": "abandon", "timestamp": 1040, "parent_id": "a02", "ref_to": ["a01"]},
            {"id": "e003", "type": "decision", "timestamp": 1050, "parent_id": "e002", "content": "Resuming baseline configuration"}
        ],
        "question": "What is the final refill_rate value set?",
        "ground_truth": "5",
    }
]


def run_scenarios():
    parser = argparse.ArgumentParser(description="Run answer quality comparisons.")
    parser.add_argument("--repeat", type=int, default=1, help="Number of repeat runs per scenario")
    parser.add_argument("--scenario", type=int, default=None, help="Specific scenario ID (1-5) to run")
    args = parser.parse_args()

    # Attempt to load from project root or user home .env files
    paths_to_try = [
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.expanduser("~/.env"),
    ]
    for p in paths_to_try:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        cleaned = line.strip()
                        if cleaned.startswith("ANTHROPIC_API_KEY="):
                            val = cleaned.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                os.environ["ANTHROPIC_API_KEY"] = val
                        elif cleaned.startswith("GEMINI_API_KEY="):
                            val = cleaned.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                os.environ["GEMINI_API_KEY"] = val
            except Exception:
                pass

    gemini_key = os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    model_provider = os.environ.get("MODEL_PROVIDER")

    provider = None
    if model_provider:
        provider = model_provider.lower()
    elif gemini_key:
        provider = "gemini"
    elif anthropic_key:
        provider = "anthropic"

    client = None
    if not provider:
        print("WARNING: Neither GEMINI_API_KEY nor ANTHROPIC_API_KEY is configured.")
        print("Running in DRY-RUN mode. Compaction will execute locally, but API calls will be skipped.\n")
    else:
        print(f"Using MODEL_PROVIDER: {provider.upper()} (Repeat: {args.repeat})\n")
        if provider == "gemini":
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gemini_key, http_options=types.HttpOptions(timeout=30_000))

    # Set up log file
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filepath = os.path.join(log_dir, f"answer_quality_{timestamp_str}.jsonl")

    def log_api_call(sc_name: str, variant: str, prompt: str, response: str, gt: str, match: str):
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "scenario_name": sc_name,
            "variant": variant,
            "prompt_text": prompt,
            "response_text": response,
            "ground_truth": gt,
            "match": match,
        }
        with open(log_filepath, "a", encoding="utf-8") as lf:
            lf.write(json.dumps(entry) + "\n")

    target_scenarios = SCENARIOS
    if args.scenario is not None:
        target_scenarios = [s for s in SCENARIOS if s["id"] == args.scenario]
        if not target_scenarios:
            print(f"Error: Invalid scenario ID {args.scenario}. Choose 1-5.")
            sys.exit(1)

    for sc in target_scenarios:
        print(f"=== {sc['name']} ===")

        # Load events
        if "file" in sc:
            fixture_path = os.path.join(os.path.dirname(__file__), "..", sc["file"])
            with open(fixture_path, "r", encoding="utf-8") as f:
                events = json.load(f)
        else:
            events = [validate_event(ev) for ev in sc["events"]]

        # Compact events
        result = compact_events(events)

        uncompacted_strings = [_render_event(ev) for ev in events]
        uncompacted_text = "\n".join(filter(None, uncompacted_strings))
        compacted_text = result["prompt"]

        print(f"Uncompacted tokens: {result['tokens_before']}")
        print(f"Compacted tokens:   {result['tokens_after']}\n")

        system_prompt = (
            "You are a precise coding assistant. Answer the user's question directly and concisely "
            "based only on the provided log events. Do not summarize or explain your reasoning."
        )

        uncompacted_prompt = f"Event history:\n\n{uncompacted_text}\n\nQuestion: {sc['question']}"
        compacted_prompt = f"Event history:\n\n{compacted_text}\n\nQuestion: {sc['question']}"

        gt_tokens = [tok.strip() for tok in sc["ground_truth"].replace("=", " ").replace(",", " ").split() if tok.strip()]

        def check_match(ans: str) -> str:
            ans_lower = ans.lower()
            if sc["ground_truth"].lower() in ans_lower:
                return "Yes"
            if all(tok.lower() in ans_lower for tok in gt_tokens):
                return "Yes"
            return "No"

        repeat_results = []
        uncompacted_correct = 0
        compacted_correct = 0

        for run_idx in range(1, args.repeat + 1):
            if args.repeat > 1:
                print(f"--- Run {run_idx}/{args.repeat} ---", flush=True)

            if provider:
                import time
                
                print(f"Querying {provider.upper()} with uncompacted context...", flush=True)
                time.sleep(25)
                if provider == "gemini":
                    ans_uncompacted = get_gemini_response(client, system_prompt, uncompacted_prompt)
                else:
                    ans_uncompacted = get_claude_response(anthropic_key, system_prompt, uncompacted_prompt)

                print(f"Run {run_idx}/{args.repeat} — uncompacted call done", flush=True)
                m_uncompacted = check_match(ans_uncompacted)
                log_api_call(sc["name"], "uncompacted", uncompacted_prompt, ans_uncompacted, sc["ground_truth"], m_uncompacted)
                if m_uncompacted == "Yes":
                    uncompacted_correct += 1

                print(f"Querying {provider.upper()} with compacted context...", flush=True)
                time.sleep(25)
                if provider == "gemini":
                    ans_compacted = get_gemini_response(client, system_prompt, compacted_prompt)
                else:
                    ans_compacted = get_claude_response(anthropic_key, system_prompt, compacted_prompt)

                print(f"Run {run_idx}/{args.repeat} — compacted call done", flush=True)
                m_compacted = check_match(ans_compacted)
                log_api_call(sc["name"], "compacted", compacted_prompt, ans_compacted, sc["ground_truth"], m_compacted)
                if m_compacted == "Yes":
                    compacted_correct += 1

                print(f"Uncompacted Ans: {ans_uncompacted!r:<20} | Match: {m_uncompacted}", flush=True)
                print(f"Compacted Ans:   {ans_compacted!r:<20} | Match: {m_compacted}", flush=True)

                repeat_results.append({
                    "run": run_idx,
                    "uncompacted_ans": ans_uncompacted,
                    "uncompacted_match": m_uncompacted,
                    "compacted_ans": ans_compacted,
                    "compacted_match": m_compacted,
                })
            else:
                print("Skipped API call (dry-run).")

        if provider and args.repeat > 1:
            print("\n" + "=" * 80)
            print(f"REPEAT RESULTS TABLE for {sc['name']}")
            print("=" * 80)
            print(f"{'Run #':<7} | {'Uncompacted Ans':<20} | {'Match':<6} | {'Compacted Ans':<20} | {'Match':<6}")
            print("-" * 80)
            for r in repeat_results:
                print(f"{r['run']:<7} | {r['uncompacted_ans']:<20} | {r['uncompacted_match']:<6} | {r['compacted_ans']:<20} | {r['compacted_match']:<6}")
            print("=" * 80)
            print(f"AGGREGATE: uncompacted correct {uncompacted_correct}/{args.repeat}, compacted correct {compacted_correct}/{args.repeat}\n")

    if provider:
        print(f"Raw API logs saved to: {log_filepath}")


if __name__ == "__main__":
    run_scenarios()
