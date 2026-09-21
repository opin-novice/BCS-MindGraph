"""Focused tests for the Milestone 3B evidence-intake boundary."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

import pilot_evidence_intake as intake


class PilotEvidenceIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pilot, _ = intake.load_inputs()
        cls.pilot_by_id = {row["fact_id"]: row for row in cls.pilot}

    def base_candidate(self, fact_id: str) -> dict:
        row = self.pilot_by_id[fact_id]
        return {
            "fact_id": fact_id,
            "source_type": intake.SOURCE_TYPES[row["source_route"]][0],
            "source_authority": "primary_official",
            "canonical_url": "https://example.gov.bd/evidence",
            "temporal_evidence_date": "2023-04-01",
            "retrieved_at": "2026-09-16T12:00:00+00:00",
            "content_hash_sha256": "a" * 64,
            "supporting_excerpt": "The source explicitly supports the claim.",
            "subject_match": True,
            "predicate_match": True,
            "object_match": True,
            "leakage_verdict": "pass",
        }

    def test_static_candidate_is_pending_not_accepted(self):
        candidate = self.base_candidate("BCSGK-0065")
        ok, reason = intake.validate_candidate(candidate, self.pilot_by_id)
        self.assertTrue(ok, reason)
        self.assertIn("pending-review", reason)

    def test_post_cutoff_evidence_is_rejected(self):
        candidate = self.base_candidate("BCSGK-0065")
        candidate["temporal_evidence_date"] = "2024-01-01"
        ok, reason = intake.validate_candidate(candidate, self.pilot_by_id)
        self.assertFalse(ok)
        self.assertIn("post-cutoff", reason)

    def test_dynamic_candidate_requires_valid_from(self):
        candidate = self.base_candidate("BCSGK-0028")
        ok, reason = intake.validate_candidate(candidate, self.pilot_by_id)
        self.assertFalse(ok)
        self.assertIn("valid_from", reason)

    def test_acceptance_status_is_rejected(self):
        candidate = self.base_candidate("BCSGK-0065")
        candidate["verification_status"] = "verified_supported"
        ok, reason = intake.validate_candidate(candidate, self.pilot_by_id)
        self.assertFalse(ok)
        self.assertIn("acceptance verdict", reason)


if __name__ == "__main__":
    unittest.main()
