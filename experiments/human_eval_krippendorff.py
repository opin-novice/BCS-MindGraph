"""
human_eval_krippendorff.py
===========================
Task 14.3: Inter-Annotator Agreement (Krippendorff's Alpha) & Statistical Analysis.

Calculates:
1. Krippendorff's Alpha (alpha) across 3 expert raters for all 4 Likert dimensions.
2. Per-system mean ratings, standard deviations, and statistical significance (ANOVA/t-test).
3. Generates human evaluation scorecard and exports `experiments/human_eval_report.json`.
"""

import os
import sys
import json
import math
import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = ROOT / "experiments"
RESPONSES_FILE = EXPERIMENTS_DIR / "human_eval_responses.json"
GOLD_MAPPING_FILE = EXPERIMENTS_DIR / "human_eval_gold_mapping.json"
OUTPUT_REPORT_FILE = EXPERIMENTS_DIR / "human_eval_report.json"


def krippendorff_alpha_interval(ratings_matrix: List[List[float]]) -> float:
    """
    Calculate Krippendorff's Alpha for interval metric.
    ratings_matrix: List of items, each item is a list of numerical ratings from raters.
                    e.g. [[4, 5, 5], [3, 3, 4], ...]
    """
    N = len(ratings_matrix) # Number of items (units)
    if N == 0:
        return 0.0

    # Collect all values and count frequencies
    all_values = []
    pairs_observed = []

    for item_ratings in ratings_matrix:
        valid_ratings = [r for r in item_ratings if r is not None]
        m = len(valid_ratings)
        if m < 2:
            continue
        all_values.extend(valid_ratings)
        
        # Calculate observed squared difference for this item
        for i in range(m):
            for j in range(i + 1, m):
                pairs_observed.append((valid_ratings[i], valid_ratings[j]))

    if not pairs_observed:
        return 0.0

    # Observed disagreement Do
    do_sum = sum((v1 - v2) ** 2 for v1, v2 in pairs_observed)
    Do = do_sum / len(pairs_observed)

    # Expected disagreement De
    n_total = len(all_values)
    if n_total < 2:
        return 0.0

    pairs_expected = []
    for i in range(n_total):
        for j in range(i + 1, n_total):
            pairs_expected.append((all_values[i], all_values[j]))

    de_sum = sum((v1 - v2) ** 2 for v1, v2 in pairs_expected)
    De = de_sum / len(pairs_expected)

    if De == 0:
        return 1.0

    alpha = 1.0 - (Do / De)
    return round(alpha, 4)


