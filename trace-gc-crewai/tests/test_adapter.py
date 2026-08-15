import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from trace_gc_crewai.adapter import (
    compact_messages,
    TraceGCCrewCallback,
    create_step_callback,
    TraceGCCrewAdapter,
)

# Gate tests requiring the real crewai package
crewai = pytest.importorskip("crewai")


def test_compact_messages_crewai_basic():
    """Test compact_messages with basic message dicts and string history."""
    history = [
        {"role": "user", "content": "I want to use PostgreSQL for database."},
        {"role": "user", "content": "Actually, switch database to MongoDB."},
    ]
    res = compact_messages(history, prune_semantic=True, tracked_decision_keys={"database"})
    assert len(res.messages) == 1
    assert "MongoDB" in res.messages[0]["content"]


def test_trace_gc_crew_callback():
    """Test TraceGCCrewCallback step recording and compaction."""
    callback = TraceGCCrewCallback(prune_semantic=True, tracked_decision_keys={"database"})

    # Simulate step outputs recorded during CrewAI agent execution
    callback("Step 1: Set database=postgres")
    callback("Step 2: Override database=sqlite")

    assert len(callback.steps) == 2
    res = callback.compact()
    assert len(res.messages) == 1
    assert "sqlite" in res.messages[0]["content"]

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
    adapter = TraceGCCrewAdapter(prune_semantic=True, tracked_decision_keys={"database"})
    cb = adapter.get_step_callback()

    cb("database=postgres configured")
    res = adapter.compact_history(["database=mongodb active"])

    assert len(res.messages) == 1
    assert "mongodb" in res.messages[0]["content"]


def test_real_crewai_agent_instantiation():
    """Verify integration with real CrewAI Agent and Crew objects."""
    from crewai import Agent, Crew, Task

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
