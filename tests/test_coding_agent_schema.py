import pytest
from trace_gc.events import validate_event
from trace_gc.compactor import _render_event, compact_events

def test_file_read_validation():
    # Valid
    event = {"id": "e1", "type": "file_read", "timestamp": 100, "parent_id": None, "path": "src/main.py"}
    assert validate_event(event) == event

    # Invalid - missing path
    with pytest.raises(ValueError, match="Missing required fields"):
        validate_event({"id": "e1", "type": "file_read", "timestamp": 100})

    # Invalid - empty path
    with pytest.raises(ValueError, match="must be a non-empty string"):
        validate_event({"id": "e1", "type": "file_read", "timestamp": 100, "path": ""})

def test_file_edit_validation():
    # Valid
    event = {"id": "e1", "type": "file_edit", "timestamp": 100, "path": "src/main.py", "diff_hash": "a1b2"}
    assert validate_event(event) == event

    # Invalid - missing diff_hash
    with pytest.raises(ValueError, match="Missing required fields"):
        validate_event({"id": "e1", "type": "file_edit", "timestamp": 100, "path": "src/main.py"})

def test_command_run_validation():
    # Valid
    event = {"id": "e1", "type": "command_run", "timestamp": 100, "command": "pytest", "exit_code": 0}
    assert validate_event(event) == event

    # Invalid - boolean exit_code
    with pytest.raises(ValueError, match="must be an integer"):
        validate_event({"id": "e1", "type": "command_run", "timestamp": 100, "command": "pytest", "exit_code": True})

    # Invalid - empty command
    with pytest.raises(ValueError, match="must be a non-empty string"):
        validate_event({"id": "e1", "type": "command_run", "timestamp": 100, "command": "", "exit_code": 0})

def test_test_run_validation():
    # Valid
    event = {
        "id": "e1", "type": "test_run", "timestamp": 100,
        "test_names": ["t1", "t2"], "exit_code": 0,
        "passed_count": 2, "failed_count": 0
    }
    assert validate_event(event) == event

    # Invalid - test_names wrong type
    with pytest.raises(ValueError, match="must be a list of strings"):
        validate_event({
            "id": "e1", "type": "test_run", "timestamp": 100,
            "test_names": "t1", "exit_code": 0,
            "passed_count": 2, "failed_count": 0
        })

def test_build_run_validation():
    event = {"id": "e1", "type": "build_run", "timestamp": 100, "exit_code": 1}
    assert validate_event(event) == event

def test_git_diff_validation():
    event = {"id": "e1", "type": "git_diff", "timestamp": 100, "diff_hash": "abc", "files_changed": ["a.py"]}
    assert validate_event(event) == event

def test_git_commit_validation():
    event = {"id": "e1", "type": "git_commit", "timestamp": 100, "commit_hash": "abcdef", "message": "feat"}
    assert validate_event(event) == event

def test_error_validation():
    # Valid with related_to
    event = {"id": "e1", "type": "error", "timestamp": 100, "message": "err", "related_to": "e0"}
    assert validate_event(event) == event

    # Valid without related_to
    event2 = {"id": "e1", "type": "error", "timestamp": 100, "message": "err"}
    assert validate_event(event2) == event2

    # Invalid related_to type
    with pytest.raises(ValueError, match="must be None or a non-empty string"):
        validate_event({"id": "e1", "type": "error", "timestamp": 100, "message": "err", "related_to": 123})

def test_artifact_created_validation():
    event = {"id": "e1", "type": "artifact_created", "timestamp": 100, "artifact_type": "plan", "path": "p.md"}
    assert validate_event(event) == event

def test_requirement_and_constraint_validation():
    req = {"id": "e1", "type": "requirement", "timestamp": 100, "content": "req"}
    assert validate_event(req) == req

    const = {"id": "e2", "type": "constraint", "timestamp": 100, "content": "const"}
    assert validate_event(const) == const

def test_verification_validation():
    event = {"id": "e1", "type": "verification", "timestamp": 100, "content": "v", "passed": True}
    assert validate_event(event) == event

def test_rendering_outputs():
    # test_run truncation (>3 names)
    tr_long = {
        "type": "test_run", "test_names": ["a", "b", "c", "d", "e"],
        "passed_count": 3, "failed_count": 2
    }
    assert _render_event(tr_long) == "TEST a, b, c, +2 more — 3 passed, 2 failed"

    # test_run short (<=3 names)
    tr_short = {
        "type": "test_run", "test_names": ["a", "b"],
        "passed_count": 2, "failed_count": 0
    }
    assert _render_event(tr_short) == "TEST a, b — 2 passed, 0 failed"

    # error rendering with related_to
    err_rel = {"type": "error", "message": "fail", "related_to": "e1"}
    assert _render_event(err_rel) == "ERROR: fail (related to e1)"

    # error rendering without related_to
    err_noreld = {"type": "error", "message": "fail", "related_to": None}
    assert _render_event(err_noreld) == "ERROR: fail"

    # verification rendering
    v_passed = {"type": "verification", "content": "tests", "passed": True}
    assert _render_event(v_passed) == "VERIFICATION: tests — passed"

    v_failed = {"type": "verification", "content": "tests", "passed": False}
    assert _render_event(v_failed) == "VERIFICATION: tests — failed"

def test_end_to_end_compaction_mix():
    events = [
        {"id": "e1", "type": "requirement", "timestamp": 100, "parent_id": None, "content": "Do X"},
        {"id": "e2", "type": "file_read", "timestamp": 110, "parent_id": "e1", "path": "a.py"},
        {"id": "e3", "type": "set_var", "timestamp": 120, "parent_id": "e2", "key": "v", "value": 1},
        {"id": "e4", "type": "file_edit", "timestamp": 130, "parent_id": "e3", "path": "a.py", "diff_hash": "h1"},
        {"id": "e5", "type": "set_var", "timestamp": 140, "parent_id": "e4", "key": "v", "value": 2},
    ]
    res = compact_events(events)
    # The first set_var should be pruned, requirement and file events should be kept
    assert "e3" in res["pruned_ids"]
    assert "e5" not in res["pruned_ids"]
    assert "e1" not in res["pruned_ids"]
    assert "e2" not in res["pruned_ids"]
    assert "e4" not in res["pruned_ids"]
