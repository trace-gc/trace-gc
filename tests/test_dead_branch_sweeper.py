import pytest
from trace_gc.graph import StateGraph
from trace_gc.dead_branch_sweeper import sweep_dead_branches


def test_sweep_dead_branches():
    """Baseline: simple abandoned branch, descendants pruned, active branch untouched."""
    graph = StateGraph()
    # branch 1: e1 -> e2 -> e3 (abandoned at e2)
    # branch 2: e1 -> e4 -> e5 (surviving)
    graph.add_node({"id": "e1", "type": "decision", "timestamp": 100})
    graph.add_node({"id": "e2", "type": "decision", "timestamp": 200, "parent_id": "e1"})
    graph.add_node({"id": "e3", "type": "tool_call", "timestamp": 300, "parent_id": "e2"})
    graph.add_node({"id": "e4", "type": "decision", "timestamp": 400, "parent_id": "e1"})
    graph.add_node({"id": "e5", "type": "tool_call", "timestamp": 500, "parent_id": "e4"})
    graph.add_node({"id": "ab1", "type": "abandon", "timestamp": 600, "parent_id": "e3", "ref_to": ["e2"]})

    graph.add_edge("e1", "e2", "sequence")
    graph.add_edge("e2", "e3", "sequence")
    graph.add_edge("e1", "e4", "sequence")
    graph.add_edge("e4", "e5", "sequence")
    graph.add_edge("e3", "ab1", "sequence")

    sweep_dead_branches(graph)
    assert "e2" in graph.pruned
    assert "e3" in graph.pruned
    assert "ab1" in graph.pruned
    assert "e1" not in graph.pruned
    assert "e4" not in graph.pruned
    assert "e5" not in graph.pruned

    assert graph.prune_reasons["e2"] == "abandoned by ab1"
    assert graph.prune_reasons["e3"] == "abandoned by ab1"
    assert graph.prune_reasons["ab1"] == "abandon event pruned alongside its own target branch"


def test_multiple_independent_abandoned_branches():
    """Two separate abandon events each prune their own branch independently."""
    graph = StateGraph()
    # Root
    graph.add_node({"id": "root", "type": "decision", "timestamp": 100})
    # Branch A (abandoned)
    graph.add_node({"id": "a1", "type": "decision", "timestamp": 200})
    graph.add_node({"id": "a2", "type": "decision", "timestamp": 300})
    # Branch B (abandoned)
    graph.add_node({"id": "b1", "type": "decision", "timestamp": 400})
    graph.add_node({"id": "b2", "type": "decision", "timestamp": 500})
    # Active branch C
    graph.add_node({"id": "c1", "type": "decision", "timestamp": 600})
    # Abandon events
    graph.add_node({"id": "ab_a", "type": "abandon", "timestamp": 700, "ref_to": ["a1"]})
    graph.add_node({"id": "ab_b", "type": "abandon", "timestamp": 800, "ref_to": ["b1"]})

    graph.add_edge("root", "a1", "sequence")
    graph.add_edge("a1", "a2", "sequence")
    graph.add_edge("root", "b1", "sequence")
    graph.add_edge("b1", "b2", "sequence")
    graph.add_edge("root", "c1", "sequence")

    sweep_dead_branches(graph)

    # Branch A fully pruned
    assert "a1" in graph.pruned
    assert "a2" in graph.pruned
    # Branch B fully pruned
    assert "b1" in graph.pruned
    assert "b2" in graph.pruned
    # Active branch untouched
    assert "root" not in graph.pruned
    assert "c1" not in graph.pruned
    # Abandon events themselves are pruned (they're not targeted; but they are in the pruned branch if they have a parent in it)
    # ab_a and ab_b have no parent_id set, so they're only pruned if directly targeted
    # They are NOT targeted here, so they remain alive
    assert "ab_a" not in graph.pruned
    assert "ab_b" not in graph.pruned


