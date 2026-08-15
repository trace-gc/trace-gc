# tracegc/middleware.py
"""Simple middleware that wraps an LLM‑style call with deterministic compaction.

The user supplies a callable ``llm_fn`` that expects a list of events and returns a
response (e.g. a string).  The middleware first runs the compaction pipeline to
produce a reduced event list and then forwards that list to ``llm_fn``.

The wrapper returns a dictionary containing the original LLM response together
with the compacted events and any receipts, making it easy for callers to
inspect what was sent to the model.
"""

from __future__ import annotations

from typing import Callable, List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .api import TraceGC

from .compactor import compact_events


class TraceGCMiddleware:
    """Callable class that applies context‑gc before invoking an LLM function."""

    def __init__(self, llm_fn: Callable[[List[Dict[str, Any]]], Any]):
        self.llm_fn = llm_fn

    def __call__(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compact *events* then call the underlying LLM function.

        Returns a mapping with ``llm_response``, ``compact_events``, ``receipts``
        and ``pruned_ids``.
        """
        comp = compact_events(events)
        response = self.llm_fn(comp["compact_events"])
        return {
            "llm_response": response,
            "compact_events": comp["compact_events"],
            "receipts": comp["receipts"],
            "pruned_ids": comp["pruned_ids"],
        }


def call_anthropic_with_compaction(
    tracegc: TraceGC,
    model: str,
    user_message: str,
    api_key: Optional[str] = None
) -> dict:
    """Compact current events and send them via the Anthropic Messages API.

    Imports ``anthropic`` lazily inside the function body to keep the core package
    dependency-free.
    """
    import os
    import anthropic

    compaction_result = tracegc.compact()
    compacted_prompt = compaction_result["prompt"]

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=compacted_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    metrics = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "tokens_before": compaction_result["tokens_before"],
        "tokens_after": compaction_result["tokens_after"],
    }

    return {
        "response_text": response.content[0].text,
        "metrics": metrics,
    }


def call_openai_with_compaction(
    tracegc: TraceGC,
    model: str,
    user_message: str,
    api_key: Optional[str] = None
) -> dict:
    """Compact current events and send them via the OpenAI Chat Completions API.

    Imports ``openai`` lazily inside the function body to keep the core package
    dependency-free.
    """
    import os
    import openai

    compaction_result = tracegc.compact()
    compacted_prompt = compaction_result["prompt"]

    key = api_key or os.environ.get("OPENAI_API_KEY")
    client = openai.OpenAI(api_key=key)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": compacted_prompt},
            {"role": "user", "content": user_message}
        ]
    )

    metrics = {
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "tokens_before": compaction_result["tokens_before"],
        "tokens_after": compaction_result["tokens_after"],
    }

    return {
        "response_text": response.choices[0].message.content,
        "metrics": metrics,
    }
