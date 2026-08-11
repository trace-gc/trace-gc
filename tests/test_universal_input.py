import pytest
from trace_gc import compact, CompactionResult, Receipt

# Mock LangChain message objects for testing
class HumanMessage:
    def __init__(self, content, **kwargs):
        self.content = content
        self.__dict__.update(kwargs)

class AIMessage:
    def __init__(self, content, tool_calls=None, **kwargs):
        self.content = content
        self.tool_calls = tool_calls or []
        self.__dict__.update(kwargs)

class ToolMessage:
    def __init__(self, content, tool_call_id, **kwargs):
        self.content = content
        self.tool_call_id = tool_call_id
        self.__dict__.update(kwargs)


def test_openai_messages_compaction():
    # Test message list with duplicate tool calls that get pruned
    messages = [
        {"role": "user", "content": "Calculate 2+2 and 3+3"},
        {"role": "assistant", "content": "Let me run that.", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "add", "arguments": {"a": 2, "b": 2}}}
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "4"},
        # Duplicate tool call
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_2", "type": "function", "function": {"name": "add", "arguments": {"a": 2, "b": 2}}}
        ]},
        {"role": "tool", "tool_call_id": "call_2", "content": "4"},
        # Distinct tool call
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_3", "type": "function", "function": {"name": "add", "arguments": {"a": 3, "b": 3}}}
        ]},
        {"role": "tool", "tool_call_id": "call_3", "content": "6"},
    ]

    result = compact(messages)
    assert isinstance(result, CompactionResult)
    assert result.tokens_before > 0
    assert result.tokens_after <= result.tokens_before
    assert result.compaction_ratio >= 0.0

    # The duplicate call (call_2) and result (call_2) should be pruned
    # Assistant message for call_2 should be dropped since it had content=None and all tool_calls pruned.
    compacted = result.messages
    assert len(compacted) == 5  # user, assistant_1, tool_1, assistant_3, tool_3

    roles = [m["role"] for m in compacted]
    assert roles == ["user", "assistant", "tool", "assistant", "tool"]

    # Verify tool call 1 and 3 are present
    assert compacted[1]["tool_calls"][0]["id"] == "call_1"
    assert compacted[3]["tool_calls"][0]["id"] == "call_3"


def test_empty_assistant_message_dropped():
    messages = [
        {"role": "user", "content": "Hello"},
        # This assistant message has only duplicate tool calls and no content, so it will be dropped entirely
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_dup", "type": "function", "function": {"name": "add", "arguments": {"a": 2, "b": 2}}}
        ]},
        {"role": "tool", "tool_call_id": "call_dup", "content": "4"},
        # First occurrence (or kept reference)
        {"role": "assistant", "content": "Kept tool call", "tool_calls": [
            {"id": "call_orig", "type": "function", "function": {"name": "add", "arguments": {"a": 2, "b": 2}}}
        ]},
        {"role": "tool", "tool_call_id": "call_orig", "content": "4"},
    ]

    result = compact(messages)
    # The duplicate assistant message (which had content=None and duplicate tool call) should be dropped.
    # The kept one is retained.
    roles = [m["role"] for m in result.messages]
    assert "assistant" in roles
    # Let's check that the first assistant message (the one with None content and duplicate tool call) is gone
    # The surviving assistant message has content "Kept tool call"
    assistant_msgs = [m for m in result.messages if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == "Kept tool call"


def test_langchain_messages_support():
    messages = [
        HumanMessage(content="Hello AI"),
        AIMessage(content="Hello Human", tool_calls=[
            {"name": "fetch", "args": {"id": 123}, "id": "lc_call_1"}
        ]),
        ToolMessage(content="Data 123", tool_call_id="lc_call_1"),
        AIMessage(content=None, tool_calls=[
            {"name": "fetch", "args": {"id": 123}, "id": "lc_call_2"}  # Duplicate
        ]),
        ToolMessage(content="Data 123", tool_call_id="lc_call_2")
    ]

    result = compact(messages)
    compacted = result.messages
    assert len(compacted) == 3  # HumanMessage, AIMessage (first), ToolMessage (first)
    assert isinstance(compacted[0], HumanMessage)
    assert isinstance(compacted[1], AIMessage)
    assert isinstance(compacted[2], ToolMessage)
    assert compacted[1].content == "Hello Human"
    assert compacted[1].tool_calls[0]["id"] == "lc_call_1"


def test_anthropic_messages_support():
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me run a tool."},
            {"type": "tool_use", "id": "ant_call_1", "name": "weather", "input": {"city": "Paris"}}
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "ant_call_1", "content": "Rainy"}
        ]}
    ]

    result = compact(messages)
    compacted = result.messages
    assert len(compacted) == 3
    assert compacted[0]["role"] == "user"
    assert compacted[1]["role"] == "assistant"
    assert len(compacted[1]["content"]) == 2
    assert compacted[1]["content"][0]["text"] == "Let me run a tool."


def test_raw_strings_support():
    # Test a single string
    result1 = compact("Hello world")
    assert result1.messages == ["Hello world"]

    # Test a list of strings
    result2 = compact(["Hello", "World"])
    assert result2.messages == ["Hello", "World"]


def test_structured_event_passthrough():
    # Test mixing message list with raw TraceGC structured events
    messages = [
        {"role": "user", "content": "Init"},
        # Pre-formed structured trace event
        {"id": "evt_set", "type": "set_var", "timestamp": 105, "key": "status", "value": "active"},
        {"role": "assistant", "content": "Done"}
    ]

    result = compact(messages)
    compacted = result.messages
    assert len(compacted) == 3
    assert compacted[0]["role"] == "user"
    assert compacted[1]["type"] == "set_var"
    assert compacted[1]["value"] == "active"
    assert compacted[2]["role"] == "assistant"


def test_determinism():
    # Identical inputs produce identical outputs
    input_messages = [
        {"role": "user", "content": "Query"},
        {"role": "assistant", "content": "Running", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get", "arguments": {}}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"}
    ]

    res1 = compact(input_messages)
    res2 = compact(input_messages)

    assert res1.messages == res2.messages
    assert [r.node_id for r in res1.receipts] == [r.node_id for r in res2.receipts]
    assert res1.tokens_before == res2.tokens_before
    assert res1.tokens_after == res2.tokens_after


def test_receipt_recovery_and_recover_all():
    messages = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_dup", "type": "function", "function": {"name": "query", "arguments": {}}}
        ]},
        {"role": "tool", "tool_call_id": "call_dup", "content": "answer"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_orig", "type": "function", "function": {"name": "query", "arguments": {}}}
        ]},
        {"role": "tool", "tool_call_id": "call_orig", "content": "answer"}
    ]

    result = compact(messages)
    assert len(result.receipts) > 0

    # Recover specific receipt
    pruned_id = result.receipts[0].node_id
    receipt_event = result.get_receipt(pruned_id)
    assert receipt_event["id"] == pruned_id
    assert receipt_event["pruned"] is True

    # Recover all original inputs
    recovered = result.recover_all()
    assert len(recovered) == len(messages)
    assert recovered == messages
