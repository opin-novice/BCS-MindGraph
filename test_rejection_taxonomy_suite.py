"""
test_rejection_taxonomy_suite.py
=================================
Unit test suite for the rejection taxonomy (§10.2) and the automated
factuality verification engine (Task 7.1).

Covers:
  1. Canonical code definitions and DESCRIPTIONS.
  2. Code mapping via map_codes().
  3. RejectionTally reporting and summary string.
  4. FactualityVerificationEngine:
     - Verified fact passes.
     - Hallucinated answer → FACTUALITY_HALLUCINATION (E-UNSUP).
     - Expired fact (valid_to ≤ t*) → TEMPORAL_CUTOFF_VIOLATION (E-TIME).
     - Non-existent fact → UNVERIFIED_EVIDENCE (E-UNSUP).
     - Source post-cutoff → POST_CUTOFF_LEAKAGE_RISK (E-LEAK).
  5. verify_factuality() module-level wrapper.
  6. HARD_FAILURE_CODES includes all Task 7.1 codes and maps to §10.2.

    python -m unittest test_rejection_taxonomy_suite -v
"""

from __future__ import annotations

import sys
import unittest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from rejection_taxonomy import (
    RejectionCode,
    DESCRIPTIONS,
    FAILURE_CODE_MAP,
    RejectionTally,
    map_codes,
)
from mcq_quality import (
    HARD_FAILURE_CODES,
    FactualityVerificationEngine,
    FactualityVerificationResult,
    verify_factuality,
)
from kg_builder import KnowledgeGraphBuilder

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class TestCanonicalCodes(unittest.TestCase):
    """Canonical code definitions and descriptions match §10.2."""

    EXPECTED_CODES = (
        "E-TIME", "E-LEAK", "E-UNSUP", "E-MULTI", "E-DIST",
        "E-AMB", "E-STYLE", "E-DUP", "E-KG", "E-SRC",
    )

    def test_all_canonical_codes_defined(self):
        for code in self.EXPECTED_CODES:
            self.assertIn(code, RejectionCode.ALL,
                          f"{code} missing from RejectionCode.ALL")

    def test_descriptions_cover_all_canonical(self):
        for code in self.EXPECTED_CODES:
            self.assertIn(code, DESCRIPTIONS,
                          f"{code} missing from DESCRIPTIONS")
            self.assertTrue(len(DESCRIPTIONS[code]) > 0,
                            f"Description for {code} is empty")

    def test_alias_codes_defined(self):
        self.assertEqual(RejectionCode.E_FACT, "E-FACT")
        self.assertEqual(RejectionCode.E_TEMP, "E-TEMP")
        self.assertEqual(RejectionCode.E_AMBIG, "E-AMBIG")

    def test_alias_descriptions_present(self):
        for alias in ("E-FACT", "E-TEMP", "E-AMBIG"):
            self.assertIn(alias, DESCRIPTIONS)


class TestCodeMapping(unittest.TestCase):
    """map_codes() translates internal codes to canonical taxonomy."""

    def test_known_codes_map(self):
        result = map_codes(["WRONG_GROUNDING", "WEAK_DISTRACTORS"])
        self.assertEqual(result, ["E-UNSUP", "E-DIST"])

    def test_identity_mapping(self):
        result = map_codes(["E-TIME", "E-LEAK"])
        self.assertEqual(result, ["E-TIME", "E-LEAK"])

    def test_deduplication(self):
        result = map_codes(["WRONG_GROUNDING", "REASONER_WRONG"])
        # Both map to E-UNSUP, should be deduplicated
        self.assertEqual(result, ["E-UNSUP"])

    def test_unknown_code_prefixed(self):
        result = map_codes(["TOTALLY_NEW_CODE"])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].startswith("UNMAPPED:"))

    def test_task_7_1_codes_mapped(self):
        """Task 7.1 failure codes all have valid taxonomy mappings."""
        codes = ["FACTUALITY_HALLUCINATION", "UNVERIFIED_EVIDENCE",
                 "UNSUPPORTED_FACT", "TEMPORAL_CUTOFF_VIOLATION",
                 "UNRELIABLE_SOURCE", "GRAPH_CONFLICT"]
        result = map_codes(codes)
        self.assertFalse(
            any(c.startswith("UNMAPPED:") for c in result),
            f"Unmapped codes found: {result}")

    def test_alias_codes_mapped(self):
        result = map_codes(["E-FACT", "E_FACT", "E-TEMP", "E_TEMP",
                            "E-AMBIG", "E_AMBIG"])
        self.assertIn("E-UNSUP", result)
        self.assertIn("E-TIME", result)
        self.assertIn("E-AMB", result)

    def test_empty_input(self):
        self.assertEqual(map_codes([]), [])


