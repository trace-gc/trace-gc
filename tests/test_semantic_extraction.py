import pytest
from trace_gc import compact, CompactionResult, Receipt
from trace_gc.semantic import extract_semantic_events


def test_extract_key_value():
    text = "database = postgres\ntimeout: 30\nretry_count = 3\ndebug_mode = true"
    events = extract_semantic_events(text, prefix_id="kv_test", start_time=100)
    
    assert len(events) == 4
    
    # Types and content checking
    assert [e["type"] for e in events] == ["set_var", "set_var", "set_var", "set_var"]
    assert events[0]["key"] == "database"
    assert events[0]["value"] == "postgres"
    assert events[1]["key"] == "timeout"
    assert events[1]["value"] == 30
    assert events[2]["key"] == "retry_count"
    assert events[2]["value"] == 3
    assert events[3]["key"] == "debug_mode"
    assert events[3]["value"] is True

    # Check source text links
    assert events[0]["source_text"] == "database = postgres"
    assert events[0]["source_message_id"] == "kv_test"

    # Sequential chaining
    assert events[0]["parent_id"] is None
    assert events[1]["parent_id"] == events[0]["id"]
    assert events[2]["parent_id"] == events[1]["id"]
    assert events[3]["parent_id"] == events[2]["id"]


def test_extract_log_error():
    text = "[ERROR] Database connection failed\n[WARN] High memory usage warning!"
    events = extract_semantic_events(text, prefix_id="err_test", start_time=200)

    assert len(events) == 2
    assert [e["type"] for e in events] == ["error", "error"]
    assert events[0]["message"] == "[ERROR] Database connection failed"
    assert events[1]["message"] == "[WARN] High memory usage warning!"


def test_extract_git_diff():
    text = (
        "Some prefix log content\n"
        "diff --git a/src/main.py b/src/main.py\n"
        "index aabbcc..ddeeff 100644\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,3 +1,4 @@\n"
        " print('Hello')\n"
        "+print('World')\n"
    )
    events = extract_semantic_events(text, prefix_id="diff_test", start_time=300)

    assert len(events) == 1
    assert events[0]["type"] == "git_diff"
    assert "src/main.py" in events[0]["files_changed"]
    assert isinstance(events[0]["diff_hash"], str) and len(events[0]["diff_hash"]) == 32


def test_extract_git_commit():
    text = (
        "commit a1b2c3d4e5f6a1b2c3d4e5f6\n"
        "Author: John Doe <john@example.com>\n"
        "Date:   Tue Aug 11 20:00:00 2026 +0530\n"
        "\n"
        "    feat: Add new user module\n"
    )
    events = extract_semantic_events(text, prefix_id="commit_test", start_time=400)

    assert len(events) == 1
    assert events[0]["type"] == "git_commit"
    assert events[0]["commit_hash"] == "a1b2c3d4e5f6a1b2c3d4e5f6"
    assert events[0]["message"] == "feat: Add new user module"


def test_extract_pytest_summary():
    text = "================== 83 passed, 2 skipped, 1 failed in 14.15s =================="
    events = extract_semantic_events(text, prefix_id="pytest_test", start_time=500)

    assert len(events) == 1
    assert events[0]["type"] == "test_run"
    assert events[0]["passed_count"] == 83
    assert events[0]["failed_count"] == 1
    assert events[0]["exit_code"] == 1


def test_multiple_extracted_events_from_one_block():
    text = (
        "database=mysql\n"
        "[ERROR] query execution timed out\n"
        "timeout=60"
    )
    events = extract_semantic_events(text, prefix_id="multi_test", start_time=600)

    assert len(events) == 3
    assert [e["type"] for e in events] == ["set_var", "error", "set_var"]
    assert events[0]["key"] == "database"
    assert events[0]["value"] == "mysql"
    assert events[1]["message"] == "[ERROR] query execution timed out"
    assert events[2]["key"] == "timeout"
    assert events[2]["value"] == 60

    # Chaining check
    assert events[0]["parent_id"] is None
    assert events[1]["parent_id"] == events[0]["id"]
    assert events[2]["parent_id"] == events[1]["id"]


def test_recovery_via_receipts_and_recover_all():
    messages = [
        # Original message 1: config set
        {"role": "user", "content": "database=postgres\ntimeout=30"},
        # Original message 2: override config set
        {"role": "user", "content": "database=sqlite"},
        {"role": "assistant", "content": "Set database configuration."}
    ]

    # Compact with semantic extraction enabled
    result = compact(messages, semantic_extraction=True)

    # database=postgres should be superseded by database=sqlite and pruned!
    # timeout=30 and database=sqlite should survive.
    compacted = result.messages
    assert len(compacted) == 3  # Both user messages and assistant message survive because timeout=30 survived in message 1
    
    # We should have pruned receipts for database=postgres
    pruned_receipts = [r for r in result.receipts if "superseded" in r.reason]
    assert len(pruned_receipts) >= 1
    
    pruned_node_id = pruned_receipts[0].node_id
    receipt_event = result.get_receipt(pruned_node_id)
    assert receipt_event["key"] == "database"
    assert receipt_event["value"] == "postgres"
    assert receipt_event["source_text"] == "database=postgres"

    # recover_all() recovers the exact original raw input messages list!
    recovered = result.recover_all()
    assert recovered == messages