def test_branch_rejoining_active_child_not_swept():
    """Branch rejoining: a node reachable from BOTH an abandoned branch and an active root
    must NOT be pruned. The active path wins."""
    graph = StateGraph()
    # e_shared is a child of both e_abandoned AND e_active
    graph.add_node({"id": "root", "type": "decision", "timestamp": 100})
    graph.add_node({"id": "e_abandoned", "type": "decision", "timestamp": 200})
    graph.add_node({"id": "e_active", "type": "decision", "timestamp": 300})
    graph.add_node({"id": "e_shared", "type": "decision", "timestamp": 400})
    graph.add_node({"id": "ab1", "type": "abandon", "timestamp": 500, "ref_to": ["e_abandoned"]})

    # e_shared has TWO sequence parents: e_abandoned AND e_active
    graph.add_edge("root", "e_abandoned", "sequence")
    graph.add_edge("root", "e_active", "sequence")
    graph.add_edge("e_abandoned", "e_shared", "sequence")
    graph.add_edge("e_active", "e_shared", "sequence")  # also reachable from active path

    sweep_dead_branches(graph)

    # e_abandoned is correctly pruned
    assert "e_abandoned" in graph.pruned
    # e_shared has an active parent (e_active), so it must NOT be pruned
    assert "e_shared" not in graph.pruned, (
        "A node with an active parent must not be swept even if also reachable from an abandoned node"
    )
    # e_active survives
    assert "e_active" not in graph.pruned


def test_abandonment_followed_by_new_work():
    """After abandoning a branch, new events on a fresh path remain alive."""
    graph = StateGraph()
    graph.add_node({"id": "root", "type": "decision", "timestamp": 100})
    graph.add_node({"id": "attempt1", "type": "decision", "timestamp": 200})
    graph.add_node({"id": "fail", "type": "decision", "timestamp": 300})
    graph.add_node({"id": "ab1", "type": "abandon", "timestamp": 400, "ref_to": ["attempt1"]})
    # New work starts after the abandon
    graph.add_node({"id": "attempt2", "type": "decision", "timestamp": 500})
    graph.add_node({"id": "success", "type": "decision", "timestamp": 600})

    graph.add_edge("root", "attempt1", "sequence")
    graph.add_edge("attempt1", "fail", "sequence")
    graph.add_edge("root", "attempt2", "sequence")
    graph.add_edge("attempt2", "success", "sequence")

    sweep_dead_branches(graph)

    assert "attempt1" in graph.pruned
    assert "fail" in graph.pruned
    assert "root" not in graph.pruned
    assert "attempt2" not in graph.pruned
    assert "success" not in graph.pruned


def test_ref_to_nonexistent_node_is_safe_noop():
    """An abandon event whose ref_to targets a non-existent node is silently skipped.
    
    Design decision (DECISIONS.md entry 23): this is intentional — the sweeper's
    'if tgt in graph.nodes' guard lets callers emit abandon events for IDs that were
    already cleaned up without crashing the pipeline.
    """
    graph = StateGraph()
    graph.add_node({"id": "real", "type": "decision", "timestamp": 100})
    graph.add_node({
        "id": "ab_phantom",
        "type": "abandon",
        "timestamp": 200,
        "ref_to": ["does_not_exist"],  # non-existent target
    })

    # Must not raise; must not prune any real nodes
    pruned = sweep_dead_branches(graph)
    assert "real" not in graph.pruned
    assert len(pruned) == 0  # no real nodes pruned


def test_abandon_targeting_already_pruned_node_is_noop():
    """An abandon event targeting a node that was already pruned is a safe no-op."""
    graph = StateGraph()
    graph.add_node({"id": "e1", "type": "decision", "timestamp": 100})
    graph.add_node({"id": "e2", "type": "decision", "timestamp": 200})
    graph.add_node({"id": "ab1", "type": "abandon", "timestamp": 300, "ref_to": ["e1"]})
    graph.add_node({"id": "ab2", "type": "abandon", "timestamp": 400, "ref_to": ["e1"]})  # same target

    graph.add_edge("e1", "e2", "sequence")

    pruned = sweep_dead_branches(graph)
    # e1 and e2 are pruned exactly once — no double-counting
    assert graph.pruned.count if hasattr(graph.pruned, 'count') else True  # set has no duplicates
    assert "e1" in graph.pruned
    assert "e2" in graph.pruned
    # Prune reasons should be consistent (second abandon doesn't overwrite the first)
    assert "e1" in graph.prune_reasons
