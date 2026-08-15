# tracegc/cli.py
"""CLI tool for tracegc compaction management."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from tracegc.compactor import compact_events, _render_event
from tracegc.receipts import get_receipt


def load_events(path: str) -> list:
    from tracegc.events import validate_event
    events = []
    with open(path, "r", encoding="utf-8") as f:
        try:
            first_char = f.read(1)
            f.seek(0)
        except Exception as e:
            raise ValueError(f"Failed to read file: {e}")
        
        if first_char == '[':
            try:
                raw = json.load(f)
            except Exception as e:
                raise ValueError(f"Failed to parse JSON array: {e}")
            if not isinstance(raw, list):
                raise ValueError("Top-level JSON must be a list of events")
            for ev in raw:
                events.append(validate_event(ev))
        else:
            # Parse JSONL
            for line_idx, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        ev = json.loads(line)
                    except Exception as e:
                        raise ValueError(f"Line {line_idx} is not valid JSON: {e}")
                    events.append(validate_event(ev))
    return events


def main():
    parser = argparse.ArgumentParser(description="TraceGC CLI tool for context compaction management.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # compact subcommand
    compact_parser = subparsers.add_parser("compact", help="Compact event trace")
    compact_parser.add_argument("trace_file", help="Path to trace file (JSON or JSONL)")
    compact_parser.add_argument("--dry-run", action="store_true", help="Print audit logs of prune/protect actions without outputting compacted prompt")

    # explain subcommand
    explain_parser = subparsers.add_parser("explain", help="Explain state of a specific node")
    explain_parser.add_argument("trace_file", help="Path to trace file (JSON or JSONL)")
    explain_parser.add_argument("node_id", help="Node ID to explain")

    # restore subcommand
    restore_parser = subparsers.add_parser("restore", help="Restore a pruned node event payload")
    restore_parser.add_argument("trace_file", help="Path to trace file (JSON or JSONL)")
    restore_parser.add_argument("node_id", help="Node ID to restore")

    # diff subcommand
    diff_parser = subparsers.add_parser("diff", help="Diff original and compacted prompt output")
    diff_parser.add_argument("trace_file", help="Path to trace file (JSON or JSONL)")

    # benchmark subcommand
    benchmark_parser = subparsers.add_parser("benchmark", help="Run comparative context compaction benchmark")
    benchmark_parser.add_argument("trace_file", nargs="?", default=None, help="Path to trace file (JSON or JSONL)")
    benchmark_parser.add_argument("--sample", action="store_true", help="Run benchmark against all 9 bundled sample fixtures")
    benchmark_parser.add_argument("--output", choices=["table", "json"], default="table", help="Output format: table (default) or json")

    args = parser.parse_args()

    if args.command == "benchmark":
        if not args.trace_file and not args.sample:
            print("Error: Either trace_file path or --sample must be specified.", file=sys.stderr)
            sys.exit(1)
        from tracegc.benchmark.cli_runner import run_benchmark_cli, format_benchmark_output
        try:
            data = run_benchmark_cli(trace_path=args.trace_file, use_sample=args.sample, output_format=args.output)
            print(format_benchmark_output(data, output_format=args.output))
        except Exception as e:
            print(f"Error running benchmark: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Load events
    try:
        events = load_events(args.trace_file)
    except Exception as e:
        print(f"Error loading events: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "compact":
        res = compact_events(events)
        graph = res["graph"]
        if args.dry_run:
            # Print audit logs
            for pid in sorted(list(graph.pruned)):
                event = graph.nodes.get(pid)
                impact = max(0, len(_render_event(event)) // 4) if event else 0
                reason = graph.prune_reasons.get(pid, "unknown")
                print(f"PRUNE {pid} | Reason: {reason} | Prompt impact: -{impact} tokens | Recoverable: yes")
            
            for pid in sorted(list(graph.protected)):
                reason = graph.protected_reasons.get(pid, "unknown")
                print(f"PROTECT {pid} | Reason: {reason}")
        else:
            print(res["prompt"])

    elif args.command == "explain":
        res = compact_events(events)
        graph = res["graph"]
        node_id = args.node_id
        if node_id not in graph.nodes:
            print(f"Error: Node {node_id} does not exist in graph.", file=sys.stderr)
            sys.exit(1)
        
        event = graph.nodes[node_id]
        print(json.dumps(event, indent=2))
        
        if event.get("type") == "error" and event.get("related_to"):
            rel_id = event["related_to"]
            if rel_id in graph.nodes:
                rel_ev = graph.nodes[rel_id]
                print(f"Related to: {rel_ev.get('type')} event (ID: {rel_id})")
            else:
                print(f"Related to: unknown event (ID: {rel_id})")
        
        if node_id in graph.pruned:
            print("Status: pruned")
            print(f"Reason: {graph.prune_reasons.get(node_id, 'unknown')}")
        elif node_id in graph.protected:
            print("Status: protected")
            print(f"Reason: {graph.protected_reasons.get(node_id, 'unknown')}")
            if node_id in graph.prune_reasons:
                print(f"Detection detail: Would be {graph.prune_reasons[node_id]}")
        else:
            print("Status: kept")

    elif args.command == "restore":
        res = compact_events(events)
        graph = res["graph"]
        node_id = args.node_id
        if node_id not in graph.nodes:
            print(f"Error: Node {node_id} does not exist.", file=sys.stderr)
            sys.exit(1)
        if node_id not in graph.pruned:
            print(f"Error: Node {node_id} was never pruned (nothing to restore).", file=sys.stderr)
            sys.exit(1)
            
        receipt = get_receipt(graph, node_id)
        print(json.dumps(receipt, indent=2))

    elif args.command == "diff":
        res = compact_events(events)
        prompt_after = res["prompt"]
        original_strings = [_render_event(ev) for ev in events]
        prompt_before = "\n".join(filter(None, original_strings))
        
        before_lines = prompt_before.splitlines()
        after_lines = prompt_after.splitlines()
        
        diff = difflib.unified_diff(before_lines, after_lines, fromfile="original", tofile="compacted", lineterm="")
        for line in diff:
            print(line)


if __name__ == "__main__":
    main()
