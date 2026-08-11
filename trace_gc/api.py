# trace_gc/api.py
"""Universal Input API client for TraceGC.

Provides the top-level compact() function and message normalization/reconstruction logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Union, Optional

from .events import validate_event, EVENT_TYPES
from .graph import StateGraph
from .compactor import compact_events
from .receipts import get_receipt as _get_receipt


@dataclass
class Receipt:
    node_id: str
    reason: str
    event: dict


@dataclass
class CompactionResult:
    messages: list[Any]
    receipts: list[Receipt]
    tokens_before: int
    tokens_after: int
    compaction_ratio: float
    _original_events: list[dict]
    _graph: StateGraph
    _original_inputs: list[Any]
    _original_types: list[str]

    def get_receipt(self, node_id: str) -> dict:
        """Recover the original normalized event/message for a pruned node."""
        if node_id not in self._graph.nodes:
            raise KeyError(f"Unknown node id: {node_id}")
        return self._graph.nodes[node_id]

    def recover_all(self) -> list[Any]:
        """Recover all original normalized inputs in trace order."""
        return reconstruct_output(self._original_events, set(), self._original_inputs, self._original_types)


class TraceGC:
    """Incremental API client for TraceGC.

    Manages a running history of events for an LLM agent, allowing events to be
    added one by one and compacted on demand.
    """

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.graph: StateGraph = StateGraph()

    def add_event(self, event: Dict[str, Any]) -> None:
        """Validate and append a single event to the history."""
        validated = validate_event(event)
        self.events.append(validated)
        self.graph.add_node(validated)
        
        parent = validated.get("parent_id")
        if parent:
            if parent not in self.graph.nodes:
                raise ValueError(
                    f"parent_id '{parent}' not found in graph — events must be added in dependency order"
                )
            self.graph.add_edge(parent, validated["id"], "sequence")

    def compact(self) -> Dict[str, Any]:
        """Runs the full compaction pipeline against all events added so far."""
        result = compact_events(self.events)
        self.graph = result["graph"]
        return result

    def get_receipt(self, node_id: str) -> Dict[str, Any]:
        """Retrieve the original event dict/receipt for a pruned node ID."""
        return _get_receipt(self.graph, node_id)


def is_lc_message(msg: Any) -> bool:
    """Helper to detect LangChain message objects."""
    return hasattr(msg, "content") and not isinstance(msg, dict) and not isinstance(msg, str)


def normalize_input(messages: Any) -> tuple[list[dict[str, Any]], list[Any], list[str]]:
    """Normalize input messages of various shapes into standard trace events.

    Returns:
        (events, original_inputs_in_order, original_types_in_order)
    """
    # 1. Standardize inputs to list format
    if isinstance(messages, str):
        raw_list = [messages]
    elif isinstance(messages, list):
        raw_list = messages
    elif is_lc_message(messages):
        raw_list = [messages]
    elif isinstance(messages, dict):
        # Could be a single OpenAI/Anthropic message or direct event
        raw_list = [messages]
    else:
        # Fallback
        raw_list = [messages]

    events: list[dict[str, Any]] = []
    original_inputs: list[Any] = []
    original_types: list[str] = []

    # Map to translate external tool call IDs to event IDs
    tool_call_id_map: dict[str, str] = {}

    for idx, item in enumerate(raw_list):
        # Establish parent reference
        prev_event_id = events[-1]["id"] if events else None

        # -- String --
        if isinstance(item, str):
            ev_id = f"txt_{idx}"
            events.append({
                "id": ev_id,
                "type": "text_chunk",
                "timestamp": idx * 10,
                "parent_id": prev_event_id,
                "content": item,
                "_original_message": item,
                "_original_type": "string",
                "_original_index": idx
            })
            original_inputs.append(item)
            original_types.append("string")

        # -- Direct Event --
        elif isinstance(item, dict) and "type" in item and item["type"] in EVENT_TYPES and "id" in item:
            ev = item.copy()
            if "parent_id" not in ev:
                ev["parent_id"] = prev_event_id
            if "timestamp" not in ev:
                ev["timestamp"] = idx * 10
            ev["_original_message"] = item
            ev["_original_type"] = "event"
            ev["_original_index"] = idx
            events.append(ev)
            original_inputs.append(item)
            original_types.append("event")

        # -- OpenAI‑style Message Dict --
        elif isinstance(item, dict) and "role" in item:
            role = item["role"]
            content = item.get("content")
            tool_calls = item.get("tool_calls")
            tc_id = item.get("tool_call_id")

            # Assistant message with tool_calls
            if role == "assistant" and tool_calls:
                # Text content block (if present)
                if content:
                    ev_id = f"msg_{idx}_text"
                    events.append({
                        "id": ev_id,
                        "type": "decision",
                        "timestamp": idx * 10,
                        "parent_id": prev_event_id,
                        "content": content,
                        "_original_message": item,
                        "_original_type": "openai",
                        "_original_index": idx,
                        "_part": "text"
                    })
                    prev_event_id = ev_id

                # Tool calls
                for tc_idx, tc in enumerate(tool_calls):
                    tc_event_id = f"tc_{idx}_{tc_idx}"
                    func = tc.get("function", {})
                    args = func.get("arguments", {})
                    # Standardize arguments payload parsing
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            pass
                    events.append({
                        "id": tc_event_id,
                        "type": "tool_call",
                        "timestamp": idx * 10 + tc_idx + 1,
                        "parent_id": prev_event_id,
                        "tool_name": func.get("name", ""),
                        "arguments": args,
                        "_original_message": item,
                        "_original_type": "openai",
                        "_original_index": idx,
                        "_part": "tool_call",
                        "_tool_call_index": tc_idx,
                        "_openai_tc_id": tc.get("id")
                    })
                    if tc.get("id"):
                        tool_call_id_map[tc.get("id")] = tc_event_id
                    prev_event_id = tc_event_id

            # Tool result message
            elif role == "tool":
                mapped_call_id = tool_call_id_map.get(tc_id) or tc_id or ""
                ev_id = f"tr_{idx}"
                events.append({
                    "id": ev_id,
                    "type": "tool_result",
                    "timestamp": idx * 10,
                    "parent_id": prev_event_id,
                    "call_id": mapped_call_id,
                    "result": content,
                    "_original_message": item,
                    "_original_type": "openai",
                    "_original_index": idx,
                    "_openai_tc_id": tc_id
                })

            # User, System, or Assistant without tool calls
            else:
                ev_id = f"msg_{idx}"
                events.append({
                    "id": ev_id,
                    "type": "decision",
                    "timestamp": idx * 10,
                    "parent_id": prev_event_id,
                    "content": content or "",
                    "_original_message": item,
                    "_original_type": "openai",
                    "_original_index": idx
                })

            original_inputs.append(item)
            original_types.append("openai")

        # -- Anthropic‑style Message Dict --
        elif isinstance(item, dict) and "content" in item:
            role = item.get("role", "user")
            content = item["content"]

            if isinstance(content, str):
                ev_id = f"msg_{idx}"
                events.append({
                    "id": ev_id,
                    "type": "decision",
                    "timestamp": idx * 10,
                    "parent_id": prev_event_id,
                    "content": content,
                    "_original_message": item,
                    "_original_type": "anthropic",
                    "_original_index": idx
                })
            elif isinstance(content, list):
                for b_idx, block in enumerate(content):
                    b_type = block.get("type")
                    if b_type == "text":
                        ev_id = f"msg_{idx}_blk_{b_idx}"
                        events.append({
                            "id": ev_id,
                            "type": "decision",
                            "timestamp": idx * 10 + b_idx,
                            "parent_id": prev_event_id,
                            "content": block.get("text", ""),
                            "_original_message": item,
                            "_original_type": "anthropic",
                            "_original_index": idx,
                            "_part": "block",
                            "_block_index": b_idx
                        })
                        prev_event_id = ev_id
                    elif b_type == "tool_use":
                        tc_id = block.get("id")
                        ev_id = f"tc_{idx}_{b_idx}"
                        events.append({
                            "id": ev_id,
                            "type": "tool_call",
                            "timestamp": idx * 10 + b_idx,
                            "parent_id": prev_event_id,
                            "tool_name": block.get("name", ""),
                            "arguments": block.get("input", {}),
                            "_original_message": item,
                            "_original_type": "anthropic",
                            "_original_index": idx,
                            "_part": "block",
                            "_block_index": b_idx,
                            "_anthropic_tc_id": tc_id
                        })
                        if tc_id:
                            tool_call_id_map[tc_id] = ev_id
                        prev_event_id = ev_id
                    elif b_type == "tool_result":
                        tc_id = block.get("tool_use_id")
                        mapped_call_id = tool_call_id_map.get(tc_id) or tc_id or ""
                        ev_id = f"tr_{idx}_{b_idx}"
                        events.append({
                            "id": ev_id,
                            "type": "tool_result",
                            "timestamp": idx * 10 + b_idx,
                            "parent_id": prev_event_id,
                            "call_id": mapped_call_id,
                            "result": block.get("content", ""),
                            "_original_message": item,
                            "_original_type": "anthropic",
                            "_original_index": idx,
                            "_part": "block",
                            "_block_index": b_idx,
                            "_anthropic_tc_id": tc_id
                        })
                        prev_event_id = ev_id

            original_inputs.append(item)
            original_types.append("anthropic")

        # -- LangChain Message Object --
        elif is_lc_message(item):
            cls_name = item.__class__.__name__
            content = getattr(item, "content", "")
            tool_calls = getattr(item, "tool_calls", [])
            tc_id = getattr(item, "tool_call_id", None)

            # AI message with tool calls
            if "AIMessage" in cls_name and tool_calls:
                if content:
                    ev_id = f"msg_{idx}_text"
                    events.append({
                        "id": ev_id,
                        "type": "decision",
                        "timestamp": idx * 10,
                        "parent_id": prev_event_id,
                        "content": content,
                        "_original_message": item,
                        "_original_type": "langchain",
                        "_original_index": idx,
                        "_part": "text"
                    })
                    prev_event_id = ev_id

                for tc_idx, tc in enumerate(tool_calls):
                    tc_event_id = f"tc_{idx}_{tc_idx}"
                    events.append({
                        "id": tc_event_id,
                        "type": "tool_call",
                        "timestamp": idx * 10 + tc_idx + 1,
                        "parent_id": prev_event_id,
                        "tool_name": tc.get("name", ""),
                        "arguments": tc.get("args", {}),
                        "_original_message": item,
                        "_original_type": "langchain",
                        "_original_index": idx,
                        "_part": "tool_call",
                        "_tool_call_index": tc_idx,
                        "_langchain_tc_id": tc.get("id")
                    })
                    if tc.get("id"):
                        tool_call_id_map[tc.get("id")] = tc_event_id
                    prev_event_id = tc_event_id

            # Tool result message
            elif "ToolMessage" in cls_name:
                mapped_call_id = tool_call_id_map.get(tc_id) or tc_id or ""
                ev_id = f"tr_{idx}"
                events.append({
                    "id": ev_id,
                    "type": "tool_result",
                    "timestamp": idx * 10,
                    "parent_id": prev_event_id,
                    "call_id": mapped_call_id,
                    "result": content,
                    "_original_message": item,
                    "_original_type": "langchain",
                    "_original_index": idx,
                    "_langchain_tc_id": tc_id
                })

            # General messages
            else:
                ev_id = f"msg_{idx}"
                events.append({
                    "id": ev_id,
                    "type": "decision",
                    "timestamp": idx * 10,
                    "parent_id": prev_event_id,
                    "content": content or "",
                    "_original_message": item,
                    "_original_type": "langchain",
                    "_original_index": idx
                })

            original_inputs.append(item)
            original_types.append("langchain")

        # -- Unsupported / Catch-all --
        else:
            ev_id = f"msg_{idx}"
            events.append({
                "id": ev_id,
                "type": "decision",
                "timestamp": idx * 10,
                "parent_id": prev_event_id,
                "content": str(item),
                "_original_message": item,
                "_original_type": "string",
                "_original_index": idx
            })
            original_inputs.append(item)
            original_types.append("string")

    return events, original_inputs, original_types


def reconstruct_output(
    original_events: list[dict],
    pruned_ids: Set[str],
    original_inputs: list[Any],
    original_types: list[str]
) -> list[Any]:
    """Reconstruct compacted messages / strings / events in their original shape."""
    # Group normalized events by their original input index
    events_by_idx: dict[int, list[dict]] = {}
    for ev in original_events:
        idx = ev["_original_index"]
        if idx not in events_by_idx:
            events_by_idx[idx] = []
        events_by_idx[idx].append(ev)

    reconstructed: list[Any] = []

    for idx, orig_input in enumerate(original_inputs):
        orig_type = original_types[idx]
        ev_list = events_by_idx.get(idx, [])

        if not ev_list:
            continue

        # -- String --
        if orig_type == "string":
            ev = ev_list[0]
            if ev["id"] not in pruned_ids:
                reconstructed.append(orig_input)

        # -- Direct Event --
        elif orig_type == "event":
            ev = ev_list[0]
            if ev["id"] not in pruned_ids:
                reconstructed.append(orig_input)

        # -- OpenAI --
        elif orig_type == "openai":
            role = orig_input.get("role")
            if role == "assistant" and orig_input.get("tool_calls"):
                # Assistant with tool calls
                text_part = next((e for e in ev_list if e.get("_part") == "text"), None)
                tc_parts = [e for e in ev_list if e.get("_part") == "tool_call"]

                has_text = text_part and (text_part["id"] not in pruned_ids)
                surviving_tcs = []
                for tc_part in tc_parts:
                    if tc_part["id"] not in pruned_ids:
                        tc_idx = tc_part["_tool_call_index"]
                        surviving_tcs.append(orig_input["tool_calls"][tc_idx])

                if has_text or surviving_tcs:
                    new_msg = orig_input.copy()
                    if has_text:
                        new_msg["content"] = orig_input["content"]
                    else:
                        new_msg["content"] = None
                    if surviving_tcs:
                        new_msg["tool_calls"] = surviving_tcs
                    else:
                        new_msg.pop("tool_calls", None)
                    reconstructed.append(new_msg)
            elif role == "tool":
                # Tool result
                ev = ev_list[0]
                if ev["id"] not in pruned_ids:
                    reconstructed.append(orig_input)
            else:
                # Other OpenAI messages
                ev = ev_list[0]
                if ev["id"] not in pruned_ids:
                    reconstructed.append(orig_input)

        # -- Anthropic --
        elif orig_type == "anthropic":
            content = orig_input.get("content")
            if isinstance(content, str):
                ev = ev_list[0]
                if ev["id"] not in pruned_ids:
                    reconstructed.append(orig_input)
            elif isinstance(content, list):
                surviving_blocks = []
                for b_idx, block in enumerate(content):
                    ev = next((e for e in ev_list if e.get("_part") == "block" and e.get("_block_index") == b_idx), None)
                    if ev and (ev["id"] not in pruned_ids):
                        surviving_blocks.append(block)
                if surviving_blocks:
                    new_msg = orig_input.copy()
                    new_msg["content"] = surviving_blocks
                    reconstructed.append(new_msg)

        # -- LangChain --
        elif orig_type == "langchain":
            cls_name = orig_input.__class__.__name__
            if "AIMessage" in cls_name and getattr(orig_input, "tool_calls", None):
                text_part = next((e for e in ev_list if e.get("_part") == "text"), None)
                tc_parts = [e for e in ev_list if e.get("_part") == "tool_call"]

                has_text = text_part and (text_part["id"] not in pruned_ids)
                surviving_tcs = []
                for tc_part in tc_parts:
                    if tc_part["id"] not in pruned_ids:
                        tc_idx = tc_part["_tool_call_index"]
                        surviving_tcs.append(orig_input.tool_calls[tc_idx])

                if has_text or surviving_tcs:
                    content_val = getattr(orig_input, "content", "") if has_text else ""
                    kwargs = {k: v for k, v in orig_input.__dict__.items() if k not in {"content", "tool_calls"}}
                    if surviving_tcs:
                        new_msg = orig_input.__class__(content=content_val, tool_calls=surviving_tcs, **kwargs)
                    else:
                        new_msg = orig_input.__class__(content=content_val, **kwargs)
                    reconstructed.append(new_msg)
            elif "ToolMessage" in cls_name:
                ev = ev_list[0]
                if ev["id"] not in pruned_ids:
                    reconstructed.append(orig_input)
            else:
                ev = ev_list[0]
                if ev["id"] not in pruned_ids:
                    reconstructed.append(orig_input)

    return reconstructed


def compact(messages: Any) -> CompactionResult:
    """Run the deterministic compaction pipeline on a universal input messages list."""
    # 1. Normalize inputs to trace-gc events
    events, orig_inputs, orig_types = normalize_input(messages)

    # 2. Run deterministic compaction
    result = compact_events(events)
    graph = result["graph"]
    pruned_ids = set(result["pruned_ids"])

    # 3. Create receipt objects
    receipts: list[Receipt] = []
    for pid in result["pruned_ids"]:
        reason = graph.prune_reasons.get(pid, "pruned")
        receipts.append(Receipt(node_id=pid, reason=reason, event=graph.nodes[pid]))

    # 4. Reconstruct output
    compacted_messages = reconstruct_output(events, pruned_ids, orig_inputs, orig_types)

    # 5. Extract token metrics and compaction ratio
    tokens_before = result["tokens_before"]
    tokens_after = result["tokens_after"]
    ratio = float(1.0 - (tokens_after / tokens_before)) if tokens_before > 0 else 0.0

    return CompactionResult(
        messages=compacted_messages,
        receipts=receipts,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        compaction_ratio=ratio,
        _original_events=events,
        _graph=graph,
        _original_inputs=orig_inputs,
        _original_types=orig_types
    )
