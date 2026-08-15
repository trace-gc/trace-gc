import pytest
from tracegc.graph import StateGraph
from tracegc.override_engine import apply_overrides
from tracegc.compactor import compact_events


def test_apply_overrides():
    """Baseline: 3 writes to same key — newest survives, older two pruned."""
    graph = StateGraph()
    graph.add_node({"id": "v1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1})
    graph.add_node({"id": "v2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2})
    graph.add_node({"id": "v3", "type": "set_var", "timestamp": 150, "key": "x", "value": 3})
    graph.add_node({"id": "v4", "type": "set_var", "timestamp": 300, "key": "y", "value": 4})

    pruned = apply_overrides(graph)
    # v2 is the newest for key "x" (timestamp 200 > 150 > 100)
    assert set(pruned) == {"v1", "v3"}
    assert "v1" in graph.pruned
    assert "v3" in graph.pruned
    assert "v2" not in graph.pruned
    assert "v4" not in graph.pruned

    edges = [(src, dst, typ) for src, dst, typ in graph.edges if typ == "supersedes"]
    assert set(edges) == {
        ("v2", "v1", "supersedes"),
        ("v2", "v3", "supersedes")
    }


def test_timestamp_tie_deterministic_tiebreak():
    """When two set_var events share a timestamp, the one appearing later in
    the input list is kept (stable sort tiebreak — deterministic for fixed input)."""
    graph = StateGraph()
    # Both at timestamp 100; "v2" appears later in input list
    graph.add_node({"id": "v1", "type": "set_var", "timestamp": 100, "key": "x", "value": "first"})
    graph.add_node({"id": "v2", "type": "set_var", "timestamp": 100, "key": "x", "value": "second"})

    pruned = apply_overrides(graph)
    # v2 appears later => it is "newest" after stable sort
    assert "v1" in graph.pruned
    assert "v2" not in graph.pruned

    # Running again with same graph (after clearing pruned) must give same result
    graph2 = StateGraph()
    graph2.add_node({"id": "v1", "type": "set_var", "timestamp": 100, "key": "x", "value": "first"})
    graph2.add_node({"id": "v2", "type": "set_var", "timestamp": 100, "key": "x", "value": "second"})
    pruned2 = apply_overrides(graph2)
    assert pruned2 == pruned


def test_set_var_after_abandon_surviving_override():
    """A set_var written after an abandon event on a fresh path is the newest and must survive."""
    events = [
        {"id": "e1", "type": "set_var", "timestamp": 100, "key": "mode", "value": "slow"},
        {"id": "ab1", "type": "abandon", "timestamp": 200, "ref_to": ["e1"]},
        {"id": "e2", "type": "set_var", "timestamp": 300, "key": "mode", "value": "fast"},
    ]
    result = compact_events(events)
    pruned_ids = result["pruned_ids"]
    live_ids = [e["id"] for e in result["compact_events"]]

    # e1 is pruned by the abandon (dead branch sweeper)
    assert "e1" in pruned_ids
    # e2 is the surviving set_var — it must not be pruned by override
    assert "e2" in live_ids
    assert "e2" not in pruned_ids


def test_prune_referenced_values_true_default():
    """Default mode (prune_referenced_values=True): older value pruned even if tool_call references it."""
    events = [
        # set x=1 (older)
        {"id": "sv1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1},
        # tool_call using x=1 in its arguments
        {"id": "tc1", "type": "tool_call", "timestamp": 150, "tool_name": "compute",
         "arguments": {"x": 1}},
        {"id": "tr1", "type": "tool_result", "timestamp": 160, "call_id": "tc1", "result": "ok"},
        # set x=2 (newer, supersedes sv1)
        {"id": "sv2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2},
    ]
    result = compact_events(events, prune_referenced_values=True)
    assert "sv1" in result["pruned_ids"], "Default mode must prune sv1 even though tc1 uses x=1"
    assert "sv2" not in result["pruned_ids"]


def test_prune_referenced_values_false_replay_safe():
    """Replay-safe mode (prune_referenced_values=False): older value retained if tool_call still references it."""
    events = [
        # set x=1 (older)
        {"id": "sv1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1},
        # active tool_call uses x=1
        {"id": "tc1", "type": "tool_call", "timestamp": 150, "tool_name": "compute",
         "arguments": {"x": 1}},
        {"id": "tr1", "type": "tool_result", "timestamp": 160, "call_id": "tc1", "result": "ok"},
        # set x=2 (newer)
        {"id": "sv2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2},
    ]
    result = compact_events(events, prune_referenced_values=False)
    # sv1 must be RETAINED because tc1 (active) has arguments["x"] == 1
    assert "sv1" not in result["pruned_ids"], (
        "Replay-safe mode must retain sv1 because tc1 references x=1"
    )
    assert "sv2" not in result["pruned_ids"]


def test_prune_referenced_values_false_unreferenced_still_pruned():
    """Replay-safe mode: an older value NOT referenced by any tool_call is still pruned."""
    events = [
        {"id": "sv1", "type": "set_var", "timestamp": 100, "key": "x", "value": 1},
        {"id": "sv2", "type": "set_var", "timestamp": 200, "key": "x", "value": 2},
        # No tool_call references x=1
    ]
    result = compact_events(events, prune_referenced_values=False)
    assert "sv1" in result["pruned_ids"], "Unreferenced older values must still be pruned in replay-safe mode"
    assert "sv2" not in result["pruned_ids"]


def test_tracked_decision_keys_generalized():
    """Verify tracked_decision_keys specifies which keys get decision-lifecycle treatment vs plain override."""
    events = [
        {"id": "v1", "type": "set_var", "timestamp": 100, "key": "auth_provider", "value": "oauth"},
        {"id": "v2", "type": "set_var", "timestamp": 200, "key": "auth_provider", "value": "saml"},
        {"id": "c1", "type": "set_var", "timestamp": 150, "key": "cache_backend", "value": "redis"},
        {"id": "c2", "type": "set_var", "timestamp": 250, "key": "cache_backend", "value": "memcached"},
    ]
    res = compact_events(events, tracked_decision_keys={"auth_provider"}, prune_semantic=False)
    assert "v1" in res["pruned_ids"]
    assert "c1" in res["pruned_ids"]
    assert res["graph"].prune_reasons["v1"] == "superseded by v2"
    assert res["graph"].prune_reasons["c1"] == "overridden by c2"


def test_untracked_key_default_path():
    """Verify that when tracked_decision_keys is not passed (None), default path treats all set_var
    keys as decision-tracked ('superseded by st2')."""
    graph = StateGraph()
    graph.add_node({"id": "st1", "type": "set_var", "timestamp": 100, "key": "session_timeout", "value": 30})
    graph.add_node({"id": "st2", "type": "set_var", "timestamp": 200, "key": "session_timeout", "value": 60})

    pruned = apply_overrides(graph)  # tracked_decision_keys not passed -> default path (None)
    assert "st1" in pruned
    assert "st2" not in pruned
    assert graph.prune_reasons["st1"] == "superseded by st2"


def test_tracked_decision_keys_preserves_untracked_pruning():
    """Verify that passing tracked_decision_keys={"auth_provider"} does NOT disable
    ordinary keep-latest override pruning for untracked keys like 'session_timeout'."""
    graph = StateGraph()
    # auth_provider (tracked decision key)
    graph.add_node({"id": "ap1", "type": "set_var", "timestamp": 100, "key": "auth_provider", "value": "oauth"})
    graph.add_node({"id": "ap2", "type": "set_var", "timestamp": 200, "key": "auth_provider", "value": "saml"})

    # session_timeout (untracked key)
    graph.add_node({"id": "st1", "type": "set_var", "timestamp": 150, "key": "session_timeout", "value": 30})
    graph.add_node({"id": "st2", "type": "set_var", "timestamp": 250, "key": "session_timeout", "value": 60})

    pruned = apply_overrides(graph, tracked_decision_keys={"auth_provider"})

    # Both ap1 (decision key) and st1 (untracked key) must be pruned!
    assert "ap1" in pruned
    assert "st1" in pruned
    assert "ap2" not in pruned
    assert "st2" not in pruned

    # ap1 gets decision-lifecycle treatment (superseded by ap2)
    assert graph.prune_reasons["ap1"] == "superseded by ap2"
    # st1 gets plain override treatment (overridden by st2)
    assert graph.prune_reasons["st1"] == "overridden by st2"


def test_decision_lifecycle_transitions_generic_key():
    """Verify ACTIVE -> PROPOSED -> CONFIRMED -> SUPERSEDED lifecycle transitions
    and metadata using a non-'database' key ('auth_provider')."""
    graph = StateGraph()

    # 1. PROPOSED: Initial decision proposed via set_var
    e_prop = {"id": "e_prop", "type": "set_var", "timestamp": 100, "key": "auth_provider", "value": "oauth"}
    graph.add_node(e_prop)

    # 2. ACTIVE / CONFIRMED: Action & result using auth_provider=oauth
    tc_use = {"id": "tc1", "type": "tool_call", "timestamp": 150, "tool_name": "authenticate", "arguments": {"auth_provider": "oauth"}}
    tr_confirm = {"id": "tr1", "type": "tool_result", "timestamp": 160, "call_id": "tc1", "result": "oauth_connected"}
    graph.add_node(tc_use)
    graph.add_node(tr_confirm)

    # 3. SUPERSEDED: Newer set_var updating auth_provider to saml
    e_new = {"id": "e_new", "type": "set_var", "timestamp": 200, "key": "auth_provider", "value": "saml"}
    graph.add_node(e_new)

    # Apply overrides with tracked_decision_keys={"auth_provider"}
    pruned = apply_overrides(graph, tracked_decision_keys={"auth_provider"})

    # Verify lifecycle transitions and metadata:
    # 1. Proposed/Initial event e_prop is now SUPERSEDED
    assert "e_prop" in pruned
    assert "e_prop" in graph.pruned
    assert graph.prune_reasons["e_prop"] == "superseded by e_new"

    # 2. Receipt stub generated for e_prop encoding superseded state for recovery
    assert "e_prop" in graph.receipts
    rcpt = graph.receipts["e_prop"]
    assert rcpt["target_id"] == "e_prop"
    assert rcpt["status"] == "pruned"

    # 3. Direct 'supersedes' edge linking newest (e_new) -> superseded (e_prop)
    supersedes_edges = [(src, dst, typ) for src, dst, typ in graph.edges if typ == "supersedes"]
    assert ("e_new", "e_prop", "supersedes") in supersedes_edges

    # 4. New event e_new remains ACTIVE / surviving
    assert "e_new" not in graph.pruned



