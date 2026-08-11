# trace_gc/benchmark/test_gemini.py
"""Sanity check script to verify Gemini API connection and token cost estimation."""

from __future__ import annotations

import os
import sys

# Load .env manually
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = line.strip()
            if cleaned.startswith("GEMINI_API_KEY="):
                val = cleaned.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    os.environ["GEMINI_API_KEY"] = val
                    print("Loaded GEMINI_API_KEY from .env")

gemini_key = os.environ.get("GEMINI_API_KEY")
if not gemini_key:
    print("Error: GEMINI_API_KEY not found in environment or .env file.")
    sys.exit(1)

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: google-genai package is not installed in this environment.")
    sys.exit(1)

print("Initializing Gemini Client...")
client = genai.Client(api_key=gemini_key, http_options=types.HttpOptions(timeout=30_000))

print("Sending single test call...")
try:
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents="Say hello and confirm you are online.",
        config={
            "temperature": 0.0,
        }
    )
    print("API Response:", response.text.strip())
    
    # Estimate token usage
    input_text = "Say hello and confirm you are online."
    output_text = response.text.strip()
    input_tokens = len(input_text) // 4
    output_tokens = len(output_text) // 4
    
    # Pricing as of July 2026 for gemini-2.5-flash:
    # Input: $0.075 / 1M tokens ($0.000075 / 1K tokens)
    # Output: $0.30 / 1M tokens ($0.000300 / 1K tokens)
    input_cost = (input_tokens / 1_000_000) * 0.075
    output_cost = (output_tokens / 1_000_000) * 0.30
    total_cost = input_cost + output_cost
    
    print(f"Estimated single call token usage: Input={input_tokens}, Output={output_tokens}")
    print(f"Estimated single call cost: ${total_cost:.8f}")
    print("Gemini API key is verified and working correctly.")
except Exception as e:
    print(f"Error during API call: {e}")
    sys.exit(1)
