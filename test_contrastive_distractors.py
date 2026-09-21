"""
test_contrastive_distractors.py
================================
Unit test suite for Temporally Contrastive Distractor Engine (Task 6.1).
"""

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kg_builder import KnowledgeGraphBuilder
from mcq_generator import (
    MCQGenerator,
    generate_temporal_distractors,
    generate_semantic_confusers,
)


class TestContrastiveDistractors(unittest.TestCase):

    def setUp(self):
        self.cutoff_date = "2023-04-19"
        self.kg = KnowledgeGraphBuilder()

        # Insert active fact valid at cutoff (2022-01-01 to open)
        self.active_fact_id = self.kg.insert_fact_pipeline(
            fact_text="Justice A was Chief Justice of Bangladesh in 2023.",
            subject_entities=[("Chief Justice of Bangladesh", "POSITION")],
            object_entities=[("Justice A", "PERSON")],
            topic="Appointments",
            source_url="https://source.example.com/active",
            relation="holds_position",
            valid_from="2022-01-01",
            valid_to=None,
            source_published_at="2023-01-10",
            source_tier=1,
            temporal_class="dynamic",
            temporal_evidence_status="verified_pre_cutoff_source",
            temporal_evidence_date="2023-01-10",
        )

        # Insert historical past-state fact valid before cutoff (2018-01-01 to 2021-12-31)
        self.past_fact_id = self.kg.insert_fact_pipeline(
            fact_text="Justice B was Chief Justice of Bangladesh from 2018 to 2021.",
            subject_entities=[("Chief Justice of Bangladesh", "POSITION")],
            object_entities=[("Justice B", "PERSON")],
            topic="Appointments",
            source_url="https://source.example.com/past",
            relation="holds_position",
            valid_from="2018-01-01",
            valid_to="2021-12-31",
            source_published_at="2022-01-05",
            source_tier=1,
            status="superseded",
            temporal_class="dynamic",
            temporal_evidence_status="verified_pre_cutoff_source",
            temporal_evidence_date="2022-01-05",
        )

        # Insert future/post-cutoff fact (2024-01-01 to 2025-12-31)
        self.future_fact_id = self.kg.insert_fact_pipeline(
            fact_text="Justice C became Chief Justice in 2024.",
            subject_entities=[("Chief Justice of Bangladesh", "POSITION")],
            object_entities=[("Justice C", "PERSON")],
            topic="Appointments",
            source_url="https://source.example.com/future",
            relation="holds_position",
            valid_from="2024-01-01",
            valid_to="2025-12-31",
            source_published_at="2024-02-01",
            source_tier=1,
            temporal_class="dynamic",
        )

        # Insert a peer/colleague entity connected in same topic for neighborhood/subtype testing
        self.peer_fact_id = self.kg.insert_fact_pipeline(
            fact_text="Justice D was appointed to the Supreme Court in 2020.",
            subject_entities=[("Supreme Court", "INSTITUTION")],
            object_entities=[("Justice D", "PERSON")],
            topic="Appointments",
            source_url="https://source.example.com/peer",
            relation="appointed_on",
            valid_from="2020-01-01",
            valid_to=None,
            source_published_at="2020-05-10",
            source_tier=1,
            temporal_class="dynamic",
            temporal_evidence_status="verified_pre_cutoff_source",
            temporal_evidence_date="2020-05-10",
        )

    def test_generate_temporal_distractors_past_state_extraction(self):
        """Verify extraction of true past-state facts valid_to <= t_cutoff as hard distractors."""
        distractors = generate_temporal_distractors(
            self.kg,
            fact_id=self.active_fact_id,
            topic="Appointments",
            t_cutoff=self.cutoff_date,
            limit=3
        )

        self.assertGreater(len(distractors), 0)
        past_distractor = distractors[0]
        self.assertEqual(past_distractor["outdated_entity"], "Justice B")
        self.assertEqual(past_distractor["valid_to"], "2021-12-31")
        self.assertEqual(past_distractor["temporal_class"], "outdated_past_state")

    def test_generate_temporal_distractors_excludes_active_answer(self):
        """Verify that the current active answer is excluded from past-state distractors."""
        distractors = generate_temporal_distractors(
            self.kg,
            fact_id=self.active_fact_id,
            topic="Appointments",
            t_cutoff=self.cutoff_date,
            limit=5
        )

        extracted_entities = [d["outdated_entity"] for d in distractors]
        self.assertNotIn("Justice A", extracted_entities)

    def test_generate_temporal_distractors_excludes_post_cutoff(self):
        """Verify that post-cutoff valid_to dates (e.g. 2024-2025) are NOT selected as past-states."""
        distractors = generate_temporal_distractors(
            self.kg,
            fact_id=self.active_fact_id,
            topic="Appointments",
            t_cutoff=self.cutoff_date,
            limit=5
        )

        extracted_entities = [d["outdated_entity"] for d in distractors]
        self.assertNotIn("Justice C", extracted_entities)

    def test_mcq_generator_method(self):
        """Verify MCQGenerator instance method wrapper for temporal distractor generation."""
        gen = MCQGenerator(hf_api_key="mock")
        distractors = gen.generate_temporal_distractors(
            self.kg,
            fact_id=self.active_fact_id,
            topic="Appointments",
            limit=3
        )
        self.assertGreater(len(distractors), 0)
        self.assertEqual(distractors[0]["outdated_entity"], "Justice B")

    # -----------------------------------------------------------------------
    # Sub-task 6.2 Tests: Near-Synonym & Semantic Confuser Distractors
    # -----------------------------------------------------------------------

    def test_generate_semantic_confusers_from_kg(self):
        """Verify semantic confuser generation retrieves same-type entities from the KG."""
        confusers = generate_semantic_confusers(
            self.kg,
            fact_id=self.active_fact_id,
            t_cutoff=self.cutoff_date,
            limit=3,
        )

        self.assertGreater(len(confusers), 0)
        confuser_names = [c["confuser_entity"] for c in confusers]
        # Target Justice A must not be among distractors
        self.assertNotIn("Justice A", confuser_names)
        # Should include other PERSON entities from the graph like Justice B or Justice D
        overlap = set(confuser_names) & {"Justice B", "Justice D"}
        self.assertTrue(len(overlap) > 0)
        # Check schema contract
        for c in confusers:
            self.assertEqual(c["temporal_class"], "semantic_confuser")
            self.assertIn(c["generation_method"], ("kg_neighborhood", "kg_subtype_overlap", "domain_taxonomy"))

    def test_generate_semantic_confusers_from_domain_taxonomy(self):
        """Verify semantic confusers from BCS domain taxonomy clusters (e.g. Rivers)."""
        confusers = generate_semantic_confusers(
            self.kg,
            entity_name="পদ্মা",
            entity_subtype="RIVER",
            topic="Geography",
            limit=4,
        )

        self.assertGreater(len(confusers), 0)
        extracted = [c["confuser_entity"] for c in confusers]
        # Target river itself must be excluded
        self.assertNotIn("পদ্মা", extracted)
        # Should pull coordinate river terms like Meghna, Jamuna
        common_rivers = {"মেঘনা", "যমুনা", "ব্রহ্মপুত্র", "কর্ণফুলী", "সুরমা"}
        self.assertTrue(len(set(extracted) & common_rivers) > 0)
        for c in confusers:
            self.assertEqual(c["subtype"], "RIVER")
            self.assertEqual(c["generation_method"], "domain_taxonomy")

    def test_generate_semantic_confusers_excludes_target_and_true_synonyms(self):
        """Verify strict exclusion of target entity and true synonyms (E-MULTI prevention)."""
        confusers = generate_semantic_confusers(
            self.kg,
            entity_name="পহেলা বৈশাখ",
            topic="Culture",
            limit=5,
        )

        extracted = [c["confuser_entity"] for c in confusers]
        self.assertNotIn("পহেলা বৈশাখ", extracted)
        # True synonyms must NOT be selected as distractors (they would cause multiple correct answers)
        self.assertNotIn("নববর্ষ", extracted)
        self.assertNotIn("বাংলা নববর্ষ", extracted)
        self.assertNotIn("পয়লা বৈশাখ", extracted)

    def test_mcq_generator_semantic_confusers_method(self):
        """Verify MCQGenerator wrapper method for semantic confusers."""
        gen = MCQGenerator(hf_api_key="mock")
        confusers = gen.generate_semantic_confusers(
            self.kg,
            entity_name="২৬ মার্চ",
            entity_subtype="DATE",
            topic="History",
            limit=3,
        )
        self.assertGreater(len(confusers), 0)
        extracted = [c["confuser_entity"] for c in confusers]
        self.assertNotIn("২৬ মার্চ", extracted)
        # Should include other historical dates
        historic_dates = {"৭ মার্চ", "২৫ মার্চ", "১০ এপ্রিল", "১৬ ডিসেম্বর", "২১ ফেব্রুয়ারি"}
        self.assertTrue(len(set(extracted) & historic_dates) > 0)

    # -----------------------------------------------------------------------
    # Sub-task 6.3 Tests: Distractor Plausibility & Ambiguity Screener
    # -----------------------------------------------------------------------

    def test_distractor_plausibility_rejects_simultaneous_valid_at_cutoff(self):
        """Verify that a distractor simultaneously valid for the same relation at t* is rejected."""
        from mcq_quality import RuleBasedScreener, check_distractor_plausibility

        # Insert a competing concurrent fact also valid at cutoff (2022-01-01 to open)
        simultaneous_fact_id = self.kg.insert_fact_pipeline(
            fact_text="Justice X simultaneously held Chief Justice position in 2023.",
            subject_entities=[("Chief Justice of Bangladesh", "POSITION")],
            object_entities=[("Justice X", "PERSON")],
            topic="Appointments",
            source_url="https://source.example.com/simultaneous",
            relation="holds_position",
            valid_from="2022-01-01",
            valid_to=None,
            source_published_at="2023-01-10",
            source_tier=1,
            temporal_class="dynamic",
            temporal_evidence_status="verified_pre_cutoff_source",
            temporal_evidence_date="2023-01-10",
        )

        mcq_dict = {
            "mcq_id": "MCQ_test_simultaneous",
            "fact_id": self.active_fact_id,
            "question": "২০২৩ সালে বাংলাদেশের প্রধান বিচারপতি কে ছিলেন?",
            "options": {
                "ক": "Justice A",  # Correct answer
                "খ": "Justice X",  # Distractor that is simultaneously valid at cutoff
                "গ": "Justice B",  # Historical past-state (2018-2021)
                "ঘ": "Justice Other",
            },
            "correct_answer": "ক",
            "explanation": "ব্যাখ্যা",
        }

        # Check direct function
        failures = check_distractor_plausibility(mcq_dict, self.kg, t_cutoff=self.cutoff_date)
        self.assertIn("DISTRACTOR_VALID_AT_CUTOFF", failures)

        # Check through RuleBasedScreener
        screener = RuleBasedScreener()
        score, screener_failures = screener.screen(mcq_dict, kg_builder=self.kg, t_cutoff=self.cutoff_date)
        self.assertIn("DISTRACTOR_VALID_AT_CUTOFF", screener_failures)

    def test_distractor_plausibility_allows_historical_past_state(self):
        """Verify that true historical past-state distractors (valid_to <= t*) are approved."""
        from mcq_quality import check_distractor_plausibility

        mcq_dict = {
            "mcq_id": "MCQ_test_past_valid",
            "fact_id": self.active_fact_id,
            "question": "২০২৩ সালে বাংলাদেশের প্রধান বিচারপতি কে ছিলেন?",
            "options": {
                "ক": "Justice A",  # Correct answer (2022-present)
                "খ": "Justice B",  # Outdated past-state distractor (2018-2021)
                "গ": "Justice Unrelated",
                "ঘ": "Justice Other",
            },
            "correct_answer": "ক",
            "explanation": "ব্যাখ্যা",
        }

        failures = check_distractor_plausibility(mcq_dict, self.kg, t_cutoff=self.cutoff_date)
        self.assertEqual(failures, [])

    def test_distractor_ambiguity_rejects_synonymous_distractor(self):
        """Verify that a distractor sharing a synonym class with the correct answer is rejected."""
        from mcq_quality import RuleBasedScreener

        mcq_dict = {
            "mcq_id": "MCQ_test_synonym",
            "fact_id": "FACT_demo",
            "question": "বাংলা সনের প্রথম দিন কোনটি?",
            "options": {
                "ক": "পহেলা বৈশাখ",  # Correct answer
                "খ": "নববর্ষ",       # True synonym (creates multiple correct answers)
                "গ": "বিজয় দিবস",
                "ঘ": "একুশে ফেব্রুয়ারি",
            },
            "correct_answer": "ক",
            "explanation": "পহেলা বৈশাখ বাংলা নববর্ষের প্রথম দিন।",
        }

        screener = RuleBasedScreener()
        score, failures = screener.screen(mcq_dict)
        self.assertIn("SYNONYM_DISTRACTOR", failures)

    def test_mcq_generator_integrated_screener_filters_invalid_candidates(self):
        """Verify that MCQGenerator integrates distractor plausibility screening."""
        from mcq_generator import MCQ, Option

        # Competing concurrent fact in KG
        self.kg.insert_fact_pipeline(
            fact_text="Justice Concurrent held position in 2023.",
            subject_entities=[("Chief Justice of Bangladesh", "POSITION")],
            object_entities=[("Justice Concurrent", "PERSON")],
            topic="Appointments",
            source_url="https://source.example.com/concurrent",
            relation="holds_position",
            valid_from="2022-01-01",
            valid_to=None,
            source_published_at="2023-01-10",
            source_tier=1,
            temporal_class="dynamic",
            temporal_evidence_status="verified_pre_cutoff_source",
            temporal_evidence_date="2023-01-10",
        )

        gen = MCQGenerator(hf_api_key="mock", kg_builder=self.kg, cutoff_date=self.cutoff_date)

        bad_mcq = MCQ(
            mcq_id="MCQ_bad_candidate",
            fact_id=self.active_fact_id,
            question="২০২৩ সালে বাংলাদেশের প্রধান বিচারপতি কে ছিলেন?",
            options=[
                Option("ক", "Justice A"),
                Option("খ", "Justice Concurrent"),  # simultaneously valid at cutoff
                Option("গ", "Justice B"),
                Option("ঘ", "Justice D"),
            ],
            correct_answer="ক",
            difficulty="medium",
            question_type="factual",
            explanation="ব্যাখ্যা",
        )

        blockers = gen._screen_candidate_distractors(bad_mcq, supporting_facts=[])
        self.assertIn("DISTRACTOR_VALID_AT_CUTOFF", blockers)


if __name__ == "__main__":
    unittest.main()
