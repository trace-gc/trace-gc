# trace_gc/benchmark/scoring.py
"""Scoring utilities for probe-based evaluation of context compaction."""

from __future__ import annotations

from typing import Dict, List, Any
from trace_gc.receipts import get_receipt


def check_semantic_equivalence(prompt: str, contains_list: List[str], excludes_list: List[str], graph: Any = None) -> bool:
    """Check if the semantic contents match the prompt or the active state graph."""
    normalized_prompt = prompt.lower()
    
    normalization_map = {
        "postgres": "postgresql",
        "postgresql": "postgres",
        "redis": "redis",
        "sqlite": "sqlite",
        "mysql": "mysql",
        "cachedb": "cachedb",
        "memcached": "memcached"
    }
    
    for item in contains_list:
        item_lower = item.lower()
        
        # 1. Check direct literal presence
        if item_lower in normalized_prompt:
            continue
            
        # 2. Check alternate normalized form presence
        alt = normalization_map.get(item_lower)
        if alt and alt in normalized_prompt:
            continue
            
        # 3. Check active graph nodes
        if graph:
            found_in_graph = False
            for node_id, event in graph.nodes.items():
                if node_id in graph.pruned:
                    continue
                # Check set_var value
                if event.get("type") == "set_var" and str(event.get("value", "")).lower() == item_lower:
                    found_in_graph = True
                    break
                if event.get("type") == "set_var" and alt and str(event.get("value", "")).lower() == alt:
                    found_in_graph = True
                    break
                # Check content fields
                if item_lower in str(event.get("content", "")).lower():
                    found_in_graph = True
                    break
                if item_lower in str(event.get("key", "")).lower():
                    found_in_graph = True
                    break
            if found_in_graph:
                continue
                
        return False
        
    for item in excludes_list:
        item_lower = item.lower()
        if item_lower in normalized_prompt:
            return False
            
    return True


def evaluate_probes(prompt: str, probes: Dict[str, Any], graph: Any = None) -> Dict[str, bool]:
    """Score the recall, artifact, continuation, and decision probes against the prompt."""
    results = {}
    
    # 1. Recall Probe
    recall = probes.get("recall", {})
    results["recall"] = check_semantic_equivalence(prompt, recall.get("contains", []), recall.get("excludes", []), graph)
    
    # 2. Artifact Probe
    artifact = probes.get("artifact", {})
    results["artifact"] = check_semantic_equivalence(prompt, artifact.get("contains", []), artifact.get("excludes", []), graph)
    
    # Verify receipt recovery (adversarial)
    recovered_spec = artifact.get("recovered")
    if recovered_spec:
        if graph:
            try:
                node = get_receipt(graph, recovered_spec["node_id"])
                val = node.get(recovered_spec["key"])
                if val != recovered_spec["value"]:
                    results["artifact"] = False
            except Exception:
                results["artifact"] = False
        else:
            if recovered_spec["value"].lower() not in prompt.lower():
                results["artifact"] = False
                
    # 3. Continuation Probe
    continuation = probes.get("continuation", {})
    results["continuation"] = check_semantic_equivalence(prompt, continuation.get("contains", []), continuation.get("excludes", []), graph)
    
    # 4. Decision Probe
    decision = probes.get("decision", {})
    results["decision"] = check_semantic_equivalence(prompt, decision.get("contains", []), decision.get("excludes", []), graph)
    
    return results
