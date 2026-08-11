import os
import pytest
from trace_gc import TraceGC
from trace_gc.middleware import call_anthropic_with_compaction, call_openai_with_compaction

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


@pytest.mark.skipif(not HAS_ANTHROPIC, reason="anthropic package is not installed")
@pytest.mark.skipif("ANTHROPIC_API_KEY" not in os.environ, reason="ANTHROPIC_API_KEY environment variable not set")
def test_anthropic_adapter_live():
    trace_gc = TraceGC()
    trace_gc.add_event({"id": "e1", "type": "set_var", "timestamp": 1000, "parent_id": None, "key": "x", "value": 10})
    trace_gc.add_event({"id": "e2", "type": "set_var", "timestamp": 1010, "parent_id": "e1", "key": "x", "value": 20})
    
    res = call_anthropic_with_compaction(
        trace_gc,
        model="claude-3-5-sonnet-20240620",
        user_message="Respond with only the final value of x."
    )
    assert "response_text" in res
    assert "metrics" in res
    assert "20" in res["response_text"]
    assert res["metrics"]["tokens_before"] > 0
    assert res["metrics"]["tokens_after"] > 0


@pytest.mark.skipif(not HAS_OPENAI, reason="openai package is not installed")
@pytest.mark.skipif("OPENAI_API_KEY" not in os.environ, reason="OPENAI_API_KEY environment variable not set")
def test_openai_adapter_live():
    trace_gc = TraceGC()
    trace_gc.add_event({"id": "e1", "type": "set_var", "timestamp": 1000, "parent_id": None, "key": "x", "value": 10})
    trace_gc.add_event({"id": "e2", "type": "set_var", "timestamp": 1010, "parent_id": "e1", "key": "x", "value": 20})
    
    res = call_openai_with_compaction(
        trace_gc,
        model="gpt-4o-mini",
        user_message="Respond with only the final value of x."
    )
    assert "response_text" in res
    assert "metrics" in res
    assert "20" in res["response_text"]
    assert res["metrics"]["tokens_before"] > 0
    assert res["metrics"]["tokens_after"] > 0
