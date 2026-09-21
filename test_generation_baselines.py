"""
test_generation_baselines.py
=============================
Unit test suite for Task 5 Generation Baselines Suite.
"""

import os
import sys
import unittest
import json
import tempfile
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baselines.generic_llm_baseline import (
    GenericLLMBaseline,
    CUTOFF_DATE,
    BENCHMARK_TOPIC_DEMAND,
    SYSTEM_PROMPT as GENERIC_SYSTEM_PROMPT,
    build_user_prompt as build_generic_user_prompt
)

from baselines.static_rag_baseline import (
    StaticRAGBaseline,
    StaticCorpusRetriever,
    SYSTEM_PROMPT as STATIC_RAG_SYSTEM_PROMPT,
    build_user_prompt as build_static_rag_user_prompt
)

from baselines.web_rag_baseline import (
    WebRAGBaseline,
    WebCorpusRetriever,
    SYSTEM_PROMPT as WEB_RAG_SYSTEM_PROMPT,
    build_user_prompt as build_web_rag_user_prompt
)

from baselines.proposed_temporal_kg import (
    ProposedTemporalKGSystem,
    ModelBWrapper
)


class TestGenerationBaselines(unittest.TestCase):

    def setUp(self):
        self.generic_baseline = GenericLLMBaseline(api_key="")
        self.static_rag_baseline = StaticRAGBaseline(api_key="")
        self.web_rag_baseline = WebRAGBaseline(api_key="")
        self.proposed_system = ProposedTemporalKGSystem(api_key="")

    def test_generic_llm_prompt_cutoff_contract(self):
        """Verify prompt contains strict 2023-04-19 temporal cutoff instructions."""
        self.assertIn(CUTOFF_DATE, GENERIC_SYSTEM_PROMPT)
        self.assertIn("45th BCS", GENERIC_SYSTEM_PROMPT)
        
        user_prompt = build_generic_user_prompt("Geography", 2)
        self.assertIn("Geography", user_prompt)
        self.assertIn(CUTOFF_DATE, user_prompt)
        self.assertIn("Exactly 2", user_prompt)

    def test_benchmark_topic_demand_distribution(self):
        """Verify benchmark demand configuration total equals 36 MCQs across 11 topics."""
        total_mcqs = sum(BENCHMARK_TOPIC_DEMAND.values())
        self.assertEqual(total_mcqs, 36)
        self.assertEqual(len(BENCHMARK_TOPIC_DEMAND), 11)

    def test_generic_llm_generation_structure(self):
        """Verify generated MCQ JSON schema structure and metadata."""
        mcqs = self.generic_baseline.generate_for_topic("History", 2)
        self.assertEqual(len(mcqs), 2)
        
        for mcq in mcqs:
            self.assertEqual(mcq["topic"], "History")
            self.assertEqual(mcq["baseline"], "generic_llm")
            self.assertEqual(mcq["cutoff_date"], CUTOFF_DATE)
            self.assertEqual(mcq["temporal_status"], "cutoff_compliant")
            self.assertIn("question", mcq)
            self.assertIn("options", mcq)
            self.assertIn("correct_answer", mcq)

    def test_generic_llm_full_benchmark_dry_run(self):
        """Verify full benchmark execution produces 36 MCQs meeting topic demand."""
        result = self.generic_baseline.generate_full_benchmark(BENCHMARK_TOPIC_DEMAND)
        self.assertEqual(result["baseline_id"], "generic_llm_baseline")
        self.assertEqual(result["total_mcqs_generated"], 36)
        self.assertTrue(result["benchmark_demand_satisfied"])
        self.assertFalse(result["context_augmentation"])
        self.assertFalse(result["knowledge_graph_used"])
        self.assertFalse(result["rag_retrieval_used"])
        self.assertEqual(len(result["mcqs"]), 36)

    def test_static_rag_retriever_and_prompt(self):
        """Verify StaticCorpusRetriever and Static RAG prompt construction."""
        retriever = StaticCorpusRetriever()
        facts = retriever.retrieve("History", top_k=3)
        self.assertGreater(len(facts), 0)
        
        user_prompt = build_static_rag_user_prompt("History", 2, facts)
        self.assertIn(CUTOFF_DATE, STATIC_RAG_SYSTEM_PROMPT)
        self.assertIn("RETRIEVED STATIC CONTEXT FACTS", user_prompt)
        self.assertIn("History", user_prompt)

    def test_static_rag_generation_structure(self):
        """Verify Static RAG MCQ structure includes retrieved context metadata."""
        mcqs = self.static_rag_baseline.generate_for_topic("Culture", 2)
        self.assertEqual(len(mcqs), 2)
        for mcq in mcqs:
            self.assertEqual(mcq["topic"], "Culture")
            self.assertEqual(mcq["baseline"], "static_rag")
            self.assertTrue(mcq["context_augmentation"])
            self.assertIn("retrieved_context", mcq)
            self.assertIsInstance(mcq["retrieved_context"], list)

    def test_static_rag_full_benchmark_dry_run(self):
        """Verify Static RAG full benchmark produces 36 MCQs satisfying demand."""
        result = self.static_rag_baseline.generate_full_benchmark(BENCHMARK_TOPIC_DEMAND)
        self.assertEqual(result["baseline_id"], "static_rag_baseline")
        self.assertEqual(result["total_mcqs_generated"], 36)
        self.assertTrue(result["benchmark_demand_satisfied"])
        self.assertTrue(result["context_augmentation"])
        self.assertFalse(result["knowledge_graph_used"])
        self.assertEqual(len(result["mcqs"]), 36)

    def test_web_rag_retriever_and_prompt(self):
        """Verify WebCorpusRetriever pre-cutoff bounds and Web RAG prompt construction."""
        retriever = WebCorpusRetriever()
        sentences = retriever.retrieve_web_sentences("History", max_sentences=3)
        self.assertGreater(len(sentences), 0)
        
        user_prompt = build_web_rag_user_prompt("History", 2, sentences)
        self.assertIn(CUTOFF_DATE, WEB_RAG_SYSTEM_PROMPT)
        self.assertIn("RETRIEVED WEB CONTEXT SENTENCES", user_prompt)
        self.assertIn("History", user_prompt)

    def test_web_rag_generation_structure(self):
        """Verify Web RAG MCQ structure includes web retrieval context metadata."""
        mcqs = self.web_rag_baseline.generate_for_topic("Economy", 2)
        self.assertEqual(len(mcqs), 2)
        for mcq in mcqs:
            self.assertEqual(mcq["topic"], "Economy")
            self.assertEqual(mcq["baseline"], "web_rag")
            self.assertTrue(mcq["context_augmentation"])
            self.assertTrue(mcq["web_retrieval_used"])
            self.assertIn("retrieved_web_context", mcq)
            self.assertIsInstance(mcq["retrieved_web_context"], list)

    def test_web_rag_full_benchmark_dry_run(self):
        """Verify Web RAG full benchmark produces 36 MCQs satisfying demand."""
        result = self.web_rag_baseline.generate_full_benchmark(BENCHMARK_TOPIC_DEMAND)
        self.assertEqual(result["baseline_id"], "web_rag_baseline")
        self.assertEqual(result["total_mcqs_generated"], 36)
        self.assertTrue(result["benchmark_demand_satisfied"])
        self.assertTrue(result["context_augmentation"])
        self.assertTrue(result["web_retrieval_used"])
        self.assertFalse(result["knowledge_graph_used"])
        self.assertEqual(len(result["mcqs"]), 36)

    def test_proposed_temporal_kg_system_integrity(self):
        """Verify Proposed System wrapper seed checksum and temporal guard integrity."""
        self.assertTrue(self.proposed_system.verify_seed_checksum())
        self.assertTrue(self.proposed_system.verify_temporal_guard())

    def test_proposed_temporal_kg_generation_and_screener(self):
        """Verify Proposed System MCQ structure includes screener verdict metadata."""
        mcqs = self.proposed_system.generate_for_topic("History", 2)
        self.assertEqual(len(mcqs), 2)
        for mcq in mcqs:
            self.assertEqual(mcq["topic"], "History")
            self.assertEqual(mcq["baseline"], "proposed_temporal_kg")
            self.assertTrue(mcq["bitemporal_snapshot_used"])
            self.assertIn("screener_verdict", mcq)
            self.assertIn("passed", mcq["screener_verdict"])

    def test_proposed_temporal_kg_full_benchmark_dry_run(self):
        """Verify Proposed System full benchmark produces 36 MCQs satisfying demand."""
        result = self.proposed_system.generate_full_benchmark(BENCHMARK_TOPIC_DEMAND)
        self.assertEqual(result["baseline_id"], "proposed_temporal_kg")
        self.assertEqual(result["total_mcqs_generated"], 36)
        self.assertTrue(result["benchmark_demand_satisfied"])
        self.assertTrue(result["knowledge_graph_used"])
        self.assertTrue(result["bitemporal_snapshot_used"])
        self.assertTrue(result["screener_used"])
        self.assertEqual(len(result["mcqs"]), 36)


if __name__ == "__main__":
    unittest.main()
