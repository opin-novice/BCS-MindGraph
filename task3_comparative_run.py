"""
task3_comparative_run.py
========================
Sub-task 8.1: Comparative Benchmark Run for BCSBatighor GK.

Executes all 4 generation systems:
1. Generic LLM Baseline (zero-shot direct parametric prompting)
2. Static RAG Baseline (untemporalized static corpus TF-IDF retrieval)
3. Web-RAG Baseline (provenance-tracked pre-cutoff web retrieval)
4. Proposed Bitemporal KG System (snapshot query engine + screener gate)

Generates 36 MCQs per system (144 MCQs total) across the 11-topic benchmark demand distribution,
strictly bound by t* = 2023-04-19 cutoff rules.

Outputs:
  - experiments/baseline_comparison_results.json
"""

import os
import sys
import json
import time
import argparse
import datetime
from pathlib import Path
from typing import Dict, Any, List

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baselines.generic_llm_baseline import GenericLLMBaseline, BENCHMARK_TOPIC_DEMAND, CUTOFF_DATE
from baselines.static_rag_baseline import StaticRAGBaseline
from baselines.web_rag_baseline import WebRAGBaseline
from baselines.proposed_temporal_kg import ProposedTemporalKGSystem

DEFAULT_OUTPUT_FILE = ROOT / "experiments" / "baseline_comparison_results.json"


def run_comparative_benchmark(output_file: Path = DEFAULT_OUTPUT_FILE, api_key: str = "") -> Dict[str, Any]:
    """
    Run full comparative benchmark across all 4 system variants.
    Returns the consolidated result dictionary and writes to output_file.
    """
    print("=" * 70)
    print("           BCSBatighor GK: 36-Question Comparative Benchmark Run           ")
    print("=" * 70)
    print(f"Target Cutoff Date (t*): {CUTOFF_DATE}")
    print(f"Benchmark Topics: {len(BENCHMARK_TOPIC_DEMAND)} topics")
    print(f"MCQ Demand per System: {sum(BENCHMARK_TOPIC_DEMAND.values())} MCQs")
    print(f"Total Target MCQs across 4 Systems: {sum(BENCHMARK_TOPIC_DEMAND.values()) * 4} MCQs")
    print("=" * 70 + "\n")

    systems = [
        ("generic_llm", GenericLLMBaseline(api_key=api_key)),
        ("static_rag", StaticRAGBaseline(api_key=api_key)),
        ("web_rag", WebRAGBaseline(api_key=api_key)),
        ("proposed_temporal_kg", ProposedTemporalKGSystem(api_key=api_key)),
    ]

    results_by_system = {}
    all_mcqs = []
    total_mcqs = 0
    start_time = time.time()

    for sys_id, sys_instance in systems:
        print(f"\n>>> Running System: [{sys_id.upper()}] ...")
        t0 = time.time()
        bench_result = sys_instance.generate_full_benchmark(BENCHMARK_TOPIC_DEMAND)
        elapsed = round(time.time() - t0, 2)

        mcqs = bench_result.get("mcqs", [])
        total_mcqs += len(mcqs)
        all_mcqs.extend(mcqs)

        results_by_system[sys_id] = {
            "baseline_id": bench_result.get("baseline_id", sys_id),
            "total_mcqs_generated": len(mcqs),
            "benchmark_demand_satisfied": len(mcqs) == 36,
            "elapsed_seconds": elapsed,
            "context_augmentation": bench_result.get("context_augmentation", False),
            "knowledge_graph_used": bench_result.get("knowledge_graph_used", False),
            "rag_retrieval_used": bench_result.get("rag_retrieval_used", False),
            "web_retrieval_used": bench_result.get("web_retrieval_used", False),
            "bitemporal_snapshot_used": bench_result.get("bitemporal_snapshot_used", False),
            "screener_used": bench_result.get("screener_used", False),
            "mcqs": mcqs,
        }

        print(f"[OK] System [{sys_id}] finished: {len(mcqs)}/36 MCQs generated in {elapsed}s")

    total_elapsed = round(time.time() - start_time, 2)

    consolidated_results = {
        "benchmark_name": "BCSBatighor GK 36-Question Comparative Benchmark Set",
        "cutoff_date": CUTOFF_DATE,
        "execution_timestamp": datetime.datetime.now().isoformat(),
        "total_mcqs_generated": total_mcqs,
        "systems_evaluated": len(systems),
        "target_mcqs_per_system": sum(BENCHMARK_TOPIC_DEMAND.values()),
        "total_elapsed_seconds": total_elapsed,
        "topic_demand_distribution": BENCHMARK_TOPIC_DEMAND,
        "results_by_system": results_by_system,
        "all_mcqs": all_mcqs,
    }

    # Ensure parent output dir exists
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(consolidated_results, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("                    COMPARATIVE BENCHMARK RUN COMPLETE                     ")
    print("=" * 70)
    print(f"Total Systems Evaluated : {len(systems)}")
    print(f"Total MCQs Generated   : {total_mcqs} / 144")
    print(f"Consolidated Artifact  : {output_file}")
    print(f"Total Execution Time   : {total_elapsed}s")
    print("=" * 70)

    return consolidated_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run 36-Question Comparative Benchmark across 4 system variants.")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT_FILE), help="Path to save benchmark results JSON.")
    parser.add_argument("--api-key", type=str, default="", help="HuggingFace API Key (optional).")
    args = parser.parse_args()

    run_comparative_benchmark(output_file=Path(args.output), api_key=args.api_key)
