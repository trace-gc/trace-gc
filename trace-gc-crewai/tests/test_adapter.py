import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from trace_gc_crewai.adapter import (
    compact_messages,
    TraceGCCrewCallback,
    create_step_callback,
    TraceGCCrewAdapter,
    _normalize_crewai_step,
)

# Gate tests requiring the real crewai package
crewai = pytest.importorskip("crewai")


def test_compact_messages_crewai_basic():
    """Test compact_messages with structured event dicts and message history."""
    history = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "key": "database", "value": "redis", "status": "FAILED"},
        {"id": "e2", "type": "set_var", "timestamp": 110, "key": "database", "value": "postgresql", "status": "CONFIRMED"},
    ]
    res = compact_messages(history)
    pruned_ids = [r.node_id for r in res.receipts]
    assert "e1" in pruned_ids
    assert "e2" not in pruned_ids


def test_trace_gc_crew_callback():
    """Test TraceGCCrewCallback step recording and compaction."""
    callback = TraceGCCrewCallback()

    step1 = {"id": "s1", "type": "command_run", "timestamp": 100, "command": "pytest tests/a.py", "exit_code": 1}
    step2 = {"id": "s2", "type": "command_run", "timestamp": 110, "command": "pytest tests/a.py", "exit_code": 0}

    callback(step1)
    callback(step2)

    assert len(callback.steps) == 2
    res = callback.compact()
    pruned_ids = [r.node_id for r in res.receipts]
    assert "s1" in pruned_ids
    assert "s2" not in pruned_ids

    callback.clear()
    assert len(callback.steps) == 0


def test_create_step_callback_wrapper():
    """Test create_step_callback factory and user callback invocation."""
    invoked = []

    def custom_fn(output):
        invoked.append(output)

    cb = create_step_callback(callback_fn=custom_fn)
    cb("Action step")

    assert len(cb.steps) == 1
    assert invoked == ["Action step"]


def test_trace_gc_crew_adapter_class():
    """Test TraceGCCrewAdapter step tracking and history compaction."""
    adapter = TraceGCCrewAdapter()
    cb = adapter.get_step_callback()

    cb({"id": "v1", "type": "set_var", "timestamp": 100, "key": "mode", "value": "slow"})
    res = adapter.compact_history([{"id": "v2", "type": "set_var", "timestamp": 200, "key": "mode", "value": "fast"}])

    pruned_ids = [r.node_id for r in res.receipts]
    assert "v1" in pruned_ids
    assert "v2" not in pruned_ids


def test_crewai_step_objects_normalization():
    """Verify normalization of real AgentAction, AgentFinish, and TaskOutput objects."""
    from langchain_core.agents import AgentAction, AgentFinish
    from crewai.tasks.task_output import TaskOutput

    action = AgentAction(
        tool="db_selector",
        tool_input={"key": "database", "value": "sqlite"},
        log="Thought: selecting db\nAction: db_selector",
    )
    norm_action = _normalize_crewai_step(action)
    assert norm_action["type"] == "tool_call"
    assert norm_action["tool_name"] == "db_selector"
    assert norm_action["arguments"] == {"key": "database", "value": "sqlite"}

    finish = AgentFinish(return_values={"output": "Selected sqlite"}, log="Finished")
    norm_finish = _normalize_crewai_step(finish)
    assert norm_finish["role"] == "assistant"
    assert "Selected sqlite" in norm_finish["content"]

    task_out = TaskOutput(description="Select DB", result="sqlite selected", agent="Architect")
    norm_task = _normalize_crewai_step(task_out)
    assert norm_task["role"] == "assistant"
    assert norm_task["content"] == "sqlite selected"


def test_real_crewai_agent_instantiation():
    """Verify integration with real CrewAI Agent and Crew objects."""
    from crewai import Agent, Crew

    agent = Agent(
        role="Researcher",
        goal="Gather data",
        backstory="Expert researcher",
        verbose=False,
    )
    assert agent.role == "Researcher"

    cb = TraceGCCrewCallback()
    crew = Crew(
        agents=[agent],
        tasks=[],
        step_callback=cb,
        verbose=False,
    )
    assert crew.step_callback is cb
