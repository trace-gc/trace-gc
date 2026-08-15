import sys
import subprocess
import json
import tempfile
import os

TEST_EVENTS = [
    {"id": "e1", "type": "set_var", "timestamp": 1000, "key": "rate", "value": 5, "importance": "critical"},
    {"id": "e2", "type": "set_var", "timestamp": 1010, "key": "rate", "value": 10},
    {"id": "e3", "type": "set_var", "timestamp": 1020, "key": "timeout", "value": 100},
    {"id": "e4", "type": "set_var", "timestamp": 1030, "key": "timeout", "value": 200}
]


def run_cli(args):
    cmd = [sys.executable, "-m", "tracegc.cli"] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res


def test_cli_compact_and_dry_run():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w+", delete=False) as f:
        json.dump(TEST_EVENTS, f)
        f.flush()
        f_name = f.name

    try:
        # Test dry-run compact
        res = run_cli(["compact", "--dry-run", f_name])
        assert res.returncode == 0
        assert "PRUNE e3 | Reason: superseded by e4" in res.stdout
        assert "PROTECT e1 | Reason: importance=critical" in res.stdout
        assert "timeout = 100" not in res.stdout

        # Test plain compact
        res2 = run_cli(["compact", f_name])
        assert res2.returncode == 0
        assert "rate = 5" in res2.stdout
        assert "rate = 10" in res2.stdout
        assert "timeout = 200" in res2.stdout
        assert "timeout = 100" not in res2.stdout
    finally:
        os.remove(f_name)


def test_cli_explain():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w+", delete=False) as f:
        json.dump(TEST_EVENTS, f)
        f.flush()
        f_name = f.name

    try:
        # Explain protected node e1
        res = run_cli(["explain", f_name, "e1"])
        assert res.returncode == 0
        assert "Status: protected" in res.stdout
        assert "Reason: importance=critical" in res.stdout
        assert "Detection detail: Would be superseded by e2" in res.stdout

        # Explain pruned node e3
        res = run_cli(["explain", f_name, "e3"])
        assert res.returncode == 0
        assert "Status: pruned" in res.stdout
        assert "Reason: superseded by e4" in res.stdout

        # Explain kept node e4
        res = run_cli(["explain", f_name, "e4"])
        assert res.returncode == 0
        assert "Status: kept" in res.stdout

        # Explain non-existent node
        res = run_cli(["explain", f_name, "non-existent"])
        assert res.returncode != 0
        assert "Error:" in res.stderr
    finally:
        os.remove(f_name)


def test_cli_restore():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w+", delete=False) as f:
        json.dump(TEST_EVENTS, f)
        f.flush()
        f_name = f.name

    try:
        # Restore pruned node e3
        res = run_cli(["restore", f_name, "e3"])
        assert res.returncode == 0
        parsed = json.loads(res.stdout)
        assert parsed["id"] == "e3"
        assert parsed["value"] == 100

        # Restore non-pruned node e4
        res2 = run_cli(["restore", f_name, "e4"])
        assert res2.returncode != 0
        assert "never pruned" in res2.stderr
    finally:
        os.remove(f_name)


def test_cli_diff():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w+", delete=False) as f:
        json.dump(TEST_EVENTS, f)
        f.flush()
        f_name = f.name

    try:
        res = run_cli(["diff", f_name])
        assert res.returncode == 0
        assert "-timeout = 100" in res.stdout
    finally:
        os.remove(f_name)
