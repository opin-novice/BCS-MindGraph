"""Generate deterministic reviewer suggestions for Model B triage.

This tool never makes final semantic, temporal, leakage, or acceptance decisions.
It reads the append-only workflow inputs and writes derived reviewer queues.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
BACKLOG_PATH = WORKFLOW / "recoverable_backlog.jsonl"
TRIAGE_PATH = WORKFLOW / "triage_decisions.jsonl"
SCHEMA_PATH = WORKFLOW / "workflow_schemas.json"
EXCLUDED_PATH = WORKFLOW / "permanently_excluded.jsonl"
OUTPUT_JSONL = WORKFLOW / "triage_reviewer_queue.jsonl"
OUTPUT_CSV = WORKFLOW / "triage_reviewer_queue.csv"
DUPLICATES_PATH = WORKFLOW / "duplicate_candidates.jsonl"
METRICS_PATH = WORKFLOW / "triage_metrics.json"

RISK_RE = re.compile(
    r"45th\s*bcs|45তম\s*bcs|question\s*paper|answer\s*key|solution|coaching|"
    r"বর্তমান|এখন|সাম্প্রতিক|সর্বশেষ|latest|current|present",
    re.IGNORECASE,
)
ROLE_RE = re.compile(
    r"প্রধানমন্ত্রী|রাষ্ট্রপতি|মন্ত্রী|গভর্নর|চেয়ারম্যান|চেয়ারম্যান|সচিব|"
    r"পরিচালক|উপাচার্য|director|chairman|governor|minister|president|"
    r"বর্তমান|current|present",
    re.IGNORECASE,
)
STAT_RE = re.compile(
    r"শতাংশ|হার|জিডিপি|জনসংখ্যা|population|gdp|percent|rate|রপ্তানি|"
    r"রিজার্ভ|literacy|ranking|per capita|কোটি|লক্ষ",
    re.IGNORECASE,
)
HISTORICAL_TOPICS = {"History", "Liberation War", "Language", "Culture", "Flora & Fauna"}

CSV_FIELDS = [
    "fact_id", "claim_text", "subject", "predicate", "object",
    "suggested_claim_domain", "suggested_temporal_class", "suggested_source_route",
    "priority_band", "leakage_flag", "duplicate_candidate_id", "confidence",
    "heuristic_reason", "search_template_1", "search_template_2",
    "reviewer_final_class", "reviewer_status", "reviewer_id", "reviewed_at",
    "reviewer_notes",
]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def route_for(domain: str) -> str:
    return {
        "static_historical": "mediawiki_pre_cutoff_revision_or_official_history",
        "static_geographic": "mediawiki_pre_cutoff_revision_or_official_geography",
        "static_institutional": "official_institutional_history",
        "dynamic_officeholder": "official_appointment_or_gazette",
        "dynamic_policy_legal": "legal_or_policy_document",
        "dynamic_statistical": "bbs_or_official_statistical_report",
        "dynamic_award_or_membership": "official_award_or_membership_record",
    }.get(domain, "manual_review")


def infer_suggestion(row: dict) -> tuple[str, str, str, str]:
    """Suggest a route independently of the earlier coarse classifier."""
    text = str(row.get("claim_text") or "")
    topic = str(row.get("topic") or "")
    predicate = str(row.get("predicate") or "")
    if not text.strip() or not row.get("subject") or not row.get("object"):
        return "malformed", "needs_clarification", "P4", "claim structure is incomplete"
    if ROLE_RE.search(text) or predicate in {"holds_position", "appointed_on", "term_start", "term_end"}:
        return "dynamic_officeholder", "dynamic", "P3", "role/office language requires interval evidence"
    if predicate in {"enacted", "amended", "adopted_as", "declared"} or "আইন" in topic or "Constitution" in topic:
        return "dynamic_policy_legal", "dynamic", "P2", "legal/policy claim requires effective-date evidence"
    if predicate in {"value_of", "reporting_period"} or STAT_RE.search(text):
        return "dynamic_statistical", "dynamic", "P3", "statistical claim requires reference-period evidence"
    if predicate in {"awarded_to", "won", "member_of", "recognized_by"}:
        return "dynamic_award_or_membership", "dynamic", "P3", "award/membership claim needs dated evidence"
    if topic in HISTORICAL_TOPICS:
        return "static_historical", "static", "P1", "historical claim can use a pre-cutoff snapshot"
    if topic == "Geography" or predicate in {"located_in", "part_of", "cultivated_in"}:
        return "static_geographic", "static", "P1", "geographic claim can use a pre-cutoff snapshot"
    if predicate in {"founded", "created_by", "known_as", "headquarters"}:
        return "static_institutional", "static", "P2", "institutional claim needs official history evidence"
    return "ambiguous", "needs_clarification", "P4", "manual claim-type review required"


def confidence(domain: str, text: str) -> float:
    if domain.startswith("static_") and len(text) >= 20:
        return 0.82
    if domain.startswith("dynamic_") and len(text) >= 20:
        return 0.78
    return 0.45


def leakage_flag(row: dict) -> str:
    text = str(row.get("claim_text") or "")
    source = str(row.get("original_source_url") or "")
    if RISK_RE.search(text) or RISK_RE.search(source) or "45" in source:
        return "suspected"
    return "not_yet_checked"


def build_search_templates(row: dict, route: str) -> tuple[str, str]:
    subject = (row.get("subject") or [""])[0]
    predicate = row.get("predicate") or ""
    object_value = (row.get("object") or [""])[0]
    claim = row.get("claim_text") or ""
    first = f'"{subject}" "{object_value}" "{predicate}" pre-2023 official'
    second = f'"{claim[:100]}" {route.replace("_", " ")}'
    return first, second


def duplicate_key(row: dict) -> str:
    parts = [
        normalize("|".join(row.get("subject") or [])),
        normalize(row.get("predicate") or ""),
        normalize("|".join(row.get("object") or [])),
    ]
    return "|".join(parts)


def check_inputs() -> tuple[list[dict], list[dict], list[dict], dict]:
    backlog = read_jsonl(BACKLOG_PATH)
    triage = read_jsonl(TRIAGE_PATH)
    excluded = read_jsonl(EXCLUDED_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert len(triage) == 136, f"expected 136 clarification records, found {len(triage)}"
    excluded_ids = {row["fact_id"] for row in excluded}
    assert not excluded_ids & {row["fact_id"] for row in backlog}
    return backlog, triage, excluded, schema


def main(check_only: bool = False) -> None:
    backlog, triage, excluded, schema = check_inputs()
    triage_ids = {row["fact_id"] for row in triage}
    candidates = [row for row in backlog if row["fact_id"] in triage_ids]
    assert len(candidates) == 136

    duplicate_groups: dict[str, list[str]] = {}
    for row in candidates:
        duplicate_groups.setdefault(duplicate_key(row), []).append(row["fact_id"])
    duplicate_id_by_fact = {}
    duplicate_rows = []
    for key, fact_ids in sorted(duplicate_groups.items()):
        if not key or len(fact_ids) < 2:
            continue
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        candidate_id = f"DUP_{digest}"
        for fact_id in fact_ids:
            duplicate_id_by_fact[fact_id] = candidate_id
        duplicate_rows.append({"duplicate_candidate_id": candidate_id, "fact_ids": sorted(fact_ids), "basis": "normalized subject|predicate|object exact match", "review_status": "unreviewed"})

    output = []
    for row in sorted(candidates, key=lambda item: item["fact_id"]):
        domain, temporal_class, priority, reason = infer_suggestion(row)
        route = route_for(domain)
        risk = leakage_flag(row)
        first, second = build_search_templates(row, route)
        output.append({
            "fact_id": row["fact_id"], "claim_text": row.get("claim_text"),
            "subject": row.get("subject", []), "predicate": row.get("predicate"),
            "object": row.get("object", []), "suggested_claim_domain": domain,
            "suggested_temporal_class": temporal_class,
            "suggested_source_route": route, "priority_band": priority,
            "leakage_flag": risk, "duplicate_candidate_id": duplicate_id_by_fact.get(row["fact_id"]),
            "confidence": confidence(domain, row.get("claim_text") or ""),
            "heuristic_reason": reason,
            "search_template_1": first, "search_template_2": second,
            "reviewer_final_class": None, "reviewer_status": "unreviewed",
            "reviewer_id": None, "reviewed_at": None, "reviewer_notes": None,
        })

    if check_only:
        assert len(output) == 136
        assert all(item["reviewer_status"] == "unreviewed" for item in output)
        assert all(item["suggested_temporal_class"] in schema["temporal_classes"] for item in output)
        assert all(item["suggested_claim_domain"] in schema["claim_domains"] for item in output)
        print("PASS triage assistant check: 136 suggestions, valid vocabulary, no final decisions")
        return

    OUTPUT_JSONL.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in output), encoding="utf-8")
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(output)
    DUPLICATES_PATH.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in duplicate_rows), encoding="utf-8")
    metrics = {
        "generated_records": len(output), "duplicate_groups": len(duplicate_rows),
        "leakage_suspected": sum(item["leakage_flag"] == "suspected" for item in output),
        "status_counts": {"unreviewed": len(output)},
        "suggested_domain_counts": {domain: sum(item["suggested_claim_domain"] == domain for item in output) for domain in sorted({item["suggested_claim_domain"] for item in output})},
        "final_decisions_made": 0, "inputs_unchanged": True,
        "schema_version": schema["schema_version"],
    }
    METRICS_PATH.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate inputs and suggestions without writing outputs")
    args = parser.parse_args()
    main(check_only=args.check)