class TestRejectionTally(unittest.TestCase):
    """RejectionTally reporting and summary string."""

    def test_empty_tally(self):
        tally = RejectionTally()
        report = tally.report()
        self.assertEqual(report["_total_items"], 0)
        self.assertEqual(report["_total_rejections"], 0)
        self.assertIsNone(report["_rejection_rate"])

    def test_add_and_report(self):
        tally = RejectionTally()
        tally.add(["E-TIME", "E-LEAK"])
        tally.add(["E-UNSUP"])
        tally.add([])  # a passing item
        report = tally.report()
        self.assertEqual(report["_total_items"], 3)
        self.assertEqual(report["_total_rejections"], 3)
        self.assertEqual(report["E-TIME"], 1)
        self.assertEqual(report["E-LEAK"], 1)
        self.assertEqual(report["E-UNSUP"], 1)

    def test_rejection_rate_counts_items_not_codes(self):
        # A single item carrying two codes must count once toward the
        # rate, not twice -- the rate is "fraction of items rejected",
        # not "fraction of code occurrences", per guideline SS8.4.
        tally = RejectionTally()
        tally.add(["E-TIME", "E-LEAK"])  # one item, two codes
        tally.add([])                    # a passing item
        report = tally.report()
        self.assertEqual(report["_total_items"], 2)
        self.assertEqual(report["_items_rejected"], 1)
        self.assertEqual(report["_total_rejections"], 2)
        self.assertEqual(report["_rejection_rate"], 0.5)
        self.assertLessEqual(report["_rejection_rate"], 1.0)

    def test_summary_str_format(self):
        tally = RejectionTally()
        tally.add(["E-TIME"])
        s = tally.summary_str()
        self.assertIn("E-TIME", s)
        self.assertIn("Rejection taxonomy report", s)
        self.assertIn("Total items evaluated", s)

    def test_unmapped_codes_in_report(self):
        tally = RejectionTally()
        tally.add(["UNMAPPED:SOME_CODE"])
        report = tally.report()
        self.assertEqual(report.get("UNMAPPED:SOME_CODE"), 1)


