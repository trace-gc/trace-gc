# tracegc/benchmark/estimate_cost.py
"""Calculates the exact token sizes and dollar cost estimate for running the benchmark."""

from __future__ import annotations

import json
import os
from tracegc.benchmark.methods import method_full_history

# Gemini 2.5 Flash Pricing (July 2026)
FLASH_INPUT_RATE_PER_1M = 0.075
FLASH_OUTPUT_RATE_PER_1M = 0.30

# Gemini 2.5 Pro Pricing (July 2026)
PRO_INPUT_RATE_PER_1M = 1.25
PRO_OUTPUT_RATE_PER_1M = 5.00

FIXTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures"))
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

total_input_tokens = 0
total_output_tokens = 0

print("=== Benchmarking Cost Estimation (Both Model Tiers) ===")
print(f"{'Fixture File':<30} | {'Events':<6} | {'Uncomp. Tok':<12} | {'Est. Single In':<14} | {'Est. Recur In':<13}")
print("-" * 85)

for filename in filenames:
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data["events"]
    _, tokens, _ = method_full_history(events)
    
    # 1. ai_summarize_single (3 runs)
    single_run_in = tokens * 3
    single_run_out = min(150, max(50, int(tokens * 0.20))) * 3
    
    # 2. ai_summarize_recursive (3 runs, threshold > 50)
    recur_run_in = 0
    recur_run_out = 0
    if len(events) > 50:
        chunk_size = 25
        num_chunks = (len(events) + chunk_size - 1) // chunk_size
        chunk_tokens = tokens // num_chunks
        chunk_in = chunk_tokens * num_chunks
        chunk_out = num_chunks * 80
        final_in = chunk_out
        final_out = 150
        
        recur_run_in = (chunk_in + final_in) * 3
        recur_run_out = (chunk_out + final_out) * 3
        
    total_input_tokens += (single_run_in + recur_run_in)
    total_output_tokens += (single_run_out + recur_run_out)
    
    print(f"{filename:<30} | {len(events):<6} | {tokens:<12} | {single_run_in:<14} | {recur_run_in:<13}")

print("-" * 85)

# Calculate costs for Flash and Pro
flash_input_cost = (total_input_tokens / 1_000_000) * FLASH_INPUT_RATE_PER_1M
flash_output_cost = (total_output_tokens / 1_000_000) * FLASH_OUTPUT_RATE_PER_1M
flash_total_cost = flash_input_cost + flash_output_cost

pro_input_cost = (total_input_tokens / 1_000_000) * PRO_INPUT_RATE_PER_1M
pro_output_cost = (total_output_tokens / 1_000_000) * PRO_OUTPUT_RATE_PER_1M
pro_total_cost = pro_input_cost + pro_output_cost

total_combined_cost = flash_total_cost + pro_total_cost

print(f"Total Estimated Input Tokens per tier:  {total_input_tokens:,}")
print(f"Total Estimated Output Tokens per tier: {total_output_tokens:,}")
print(f"Flash Estimated Cost:                  ${flash_total_cost:.6f}")
print(f"Pro Estimated Cost:                    ${pro_total_cost:.6f}")
print(f"Total Combined Benchmark Cost:         ${total_combined_cost:.4f} (approx. {total_combined_cost*100:.2f} cents)")
