"""Build the append-only triage and evidence-ledger foundation for Model B."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_PATH = ROOT / "bcs_gk_facts.json"
SEED_PATH = ROOT / "bcs_gk_facts_model_b.json"
RELEASE_PATH = ROOT / "model_b_release_manifest.json"
OUT_DIR = ROOT / "model_b_workflow"
CUTOFF_DATE = "2023-04-19"
TODAY = date.today().isoformat()

STATUS = [
    "unreviewed", "triaged", "source_searching", "candidate_source_found",
    "under_semantic_review", "under_temporal_review", "accepted_for_build",
    "deferred", "rejected_permanent",
]
CLAIM_DOMAINS = [
    "static_historical", "static_geographic", "static_institutional",
    "dynamic_officeholder", "dynamic_policy_legal", "dynamic_statistical",
    "dynamic_award_or_membership", "ambiguous", "malformed",
    "duplicate_candidate", "holdout_contaminated",
]
TEMPORAL_CLASSES = ["static", "dynamic", "blocked", "needs_clarification"]

DYNAMIC_RELATIONS = {
    "holds_position", "appointed_on", "term_start", "term_end", "value_of",
    "reporting_period", "enacted", "amended", "adopted_as",
}
ROLE_CUES = re.compile(
    r"প্রধানমন্ত্রী|রাষ্ট্রপতি|মন্ত্রী|গভর্নর|চেয়ারম্যান|চেয়ারম্যান|সচিব|পরিচালক|"
    r"উপাচার্য|director|chairman|governor|minister|president|current|বর্তমান",
    re.IGNORECASE,
)
STAT_CUES = re.compile(
    r"শতাংশ|হার|জিডিপি|জনসংখ্যা|population|gdp|percent|rate|রপ্তানি|রিজার্ভ|"
    r"literacy|ranking|per capita|কোটি|লক্ষ",
    re.IGNORECASE,
)
POLICY_RELATIONS = {"enacted", "amended", "adopted_as", "declared"}
AWARD_RELATIONS = {"awarded_to", "won", "member_of", "recognized_by"}
STATIC_TOPICS = {"History", "Liberation War", "Geography", "Culture", "Language", "Flora & Fauna"}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def classify(fact: dict) -> tuple[str, str, str, str]:
    text = str(fact.get("fact_text") or "")
    topic = str(fact.get("topic") or "")
    relation = str(fact.get("relation") or "STATED_AS")
    if not text.strip() or not fact.get("subject_entities") or not fact.get("object_entities"):
        return "malformed", "needs_clarification", "P4", "missing claim structure"
    if relation in DYNAMIC_RELATIONS or ROLE_CUES.search(text):
        return "dynamic_officeholder", "dynamic", "P3", "role or office state can change over time"
    if relation in POLICY_RELATIONS or "Constitution" in topic or "Law" in topic:
        return "dynamic_policy_legal", "dynamic", "P2", "legal or policy state requires effective-date evidence"
    if relation in AWARD_RELATIONS:
        return "dynamic_award_or_membership", "dynamic", "P3", "award or membership has an event/effective date"
    if relation == "value_of" or STAT_CUES.search(text):
        return "dynamic_statistical", "dynamic", "P3", "statistic or measured value requires reference period"
    if topic in STATIC_TOPICS:
        if relation in {"located_in", "part_of", "cultivated_in"} or topic == "Geography":
            return "static_geographic", "static", "P1", "stable geographic relation"
        return "static_historical", "static", "P1", "historical claim suitable for snapshot evidence"
    if relation in {"founded", "created_by", "known_as", "headquarters"}:
        return "static_institutional", "static", "P2", "institutional identity/history claim"
    return "ambiguous", "needs_clarification", "P4", "automated classifier lacks sufficient certainty"


def priority_source_policy(domain: str) -> list[str]:
    if domain == "dynamic_officeholder":
        return ["dpp.gov.bd", "pmo.gov.bd", "cabinet.gov.bd", "relevant ministry gov.bd", "pre-cutoff archive"]
    if domain == "dynamic_policy_legal":
        return ["dpp.gov.bd", "bdlaws.minlaw.gov.bd", "parliament.gov.bd", "relevant ministry gov.bd"]
    if domain == "dynamic_statistical":
        return ["bbs.gov.bd", "bb.org.bd", "worldbank.org", "imf.org", "official annual report"]
    if domain == "dynamic_award_or_membership":
        return ["official awarding body", "relevant ministry gov.bd", "institutional archive"]
    if domain == "static_institutional":
        return ["official institutional history", "gov.bd", "UN/World Bank/UNESCO official source", "pre-cutoff revision"]
    return ["official archive", "Banglapedia", "pre-cutoff MediaWiki revision", "institutional history"]


def main() -> None:
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    release = json.loads(RELEASE_PATH.read_text(encoding="utf-8"))
    seed_ids = {fact["fact_uid"] for fact in seed}
    excluded = [fact for fact in raw if fact.get("holdout_eligible") is False]
    recoverable = [fact for fact in raw if fact["fact_uid"] not in seed_ids and fact.get("holdout_eligible") is not False]

    OUT_DIR.mkdir(exist_ok=True)
    excluded_rows = []
    for fact in excluded:
        excluded_rows.append({
            "fact_id": fact["fact_uid"], "claim_text": fact.get("fact_text"),
            "source_corpus_status": "permanently_holdout_excluded",
            "work_status": "rejected_permanent",
            "exclusion_reason": "; ".join(fact.get("holdout_exclusion_reasons") or ["holdout exclusion"]),
            "created_from_release": release["release_id"], "created_at": TODAY,
        })

    backlog_rows = []
    triage_rows = []
    for fact in recoverable:
        domain, temporal_class, priority, reason = classify(fact)
        subject = [entity[0] for entity in fact.get("subject_entities") or []]
        object_values = [entity[0] for entity in fact.get("object_entities") or []]
        row = {
            "fact_id": fact["fact_uid"], "raw_fact_id": fact["fact_uid"],
            "subject": subject, "predicate": fact.get("relation") or "STATED_AS",
            "object": object_values, "qualifiers": {}, "topic": fact.get("topic"),
            "claim_text": fact.get("fact_text"),
            "original_source_url": fact.get("source_url"), "source_corpus_status": "recoverable",
            "work_status": "unreviewed", "priority_band": priority,
            "suggested_temporal_class": temporal_class, "suggested_claim_domain": domain,
            "claim_domain": None, "temporal_class": None, "assigned_to": None,
            "suggestion_reason": reason, "source_policy": priority_source_policy(domain),
            "created_from_release": release["release_id"], "created_at": TODAY,
        }
        backlog_rows.append(row)
        if temporal_class == "needs_clarification":
            triage_rows.append({
                "triage_id": f"TRIAGE_{fact['fact_uid']}", "fact_id": fact["fact_uid"],
                "reviewer_id": None, "reviewed_at": None, "claim_domain": domain,
                "suggested_temporal_class": temporal_class, "temporal_class": None,
                "triage_verdict": "needs_human_review", "priority_band": priority,
                "reason_short": reason, "possible_duplicate_of": None,
                "leakage_risk": "not_yet_checked", "next_action": "manual_claim_clarification",
                "review_policy_version": "model_b_triage_v1", "review_status": "unreviewed",
            })

    # Empty append-only ledgers are initialized with versioned schemas separately.
    write_jsonl(OUT_DIR / "recoverable_backlog.jsonl", backlog_rows)
    write_jsonl(OUT_DIR / "permanently_excluded.jsonl", excluded_rows)
    write_jsonl(OUT_DIR / "triage_decisions.jsonl", triage_rows)

    # Deterministic pilot sampling. A category shortfall is reported, never silently relabeled.
    targets = [
        ("static_historical", 20), ("static_institutional", 10),
        ("dynamic_officeholder", 10), ("dynamic_policy_legal", 5),
        ("dynamic_statistical", 5),
    ]
    remaining = list(backlog_rows)
    pilot = []
    category_counts = {}
    for domain, count in targets:
        selected = [row for row in remaining if row["suggested_claim_domain"] == domain][:count]
        pilot.extend(selected)
        selected_ids = {row["fact_id"] for row in selected}
        remaining = [row for row in remaining if row["fact_id"] not in selected_ids]
        category_counts[domain] = len(selected)
    pilot_rows = [{**row, "pilot_status": "sampled_not_accepted", "pilot_batch": "M2-PILOT-50"} for row in pilot]
    write_jsonl(OUT_DIR / "pilot_batch.jsonl", pilot_rows)

    schemas = {
        "schema_version": "model_b_workflow_v1",
        "fact_work_status": STATUS,
        "claim_domains": CLAIM_DOMAINS,
        "temporal_classes": TEMPORAL_CLASSES,
        "semantic_verdicts": ["supported", "refuted", "ambiguous", "not_enough_evidence"],
        "temporal_verdicts": ["admissible_static", "admissible_dynamic", "outside_cutoff", "interval_does_not_cover_cutoff", "missing_validity_interval", "date_unverifiable", "not_applicable"],
        "leakage_verdicts": ["pass", "suspected", "confirmed_holdout_leakage", "not_yet_checked"],
        "evidence_ledger_fields": ["evidence_id", "evidence_version", "source_type", "source_authority", "publisher", "document_title", "canonical_url", "archive_url", "source_published_at", "temporal_evidence_date", "archive_capture_timestamp", "retrieved_at", "content_hash_sha256", "document_locator", "access_status", "admissibility_status", "notes"],
        "fact_evidence_link_fields": ["link_id", "fact_id", "evidence_id", "support_role", "supporting_excerpt", "subject_match", "predicate_match", "object_match", "semantic_verdict", "temporal_field_name", "temporal_raw_value", "valid_from", "valid_to", "temporal_verdict", "leakage_verdict", "review_status", "reviewer_id"],
        "source_allowlist": ["dpp.gov.bd", "bdlaws.minlaw.gov.bd", "bbs.gov.bd", "bb.org.bd", "parliament.gov.bd", "relevant ministry gov.bd", "UN/World Bank/UNESCO official source", "pre-cutoff archived or MediaWiki revision"],
        "cutoff_date": CUTOFF_DATE,
    }
    (OUT_DIR / "workflow_schemas.json").write_text(json.dumps(schemas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT_DIR / "evidence_ledger.jsonl").write_text("", encoding="utf-8")
    (OUT_DIR / "fact_evidence_links.jsonl").write_text("", encoding="utf-8")

    summary = {
        "generated_at": TODAY, "release_id": release["release_id"],
        "raw_count": len(raw), "accepted_seed_count": len(seed),
        "permanently_excluded_count": len(excluded), "recoverable_count": len(recoverable),
        "unclassified_recoverable_count": sum(row["suggested_temporal_class"] == "needs_clarification" for row in backlog_rows),
        "triage_decision_rows": len(triage_rows), "pilot_count": len(pilot),
        "pilot_category_counts": category_counts,
        "pilot_shortfall": {domain: target - category_counts[domain] for domain, target in targets if category_counts[domain] < target},
        "evidence_ledger_rows": 0, "fact_evidence_link_rows": 0,
        "seed_merge_performed": False,
        "reconciliation": f"{len(raw)} = {len(seed)} + {len(excluded)} + {len(recoverable)}",
    }
    (OUT_DIR / "milestone2_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert summary["reconciliation"] == "616 = 69 + 20 + 527"
    assert len(backlog_rows) == 527
    assert len(excluded_rows) == 20
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
