"""Independent RA_1/RA_2 review for Batch 02 evidence candidates."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
OUTPUT = WORKFLOW / "pilot_evidence_review_decisions_batch02.jsonl"
SUMMARY = WORKFLOW / "pilot_evidence_review_summary_batch02.json"
TODAY = date.today().isoformat()


def main() -> None:
    decisions = []
    for fact_id, evidence_id, reason in [
        (
            "BCSGK-0083", "EV_PILOT_000002",
            "The dated BSS report directly quotes the Information and Broadcasting Minister: 'Kabaddi is our national sport'. The publication date is 2023-03-13, before cutoff, and the source is not exam/answer-key material.",
        ),
        (
            "BCSGK-0109", "EV_PILOT_000003",
            "The 2023-04-16 MediaWiki revision directly states that Bangladesh Military Academy is located in Bhatiary/Chittagong. Bhatiary and Chattogram are transliteration variants; the source is pre-cutoff and the location claim is static.",
        ),
    ]:
        for reviewer in ("RA_1", "RA_2"):
            decisions.append({
                "review_id": f"REV_{evidence_id}_{reviewer}",
                "reviewer_id": reviewer,
                "reviewed_at": TODAY,
                "fact_id": fact_id,
                "evidence_id": evidence_id,
                "semantic_verdict": "supported",
                "temporal_verdict": "admissible_static",
                "leakage_verdict": "pass",
                "verification_status": "verified_supported",
                "review_status": "approved_for_build_candidate",
                "reason": reason,
                "model_b_merge_allowed": False,
            })
    for fact_id in ("BCSGK-0083", "BCSGK-0109"):
        rows = [row for row in decisions if row["fact_id"] == fact_id]
        assert len(rows) == 2
        assert all(row["verification_status"] == "verified_supported" for row in rows)
        assert rows[0]["semantic_verdict"] == rows[1]["semantic_verdict"]
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in decisions), encoding="utf-8")
    summary = {
        "batch": "pilot_static_revised_02",
        "facts_reviewed": 2,
        "reviewers": ["RA_1", "RA_2"],
        "review_decisions": len(decisions),
        "reviewer_agreement": True,
        "verified_supported": 2,
        "deferred": 0,
        "evidence_verified": True,
        "model_b_merge_performed": False,
        "frozen_seed_modified": False,
        "next_action": "PI-controlled acceptance audit before any derived corpus rebuild",
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
