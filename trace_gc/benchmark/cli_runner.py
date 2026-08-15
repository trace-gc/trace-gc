# trace_gc/benchmark/cli_runner.py
"""CLI runner for trace-gc comparative benchmarking.

Provides functions to run deterministic benchmark methods against trace files or
the 9 bundled benchmark fixtures, and format results as JSON or plain-text tables.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Any, Optional, Tuple

from trace_gc import TraceGC, compact_events
from trace_gc.events import validate_event
from trace_gc.benchmark.methods import (
    method_full_history,
    method_truncate_by_event_count,
    method_truncate_by_token_count,
)
from trace_gc.benchmark.scoring import evaluate_probes

FIXTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures"))

BUNDLED_FIXTURE_NAMES = [
    "coding_agent_short.json",
    "coding_agent_medium.json",
    "coding_agent_long.json",
    "research_agent_short.json",
    "research_agent_medium.json",
    "research_agent_long.json",
    "customer_support_short.json",
    "customer_support_medium.json",
    "customer_support_long.json",
]


def load_trace_file(path: str) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]], Optional[Dict[str, Any]]]:
    """Load trace events, injected edges, and optional probes from a JSON or JSONL file.

    Supports:
    - Dict with "events" key (list) and optional "probes" key (dict)
    - Bare list of events
    - JSONL file (lines of event dicts)
    """
    with open(path, "r", encoding="utf-8") as f:
        try:
            first_char = f.read(1)
            f.seek(0)
        except Exception as e:
            raise ValueError(f"Failed to read file: {e}")

        if first_char in ("[", "{"):
            try:
                raw = json.load(f)
            except Exception as e:
                raise ValueError(f"Failed to parse JSON: {e}")

            if isinstance(raw, dict):
                events_raw = raw.get("events", [])
                probes = raw.get("probes")
            elif isinstance(raw, list):
                events_raw = raw
                probes = None
            else:
                raise ValueError("Top-level JSON must be a list or dict")
        else:
            # Parse JSONL
            events_raw = []
            probes = None
            for line_idx, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        ev = json.loads(line)
                    except Exception as e:
                        raise ValueError(f"Line {line_idx} is not valid JSON: {e}")
                    events_raw.append(ev)

        clean_events = []
        injected_edges = []
        for ev in events_raw:
            if isinstance(ev, dict) and ev.get("type") == "edge_injection":
                injected_edges.append((ev["src"], ev["dst"]))
            else:
                clean_events.append(validate_event(ev))

        return clean_events, injected_edges, probes


def run_single_benchmark(
    events: List[Dict[str, Any]],
    injected_edges: Optional[List[Tuple[str, str]]] = None,
    probes: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Run all deterministic benchmark methods on a single event trace."""
    methods_res = []

    # 1. full_history
    prompt_fh, tokens_fh, elapsed_fh = method_full_history(events)
    probes_fh = evaluate_probes(prompt_fh, probes) if probes else None
    methods_res.append({
        "method": "full_history",
        "tokens": tokens_fh,
        "elapsed_seconds": round(elapsed_fh, 4),
        "deterministic": True,
        "probes": probes_fh,
    })

    # 2. truncate_by_event_count
    prompt_ec, tokens_ec, elapsed_ec = method_truncate_by_event_count(events)
    probes_ec = evaluate_probes(prompt_ec, probes) if probes else None
    methods_res.append({
        "method": "truncate_by_event_count",
        "tokens": tokens_ec,
        "elapsed_seconds": round(elapsed_ec, 4),
        "deterministic": True,
        "probes": probes_ec,
    })

    # 3. truncate_by_token_count
    prompt_tc, tokens_tc, elapsed_tc = method_truncate_by_token_count(events)
    probes_tc = evaluate_probes(prompt_tc, probes) if probes else None
    methods_res.append({
        "method": "truncate_by_token_count",
        "tokens": tokens_tc,
        "elapsed_seconds": round(elapsed_tc, 4),
        "deterministic": True,
        "probes": probes_tc,
    })

    # 4. trace_gc_pipeline
    start = time.perf_counter()
    if injected_edges:
        client = TraceGC()
        for ev in events:
            client.add_event(ev)
        for src, dst in injected_edges:
            client.graph.add_edge(src, dst, "sequence")
        comp_res = client.compact()
    else:
        comp_res = compact_events(events)
    elapsed_gc = time.perf_counter() - start
    prompt_gc = comp_res["prompt"]
    tokens_gc = comp_res["tokens_after"]
    graph_gc = comp_res["graph"]
    probes_gc = evaluate_probes(prompt_gc, probes, graph=graph_gc) if probes else None
    methods_res.append({
        "method": "trace_gc_pipeline",
        "tokens": tokens_gc,
        "elapsed_seconds": round(elapsed_gc, 4),
        "deterministic": True,
        "probes": probes_gc,
    })

    return methods_res


