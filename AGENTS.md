# TraceGC Agent Developer Guide

This document outlines the developer contracts, build commands, test instructions, and event schema definitions for **TraceGC**.

## Runtime Dependency Contract

* **Zero Required Runtime Dependencies**: The core TraceGC library (`tracegc/`) is fully self-contained and does not require any external runtime libraries (e.g., no packages like `numpy`, `networkx`, `requests`, or LLM client libraries are needed to run compaction).
* **Optional / Test Dependencies**: Testing tools and benchmark runners (such as `pytest`, `hypothesis`, and `google-genai`) are defined under the optional `test` dependency group in `pyproject.toml`.

---

## Build & Test Commands

To install and verify the library locally:

### Installation
For local development, install in editable mode:
```bash
pip install -e .
```

To install test dependencies:
```bash
pip install -e ".[test]"
```

### Running Tests
Run the complete deterministic verification suite (including property-based tests, schema fuzzing, and semantic probes):
```bash
pytest
```

---

## Event Schema Contract

TraceGC expects events to follow a strict validation schema defined in [tracegc/events.py](tracegc/events.py). Every event is a dictionary containing the following core fields:
* `id` (string, required): A unique identifier for the event.
* `type` (string, required): One of the five structured types listed below.
* `timestamp` (integer or float, required): Unix timestamp or sequential counter.
* `parent_id` (string, optional): Points to the immediate predecessor event to reconstruct the execution trace path.

### The Five Event Types

#### 1. `set_var` (State Assignment)
Indicates a state variable update. Superseded variables of the same key are pruned.
* `key` (string, required)
* `value` (any JSON-serializable, required)

#### 2. `tool_call` (Tool Execution Intent)
Represents the invocation of a tool or function.
* `tool_name` (string, required)
* `arguments` (dict, required)

#### 3. `tool_result` (Tool Return Value)
Captures the output of a previously executed tool.
* `call_id` (string, required): Maps directly to the `id` of the corresponding `tool_call`.
* `result` (any JSON-serializable, required)

#### 4. `abandon` (Branch Rollback)
Instructs the dead-branch sweeper to prune a failed or aborted execution path.
* `ref_to` (list of strings, required): List of node IDs representing the start of the branch to prune.

#### 5. `decision` (Agent Log / Reasoning)
Captures the internal reasoning or transitions of the agent.
* `content` (string, required): Prose describing the agent's state or action.