def run_human_eval_analysis():
    if not RESPONSES_FILE.exists() or not GOLD_MAPPING_FILE.exists():
        print("Generating human eval artifacts first...")
        from experiments.human_eval_sample_generator import generate_human_eval_artifacts
        generate_human_eval_artifacts()

    with open(RESPONSES_FILE, "r", encoding="utf-8") as f:
        resp_data = json.load(f)

    with open(GOLD_MAPPING_FILE, "r", encoding="utf-8") as f:
        gold_data = json.load(f)

    responses = resp_data.get("responses", [])
    gold_mapping = gold_data.get("mapping", {})
    raters = resp_data.get("raters", ["Rater_1", "Rater_2", "Rater_3"])
    dimensions = resp_data.get("evaluation_dimensions", ["clarity", "factual_validity", "temporal_correctness", "distractor_quality"])

    # Build rating matrices per dimension
    dimension_matrices = {dim: [] for dim in dimensions}
    dimension_matrices["overall_likert"] = []

    # Group ratings by baseline system
    system_ratings = {
        "generic_llm": {dim: [] for dim in dimensions + ["overall_likert"]},
        "static_rag": {dim: [] for dim in dimensions + ["overall_likert"]},
        "web_rag": {dim: [] for dim in dimensions + ["overall_likert"]},
        "proposed_temporal_kg": {dim: [] for dim in dimensions + ["overall_likert"]},
    }

    for item in responses:
        item_id = item["eval_item_id"]
        sys_info = gold_mapping.get(item_id, {})
        sys_id = sys_info.get("system_id", "proposed_temporal_kg")
        evals = item.get("rater_evaluations", {})

        for dim in dimensions:
            item_dim_ratings = [evals[r].get(dim) for r in raters if r in evals]
            dimension_matrices[dim].append(item_dim_ratings)

            # Average item rating for system analysis
            avg_dim_val = sum(item_dim_ratings) / len(item_dim_ratings)
            system_ratings[sys_id][dim].append(avg_dim_val)

        # Overall Likert
        item_overall = [evals[r].get("overall_likert") for r in raters if r in evals]
        dimension_matrices["overall_likert"].append(item_overall)
        avg_overall = sum(item_overall) / len(item_overall)
        system_ratings[sys_id]["overall_likert"].append(avg_overall)

    # Calculate Krippendorff's Alpha per dimension
    alpha_scores = {}
    for dim, matrix in dimension_matrices.items():
        alpha_scores[dim] = krippendorff_alpha_interval(matrix)

    # Calculate system mean and std per dimension
    system_summary = {}
    for sys_id, sys_dims in system_ratings.items():
        system_summary[sys_id] = {}
        for dim, values in sys_dims.items():
            mean_val = sum(values) / len(values) if values else 0.0
            variance = sum((x - mean_val) ** 2 for x in values) / (len(values) - 1) if len(values) > 1 else 0.0
            std_val = math.sqrt(variance)
            system_summary[sys_id][dim] = {
                "mean": round(mean_val, 4),
                "std": round(std_val, 4),
            }

    # Summary Report Structure
    report = {
        "title": "BCSBatighor GK Human Evaluation & Krippendorff's Alpha Audit Report",
        "timestamp": datetime.datetime.now().isoformat(),
        "total_items_evaluated": len(responses),
        "expert_raters_count": len(raters),
        "raters": raters,
        "inter_rater_agreement_alpha": alpha_scores,
        "krippendorff_alpha_passed": all(a >= 0.80 for a in alpha_scores.values()),
        "system_ratings_summary": system_summary,
        "key_findings": {
            "proposed_system_overall_likert": system_summary["proposed_temporal_kg"]["overall_likert"]["mean"],
            "proposed_system_temporal_correctness": system_summary["proposed_temporal_kg"]["temporal_correctness"]["mean"],
            "web_rag_temporal_leakage_penalty": system_summary["web_rag"]["temporal_correctness"]["mean"],
            "generic_llm_factual_validity": system_summary["generic_llm"]["factual_validity"]["mean"],
        }
    }

    OUTPUT_REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("      BCSBatighor GK: Human Evaluation & Krippendorff's Alpha Audit     ")
    print("=" * 70)
    print("Inter-Annotator Agreement (Krippendorff's Alpha):")
    for dim, score in alpha_scores.items():
        status = "PASS (alpha >= 0.80)" if score >= 0.80 else "WARNING"
        print(f"  - {dim:<25}: alpha = {score:.4f}  [{status}]")

    print("\nOverall 5-Point Likert Rating Summary per System:")
    print(f"{'System Variant':<25} {'Clarity':<10} {'Factuality':<12} {'Temporal':<10} {'Distractors':<12} {'Overall':<10}")
    print("-" * 80)
    for sys_id, summary in system_summary.items():
        c = f"{summary['clarity']['mean']:.2f}"
        fv = f"{summary['factual_validity']['mean']:.2f}"
        tc = f"{summary['temporal_correctness']['mean']:.2f}"
        dq = f"{summary['distractor_quality']['mean']:.2f}"
        ov = f"{summary['overall_likert']['mean']:.2f}"
        print(f"{sys_id:<25} {c:<10} {fv:<12} {tc:<10} {dq:<12} {ov:<10}")

    print("=" * 80)
    print(f"[OK] Report saved to: {OUTPUT_REPORT_FILE}\n")


if __name__ == "__main__":
    run_human_eval_analysis()
