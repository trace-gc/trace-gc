# trace_gc/benchmark/generate_fixtures.py
"""Programmatic generator for the 9 benchmark traces.

Produces short, medium, and long traces across three domains:
1. Coding Agent
2. Research Agent
3. Customer-Support Agent

Each trace file contains the 'events' list and 'probes' metadata.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Any


def ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def generate_coding_short() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Task: configure cache"},
        {"id": "e01_sub", "type": "decision", "timestamp": 1005, "parent_id": "e01", "content": "Checking active system variables..."},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01_sub", "key": "cache_type", "value": "redis"},
        {"id": "e02_ref", "type": "set_var", "timestamp": 1015, "parent_id": "e02", "key": "port", "value": 6379},
        {"id": "e03", "type": "set_var", "timestamp": 1020, "parent_id": "e02_ref", "key": "cache_size", "value": "100MB"},
        # Abandoned attempt
        {"id": "a01", "type": "decision", "timestamp": 1030, "parent_id": "e03", "content": "Attempt memcached"},
        {"id": "a02", "type": "set_var", "timestamp": 1040, "parent_id": "a01", "key": "cache_type", "value": "memcached"},
        {"id": "a03", "type": "tool_call", "timestamp": 1050, "parent_id": "a02", "tool_name": "test_cache", "arguments": {}},
        {"id": "a04", "type": "tool_result", "timestamp": 1060, "parent_id": "a03", "call_id": "a03", "result": "error: connection refused"},
        {"id": "a04_retry", "type": "tool_call", "timestamp": 1065, "parent_id": "a04", "tool_name": "test_cache", "arguments": {"retry": 1}},
        {"id": "a04_res", "type": "tool_result", "timestamp": 1068, "parent_id": "a04_retry", "call_id": "a04_retry", "result": "error: connection refused retry 1"},
        {"id": "ab01", "type": "abandon", "timestamp": 1070, "parent_id": "a04_res", "ref_to": ["a01"]},
        # Active continuation
        {"id": "e04", "type": "decision", "timestamp": 1080, "parent_id": "e03", "content": "Pivoting to in-memory CacheDB due to memcached connection error"},
        {"id": "e05", "type": "set_var", "timestamp": 1090, "parent_id": "e04", "key": "cache_type", "value": "cachedb"},
        {"id": "e06", "type": "tool_call", "timestamp": 1100, "parent_id": "e05", "tool_name": "test_cache", "arguments": {"db": "cachedb"}},
        {"id": "e07", "type": "tool_result", "timestamp": 1110, "parent_id": "e06", "call_id": "e06", "result": "cache_ok_conn_99"},
        {"id": "e08", "type": "decision", "timestamp": 1120, "parent_id": "e07", "content": "Setup complete"}
    ]
    probes = {
        "recall": {
            "contains": ["100MB"],
            "excludes": []
        },
        "artifact": {
            "contains": ["cache_ok_conn_99"],
            "excludes": ["a03", "a04"],
            "recovered": {"node_id": "e07", "key": "result", "value": "cache_ok_conn_99"}
        },
        "continuation": {
            "contains": ["cachedb", "complete"],
            "excludes": ["cache_type = memcached"]
        },
        "decision": {
            "contains": ["Pivoting", "CacheDB", "memcached"],
            "excludes": ["test_cache", "connection refused"]
        }
    }
    return {"events": events, "probes": probes}


def generate_coding_medium() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Task: write schema"},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01", "key": "db_engine", "value": "mysql"},
        {"id": "e03", "type": "set_var", "timestamp": 1020, "parent_id": "e02", "key": "db_port", "value": 3306},
        # Abandoned attempt 1
        {"id": "a1", "type": "decision", "timestamp": 1030, "parent_id": "e03", "content": "Setup postgres"},
        {"id": "a2", "type": "set_var", "timestamp": 1040, "parent_id": "a1", "key": "db_engine", "value": "postgres"},
        {"id": "a3", "type": "tool_call", "timestamp": 1050, "parent_id": "a2", "tool_name": "run_pg_ctl", "arguments": {"port": 5432}},
        {"id": "a4", "type": "tool_result", "timestamp": 1060, "parent_id": "a3", "call_id": "a3", "result": "pg_err_auth"},
        {"id": "ab1", "type": "abandon", "timestamp": 1070, "parent_id": "a4", "ref_to": ["a1"]},
        # Active continuation
        {"id": "e04", "type": "decision", "timestamp": 1080, "parent_id": "e03", "content": "Postgres failed. Pivoting to sqlite for zero setup overhead"},
        {"id": "e05", "type": "set_var", "timestamp": 1090, "parent_id": "e04", "key": "db_engine", "value": "sqlite"},
        # Loop of capacity tuning (cycle)
        {"id": "c1", "type": "decision", "timestamp": 1100, "parent_id": "e05", "content": "Tuning connections: test 50"},
        {"id": "c2", "type": "tool_call", "timestamp": 1110, "parent_id": "c1", "tool_name": "stress_test", "arguments": {"conns": 50}},
        {"id": "c3", "type": "tool_result", "timestamp": 1120, "parent_id": "c2", "call_id": "c2", "result": "high latency, retry 10"},
        # Cycle back-edge c3 -> c1 will be added in benchmark runner manually
        
        # Override connection pool size
        {"id": "e06", "type": "set_var", "timestamp": 1130, "parent_id": "c3", "key": "pool_size", "value": 10},
    ]
    parent = "e06"
    for i in range(1, 13):
        tc_id = f"tc_mig_{i}"
        tr_id = f"tr_mig_{i}"
        events.append({"id": tc_id, "type": "tool_call", "timestamp": 1140 + i*10, "parent_id": parent, "tool_name": f"apply_migration_{i}", "arguments": {}})
        events.append({"id": tr_id, "type": "tool_result", "timestamp": 1145 + i*10, "parent_id": tc_id, "call_id": tc_id, "result": "migration_success"})
        parent = tr_id

    events.append({"id": "e07", "type": "tool_call", "timestamp": 1300, "parent_id": parent, "tool_name": "db_check", "arguments": {"pool": 10}})
    events.append({"id": "e08", "type": "tool_result", "timestamp": 1310, "parent_id": "e07", "call_id": "e07", "result": "db_session_id_999"})
    
    parent = "e08"
    for d in range(1, 6):
        events.append({"id": f"e09_{d}", "type": "tool_call", "timestamp": 1320 + d*10, "parent_id": parent, "tool_name": "db_check", "arguments": {"pool": 10}})
        events.append({"id": f"e10_{d}", "type": "tool_result", "timestamp": 1325 + d*10, "parent_id": f"e09_{d}", "call_id": f"e09_{d}", "result": "db_session_id_999"})
        parent = f"e10_{d}"

    events.append({"id": "e11", "type": "decision", "timestamp": 1400, "parent_id": parent, "content": "Database Schema initialized."})
    events.append({"id": "cycle_link", "type": "edge_injection", "src": "c3", "dst": "c1"})
    
    probes = {
        "recall": {
            "contains": ["3306"],
            "excludes": []
        },
        "artifact": {
            "contains": ["db_session_id_999"],
            "excludes": ["pg_err_auth"],
            "recovered": {"node_id": "e08", "key": "result", "value": "db_session_id_999"}
        },
        "continuation": {
            "contains": ["sqlite", "10"],
            "excludes": ["db_engine = postgres"]
        },
        "decision": {
            "contains": ["sqlite", "Postgres", "Pivoting"],
            "excludes": ["pg_err_auth", "run_pg_ctl"]
        }
    }
    return {"events": events, "probes": probes}


def generate_coding_long() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Task: write full API service"},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01", "key": "port", "value": 8080},
        {"id": "e03", "type": "set_var", "timestamp": 1020, "parent_id": "e02", "key": "env", "value": "development"}
    ]
    
    parent = "e03"
    for i in range(1, 9):
        a_root = f"ab_branch_{i}"
        events.append({"id": a_root, "type": "decision", "timestamp": 1030 + i*10, "parent_id": parent, "content": f"Attempting dep config option {i}"})
        events.append({"id": f"tc_a_{i}", "type": "tool_call", "timestamp": 1031 + i*10, "parent_id": a_root, "tool_name": f"dep_check_{i}", "arguments": {}})
        events.append({"id": f"tr_a_{i}", "type": "tool_result", "timestamp": 1032 + i*10, "parent_id": f"tc_a_{i}", "call_id": f"tc_a_{i}", "result": f"failed dependency {i} setup"})
        events.append({"id": f"ab_end_{i}", "type": "abandon", "timestamp": 1033 + i*10, "parent_id": f"tr_a_{i}", "ref_to": [a_root]})
    
    # Active pivot
    pivot_id = "e04"
    events.append({"id": pivot_id, "type": "decision", "timestamp": 1150, "parent_id": "e03", "content": "Pivoting to standalone docker configuration since standard dependencies failed"})
    events.append({"id": "e05", "type": "set_var", "timestamp": 1160, "parent_id": pivot_id, "key": "deployment_mode", "value": "docker"})
    
    parent = "e05"
    for j in range(1, 31):
        c_id = f"tc_code_{j}"
        r_id = f"tr_code_{j}"
        events.append({"id": c_id, "type": "tool_call", "timestamp": 1170 + j*10, "parent_id": parent, "tool_name": f"write_file_{j}", "arguments": {"file": f"file_{j}.py"}})
        events.append({"id": r_id, "type": "tool_result", "timestamp": 1175 + j*10, "parent_id": c_id, "call_id": c_id, "result": f"written file_{j}_checksum_abc{j}"})
        
        var_id = f"v_set_{j}"
        events.append({"id": var_id, "type": "set_var", "timestamp": 1178 + j*10, "parent_id": r_id, "key": "build_version", "value": f"v1.{j}"})
        parent = var_id

    for d in range(1, 11):
        events.append({"id": f"tc_dup_{d}", "type": "tool_call", "timestamp": 1500 + d*10, "parent_id": parent, "tool_name": "checksum_check", "arguments": {"target": "file_30.py"}})
        events.append({"id": f"tr_dup_{d}", "type": "tool_result", "timestamp": 1505 + d*10, "parent_id": f"tc_dup_{d}", "call_id": f"tc_dup_{d}", "result": "checksum matched"})
        parent = f"tr_dup_{d}"

    events.append({"id": "e100", "type": "decision", "timestamp": 1700, "parent_id": parent, "content": "Docker container successfully compiled, artifact written."})
    
    probes = {
        "recall": {
            "contains": ["8080"],
            "excludes": []
        },
        "artifact": {
            "contains": ["checksum matched"],
            "excludes": ["failed dependency 3 setup"],
            "recovered": {"node_id": "tr_code_1", "key": "result", "value": "written file_1_checksum_abc1"}
        },
        "continuation": {
            "contains": ["docker", "v1.30"],
            "excludes": ["build_version = v1.5"]
        },
        "decision": {
            "contains": ["docker", "dependencies failed", "Pivoting"],
            "excludes": ["dep_check_1", "dep_check_5"]
        }
    }
    return {"events": events, "probes": probes}


# Research Agent Traces
def generate_research_short() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Research: LLM alignment methods"},
        {"id": "e01_sub", "type": "decision", "timestamp": 1005, "parent_id": "e01", "content": "Initiating search logs..."},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01_sub", "key": "target_topic", "value": "DPO"},
        {"id": "e02_sub", "type": "set_var", "timestamp": 1015, "parent_id": "e02", "key": "max_records", "value": 50},
        # Abandoned search query
        {"id": "a01", "type": "decision", "timestamp": 1020, "parent_id": "e02_sub", "content": "Search query: RLHF PPO Pytorch code"},
        {"id": "a02", "type": "tool_call", "timestamp": 1030, "parent_id": "a01", "tool_name": "arxiv_search", "arguments": {"q": "RLHF PPO Pytorch"}},
        {"id": "a03", "type": "tool_result", "timestamp": 1040, "parent_id": "a02", "call_id": "a02", "result": "no results matching code query"},
        {"id": "a04_retry", "type": "tool_call", "timestamp": 1045, "parent_id": "a03", "tool_name": "arxiv_search", "arguments": {"q": "PPO Pytorch retry"}},
        {"id": "a04_res", "type": "tool_result", "timestamp": 1048, "parent_id": "a04_retry", "call_id": "a04_retry", "result": "no results"},
        {"id": "ab01", "type": "abandon", "timestamp": 1050, "parent_id": "a04_res", "ref_to": ["a01"]},
        # Active pivot
        {"id": "e03", "type": "decision", "timestamp": 1060, "parent_id": "e02_sub", "content": "PPO query empty. Pivoting to DPO literature in bioRxiv/arXiv"},
        {"id": "e04", "type": "tool_call", "timestamp": 1070, "parent_id": "e03", "tool_name": "arxiv_search", "arguments": {"q": "Direct Preference Optimization"}},
        {"id": "e05", "type": "tool_result", "timestamp": 1080, "parent_id": "e04", "call_id": "e04", "result": "found paper_id: arxiv_2305.18290"},
        {"id": "e06", "type": "decision", "timestamp": 1090, "parent_id": "e05", "content": "Relevant paper found. Analysis completed."}
    ]
    probes = {
        "recall": {
            "contains": ["DPO"],
            "excludes": []
        },
        "artifact": {
            "contains": ["arxiv_2305.18290"],
            "excludes": ["RLHF PPO Pytorch"],
            "recovered": {"node_id": "e05", "key": "result", "value": "found paper_id: arxiv_2305.18290"}
        },
        "continuation": {
            "contains": ["arxiv_2305.18290", "completed"],
            "excludes": ["RLHF PPO Pytorch code"]
        },
        "decision": {
            "contains": ["PPO", "DPO", "Pivoting"],
            "excludes": ["no results matching code query"]
        }
    }
    return {"events": events, "probes": probes}


def generate_research_medium() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Research: cancer immunotherapy clinical trials"},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01", "key": "agent", "value": "Pembrolizumab"},
        {"id": "e03", "type": "set_var", "timestamp": 1020, "parent_id": "e02", "key": "phase", "value": "Phase II"},
        # Abandoned search branch
        {"id": "a1", "type": "decision", "timestamp": 1030, "parent_id": "e03", "content": "Search lung cancer trials in bioRxiv"},
        {"id": "a2", "type": "tool_call", "timestamp": 1040, "parent_id": "a1", "tool_name": "biorxiv_search", "arguments": {"q": "Pembrolizumab lung"}},
        {"id": "a3", "type": "tool_result", "timestamp": 1050, "parent_id": "a2", "call_id": "a2", "result": "no preprints found"},
        {"id": "ab1", "type": "abandon", "timestamp": 1060, "parent_id": "a3", "ref_to": ["a1"]},
        # Active pivot
        {"id": "e04", "type": "decision", "timestamp": 1070, "parent_id": "e03", "content": "No preprints in bioRxiv. Pivoting to clinicaltrials.gov database"},
        {"id": "e05", "type": "set_var", "timestamp": 1080, "parent_id": "e04", "key": "phase", "value": "Phase III"},
        # Cycle loop of searching citations
        {"id": "c1", "type": "decision", "timestamp": 1100, "parent_id": "e05", "content": "Searching trial records: look for NCT001"},
        {"id": "c2", "type": "tool_call", "timestamp": 1110, "parent_id": "c1", "tool_name": "clinical_trials_search", "arguments": {"id": "NCT001"}},
        {"id": "c3", "type": "tool_result", "timestamp": 1120, "parent_id": "c2", "call_id": "c2", "result": "NCT001_details, cross reference NCT002"},
        
        # Override connection pool size
        {"id": "e06", "type": "set_var", "timestamp": 1130, "parent_id": "c3", "key": "trial_id", "value": "NCT00244673"},
    ]
    
    parent = "e06"
    for i in range(1, 13):
        tc_id = f"tc_paper_{i}"
        tr_id = f"tr_paper_{i}"
        events.append({"id": tc_id, "type": "tool_call", "timestamp": 1140 + i*10, "parent_id": parent, "tool_name": f"fetch_trial_abstract_{i}", "arguments": {}})
        events.append({"id": tr_id, "type": "tool_result", "timestamp": 1145 + i*10, "parent_id": tc_id, "call_id": tc_id, "result": "abstract_parsed"})
        parent = tr_id

    events.append({"id": "e07", "type": "tool_call", "timestamp": 1300, "parent_id": parent, "tool_name": "clinical_trials_search", "arguments": {"id": "NCT00244673"}})
    events.append({"id": "e08", "type": "tool_result", "timestamp": 1310, "parent_id": "e07", "call_id": "e07", "result": "NCT_verified_pembro_melanoma"})
    
    parent = "e08"
    for d in range(1, 6):
        events.append({"id": f"e09_{d}", "type": "tool_call", "timestamp": 1320 + d*10, "parent_id": parent, "tool_name": "clinical_trials_search", "arguments": {"id": "NCT00244673"}})
        events.append({"id": f"e10_{d}", "type": "tool_result", "timestamp": 1325 + d*10, "parent_id": f"e09_{d}", "call_id": f"e09_{d}", "result": "NCT_verified_pembro_melanoma"})
        parent = f"e10_{d}"

    events.append({"id": "e11", "type": "decision", "timestamp": 1400, "parent_id": parent, "content": "Literature scan verified"})
    events.append({"id": "cycle_link", "type": "edge_injection", "src": "c3", "dst": "c1"})
    
    probes = {
        "recall": {
            "contains": ["Pembrolizumab"],
            "excludes": []
        },
        "artifact": {
            "contains": ["NCT_verified_pembro_melanoma"],
            "excludes": ["no preprints found"],
            "recovered": {"node_id": "e08", "key": "result", "value": "NCT_verified_pembro_melanoma"}
        },
        "continuation": {
            "contains": ["Phase III", "NCT00244673"],
            "excludes": ["phase = Phase II"]
        },
        "decision": {
            "contains": ["bioRxiv", "clinicaltrials.gov", "Pivoting"],
            "excludes": ["Pembrolizumab lung", "biorxiv_search"]
        }
    }
    return {"events": events, "probes": probes}


def generate_research_long() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Research: CRISPR Cas9 off-target mutations"},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01", "key": "search_db", "value": "pubmed"},
        {"id": "e03", "type": "set_var", "timestamp": 1020, "parent_id": "e02", "key": "target_organism", "value": "human"}
    ]
    
    parent = "e03"
    for i in range(1, 9):
        a_root = f"ab_branch_{i}"
        events.append({"id": a_root, "type": "decision", "timestamp": 1030 + i*10, "parent_id": parent, "content": f"Query search option {i}"})
        events.append({"id": f"tc_a_{i}", "type": "tool_call", "timestamp": 1031 + i*10, "parent_id": a_root, "tool_name": f"pubmed_search_opt_{i}", "arguments": {}})
        events.append({"id": f"tr_a_{i}", "type": "tool_result", "timestamp": 1032 + i*10, "parent_id": f"tc_a_{i}", "call_id": f"tc_a_{i}", "result": f"yielded zero relevance papers under options {i}"})
        events.append({"id": f"ab_end_{i}", "type": "abandon", "timestamp": 1033 + i*10, "parent_id": f"tr_a_{i}", "ref_to": [a_root]})

    # Active pivot
    pivot_id = "e04"
    events.append({"id": pivot_id, "type": "decision", "timestamp": 1150, "parent_id": "e03", "content": "Pubmed yields dry. Pivoting to EuropePMC database for full-text search"})
    events.append({"id": "e05", "type": "set_var", "timestamp": 1160, "parent_id": pivot_id, "key": "search_db", "value": "europepmc"})
    
    parent = "e05"
    for j in range(1, 31):
        c_id = f"tc_crawl_{j}"
        r_id = f"tr_crawl_{j}"
        events.append({"id": c_id, "type": "tool_call", "timestamp": 1170 + j*10, "parent_id": parent, "tool_name": "fetch_pmcid", "arguments": {"pmcid": f"PMC242{j}"}})
        events.append({"id": r_id, "type": "tool_result", "timestamp": 1175 + j*10, "parent_id": c_id, "call_id": c_id, "result": f"extracted PMID {j} off-target guide: GACT{j}"})
        
        var_id = f"v_set_{j}"
        events.append({"id": var_id, "type": "set_var", "timestamp": 1178 + j*10, "parent_id": r_id, "key": "active_paper", "value": f"PMC242{j}"})
        parent = var_id

    for d in range(1, 11):
        events.append({"id": f"tc_dup_{d}", "type": "tool_call", "timestamp": 1500 + d*10, "parent_id": parent, "tool_name": "ledger_status_check", "arguments": {}})
        events.append({"id": f"tr_dup_{d}", "type": "tool_result", "timestamp": 1505 + d*10, "parent_id": f"tc_dup_{d}", "call_id": f"tc_dup_{d}", "result": "online"})
        parent = f"tr_dup_{d}"

    events.append({"id": "e100", "type": "decision", "timestamp": 1600, "parent_id": parent, "content": "CRISPR off-target audit trail compiled successfully."})
    
    probes = {
        "recall": {
            "contains": ["human"],
            "excludes": []
        },
        "artifact": {
            "contains": ["GACT30"],
            "excludes": ["yielded zero relevance papers under options 1"],
            "recovered": {"node_id": "tr_crawl_1", "key": "result", "value": "extracted PMID 1 off-target guide: GACT1"}
        },
        "continuation": {
            "contains": ["europepmc", "PMC24230"],
            "excludes": ["search_db = pubmed", "active_paper = PMC2425"]
        },
        "decision": {
            "contains": ["Pubmed", "EuropePMC", "Pivoting"],
            "excludes": ["pubmed_search_opt_1", "pubmed_search_opt_5"]
        }
    }
    return {"events": events, "probes": probes}


# Customer Support Agent Traces
def generate_support_short() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Dispute: customer charging discrepancy"},
        {"id": "e01_sub", "type": "decision", "timestamp": 1005, "parent_id": "e01", "content": "Locating account flags..."},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01_sub", "key": "user_id", "value": "usr_992"},
        {"id": "e02_sub", "type": "set_var", "timestamp": 1015, "parent_id": "e02", "key": "tier", "value": "gold"},
        # Abandoned refund attempt
        {"id": "a01", "type": "decision", "timestamp": 1020, "parent_id": "e02_sub", "content": "Refund via automated gateway"},
        {"id": "a02", "type": "tool_call", "timestamp": 1030, "parent_id": "a01", "tool_name": "auto_refund", "arguments": {"usr": "usr_992", "amount": 500}},
        {"id": "a03", "type": "tool_result", "timestamp": 1040, "parent_id": "a02", "call_id": "a02", "result": "error: manual approval required for amount > 100"},
        {"id": "a04_retry", "type": "tool_call", "timestamp": 1045, "parent_id": "a03", "tool_name": "auto_refund", "arguments": {"usr": "usr_992", "amount": 499}},
        {"id": "a04_res", "type": "tool_result", "timestamp": 1048, "parent_id": "a04_retry", "call_id": "a04_retry", "result": "error: manual approval required retry 1"},
        {"id": "ab01", "type": "abandon", "timestamp": 1050, "parent_id": "a04_res", "ref_to": ["a01"]},
        # Active pivot
        {"id": "e03", "type": "decision", "timestamp": 1060, "parent_id": "e02_sub", "content": "Auto refund failed (>100). Pivoting to manager escalation workflow"},
        {"id": "e04", "type": "set_var", "timestamp": 1070, "parent_id": "e03", "key": "approval_type", "value": "manager_manual"},
        {"id": "e05", "type": "tool_call", "timestamp": 1080, "parent_id": "e04", "tool_name": "request_manual_approval", "arguments": {"id": "usr_992"}},
        {"id": "e06", "type": "tool_result", "timestamp": 1090, "parent_id": "e05", "call_id": "e05", "result": "approved: tx_hash_ref_88"},
        {"id": "e07", "type": "decision", "timestamp": 1100, "parent_id": "e06", "content": "Refund processed and customer notified."}
    ]
    probes = {
        "recall": {
            "contains": ["usr_992"],
            "excludes": []
        },
        "artifact": {
            "contains": ["tx_hash_ref_88"],
            "excludes": ["auto_refund", "manual approval required"],
            "recovered": {"node_id": "e06", "key": "result", "value": "approved: tx_hash_ref_88"}
        },
        "continuation": {
            "contains": ["manager_manual", "tx_hash_ref_88"],
            "excludes": ["error: manual approval required"]
        },
        "decision": {
            "contains": ["Auto refund failed", "manager escalation", "Pivoting"],
            "excludes": ["amount: 500"]
        }
    }
    return {"events": events, "probes": probes}


def generate_support_medium() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Dispute: credit card chargeback dispute"},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01", "key": "dispute_id", "value": "dis_771"},
        {"id": "e03", "type": "set_var", "timestamp": 1020, "parent_id": "e02", "key": "escalation_level", "value": "tier1"},
        # Abandoned branch
        {"id": "a1", "type": "decision", "timestamp": 1030, "parent_id": "e03", "content": "Verify via stripe charge retrieval"},
        {"id": "a2", "type": "tool_call", "timestamp": 1040, "parent_id": "a1", "tool_name": "stripe_retrieve", "arguments": {"charge_id": "ch_stripe"}},
        {"id": "a3", "type": "tool_result", "timestamp": 1050, "parent_id": "a2", "call_id": "a2", "result": "stripe_charge_not_found"},
        {"id": "ab1", "type": "abandon", "timestamp": 1060, "parent_id": "a3", "ref_to": ["a1"]},
        # Active pivot
        {"id": "e04", "type": "decision", "timestamp": 1070, "parent_id": "e03", "content": "Stripe retrieval failed. Pivoting to PayPal transaction dispute lookup"},
        {"id": "e05", "type": "set_var", "timestamp": 1080, "parent_id": "e04", "key": "escalation_level", "value": "tier2"},
        # Cycle loop of customer correspondence
        {"id": "c1", "type": "decision", "timestamp": 1100, "parent_id": "e05", "content": "Request documentation: verify ID"},
        {"id": "c2", "type": "tool_call", "timestamp": 1110, "parent_id": "c1", "tool_name": "send_email", "arguments": {"template": "request_docs"}},
        {"id": "c3", "type": "tool_result", "timestamp": 1120, "parent_id": "c2", "call_id": "c2", "result": "email sent, customer requested details"},
        
        # Override connection pool size
        {"id": "e06", "type": "set_var", "timestamp": 1130, "parent_id": "c3", "key": "paypal_tx", "value": "tx_paypal_881a"},
    ]
    
    parent = "e06"
    for i in range(1, 13):
        tc_id = f"tc_audit_{i}"
        tr_id = f"tr_audit_{i}"
        events.append({"id": tc_id, "type": "tool_call", "timestamp": 1140 + i*10, "parent_id": parent, "tool_name": f"audit_ledger_status_{i}", "arguments": {}})
        events.append({"id": tr_id, "type": "tool_result", "timestamp": 1145 + i*10, "parent_id": tc_id, "call_id": tc_id, "result": "ledger_clean"})
        parent = tr_id

    events.append({"id": "e07", "type": "tool_call", "timestamp": 1300, "parent_id": parent, "tool_name": "paypal_lookup", "arguments": {"tx": "tx_paypal_881a"}})
    events.append({"id": "e08", "type": "tool_result", "timestamp": 1310, "parent_id": "e07", "call_id": "e07", "result": "found_dispute_settlement_approved"})
    
    parent = "e08"
    for d in range(1, 6):
        events.append({"id": f"e09_{d}", "type": "tool_call", "timestamp": 1320 + d*10, "parent_id": parent, "tool_name": "paypal_lookup", "arguments": {"tx": "tx_paypal_881a"}})
        events.append({"id": f"e10_{d}", "type": "tool_result", "timestamp": 1325 + d*10, "parent_id": f"e09_{d}", "call_id": f"e09_{d}", "result": "found_dispute_settlement_approved"})
        parent = f"e10_{d}"

    events.append({"id": "e11", "type": "decision", "timestamp": 1400, "parent_id": parent, "content": "PayPal chargeback resolved successfully"})
    events.append({"id": "cycle_link", "type": "edge_injection", "src": "c3", "dst": "c1"})
    
    probes = {
        "recall": {
            "contains": ["dis_771"],
            "excludes": []
        },
        "artifact": {
            "contains": ["found_dispute_settlement_approved"],
            "excludes": ["stripe_charge_not_found"],
            "recovered": {"node_id": "e08", "key": "result", "value": "found_dispute_settlement_approved"}
        },
        "continuation": {
            "contains": ["tier2", "tx_paypal_881a"],
            "excludes": ["escalation_level = tier1"]
        },
        "decision": {
            "contains": ["Stripe", "PayPal", "Pivoting"],
            "excludes": ["charge_id: ch_stripe", "stripe_retrieve"]
        }
    }
    return {"events": events, "probes": probes}


def generate_support_long() -> Dict[str, Any]:
    events = [
        {"id": "e01", "type": "decision", "timestamp": 1000, "parent_id": None, "content": "Dispute: massive commercial billing audit"},
        {"id": "e02", "type": "set_var", "timestamp": 1010, "parent_id": "e01", "key": "account_id", "value": "acc_com_889"},
        {"id": "e03", "type": "set_var", "timestamp": 1020, "parent_id": "e02", "key": "audit_scope", "value": "Q1_billing"}
    ]
    
    parent = "e03"
    for i in range(1, 9):
        a_root = f"ab_branch_{i}"
        events.append({"id": a_root, "type": "decision", "timestamp": 1030 + i*10, "parent_id": parent, "content": f"Investigating billing sub-system {i}"})
        events.append({"id": f"tc_a_{i}", "type": "tool_call", "timestamp": 1031 + i*10, "parent_id": a_root, "tool_name": f"crm_query_{i}", "arguments": {}})
        events.append({"id": f"tr_a_{i}", "type": "tool_result", "timestamp": 1032 + i*10, "parent_id": f"tc_a_{i}", "call_id": f"tc_a_{i}", "result": f"failed to fetch CRM transaction status {i} due to token timeout"})
        events.append({"id": f"ab_end_{i}", "type": "abandon", "timestamp": 1033 + i*10, "parent_id": f"tr_a_{i}", "ref_to": [a_root]})

    # Active pivot
    pivot_id = "e04"
    events.append({"id": pivot_id, "type": "decision", "timestamp": 1150, "parent_id": "e03", "content": "CRM lookup failed. Pivoting to bulk ledger export verification"})
    events.append({"id": "e05", "type": "set_var", "timestamp": 1160, "parent_id": pivot_id, "key": "audit_scope", "value": "Full_Ledger_Q1"})
    
    parent = "e05"
    for j in range(1, 31):
        c_id = f"tc_ledger_{j}"
        r_id = f"tr_ledger_{j}"
        events.append({"id": c_id, "type": "tool_call", "timestamp": 1170 + j*10, "parent_id": parent, "tool_name": "fetch_invoice_ledger", "arguments": {"id": f"inv_led_{j}"}})
        events.append({"id": r_id, "type": "tool_result", "timestamp": 1175 + j*10, "parent_id": c_id, "call_id": c_id, "result": f"ledger verification successful for invoice_{j}_auth_id_{j*5}"})
        
        var_id = f"v_set_{j}"
        events.append({"id": var_id, "type": "set_var", "timestamp": 1178 + j*10, "parent_id": r_id, "key": "last_processed_invoice", "value": f"inv_led_{j}"})
        parent = var_id

    for d in range(1, 11):
        events.append({"id": f"tc_dup_{d}", "type": "tool_call", "timestamp": 1500 + d*10, "parent_id": parent, "tool_name": "ledger_status_check", "arguments": {}})
        events.append({"id": f"tr_dup_{d}", "type": "tool_result", "timestamp": 1505 + d*10, "parent_id": f"tc_dup_{d}", "call_id": f"tc_dup_{d}", "result": "synced"})
        parent = f"tr_dup_{d}"

    events.append({"id": "e100", "type": "decision", "timestamp": 1700, "parent_id": parent, "content": "Ledger audit trail verified, dispute closed successfully."})
    
    probes = {
        "recall": {
            "contains": ["acc_com_889"],
            "excludes": []
        },
        "artifact": {
            "contains": ["invoice_30_auth_id_150"],
            "excludes": ["failed to fetch CRM transaction status 1"],
            "recovered": {"node_id": "tr_ledger_1", "key": "result", "value": "ledger verification successful for invoice_1_auth_id_5"}
        },
        "continuation": {
            "contains": ["Full_Ledger_Q1", "inv_led_30"],
            "excludes": ["audit_scope = Q1_billing", "last_processed_invoice = inv_led_5"]
        },
        "decision": {
            "contains": ["CRM", "bulk ledger", "Pivoting"],
            "excludes": ["crm_query_1", "crm_query_5"]
        }
    }
    return {"events": events, "probes": probes}


def main():
    fixtures_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "fixtures"))
    
    generators = {
        "coding_agent_short.json": generate_coding_short,
        "coding_agent_medium.json": generate_coding_medium,
        "coding_agent_long.json": generate_coding_long,
        "research_agent_short.json": generate_research_short,
        "research_agent_medium.json": generate_research_medium,
        "research_agent_long.json": generate_research_long,
        "customer_support_short.json": generate_support_short,
        "customer_support_medium.json": generate_support_medium,
        "customer_support_long.json": generate_support_long,
    }
    
    for filename, gen_fn in generators.items():
        data = gen_fn()
        filepath = os.path.join(fixtures_dir, filename)
        ensure_dir(filepath)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Generated {filename}: {len(data['events'])} events")


if __name__ == "__main__":
    main()
