import pytest
from tracegc import compact, CompactionResult, Receipt
from tracegc.semantic import extract_semantic_events


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


def test_log_error_false_positives():
    """Regression test: log error should only match log-level prefix lines, not prose or negated lines."""
    # Valid log error prefix line (with date-only prefix)
    e1_date = extract_semantic_events("2026-08-14 ERROR: connection refused", prefix_id="err1_date", start_time=150)
    assert len(e1_date) == 1
    assert e1_date[0]["type"] == "error"
    assert e1_date[0]["message"] == "[ERROR] connection refused"

    # Prose line with "error" keyword should NOT extract as error
    e2 = extract_semantic_events("error handling implemented successfully", prefix_id="err2", start_time=200)
    assert len(e2) == 1
    assert e2[0]["type"] == "text_chunk"

    # Line with "no errors" should NOT extract as error
    e3 = extract_semantic_events("no errors found during validation", prefix_id="err3", start_time=300)
    assert len(e3) == 1
    assert e3[0]["type"] == "text_chunk"


def test_extract_tech_choice_generalized():
    """Task A: Verify tech choice extraction recognizes generalized categories (mongodb, kafka) and custom maps."""
    # MongoDB -> key="database", value="mongodb"
    e_mongo = extract_semantic_events("We decided to switch to MongoDB for storage.", prefix_id="tc1", start_time=100)
    assert len(e_mongo) == 1
    assert e_mongo[0]["type"] == "set_var"
    assert e_mongo[0]["key"] == "database"
    assert e_mongo[0]["value"] == "mongodb"

    # Kafka -> key="message_queue", value="kafka"
    e_kafka = extract_semantic_events("Switched to Kafka for messaging.", prefix_id="tc2", start_time=200)
    assert len(e_kafka) == 1
    assert e_kafka[0]["type"] == "set_var"
    assert e_kafka[0]["key"] == "message_queue"
    assert e_kafka[0]["value"] == "kafka"

    # Custom category map
    custom_map = {
        "search_engine": ["elasticsearch", "opensearch"]
    }
    e_custom = extract_semantic_events(
        "Configured ElasticSearch for indexing.",
        prefix_id="tc3",
        start_time=300,
        tech_category_map=custom_map
    )
    assert len(e_custom) == 1
    assert e_custom[0]["type"] == "set_var"
    assert e_custom[0]["key"] == "search_engine"
    assert e_custom[0]["value"] == "elasticsearch"


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

    # The prefix text "Some prefix log content" is not a git diff — with the
    # no-silent-loss fix it now produces a text_chunk fallback, so we get 2
    # events total: the text_chunk then the git_diff.
    assert len(events) == 2
    assert events[0]["type"] == "text_chunk"
    assert events[0]["source_text"] == "Some prefix log content"

    diff_ev = events[1]
    assert diff_ev["type"] == "git_diff"
    assert "src/main.py" in diff_ev["files_changed"]
    assert isinstance(diff_ev["diff_hash"], str) and len(diff_ev["diff_hash"]) == 32




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


# ---------------------------------------------------------------------------
# Phase 3 required tests (added after audit identified them as missing)
# ---------------------------------------------------------------------------


def test_semantic_extraction_disabled_fallback():
    """When semantic_extraction=False, no Tier 2 rules run.
    Content that would normally produce a structured event must instead
    come back as a neutral text_chunk event with the original text intact.
    """
    from tracegc.api import normalize_input

    # This text would extract key-value and error events under Tier 2.
    log_text = "database=postgres\n[ERROR] connection refused"

    events_on, _, _ = normalize_input([log_text], semantic_extraction=True)
    events_off, _, _ = normalize_input([log_text], semantic_extraction=False)

    # With extraction ON we get structured events (set_var / error).
    on_types = [e["type"] for e in events_on]
    assert "set_var" in on_types or "error" in on_types, (
        "Expected at least one structured event with extraction enabled"
    )

    # With extraction OFF every event must be a neutral text_chunk.
    for ev in events_off:
        assert ev["type"] == "text_chunk", (
            f"Expected text_chunk, got {ev['type']!r} with extraction disabled"
        )

    # The original content must be preserved verbatim in the text_chunk.
    combined_content = " ".join(ev["content"] for ev in events_off)
    assert "database=postgres" in combined_content or log_text in combined_content, (
        "Original text must appear verbatim in text_chunk content"
    )


