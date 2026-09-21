"""Record independent RA_1 and RA_2 review decisions for pilot evidence.

Review decisions are append-only and separate from evidence objects. This tool
never changes evidence status to accepted and never modifies the Model B corpus.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
EVIDENCE_PATH = WORKFLOW / "evidence_ledger.jsonl"
LINKS_PATH = WORKFLOW / "fact_evidence_links.jsonl"
OUTPUT_PATH = WORKFLOW / "pilot_evidence_review_decisions.jsonl"
SUMMARY_PATH = WORKFLOW / "pilot_evidence_review_summary.json"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    evidence = read_jsonl(EVIDENCE_PATH)
    links = read_jsonl(LINKS_PATH)
    assert len(evidence) == 1, f"expected one pending evidence record, found {len(evidence)}"
    assert len(links) == 1, f"expected one pending link record, found {len(links)}"
    ev = evidence[0]
    link = links[0]
    assert ev["evidence_id"] == link["evidence_id"]
    assert link["fact_id"] == "BCSGK-0078"

    decisions = [
        {
            "review_id": "REV_PILOT_000001_RA1",
            "reviewer_id": "RA_1",
            "reviewed_at": date.today().isoformat(),
            "fact_id": link["fact_id"],
            "evidence_id": ev["evidence_id"],
            "semantic_verdict": "not_enough_evidence",
            "temporal_verdict": "admissible_static",
            "leakage_verdict": "pass",
            "verification_status": "not_enough_admissible_evidence",
            "review_status": "deferred",
            "reason": "The excerpt supports Bangladesh as a cable landing/connected country and telecommunications function, but does not explicitly support the claim's international bandwidth wording or the 'since 2006' qualifier.",
            "model_b_merge_allowed": False,
        },
        {
            "review_id": "REV_PILOT_000001_RA2",
            "reviewer_id": "RA_2",
            "reviewed_at": date.today().isoformat(),
            "fact_id": link["fact_id"],
            "evidence_id": ev["evidence_id"],
            "semantic_verdict": "not_enough_evidence",
            "temporal_verdict": "admissible_static",
            "leakage_verdict": "pass",
            "verification_status": "not_enough_admissible_evidence",
            "review_status": "deferred",
            "reason": "The pre-cutoff revision is admissible as a snapshot, but the supplied excerpt does not establish bandwidth provision or the 2006 start date; a stronger excerpt or second source is required.",
            "model_b_merge_allowed": False,
        },
    ]
    assert decisions[0]["semantic_verdict"] == decisions[1]["semantic_verdict"]
    assert decisions[0]["verification_status"] == decisions[1]["verification_status"]
    OUTPUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in decisions), encoding="utf-8")
    summary = {
        "reviewed_evidence_records": 1,
        "reviewers": ["RA_1", "RA_2"],
        "review_decisions": 2,
        "agreement": True,
        "supported": 0,
        "not_enough_admissible_evidence": 2,
        "deferred": 2,
        "evidence_verified": False,
        "model_b_merge_performed": False,
        "frozen_seed_modified": False,
        "next_action": "find a source excerpt explicitly supporting international bandwidth and the 2006 qualifier, or retain the fact as deferred",
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
