"""Apply an auditable RA_1 triage review to the 136 clarification records.

This is routing review only. It does not verify sources or accept facts into Model B.
The generated RA_1 ledger is separate from the automation queue and raw corpus.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
INPUT_PATH = WORKFLOW / "triage_reviewer_queue.jsonl"
OUTPUT_JSONL = WORKFLOW / "triage_decisions_ra_1.jsonl"
OUTPUT_CSV = WORKFLOW / "triage_reviewer_queue_ra_1.csv"
METRICS_PATH = WORKFLOW / "triage_metrics_ra_1.json"
REVIEWER_ID = "RA_1"
REVIEWED_AT = date.today().isoformat()
CUTOFF_YEAR = 2023

FINAL_CLASSES = {
    "static_historical", "static_geographic", "static_institutional",
    "dynamic_officeholder", "dynamic_policy_legal", "dynamic_statistical",
    "dynamic_award_or_membership", "ambiguous", "malformed",
    "duplicate_candidate", "holdout_contaminated",
}

POST_CUTOFF_RE = re.compile(r"(?<!\d)(20(?:2[4-9]|[3-9]\d))(?!\d)")
ROLE_RE = re.compile(
    r"appointed|served as|governor|president|prime minister|minister|chairman|"
    r"chairperson|secretary|director|chief|commander|উপাচার্য|গভর্নর|রাষ্ট্রপতি|"
    r"প্রধানমন্ত্রী|মন্ত্রী|চেয়ারম্যান|চেয়ারম্যান|সচিব|পরিচালক|নিয়োগ|নিয়োগ|দায়িত্ব",
    re.IGNORECASE,
)
STAT_RE = re.compile(
    r"gdp|population|literacy|percentage|percent|rate|exports?|remittance|"
    r"income|ranking|ranked|largest|smallest|million|billion|annual|per capita|"
    r"শতাংশ|হার|জনসংখ্যা|রপ্তানি|রেমিট্যান্স|আয়|জিডিপি|কোটি|লক্ষ|মিলিয়ন|বিলিয়ন",
    re.IGNORECASE,
)
POLICY_RE = re.compile(
    r"law|act|policy|regulation|prohibited|constitution|treaty|accord|গেজেট|"
    r"আইন|বিধি|নীতি|নিষেধ|সংবিধান|চুক্তি|সমঝোতা",
    re.IGNORECASE,
)
MEMBERSHIP_RE = re.compile(
    r"member|membership|elected to|award|awarded|recognized|সদস্য|নির্বাচিত|"
    r"পুরস্কার|স্বীকৃতি",
    re.IGNORECASE,
)
GEOGRAPHIC_RE = re.compile(
    r"capital|located|lies|situated|area|river|district|port|bridge|mountain|"
    r"রাজধানী|অবস্থিত|আয়তন|আয়তন|নদী|জেলা|বন্দর|সেতু|পাহাড়|পাহাড়",
    re.IGNORECASE,
)
VAGUE_RE = re.compile(r"something about|some fact|maybe|তথ্যটি|কিছু একটা", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def has_post_cutoff_claim(text: str) -> bool:
    return any(int(year) > CUTOFF_YEAR for year in POST_CUTOFF_RE.findall(text))


def review_one(row: dict) -> dict:
    text = str(row.get("claim_text") or "").strip()
    subject = row.get("subject") or []
    object_values = row.get("object") or []
    duplicate_id = row.get("duplicate_candidate_id")
    domain = "ambiguous"
    status = "approved"
    temporal_class = "needs_clarification"
    route = "manual_review"
    priority = "P3"
    reason = ""
    next_action = "manual_semantic_review"
    leakage = "pass"

    if has_post_cutoff_claim(text):
        return {
            "reviewer_final_class": "holdout_contaminated",
            "reviewer_status": "rejected",
            "temporal_class": "blocked",
            "claim_domain": "holdout_contaminated",
            "priority_band": "P1",
            "source_route": "none_holdout_excluded",
            "leakage_risk": "confirmed_holdout_leakage",
            "next_action": "exclude_from_2023_holdout",
            "reason": "claim explicitly refers to a post-2023 event, value, or source state",
        }

    if not text or VAGUE_RE.search(text):
        return {
            "reviewer_final_class": "malformed",
            "reviewer_status": "rejected",
            "temporal_class": "blocked",
            "claim_domain": "malformed",
            "priority_band": "P4",
            "source_route": "manual_claim_rewrite",
            "leakage_risk": "not_yet_checked",
            "next_action": "rewrite_or_remove_claim",
            "reason": "claim is too vague or incomplete for reproducible source matching",
        }

    if duplicate_id:
        domain = "duplicate_candidate"
        status = "needs_second_review"
        temporal_class = "needs_clarification"
        priority = "P4"
        route = "duplicate_resolution"
        leakage = "not_yet_checked"
        next_action = "compare_duplicate_group_before_sourcing"
        reason = "automation identified a normalized subject-predicate-object collision; do not merge automatically"
    elif ROLE_RE.search(text):
        domain = "dynamic_officeholder"
        temporal_class = "dynamic"
        priority = "P2"
        route = "official_appointment_or_gazette"
        next_action = "find_cutoff_covering_term_interval"
        reason = "office-holder or appointment claim requires valid_from/valid_to evidence"
    elif STAT_RE.search(text):
        domain = "dynamic_statistical"
        temporal_class = "dynamic"
        priority = "P2"
        route = "bbs_or_official_statistical_report"
        next_action = "find_reference_period_and_pre_cutoff_report"
        reason = "numeric, ranking, rate, or economic claim requires reference-period evidence"
    elif POLICY_RE.search(text):
        domain = "dynamic_policy_legal"
        temporal_class = "dynamic"
        priority = "P2"
        route = "legal_or_policy_document"
        next_action = "find_effective_date_or_gazette"
        reason = "legal, policy, treaty, or regulatory claim needs dated admissible evidence"
    elif MEMBERSHIP_RE.search(text):
        domain = "dynamic_award_or_membership"
        temporal_class = "dynamic"
        priority = "P3"
        route = "official_award_or_membership_record"
        next_action = "find_accession_award_or_recognition_date"
        reason = "membership, award, or recognition claim needs dated official evidence"
    elif GEOGRAPHIC_RE.search(text):
        domain = "static_geographic"
        temporal_class = "static"
        priority = "P1"
        route = "mediawiki_pre_cutoff_revision_or_official_geography"
        next_action = "find_pre_cutoff_claim_supporting_snapshot"
        reason = "geographic claim can be routed to pre-cutoff snapshot evidence"
    elif any(word in text.casefold() for word in ("founded", "established", "known as", "official currency", "institution")):
        domain = "static_institutional"
        temporal_class = "static"
        priority = "P2"
        route = "official_institutional_history"
        next_action = "find_pre_cutoff_official_history"
        reason = "institutional identity or historical establishment claim"
    else:
        domain = "static_historical"
        temporal_class = "static"
        priority = "P2"
        route = "mediawiki_pre_cutoff_revision_or_official_history"
        next_action = "find_pre_cutoff_claim_supporting_snapshot"
        reason = "historical/event claim routed to pre-cutoff evidence review"

    return {
        "reviewer_final_class": domain,
        "reviewer_status": status,
        "temporal_class": temporal_class,
        "claim_domain": domain,
        "priority_band": priority,
        "source_route": route,
        "leakage_risk": leakage,
        "next_action": next_action,
        "reason": reason,
    }


def main() -> None:
    rows = read_jsonl(INPUT_PATH)
    assert len(rows) == 136, f"expected 136 queue rows, found {len(rows)}"
    decisions = []
    enriched = []
    for row in rows:
        decision = review_one(row)
        assert decision["reviewer_final_class"] in FINAL_CLASSES
        decisions.append({
            "triage_id": f"TRIAGE-{row['fact_id']}", "fact_id": row["fact_id"],
            "reviewer_id": REVIEWER_ID, "reviewed_at": REVIEWED_AT,
            **decision, "review_policy_version": "model_b_triage_v1_ra1",
        })
        enriched.append({**row, **{
            "reviewer_final_class": decision["reviewer_final_class"],
            "reviewer_status": decision["reviewer_status"],
            "reviewer_id": REVIEWER_ID, "reviewed_at": REVIEWED_AT,
            "reviewer_notes": decision["reason"],
            "final_temporal_class": decision["temporal_class"],
            "final_claim_domain": decision["claim_domain"],
            "final_source_route": decision["source_route"],
            "final_priority_band": decision["priority_band"],
            "final_leakage_risk": decision["leakage_risk"],
            "next_action": decision["next_action"],
        }})

    OUTPUT_JSONL.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in decisions), encoding="utf-8")
    fields = list(enriched[0].keys())
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(enriched)

    def count(field: str, value: str) -> int:
        return sum(item[field] == value for item in decisions)

    metrics = {
        "reviewer_id": REVIEWER_ID, "reviewed_at": REVIEWED_AT,
        "total_reviewed": len(decisions),
        "reviewer_status_counts": {value: count("reviewer_status", value) for value in ["approved", "needs_second_review", "deferred", "rejected"]},
        "final_class_counts": {value: sum(item["reviewer_final_class"] == value for item in decisions) for value in sorted(FINAL_CLASSES)},
        "temporal_class_counts": {value: sum(item["temporal_class"] == value for item in decisions) for value in ["static", "dynamic", "blocked", "needs_clarification"]},
        "review_policy_version": "model_b_triage_v1_ra1",
        "evidence_verified": False,
        "model_b_merge_performed": False,
    }
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
