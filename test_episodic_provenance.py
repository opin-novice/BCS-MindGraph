"""
test_episodic_provenance.py
============================
Unit and integration test suite for Provenance-Tracked Episodic Web Retrieval Engine (Task 4.1).
"""

import unittest
import datetime
import os
import tempfile
from web_scraper import WebScraper, CutoffPolicy, ScrapedSentence, ScrapedResult
from episodic_store import EpisodicMemory


class TestEpisodicProvenance(unittest.TestCase):

    def setUp(self):
        self.cutoff_date = datetime.date(2023, 4, 19)
        self.policy = CutoffPolicy(cutoff_date=self.cutoff_date, use_wayback_fallback=True)
        self.strict_policy = CutoffPolicy(cutoff_date=self.cutoff_date, use_wayback_fallback=False)

        # Temporary database for episodic store test
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        self.mem = EpisodicMemory(db_path=self.temp_db_path)

    def tearDown(self):
        self.mem.close()
        os.close(self.temp_db_fd)
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def test_wayback_timestamp_matching(self):
        """Verify strict cutoff evaluation at or before t* = 2023-04-19."""
        # Pre-cutoff page
        decision, reason = self.policy.evaluate(
            url="https://gov.bd/page1",
            published=datetime.date(2020, 1, 1),
            modified=None
        )
        self.assertEqual(decision, "accepted")
        self.assertIn("2020-01-01 <= cutoff 2023-04-19", reason)

        # Post-cutoff page with wayback fallback enabled
        decision, reason = self.policy.evaluate(
            url="https://gov.bd/page2",
            published=datetime.date(2024, 1, 1),
            modified=None
        )
        self.assertEqual(decision, "needs_archive")
        self.assertIn("after cutoff", reason)

    def test_post_cutoff_blocking(self):
        """Verify blocking of post-cutoff live web pages when no archive fallback is enabled."""
        decision, reason = self.strict_policy.evaluate(
            url="https://news.com/post2024",
            published=datetime.date(2024, 6, 1),
            modified=None
        )
        self.assertEqual(decision, "rejected")
        self.assertIn("after cutoff", reason)

    def test_scraped_sentence_provenance_metadata(self):
        """Verify ScrapedSentence and ScrapedResult carry full provenance metadata."""
        sentence = ScrapedSentence.from_text(
            text="The 45th BCS exam syllabus covers Bangladesh Affairs.",
            url="https://bpsc.gov.bd/notice",
            source_published_at="2023-01-10",
            source_tier=1,
            retrieved_via="wayback",
            html_digest="a1b2c3d4e5f67890",
            archive_permalink="https://web.archive.org/web/20230110/https://bpsc.gov.bd/notice",
            snapshot_date="2023-01-10",
            observed_at="2023-01-11T10:00:00"
        )

        self.assertEqual(sentence.html_digest, "a1b2c3d4e5f67890")
        self.assertEqual(sentence.archive_permalink, "https://web.archive.org/web/20230110/https://bpsc.gov.bd/notice")
        self.assertEqual(sentence.snapshot_date, "2023-01-10")
        self.assertEqual(sentence.retrieved_via, "wayback")

        res = ScrapedResult(
            query_bangla="বিসিএস সিলেবাস",
            query_english="BCS syllabus",
            topic="Government",
            urls_searched=["https://bpsc.gov.bd/notice"],
            sentences=[sentence]
        )

        facts = res.as_fact_dicts()
        self.assertEqual(len(facts), 1)
        f = facts[0]
        self.assertEqual(f["source_url"], "https://web.archive.org/web/20230110/https://bpsc.gov.bd/notice")
        self.assertEqual(f["source_tier"], 1)
        self.assertEqual(f["temporal_evidence_status"], "verified_pre_cutoff_source")
        self.assertEqual(f["temporal_evidence_snapshot_hash"], "a1b2c3d4e5f67890")

    def test_episodic_store_provenance_logging(self):
        """Verify EpisodicMemory persists and queries web retrieval provenance records."""
        row_id = self.mem.log_web_retrieval(
            url="https://bbs.gov.bd/stats",
            retrieved_via="wayback",
            source_tier=1,
            observed_at="2023-04-10T12:00:00",
            status="accepted",
            archive_permalink="https://web.archive.org/web/20230410/https://bbs.gov.bd/stats",
            html_digest="digest12345",
            snapshot_date="2023-04-10"
        )

        self.assertGreater(row_id, 0)

        prov = self.mem.get_web_retrieval_provenance("digest12345")
        self.assertIsNotNone(prov)
        self.assertEqual(prov["url"], "https://bbs.gov.bd/stats")
        self.assertEqual(prov["archive_permalink"], "https://web.archive.org/web/20230410/https://bbs.gov.bd/stats")
    def test_source_tier_evidence_weighting(self):
        """Verify automated source-tier evidence weighting calculation (Task 4.2)."""
        from episodic_store import compute_evidence_weight, is_admissible_evidence

        # Tier 1 = 1.0, Tier 2 = 0.85, Tier 3 = 0.70, Tier 4 = 0.55, Tier 5 = 0.30
        self.assertEqual(compute_evidence_weight(1), 1.00)
        self.assertEqual(compute_evidence_weight(2), 0.85)
        self.assertEqual(compute_evidence_weight(3), 0.70)
        self.assertEqual(compute_evidence_weight(4), 0.55)
        self.assertEqual(compute_evidence_weight(5, max_allowed_tier=5), 0.30)
        self.assertEqual(compute_evidence_weight(5), 0.0)  # rejected under max_allowed_tier=4 floor

        self.assertTrue(is_admissible_evidence(1))
        self.assertTrue(is_admissible_evidence(4))
        self.assertFalse(is_admissible_evidence(5))
        self.assertFalse(is_admissible_evidence(1, is_pre_cutoff=False))

    def test_scraped_sentence_evidence_weight(self):
        """Verify ScrapedSentence computes and holds evidence_weight from source_tier."""
        s1 = ScrapedSentence.from_text(text="Official government statement.", url="https://bbs.gov.bd", source_tier=1)
        s4 = ScrapedSentence.from_text(text="News article report.", url="https://prothomalo.com", source_tier=4)

        self.assertEqual(s1.evidence_weight, 1.0)
        self.assertEqual(s4.evidence_weight, 0.55)

        res = ScrapedResult(
            query_bangla="বিসিএস",
            query_english="BCS",
            topic="Government",
            urls_searched=["https://bbs.gov.bd"],
            sentences=[s1, s4]
        )
        facts = res.as_fact_dicts()
        self.assertEqual(facts[0]["source_reliability"], 1.0)
        self.assertEqual(facts[1]["source_reliability"], 0.55)

    def test_episodic_store_evidence_weight_logging(self):
        """Verify log_web_retrieval stores calculated or explicit evidence_weight."""
        row_id = self.mem.log_web_retrieval(
            url="https://worldbank.org/report",
            retrieved_via="live",
            source_tier=2,
            observed_at="2023-04-10T12:00:00",
            status="accepted"
        )
        prov = self.mem.get_web_retrieval_provenance("https://worldbank.org/report")
        self.assertIsNotNone(prov)
        self.assertEqual(prov["source_tier"], 2)
        self.assertEqual(prov["evidence_weight"], 0.85)


if __name__ == "__main__":
    unittest.main()