class TestFactualityVerificationEngine(unittest.TestCase):
    """
    FactualityVerificationEngine cross-verifies MCQ options against
    bitemporal KG evidence.
    """

    def setUp(self):
        """Build a small KG for testing factuality verification."""
        self.kg = KnowledgeGraphBuilder()

        # Fact 1: Valid at cutoff, properly sourced, grounded.
        self.fid_valid = self.kg.insert_fact_pipeline(
            fact_text="Sheikh Mujibur Rahman was the first president of Bangladesh",
            subject_entities=[("Bangladesh", "COUNTRY")],
            object_entities=[("Sheikh Mujibur Rahman", "PERSON")],
            topic="History",
            source_url="https://gov.bd/a",
            publisher="GoB",
            relation="first_president",
            valid_from="1971-01-01",
            source_published_at="2020-01-01",
            source_tier=1,
        )

        # Fact 2: Expired (valid_to <= t*)
        self.fid_expired = self.kg.insert_fact_pipeline(
            fact_text="Khaleda Zia was Prime Minister of Bangladesh",
            subject_entities=[("Bangladesh", "COUNTRY")],
            object_entities=[("Khaleda Zia", "PERSON")],
            topic="Government",
            source_url="https://gov.bd/b",
            publisher="GoB",
            relation="prime_minister",
            valid_from="2001-10-10",
            valid_to="2006-10-29",
            source_published_at="2002-01-01",
            source_tier=1,
        )

        # Fact 3: Source published after cutoff
        self.fid_post_cutoff = self.kg.insert_fact_pipeline(
            fact_text="New policy enacted in 2024",
            subject_entities=[("Bangladesh", "COUNTRY")],
            object_entities=[("Policy2024", "POLICY")],
            topic="Government",
            source_url="https://gov.bd/c",
            publisher="GoB",
            relation="enacted",
            valid_from="2024-01-01",
            source_published_at="2024-06-01",
            source_tier=1,
        )

        self.t_cutoff = "2023-04-19"

    def test_verified_fact_passes(self):
        """A valid, properly grounded MCQ passes factuality verification."""
        mcq = {
            "mcq_id": "MCQ_FACT_OK",
            "fact_id": self.fid_valid,
            "question": "বাংলাদেশের প্রথম রাষ্ট্রপতি কে ছিলেন?",
            "options": {
                "ক": "Sheikh Mujibur Rahman",
                "খ": "Ziaur Rahman",
                "গ": "Ershad",
                "ঘ": "Tajuddin Ahmad",
            },
            "correct_answer": "ক",
        }
        engine = FactualityVerificationEngine(self.kg, self.t_cutoff)
        result = engine.verify(mcq)
        self.assertTrue(result.verified,
                        f"Expected verified=True but got failures: {result.failure_codes}")
        self.assertEqual(result.failure_codes, [])

    def test_hallucinated_answer(self):
        """A correct answer not grounded in fact → FACTUALITY_HALLUCINATION."""
        mcq = {
            "mcq_id": "MCQ_HALLUC",
            "fact_id": self.fid_valid,
            "question": "বাংলাদেশের প্রথম রাষ্ট্রপতি কে?",
            "options": {
                "ক": "CompletelyInventedName",
                "খ": "Ziaur Rahman",
                "গ": "Ershad",
                "ঘ": "Tajuddin Ahmad",
            },
            "correct_answer": "ক",
        }
        engine = FactualityVerificationEngine(self.kg, self.t_cutoff)
        result = engine.verify(mcq)
        self.assertFalse(result.verified)
        self.assertIn("FACTUALITY_HALLUCINATION", result.failure_codes)
        # Must map to E-UNSUP
        canonical = map_codes(result.failure_codes)
        self.assertIn("E-UNSUP", canonical)

    def test_expired_fact_violation(self):
        """A fact with valid_to <= t* → TEMPORAL_CUTOFF_VIOLATION."""
        mcq = {
            "mcq_id": "MCQ_EXPIRED",
            "fact_id": self.fid_expired,
            "question": "বাংলাদেশের প্রধানমন্ত্রী কে ছিলেন?",
            "options": {
                "ক": "Khaleda Zia",
                "খ": "Sheikh Hasina",
                "গ": "Ershad",
                "ঘ": "Zia",
            },
            "correct_answer": "ক",
        }
        engine = FactualityVerificationEngine(self.kg, self.t_cutoff)
        result = engine.verify(mcq)
        self.assertFalse(result.verified)
        self.assertIn("TEMPORAL_CUTOFF_VIOLATION", result.failure_codes)
        canonical = map_codes(result.failure_codes)
        self.assertIn("E-TIME", canonical)

    def test_nonexistent_fact(self):
        """A fact_id not in the KG → UNVERIFIED_EVIDENCE."""
        mcq = {
            "mcq_id": "MCQ_GHOST",
            "fact_id": "FACT_DOES_NOT_EXIST",
            "question": "কিছু?",
            "options": {
                "ক": "Option A",
                "খ": "Option B",
                "গ": "Option C",
                "ঘ": "Option D",
            },
            "correct_answer": "ক",
        }
        engine = FactualityVerificationEngine(self.kg, self.t_cutoff)
        result = engine.verify(mcq)
        self.assertFalse(result.verified)
        self.assertIn("UNVERIFIED_EVIDENCE", result.failure_codes)
        canonical = map_codes(result.failure_codes)
        self.assertIn("E-UNSUP", canonical)

    def test_post_cutoff_source(self):
        """A fact with source_published_at > t* → POST_CUTOFF_LEAKAGE_RISK."""
        mcq = {
            "mcq_id": "MCQ_LEAK",
            "fact_id": self.fid_post_cutoff,
            "question": "২০২৪ সালে কী ঘটেছে?",
            "options": {
                "ক": "Policy2024",
                "খ": "Nothing",
                "গ": "Something",
                "ঘ": "Other",
            },
            "correct_answer": "ক",
        }
        engine = FactualityVerificationEngine(self.kg, self.t_cutoff)
        result = engine.verify(mcq)
        self.assertFalse(result.verified)
        self.assertIn("POST_CUTOFF_LEAKAGE_RISK", result.failure_codes)

    def test_no_kg_builder_skips(self):
        """When kg_builder is None, verification is trivially passed."""
        mcq = {
            "mcq_id": "MCQ_NO_KG",
            "fact_id": "FACT_X",
            "question": "কিছু?",
            "options": {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"},
            "correct_answer": "ক",
        }
        engine = FactualityVerificationEngine(None, self.t_cutoff)
        result = engine.verify(mcq)
        self.assertTrue(result.verified)
        self.assertEqual(result.failure_codes, [])


class TestVerifyFactualityWrapper(unittest.TestCase):
    """verify_factuality() module-level convenience wrapper."""

    def test_wrapper_delegates_correctly(self):
        kg = KnowledgeGraphBuilder()
        fid = kg.insert_fact_pipeline(
            fact_text="Dhaka is the capital of Bangladesh",
            subject_entities=[("Bangladesh", "COUNTRY")],
            object_entities=[("Dhaka", "CITY")],
            topic="Geography",
            source_url="https://gov.bd/d",
            publisher="GoB",
            relation="capital",
            valid_from="1971-01-01",
            source_published_at="2020-01-01",
            source_tier=1,
        )
        mcq = {
            "mcq_id": "MCQ_WRAP_OK",
            "fact_id": fid,
            "question": "বাংলাদেশের রাজধানী কোথায়?",
            "options": {
                "ক": "Dhaka",
                "খ": "Chittagong",
                "গ": "Sylhet",
                "ঘ": "Rajshahi",
            },
            "correct_answer": "ক",
        }
        result = verify_factuality(mcq, kg, "2023-04-19")
        self.assertIsInstance(result, FactualityVerificationResult)
        self.assertTrue(result.verified)

    def test_wrapper_none_kg(self):
        mcq = {
            "mcq_id": "MCQ_WRAP_NONE",
            "fact_id": "F1",
            "question": "Q?",
            "options": {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"},
            "correct_answer": "ক",
        }
        result = verify_factuality(mcq, None)
        self.assertTrue(result.verified)


class TestHardFailureCodesIntegrity(unittest.TestCase):
    """Task 7.1 codes are in HARD_FAILURE_CODES and map to §10.2."""

    TASK_71_CODES = (
        "FACTUALITY_HALLUCINATION",
        "UNVERIFIED_EVIDENCE",
        "TEMPORAL_CUTOFF_VIOLATION",
    )

    def test_task71_codes_in_hard_failures(self):
        for code in self.TASK_71_CODES:
            self.assertIn(code, HARD_FAILURE_CODES,
                          f"{code} missing from HARD_FAILURE_CODES")

    def test_all_hard_failures_map_to_taxonomy(self):
        mapped = map_codes(list(HARD_FAILURE_CODES))
        unmapped = [c for c in mapped if c.startswith("UNMAPPED:")]
        self.assertEqual(unmapped, [],
                         f"Unmapped hard-failure codes: {unmapped}")


if __name__ == "__main__":
    unittest.main()
