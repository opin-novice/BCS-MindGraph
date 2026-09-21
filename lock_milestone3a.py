"""Adjudicate duplicate candidates and lock the Milestone 3A pilot.

This creates review artifacts only. It does not collect evidence or merge facts.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
DUPLICATES = WORKFLOW / "duplicate_candidates.jsonl"
RA1_QUEUE = WORKFLOW / "triage_reviewer_queue_ra_1.csv"
DECISIONS = WORKFLOW / "duplicate_adjudications_ra_1.jsonl"
PILOT = WORKFLOW / "pilot_batch_final.jsonl"
MANIFEST = WORKFLOW / "milestone3a_pilot_manifest.json"
REVIEWER = "RA_1"
TODAY = date.today().isoformat()

# These are semantic routing decisions, not evidence or truth decisions.
DUPLICATE_VERDICTS = {
    "DUP_0f7aa2c2f4": (
        "not_duplicate",
        "Claims contain distinct area, GDP, remittance, literacy, education, internet, population, climate, and flood assertions.",
    ),
    "DUP_2a415fe466": (
        "not_duplicate",
        "UN peacekeeping contribution and LDC graduation are different predicates and temporal claims.",
    ),
    "DUP_1c278ad880": (
        "not_duplicate",
        "One record describes settlement distribution; the other describes a cultural dance. Different claims.",
    ),
    "DUP_92da0978cb": (
        "not_duplicate",
        "Both concern wheat varieties, but name different varieties; keep as separate claims.",
    ),
    "DUP_609a7510fe": (
        "merge_candidate",
        "Overlapping banana-variety lists with spelling variation; retain both until canonical claim consolidation review.",
    ),
}

PILOT_TARGETS = {
    "static_historical": 24,
    "static_geographic": 10,
    "static_institutional": 1,
    "dynamic_officeholder": 3,
    "dynamic_policy_legal": 7,
    "dynamic_statistical": 3,
    "dynamic_award_or_membership": 2,
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    groups = read_jsonl(DUPLICATES)
    queue = read_csv(RA1_QUEUE)
    decisions = []
    blocked_ids = set()
    merge_groups = set()
    for group in groups:
        verdict, reason = DUPLICATE_VERDICTS[group["duplicate_candidate_id"]]
        if verdict == "merge_candidate":
            merge_groups.add(group["duplicate_candidate_id"])
        for fact_id in group["fact_ids"]:
            decisions.append({
                "duplicate_candidate_id": group["duplicate_candidate_id"],
                "fact_id": fact_id,
                "verdict": verdict,
                "reviewer_id": REVIEWER,
                "reviewed_at": TODAY,
                "reason": reason,
                "source_search_allowed": verdict == "not_duplicate",
                "model_b_merge_allowed": False,
            })

    DECISIONS.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in decisions), encoding="utf-8")

    # Pilot only uses RA_1 approved records and excludes the unresolved merge group.
    eligible = [
        row for row in queue
        if row.get("reviewer_status") == "approved"
        and row.get("reviewer_final_class") in PILOT_TARGETS
        and row.get("duplicate_candidate_id") not in merge_groups
    ]
    pilot = []
    for claim_class, target in PILOT_TARGETS.items():
        selected = [row for row in eligible if row.get("reviewer_final_class") == claim_class][:target]
        if len(selected) < target:
            raise RuntimeError(f"pilot shortfall for {claim_class}: {len(selected)}/{target}")
        pilot.extend(selected)

    pilot_rows = []
    for index, row in enumerate(pilot, start=1):
        pilot_rows.append({
            "pilot_id": f"M3A-PILOT-{index:03d}",
            "fact_id": row["fact_id"],
            "claim_text": row["claim_text"],
            "reviewer_final_class": row["reviewer_final_class"],
            "source_route": row.get("final_source_route"),
            "priority_band": row.get("final_priority_band"),
            "pilot_status": "ready_for_evidence_collection",
            "evidence_verified": False,
            "model_b_merge_allowed": False,
            "assigned_to": None,
            "created_at": TODAY,
        })
    PILOT.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in pilot_rows), encoding="utf-8")

    manifest = {
        "milestone": "3A_duplicate_adjudication_and_pilot_lock",
        "generated_at": TODAY,
        "reviewer_id": REVIEWER,
        "duplicate_groups": len(groups),
        "duplicate_records_reviewed": len(decisions),
        "duplicate_verdict_counts": {
            verdict: sum(row["verdict"] == verdict for row in decisions)
            for verdict in sorted({row["verdict"] for row in decisions})
        },
        "unresolved_merge_groups": sorted(merge_groups),
        "pilot_count": len(pilot_rows),
        "pilot_category_counts": {
            category: sum(row["reviewer_final_class"] == category for row in pilot_rows)
            for category in PILOT_TARGETS
        },
        "pilot_sha256": hashlib.sha256(PILOT.read_bytes()).hexdigest(),
        "evidence_verified": False,
        "model_b_merge_performed": False,
        "frozen_seed_modified": False,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert len(decisions) == 21
    assert len(pilot_rows) == 50
    assert sum(manifest["pilot_category_counts"].values()) == 50
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
