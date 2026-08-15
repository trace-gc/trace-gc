# trace-gc-crewai

`trace-gc-crewai` is an official adapter package providing `TraceGC` graph-based context compaction for [CrewAI](https://github.com/crewAIInc/crewAI) agents, crews, and tasks.

## Installation

```bash
pip install trace-gc-crewai
```

Or install with test dependencies:
```bash
pip install trace-gc-crewai[test]
```

## Quick Start

### 1. Compact CrewAI Execution Steps via `step_callback`

Use `TraceGCCrewCallback` or `create_step_callback()` to record and compact step outputs during Crew execution:

```python
from crewai import Agent, Crew, Task
from trace_gc_crewai import create_step_callback

# Create a TraceGC step callback
step_cb = create_step_callback(
    prune_semantic=True,
    tracked_decision_keys={"database", "cache_backend"}
)

# Instantiate CrewAI Agent and Crew with step_callback
agent = Agent(
    role="Backend Architect",
    goal="Design scalable infrastructure",
    backstory="Senior system architect"
)

task = Task(
    description="Select storage engine and caching layer",
    expected_output="Final tech stack recommendation",
    agent=agent
)

crew = Crew(
    agents=[agent],
    tasks=[task],
    step_callback=step_cb
)

# Run crew...
# crew.kickoff()

# Compact accumulated step context
result = step_cb.compact()
print("Compacted messages:", len(result.messages))
print("Pruned receipts:", len(result.receipts))
```

### 2. Manual Context Compaction via `compact_messages`

If you manage task histories or step outputs directly:

```python
from trace_gc_crewai import compact_messages

step_history = [
    "Configured database=postgres",
    "Switched database=mongodb due to schema flexibility",
]

result = compact_messages(step_history, prune_semantic=True, tracked_decision_keys={"database"})
for msg in result.messages:
    print(msg["content"])
# Output retains only the active 'mongodb' decision and prunes the superseded 'postgres' choice.
```

---

## Why TraceGC instead of CrewAI's Built-in Memory?

CrewAI provides a built-in memory subsystem (`memory=True`) featuring `ShortTermMemory` (using ChromaDB vector RAG), `LongTermMemory`, and `EntityMemory`.

While CrewAI's built-in memory excels at **semantic retrieval (RAG)** across tasks:
- **CrewAI Memory** retrieves relevant facts by embedding similarity, but retains all past execution steps in raw history. It does not prune obsolete variables, superseded tech choices, or dead search branches.
- **TraceGC** builds a deterministic dependency DAG over trace events. It mathematically prunes obsolete states, superseded configuration decisions, and abandoned branches while preserving exact provenance and recoverable receipts.

Using `trace-gc-crewai` alongside CrewAI's memory ensures your agents run with minimal token context windows, zero obsolete state confusion, and full auditability.
