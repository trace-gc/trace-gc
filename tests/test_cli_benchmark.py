# tests/test_cli_benchmark.py
"""Tests for the tracegc benchmark CLI runner."""

from __future__ import annotations

import json
import os
import pytest
from tracegc.benchmark.cli_runner import (
    run_benchmark_cli,
    format_benchmark_output,
    BUNDLED_FIXTURE_NAMES,
    FIXTURES_DIR,
)


def test_run_benchmark_cli_single_fixture():
    """Verify run_benchmark_cli against a single bundled fixture."""
    fixture_path = os.path.join(FIXTURES_DIR, "coding_agent_short.json")
    res = run_benchmark_cli(trace_path=fixture_path)
    
    assert "results" in res
    assert len(res["results"]) == 1
    
    trace_item = res["results"][0]
    assert trace_item["name"] == "coding_agent_short"
    assert trace_item["event_count"] > 0
    assert len(trace_item["methods"]) == 4
    
    method_names = [m["method"] for m in trace_item["methods"]]
    assert method_names == ["full_history", "truncate_by_event_count", "truncate_by_token_count", "tracegc_pipeline"]
    
    for m in trace_item["methods"]:
        assert "tokens" in m
        assert "elapsed_seconds" in m
        assert m["deterministic"] is True
        assert "probes" in m
        assert isinstance(m["probes"], dict)


def test_run_benchmark_cli_sample_all_fixtures():
    """Verify --sample runs against all 9 bundled fixtures."""
    res = run_benchmark_cli(use_sample=True)
    assert "results" in res
    assert len(res["results"]) == len(BUNDLED_FIXTURE_NAMES) == 9
    
    names = [r["name"] for r in res["results"]]
    for expected_file in BUNDLED_FIXTURE_NAMES:
        expected_name = expected_file.replace(".json", "")
        assert expected_name in names


def test_format_benchmark_output_json():
    """Verify output_format='json' produces valid, parseable JSON."""
    data = run_benchmark_cli(use_sample=True)
    json_str = format_benchmark_output(data, output_format="json")
    
    parsed = json.loads(json_str)
    assert "results" in parsed
    assert len(parsed["results"]) == 9


def test_benchmark_determinism():
    """Verify two consecutive benchmark runs produce byte-identical token counts and probe scores."""
    run1 = run_benchmark_cli(use_sample=True)
    run2 = run_benchmark_cli(use_sample=True)
    
    assert len(run1["results"]) == len(run2["results"]) == 9
    
    for trace1, trace2 in zip(run1["results"], run2["results"]):
        assert trace1["name"] == trace2["name"]
        assert trace1["event_count"] == trace2["event_count"]
        
        for m1, m2 in zip(trace1["methods"], trace2["methods"]):
            assert m1["method"] == m2["method"]
            assert m1["tokens"] == m2["tokens"], f"Token mismatch for {trace1['name']} / {m1['method']}"
            assert m1["probes"] == m2["probes"], f"Probe score mismatch for {trace1['name']} / {m1['method']}"


def test_cli_main_benchmark_sample(capsys, monkeypatch):
    """Test CLI main() invocation with 'benchmark --sample'."""
    from tracegc.cli import main
    monkeypatch.setattr("sys.argv", ["tracegc", "benchmark", "--sample"])
    main()
    captured = capsys.readouterr()
    assert "Benchmark Trace: coding_agent_short" in captured.out
    assert "| tracegc_pipeline" in captured.out


def test_cli_main_benchmark_file(capsys, monkeypatch):
    """Test CLI main() invocation with 'benchmark <path>'."""
    from tracegc.cli import main
    fixture_path = os.path.join(FIXTURES_DIR, "coding_agent_short.json")
    monkeypatch.setattr("sys.argv", ["tracegc", "benchmark", fixture_path, "--output", "json"])
    main()
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert len(parsed["results"]) == 1
    assert parsed["results"][0]["name"] == "coding_agent_short"