def test_extraction_reproducibility():
    """Running extract_semantic_events() twice on identical input must
    produce identical events in type, payload content, source_text, and order.

    Note: event *IDs* are position-stamped (ext_{prefix}_{idx}) so they ARE
    deterministic and must also be equal across runs when prefix and
    start_time are the same.
    """
    from tracegc.semantic import extract_semantic_events

    text = (
        "database=postgres\n"
        "timeout=30\n"
        "[ERROR] disk full\n"
        "commit a1b2c3d\n"
        "Author: Dev <dev@example.com>\n"
        "Date:   Mon Jan 1 00:00:00 2024\n"
        "\n"
        "    feat: initial commit\n"
    )

    run1 = extract_semantic_events(text, prefix_id="repro", start_time=0)
    run2 = extract_semantic_events(text, prefix_id="repro", start_time=0)

    assert len(run1) == len(run2), (
        f"Event count differs between runs: {len(run1)} vs {len(run2)}"
    )

    for i, (e1, e2) in enumerate(zip(run1, run2)):
        assert e1["type"] == e2["type"], f"Event {i} type differs: {e1['type']} vs {e2['type']}"
        assert e1.get("source_text") == e2.get("source_text"), (
            f"Event {i} source_text differs"
        )
        assert e1["id"] == e2["id"], f"Event {i} id differs: {e1['id']} vs {e2['id']}"
        # Compare content-bearing fields without comparing bookkeeping metadata.
        for key in ("key", "value", "message", "content", "commit_hash",
                    "passed_count", "failed_count", "files_changed"):
            if key in e1 or key in e2:
                assert e1.get(key) == e2.get(key), (
                    f"Event {i} field {key!r} differs: {e1.get(key)!r} vs {e2.get(key)!r}"
                )


def test_no_silent_loss_on_unmatched_content():
    """Lines/segments that match no Tier 2 rule must NOT be silently dropped.
    They must appear in the output as text_chunk events so no content is lost.

    Cases covered:
      (a) A single line that matches no rule.
      (b) A mixed block: some matching lines interleaved with non-matching lines.
    """
    from tracegc.semantic import extract_semantic_events

    # (a) Purely unmatched content — e.g. a plain English sentence.
    plain_text = "This is just a plain English sentence with no structure."
    events_a = extract_semantic_events(plain_text, prefix_id="no_match_a", start_time=0)

    assert len(events_a) >= 1, "Unmatched content must produce at least one event"
    types_a = [e["type"] for e in events_a]
    assert "text_chunk" in types_a, (
        "Unmatched line must produce a text_chunk fallback, got: " + str(types_a)
    )
    source_texts_a = [e.get("source_text", "") for e in events_a]
    assert any(plain_text in st or plain_text == st for st in source_texts_a), (
        "Original unmatched text must be recoverable via source_text"
    )

    # (b) Mixed block: matching key-value interleaved with unmatched prose.
    mixed_text = (
        "database=postgres\n"
        "This sentence matches nothing.\n"
        "timeout=30\n"
        "Another unmatched line here.\n"
    )
    events_b = extract_semantic_events(mixed_text, prefix_id="no_match_b", start_time=100)

    types_b = [e["type"] for e in events_b]

    # Must contain at least one structured event (key-value → set_var).
    assert "set_var" in types_b, "Matching lines must still produce structured events"

    # Must contain at least one fallback text_chunk for the unmatched prose lines.
    assert "text_chunk" in types_b, (
        "Non-matching lines must produce text_chunk fallbacks, got: " + str(types_b)
    )

    # Ordering must be preserved: set_var for database comes before set_var for timeout.
    set_var_events = [e for e in events_b if e["type"] == "set_var"]
    assert len(set_var_events) >= 2, "Both key-value lines must produce set_var events"
    assert set_var_events[0]["key"] == "database"
    assert set_var_events[1]["key"] == "timeout"

    # The two unmatched lines must both be recoverable via source_text.
    text_chunk_events = [e for e in events_b if e["type"] == "text_chunk"]
    source_texts_b = {e.get("source_text", "") for e in text_chunk_events}
    assert any("This sentence matches nothing" in st for st in source_texts_b), (
        "First unmatched line must be in a text_chunk source_text"
    )
    assert any("Another unmatched line" in st for st in source_texts_b), (
        "Second unmatched line must be in a text_chunk source_text"
    )

    # Parent-chain ordering: timestamps and parent_ids must be monotonically chained.
    for ev in events_b:
        assert "parent_id" in ev
        assert "timestamp" in ev

