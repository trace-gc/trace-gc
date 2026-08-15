# tracegc_crewai/adapter.py
"""TraceGC adapter for CrewAI agents, tasks, and step callbacks.

Provides helper functions and callback handlers to compact CrewAI execution
history, step outputs, and task outputs using TraceGC graph-based compaction.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from tracegc.api import compact, CompactionResult


def _normalize_crewai_step(step: Any) -> dict:
    """Convert a CrewAI step output object, tuple, or dict into a standard TraceGC message dict."""
    if isinstance(step, dict):
        return dict(step)

    if isinstance(step, str):
        return {"role": "user", "content": step}

    if isinstance(step, (list, tuple)):
        parts = [_normalize_crewai_step(item) for item in step]
        combined_content = "\n".join(
            p.get("content", str(p)) if isinstance(p, dict) else str(p) for p in parts
        )
        return {"role": "assistant", "content": combined_content}

    # CrewAI AgentAction (has .tool / .tool_input / .log / .thought)
    if hasattr(step, "tool") or hasattr(step, "tool_input"):
        tool_name = getattr(step, "tool", "unknown_tool")
        tool_input = getattr(step, "tool_input", {})
        thought = getattr(step, "thought", getattr(step, "log", ""))
        text = getattr(step, "text", getattr(step, "result", getattr(step, "output", "")))
        content = f"Thought: {thought}\nAction: {tool_name}\nInput: {tool_input}\nOutput: {text}".strip()
        return {
            "type": "tool_call",
            "tool_name": str(tool_name),
            "arguments": tool_input if isinstance(tool_input, dict) else {"input": str(tool_input)},
            "content": content,
        }

    # CrewAI AgentFinish (has .return_values / .log)
    if hasattr(step, "return_values"):
        rv = getattr(step, "return_values", {})
        output_str = rv.get("output", str(rv)) if isinstance(rv, dict) else str(rv)
        log_str = getattr(step, "log", "")
        content = f"{output_str}\n{log_str}".strip()
        return {"role": "assistant", "content": content}

    # CrewAI TaskOutput (has .result / .raw / .raw_output)
    if hasattr(step, "result") or hasattr(step, "raw") or hasattr(step, "raw_output"):
        result_text = getattr(step, "result", getattr(step, "raw", getattr(step, "raw_output", str(step))))
        return {"role": "assistant", "content": str(result_text)}

    return {"role": "user", "content": str(step)}


def compact_messages(
    history: List[Any],
    **kwargs: Any,
) -> CompactionResult:
    """Compact a history of CrewAI step outputs, task outputs, or message objects.

    Parameters
    ----------
    history:
        List of CrewAI step outputs (AgentAction, ToolResult, AgentFinish, TaskOutput),
        tuples, dicts, or strings representing execution history.
    **kwargs:
        Additional options passed directly to ``tracegc.api.compact()``
        (e.g., ``prune_semantic=True``, ``tracked_decision_keys={...}``).

    Returns
    -------
    CompactionResult
        The result containing compacted messages, graph, and pruned receipts.
    """
    normalized = [_normalize_crewai_step(item) for item in history]
    return compact(normalized, **kwargs)


class TraceGCCrewCallback:
    """CrewAI step callback that accumulates and compacts execution steps.

    Can be passed directly as ``step_callback`` or ``task_callback`` to a CrewAI Crew
    or Agent instance.
    """

    def __init__(
        self,
        on_step_callback: Optional[Callable[[Any], None]] = None,
        **compact_kwargs: Any,
    ) -> None:
        self.steps: List[Any] = []
        self.on_step_callback = on_step_callback
        self.compact_kwargs = compact_kwargs

    def __call__(self, step_output: Any) -> None:
        """Invoked by CrewAI as step_callback(step_output)."""
        self.steps.append(step_output)
        if self.on_step_callback is not None:
            self.on_step_callback(step_output)

    def compact(self) -> CompactionResult:
        """Compact accumulated step history and return the CompactionResult."""
        return compact_messages(self.steps, **self.compact_kwargs)

    def clear(self) -> None:
        """Clear recorded steps."""
        self.steps.clear()


def create_step_callback(
    callback_fn: Optional[Callable[[Any], None]] = None,
    **compact_kwargs: Any,
) -> TraceGCCrewCallback:
    """Factory function creating a TraceGCCrewCallback instance for CrewAI step_callback."""
    return TraceGCCrewCallback(on_step_callback=callback_fn, **compact_kwargs)


class TraceGCCrewAdapter:
    """Adapter wrapping CrewAI step and task output tracking for TraceGC compaction."""

    def __init__(self, **compact_kwargs: Any) -> None:
        self.callback = TraceGCCrewCallback(**compact_kwargs)

    def get_step_callback(self) -> TraceGCCrewCallback:
        """Return the step_callback instance for registering with CrewAI Crew or Agent."""
        return self.callback

    def compact_history(self, extra_history: Optional[List[Any]] = None) -> CompactionResult:
        """Compact accumulated step history plus optional additional task/message history."""
        all_items = list(self.callback.steps)
        if extra_history:
            all_items.extend(extra_history)
        return compact_messages(all_items, **self.callback.compact_kwargs)
