# trace_gc/semantic.py
"""Rules-based, deterministic semantic extraction module.

Converts unstructured text logs, key-value configurations, git outputs, and test summaries
into structured trace events without calling any LLMs.
"""

from __future__ import annotations

import re
import hashlib
from typing import Any, Dict, List, Optional


class PatternRegistry:
    """Registry to store regex patterns and extraction functions."""

    def __init__(self) -> None:
        self.rules: List[Dict[str, Any]] = []

    def register(self, name: str, pattern: str, priority: int, extract_fn: Any) -> None:
        """Register a new extraction rule."""
        self.rules.append({
            "name": name,
            "pattern": re.compile(pattern),
            "priority": priority,
            "extract_fn": extract_fn
        })
        self.rules.sort(key=lambda x: x["priority"])


registry = PatternRegistry()


# -- 1. Git Diff Extraction Rule --
def extract_git_diff(match: re.Match, text: str) -> dict:
    diff_text = match.group(0)
    files = re.findall(r"^diff --git a/(\S+) b/\S+", diff_text, re.M)
    h = hashlib.md5(diff_text.encode("utf-8")).hexdigest()
    return {
        "type": "git_diff",
        "diff_hash": h,
        "files_changed": files
    }


registry.register(
    "git_diff",
    r"(?ms)^diff --git a/\S+ b/\S+\r?\n(?:(?!^diff --git a/).)*",
    10,
    extract_git_diff
)


# -- 2. Git Commit Extraction Rule --
def extract_git_commit(match: re.Match, text: str) -> dict:
    commit_hash = match.group(1)
    msg = match.group(2).strip()
    return {
        "type": "git_commit",
        "commit_hash": commit_hash,
        "message": msg
    }


registry.register(
    "git_commit",
    r"(?ms)^commit ([0-9a-f]{7,40})(?:\r?\n|\s+)(?:Author:.*?\r?\nDate:.*?\r?\n)?\r?\n?(.*?)(?=(?:^commit [0-9a-f]{7,40}|\Z))",
    20,
    extract_git_commit
)


# -- 3. Pytest Summary Extraction Rule --
def extract_pytest_summary(match: re.Match, text: str) -> dict:
    line = match.group(0)
    passed = 0
    failed = 0

    m_passed = re.search(r"(\d+)\s+passed", line)
    if m_passed:
        passed = int(m_passed.group(1))

    m_failed = re.search(r"(\d+)\s+failed", line)
    if m_failed:
        failed = int(m_failed.group(1))

    m_errors = re.search(r"(\d+)\s+error", line)
    if m_errors:
        failed += int(m_errors.group(1))

    exit_code = 1 if failed > 0 else 0
    return {
        "type": "test_run",
        "test_names": [],
        "exit_code": exit_code,
        "passed_count": passed,
        "failed_count": failed
    }


registry.register(
    "pytest_summary",
    r"(?i)=+.*?(?:\d+\s+passed|\d+\s+failed|\d+\s+error).*?=+",
    30,
    extract_pytest_summary
)


# -- 4. Key-Value Extraction Rule --
def extract_key_value(match: re.Match, text: str) -> dict:
    key = match.group(1).strip()
    val_str = match.group(2).strip()
    if (val_str.startswith('"') and val_str.endswith('"')) or (val_str.startswith("'") and val_str.endswith("'")):
        val_str = val_str[1:-1].strip()

    if val_str.lower() == "true":
        value = True
    elif val_str.lower() == "false":
        value = False
    elif val_str.lower() in ("none", "null"):
        value = None
    else:
        try:
            if "." in val_str:
                value = float(val_str)
            else:
                value = int(val_str)
        except ValueError:
            value = val_str

    return {
        "type": "set_var",
        "key": key,
        "value": value
    }


registry.register(
    "key_value",
    r"^\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*[:=]\s*([^\n\r]+)$",
    40,
    extract_key_value
)


# -- 5. Error/Log Extraction Rule --
def extract_log_error(match: re.Match, text: str) -> dict:
    level = match.group(1).upper()
    msg = match.group(2).strip()
    msg = re.sub(r"^[\s\]:\-]+", "", msg).strip()
    return {
        "type": "error",
        "message": f"[{level}] {msg}" if msg else match.group(0).strip()
    }


registry.register(
    "log_error",
    r"(?i)\b(error|warn|warning|fatal|exception)\b[:\s]*(.*)",
    50,
    extract_log_error
)


def extract_semantic_events(text: str, prefix_id: str, start_time: int) -> list[dict]:
    """Parse unstructured text blocks and extract structured TraceGC events rules-based."""
    extracted_events: list[dict] = []

    block_rules = [r for r in registry.rules if r["name"] in {"git_diff", "git_commit", "pytest_summary"}]
    line_rules = [r for r in registry.rules if r["name"] in {"key_value", "log_error"}]

    # Find block-level matches first
    matches = []
    for rule in block_rules:
        for match in rule["pattern"].finditer(text):
            matches.append((match.start(), match.end(), rule, match))

    # Sort matches by start position
    matches.sort(key=lambda x: x[0])

    # Filter overlapping matches
    non_overlapping = []
    last_end = 0
    for start, end, rule, match in matches:
        if start >= last_end:
            non_overlapping.append((start, end, rule, match))
            last_end = end

    # Split into segments
    segments = []
    curr = 0
    for start, end, rule, match in non_overlapping:
        if start > curr:
            segments.append((text[curr:start], None, None))
        segments.append((match.group(0), rule, match))
        curr = end
    if curr < len(text):
        segments.append((text[curr:], None, None))

    # Parse segments
    for seg_text, rule, match in segments:
        if rule is not None and match is not None:
            # Block-level rule: attempt extraction; fall back to text_chunk on failure
            # so no content is silently dropped (Phase 3 no-silent-loss invariant).
            try:
                payload = rule["extract_fn"](match, seg_text)
                payload["source_text"] = seg_text.strip()
                extracted_events.append(payload)
            except Exception:
                # Extraction failed — preserve text verbatim as a neutral event.
                if seg_text.strip():
                    extracted_events.append({
                        "type": "text_chunk",
                        "content": seg_text.strip(),
                        "source_text": seg_text.strip(),
                    })
        else:
            # Unstructured segment: parse line-by-line.
            lines = seg_text.splitlines()
            for line in lines:
                if not line.strip():
                    # Blank lines carry no content — skip without emitting an event.
                    continue
                matched = False
                for r in line_rules:
                    m = r["pattern"].search(line)
                    if m:
                        try:
                            payload = r["extract_fn"](m, line)
                            payload["source_text"] = line.strip()
                            extracted_events.append(payload)
                            matched = True
                            break
                        except Exception:
                            # Rule matched but extraction raised — treat as no-match
                            # so the fallback below runs.
                            pass
                if not matched:
                    # No rule matched (or all extract_fns failed) — preserve the
                    # line verbatim as a neutral text_chunk.  This is the
                    # "fail-closed" path that prevents silent data loss.
                    extracted_events.append({
                        "type": "text_chunk",
                        "content": line.strip(),
                        "source_text": line.strip(),
                    })

    # Chain events with sequence parent_id
    final_events = []
    prev_id = None
    for idx, ev in enumerate(extracted_events):
        ev_id = f"ext_{prefix_id}_{idx}"
        ev["id"] = ev_id
        ev["timestamp"] = start_time + idx
        ev["parent_id"] = prev_id
        ev["source_message_id"] = prefix_id
        final_events.append(ev)
        prev_id = ev_id

    return final_events
