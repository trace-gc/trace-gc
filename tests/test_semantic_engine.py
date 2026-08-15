# tests/test_semantic_engine.py
"""Tests for semantic decision-lifecycle status transitions (PROPOSED -> ACTIVE -> CONFIRMED -> SUPERSEDED)."""

import pytest
from tracegc.graph import StateGraph
from tracegc.semantic_engine import update_decision_lifecycle_status
from tracegc.analysis import get_active_decisions


def test_decision_lifecycle_status_transitions_generic_key():
    """Verify PROPOSED -> ACTIVE -> CONFIRMED -> SUPERSEDED status field transitions on graph nodes
    using a non-'database' key ('auth_provider') with tracked_decision_keys."""
    graph = StateGraph()

    # Step 1: PROPOSED - initial set_var
    ap1 = {"id": "ap1", "type": "set_var", "timestamp": 100, "key": "auth_provider", "value": "oauth"}
    graph.add_node(ap1)

    statuses = update_decision_lifecycle_status(graph, tracked_decision_keys={"auth_provider"})
    assert statuses["ap1"] == "PROPOSED"
    assert graph.decision_status["ap1"] == "PROPOSED"

    # Step 2: ACTIVE - tool_call referencing auth_provider="oauth"
    tc1 = {"id": "tc1", "type": "tool_call", "timestamp": 150, "tool_name": "auth", "arguments": {"auth_provider": "oauth"}}
    graph.add_node(tc1)

    statuses = update_decision_lifecycle_status(graph, tracked_decision_keys={"auth_provider"})
    assert statuses["ap1"] == "ACTIVE"
    assert graph.decision_status["ap1"] == "ACTIVE"

    # Step 3: CONFIRMED - tool_result confirming oauth connection
    tr1 = {"id": "tr1", "type": "tool_result", "timestamp": 160, "call_id": "tc1", "result": "oauth connected"}
    graph.add_node(tr1)

    statuses = update_decision_lifecycle_status(graph, tracked_decision_keys={"auth_provider"})
    assert statuses["ap1"] == "CONFIRMED"
    assert graph.decision_status["ap1"] == "CONFIRMED"

    # Verify analysis.py active decisions filter returns ap1
    active = get_active_decisions(graph, tracked_decision_keys={"auth_provider"})
    assert "ap1" in active

    # Step 4: SUPERSEDED - newer set_var updating auth_provider to saml
    ap2 = {"id": "ap2", "type": "set_var", "timestamp": 200, "key": "auth_provider", "value": "saml"}
    graph.add_node(ap2)

    statuses = update_decision_lifecycle_status(graph, tracked_decision_keys={"auth_provider"})
    # ap1 is now SUPERSEDED
    assert statuses["ap1"] == "SUPERSEDED"
    assert graph.decision_status["ap1"] == "SUPERSEDED"
    # ap2 is now PROPOSED (newest)
    assert statuses["ap2"] == "PROPOSED"
    assert graph.decision_status["ap2"] == "PROPOSED"


def test_decision_lifecycle_untracked_key_default():
    """Verify that when tracked_decision_keys is default (None -> {"database"}),
    'database' gets decision-lifecycle status updates while untracked keys are omitted."""
    graph = StateGraph()
    db1 = {"id": "db1", "type": "set_var", "timestamp": 100, "key": "database", "value": "postgres"}
    st1 = {"id": "st1", "type": "set_var", "timestamp": 110, "key": "session_timeout", "value": 30}
    graph.add_node(db1)
    graph.add_node(st1)

    statuses = update_decision_lifecycle_status(graph)  # default tracked_decision_keys=None -> {"database"}
    assert "db1" in statuses
    assert statuses["db1"] == "PROPOSED"
    assert "st1" not in statuses