def run_benchmark_cli(
    trace_path: Optional[str] = None,
    use_sample: bool = False,
    output_format: str = "table",
) -> Dict[str, Any]:
    """Run benchmark against user trace file or bundled sample fixtures.

    Returns structured dict of benchmark results.
    """
    if not trace_path and not use_sample:
        raise ValueError("Either trace_path or --sample must be specified")

    traces_to_run = []
    if use_sample:
        for fname in BUNDLED_FIXTURE_NAMES:
            fpath = os.path.join(FIXTURES_DIR, fname)
            traces_to_run.append((fname.replace(".json", ""), fpath))
    else:
        name = os.path.basename(trace_path).replace(".json", "").replace(".jsonl", "")
        traces_to_run.append((name, trace_path))

    results = []
    for name, fpath in traces_to_run:
        events, injected_edges, probes = load_trace_file(fpath)
        methods_output = run_single_benchmark(events, injected_edges, probes)
        results.append({
            "name": name,
            "event_count": len(events),
            "methods": methods_output,
        })

    return {"results": results}


def format_benchmark_output(data: Dict[str, Any], output_format: str = "table") -> str:
    """Format benchmark result dict into JSON or human-readable Markdown/ASCII table."""
    if output_format == "json":
        return json.dumps(data, indent=2)

    lines = []
    for trace_item in data.get("results", []):
        trace_name = trace_item["name"]
        methods = trace_item.get("methods", [])
        has_probes = any(m.get("probes") is not None for m in methods)

        lines.append(f"### Benchmark Trace: {trace_name} ({trace_item['event_count']} events)")
        lines.append("")

        if has_probes:
            headers = ["Method", "Tokens", "Recall", "Artifact", "Continuation", "Decision", "Deterministic"]
            rows = []
            for m in methods:
                pr = m.get("probes") or {}
                rows.append([
                    m["method"],
                    str(m["tokens"]),
                    "100%" if pr.get("recall") else "0.0%",
                    "100%" if pr.get("artifact") else "0.0%",
                    "100%" if pr.get("continuation") else "0.0%",
                    "100%" if pr.get("decision") else "0.0%",
                    "Yes" if m["deterministic"] else "No",
                ])
        else:
            headers = ["Method", "Tokens", "Elapsed (s)", "Deterministic"]
            rows = []
            for m in methods:
                rows.append([
                    m["method"],
                    str(m["tokens"]),
                    f"{m['elapsed_seconds']:.4f}",
                    "Yes" if m["deterministic"] else "No",
                ])

        # Compute column widths
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, val in enumerate(row):
                col_widths[i] = max(col_widths[i], len(val))

        header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
        sep_line = "|-" + "-|-".join("-" * col_widths[i] for i in range(len(headers))) + "-|"

        lines.append(header_line)
        lines.append(sep_line)
        for row in rows:
            row_line = "| " + " | ".join(val.ljust(col_widths[i]) for i, val in enumerate(row)) + " |"
            lines.append(row_line)

        lines.append("")

    return "\n".join(lines).strip()
