"""
test_comparative_execution.py
==============================
Unit & Integrity Test Suite for Task 8.2 Execution Audit & Integrity Verification.

Audits `experiments/baseline_comparison_results.json` to verify:
1. File existence & JSON schema validity.
2. Top-level metadata integrity (144 MCQs, 4 systems, t* = 2023-04-19, 11 benchmark topics).
3. System-level demand distribution (exactly 36 MCQs per system matching BENCHMARK_TOPIC_DEMAND).
4. MCQ-level schema completeness (zero missing/null fields, valid options keys, valid correct_answer).
5. Baseline-specific context & provenance metadata.
"""

import os
import sys
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS_FILE = ROOT / "experiments" / "baseline_comparison_results.json"
CUTOFF_DATE = "2023-04-19"
EXPECTED_TOPIC_DEMAND = {
    "Appointments": 4,
    "Constitution": 1,
    "Constitution & Law": 4,
    "Culture": 5,
    "Economy": 4,
    "Flora & Fauna": 3,
    "Geography": 2,
    "Government": 3,
    "History": 4,
    "Language": 3,
    "Liberation War": 3,
}
EXPECTED_SYSTEMS = ["generic_llm", "static_rag", "web_rag", "proposed_temporal_kg"]
REQUIRED_MCQ_KEYS = [
    "mcq_id", "topic", "baseline", "cutoff_date", "question",
    "options", "correct_answer", "difficulty", "explanation",
    "supporting_fact_ids", "evidence_ids"
]


class TestComparativeExecution(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Ensure baseline comparison results JSON exists and load data."""
        if not RESULTS_FILE.exists():
            from task3_comparative_run import run_comparative_benchmark
            run_comparative_benchmark(RESULTS_FILE)

        assert RESULTS_FILE.exists(), f"Benchmark results file not found at {RESULTS_FILE}"
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            cls.data = json.load(f)

    def test_top_level_metadata_integrity(self):
        """Verify top-level metadata keys and summary totals."""
        data = self.data
        self.assertIn("benchmark_name", data)
        self.assertEqual(data.get("cutoff_date"), CUTOFF_DATE)
        self.assertEqual(data.get("total_mcqs_generated"), 144)
        self.assertEqual(data.get("systems_evaluated"), 4)
        self.assertEqual(data.get("target_mcqs_per_system"), 36)
        self.assertIn("topic_demand_distribution", data)
        self.assertEqual(data["topic_demand_distribution"], EXPECTED_TOPIC_DEMAND)

    def test_systems_evaluated_completeness(self):
        """Verify all 4 baseline systems are present and satisfied demand."""
        results_by_sys = self.data.get("results_by_system", {})
        self.assertEqual(len(results_by_sys), 4)

        for sys_id in EXPECTED_SYSTEMS:
            self.assertIn(sys_id, results_by_sys)
            sys_data = results_by_sys[sys_id]
            self.assertEqual(sys_data.get("total_mcqs_generated"), 36)
            self.assertTrue(sys_data.get("benchmark_demand_satisfied"))
            self.assertEqual(len(sys_data.get("mcqs", [])), 36)

    def test_topic_demand_distribution_per_system(self):
        """Verify each system satisfies the exact 11-topic demand distribution."""
        results_by_sys = self.data.get("results_by_system", {})

        for sys_id, sys_data in results_by_sys.items():
            topic_counts = {}
            for mcq in sys_data.get("mcqs", []):
                t = mcq.get("topic")
                topic_counts[t] = topic_counts.get(t, 0) + 1

            self.assertEqual(
                topic_counts, EXPECTED_TOPIC_DEMAND,
                f"Topic demand mismatch for system '{sys_id}'"
            )

    def test_mcq_object_schema_integrity(self):
        """Verify every single MCQ object has zero missing/null required fields."""
        all_mcqs = self.data.get("all_mcqs", [])
        self.assertEqual(len(all_mcqs), 144, "Total all_mcqs list must contain exactly 144 MCQs")

        for idx, mcq in enumerate(all_mcqs):
            # Check required keys
            for key in REQUIRED_MCQ_KEYS:
                self.assertIn(key, mcq, f"MCQ #{idx} missing required key '{key}'")
                self.assertIsNotNone(mcq[key], f"MCQ #{idx} key '{key}' is None")
                if isinstance(mcq[key], str):
                    self.assertGreater(len(mcq[key].strip()), 0, f"MCQ #{idx} key '{key}' is empty string")
                elif isinstance(mcq[key], list):
                    self.assertGreater(len(mcq[key]), 0, f"MCQ #{idx} key '{key}' list is empty")

            # Check options structure
            options = mcq.get("options")
            self.assertIsInstance(options, dict, f"MCQ #{idx} options must be a dict")
            self.assertEqual(set(options.keys()), {"ক", "খ", "গ", "ঘ"}, f"MCQ #{idx} options must contain Bengali keys ক/খ/গ/ঘ")
            for opt_key, opt_val in options.items():
                self.assertGreater(len(str(opt_val).strip()), 0, f"MCQ #{idx} option '{opt_key}' is empty")

            # Check correct_answer
            correct_ans = mcq.get("correct_answer")
            self.assertIn(correct_ans, options, f"MCQ #{idx} correct_answer '{correct_ans}' must be one of options keys")

            # Check supporting_fact_ids and evidence_ids
            self.assertIsInstance(mcq.get("supporting_fact_ids"), list)
            self.assertIsInstance(mcq.get("evidence_ids"), list)

            # Check cutoff date contract
            self.assertEqual(mcq.get("cutoff_date"), CUTOFF_DATE, f"MCQ #{idx} cutoff_date must be {CUTOFF_DATE}")

    def test_system_specific_feature_flags_and_provenance(self):
        """Verify feature flags and specific provenance metadata per system."""
        results_by_sys = self.data.get("results_by_system", {})

        # Generic LLM
        generic = results_by_sys["generic_llm"]
        self.assertFalse(generic["context_augmentation"])
        self.assertFalse(generic["knowledge_graph_used"])

        # Static RAG
        static_rag = results_by_sys["static_rag"]
        self.assertTrue(static_rag["context_augmentation"])
        self.assertFalse(static_rag["knowledge_graph_used"])
        for mcq in static_rag["mcqs"]:
            self.assertIn("retrieved_context", mcq)

        # Web RAG
        web_rag = results_by_sys["web_rag"]
        self.assertTrue(web_rag["context_augmentation"])
        self.assertTrue(web_rag["web_retrieval_used"])
        for mcq in web_rag["mcqs"]:
            self.assertIn("retrieved_web_context", mcq)

        # Proposed System
        proposed = results_by_sys["proposed_temporal_kg"]
        self.assertTrue(proposed["knowledge_graph_used"])
        self.assertTrue(proposed["bitemporal_snapshot_used"])
        self.assertTrue(proposed["screener_used"])
        for mcq in proposed["mcqs"]:
            self.assertTrue(mcq.get("bitemporal_snapshot_used"))
            self.assertIn("screener_verdict", mcq)


if __name__ == "__main__":
    unittest.main()
