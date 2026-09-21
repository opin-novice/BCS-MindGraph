"""Evidence-intake foundation for the locked 50-fact Model B pilot.

This tool does not browse, verify, accept, or merge facts. It creates a
reviewer queue and validates manually supplied candidate evidence records.
All ledger writes are append-only and remain pending reviewer approval.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
PILOT_PATH = WORKFLOW / "pilot_batch_final.jsonl"
SCHEMA_PATH = WORKFLOW / "workflow_schemas.json"
EVIDENCE_PATH = WORKFLOW / "evidence_ledger.jsonl"
LINKS_PATH = WORKFLOW / "fact_evidence_links.jsonl"
ROUTING_OVERRIDES_PATH = WORKFLOW / "batch02_reclassification_ra1.jsonl"
QUEUE_CSV = WORKFLOW / "pilot_evidence_work_queue.csv"
REVIEW_QUEUE_CSV = WORKFLOW / "pilot_evidence_review_queue.csv"
INTAKE_LOG = WORKFLOW / "pilot_evidence_intake_log.jsonl"
METRICS_PATH = WORKFLOW / "pilot_evidence_intake_metrics.json"
CUTOFF = "2023-04-19"
FORBIDDEN_ACCEPTANCE = {"accepted_for_build", "verified_supported", "accepted", "resolved"}

SOURCE_TYPES = {
    "mediawiki_pre_cutoff_revision_or_official_history": [
        "mediawiki_revision", "official_history", "archived_official_page"
    ],
    "mediawiki_pre_cutoff_revision_or_official_geography": [
        "mediawiki_revision", "official_geographic_record", "archived_official_page"
    ],
    "official_institutional_history": [
        "official_history", "annual_report", "archived_official_page"
    ],
    "pre_cutoff_official_sports_or_cultural_reference": [
        "official_sports_record", "government_news_agency_report", "official_cultural_reference", "archived_official_page"
    ],
    "pre_cutoff_official_or_archived_institutional_reference": [
        "official_history", "archived_official_page", "mediawiki_revision"
    ],
    "pre_cutoff_official_or_mediawiki_geographic_reference": [
        "official_geographic_record", "mediawiki_revision", "archived_official_page"
    ],
    "official_appointment_or_gazette": [
        "official_gazette_pdf", "appointment_notice", "archived_official_page"
    ],
    "legal_or_policy_document": [
        "official_gazette_pdf", "law_or_code", "ministry_notification", "treaty_document"
    ],
    "bbs_or_official_statistical_report": [
        "official_statistical_report", "central_bank_report", "annual_report"
    ],
    "official_award_or_membership_record": [
        "official_award_record", "treaty_document", "official_gazette_pdf"
    ],
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def date_key(value: str | None) -> str:
    if not value:
        return ""
    parts = value.split("-")
    return "-".join(parts + ["00"] * (3 - len(parts)))


def valid_date(value: str | None) -> bool:
    if value in (None, ""):
        return True
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return False
    try:
        datetime.strptime(value, "%Y" if len(value) == 4 else "%Y-%m" if len(value) == 7 else "%Y-%m-%d")
    except ValueError:
        return False
    return True


def safe_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def query_templates(row: dict) -> list[str]:
    claim = str(row.get("claim_text") or "")[:140]
    route = str(row.get("source_route") or "").replace("_", " ")
    return [f'"{claim}" pre-cutoff {route}', f'"{claim[:90]}" official source {CUTOFF}']


def load_inputs() -> tuple[list[dict], dict]:
    pilot = read_jsonl(PILOT_PATH)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert len(pilot) == 50, f"locked pilot must contain 50 facts, found {len(pilot)}"
    assert len({row["fact_id"] for row in pilot}) == 50, "pilot fact IDs must be unique"
    if ROUTING_OVERRIDES_PATH.exists():
        overrides = {
            row["fact_id"]: row for row in read_jsonl(ROUTING_OVERRIDES_PATH)
            if row.get("decision") not in {"remove_from_static_geographic_pilot", "hold_before_source_search"}
        }
        for row in pilot:
            override = overrides.get(row["fact_id"])
            if override:
                row["reviewer_final_class"] = override["reviewed_route"]
                row["source_route"] = {
                    "static_cultural_or_national_symbol": "pre_cutoff_official_sports_or_cultural_reference",
                    "static_institutional_geographic": "pre_cutoff_official_or_archived_institutional_reference",
                }.get(override["reviewed_route"], row["source_route"])
    return pilot, schema


def build_queue() -> list[dict]:
    pilot, _ = load_inputs()
    rows = []
    for row in pilot:
        route = row["source_route"]
        temporal_class = "dynamic" if row["reviewer_final_class"].startswith("dynamic_") else "static"
        required = ["temporal_evidence_date", "supporting_excerpt", "content_hash_sha256"]
        if temporal_class == "dynamic":
            required += ["valid_from", "valid_to"]
        rows.append({
            "pilot_id": row["pilot_id"], "fact_id": row["fact_id"],
            "claim_text": row["claim_text"], "final_routing_class": row["reviewer_final_class"],
            "temporal_class": temporal_class, "source_route": route,
            "allowed_source_types": ";".join(SOURCE_TYPES.get(route, ["manual_review"])),
            "required_temporal_fields": ";".join(required),
            "source_query_template_1": query_templates(row)[0],
            "source_query_template_2": query_templates(row)[1],
            "intake_status": "not_started", "evidence_verified": "false",
            "model_b_merge_allowed": "false",
        })
    return rows


def write_queue(rows: list[dict]) -> None:
    fields = list(rows[0].keys())
    for path in (QUEUE_CSV, REVIEW_QUEUE_CSV):
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def next_id(path: Path, prefix: str) -> str:
    existing = read_jsonl(path) if path.exists() else []
    return f"{prefix}_{len(existing) + 1:06d}"


def validate_candidate(candidate: dict, pilot_by_id: dict) -> tuple[bool, str]:
    fact_id = candidate.get("fact_id")
    row = pilot_by_id.get(fact_id)
    if row is None:
        return False, "fact_id is not in the locked pilot"
    for field in ("canonical_url", "source_type", "source_authority", "temporal_evidence_date", "retrieved_at", "content_hash_sha256", "supporting_excerpt"):
        if not candidate.get(field):
            return False, f"missing required field: {field}"
    if candidate.get("source_type") not in SOURCE_TYPES.get(row["source_route"], []):
        return False, "source_type is not allowed for this routing class"
    if not valid_date(candidate.get("temporal_evidence_date")):
        return False, "invalid temporal_evidence_date"
    if date_key(candidate["temporal_evidence_date"]) > date_key(CUTOFF):
        return False, "temporal evidence is post-cutoff"
    if not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", str(candidate["content_hash_sha256"])):
        return False, "content_hash_sha256 must be a 64-hex SHA-256 digest"
    if candidate.get("source_published_at") and not valid_date(candidate["source_published_at"]):
        return False, "invalid source_published_at"
    if row["reviewer_final_class"].startswith("dynamic_"):
        if not candidate.get("valid_from") or not valid_date(candidate.get("valid_from")):
            return False, "dynamic evidence requires valid_from"
        if date_key(candidate["valid_from"]) > date_key(CUTOFF):
            return False, "dynamic valid_from is post-cutoff"
        if candidate.get("valid_to") and not valid_date(candidate["valid_to"]):
            return False, "invalid valid_to"
        if candidate.get("valid_to") and not date_key(candidate["valid_to"]) > date_key(CUTOFF):
            return False, "dynamic interval does not cover cutoff"
    if candidate.get("semantic_verdict") in FORBIDDEN_ACCEPTANCE or candidate.get("verification_status") in FORBIDDEN_ACCEPTANCE:
        return False, "intake cannot write an acceptance verdict"
    for field in ("subject_match", "predicate_match", "object_match"):
        if candidate.get(field) is not True:
            return False, f"{field} must be true before reviewer review"
    if candidate.get("leakage_verdict") not in {"pass", "suspected", "not_yet_checked"}:
        return False, "invalid leakage_verdict"
    return True, "candidate accepted into pending-review intake only"


def append_candidate(candidate: dict) -> None:
    pilot, _ = load_inputs()
    pilot_by_id = {row["fact_id"]: row for row in pilot}
    ok, reason = validate_candidate(candidate, pilot_by_id)
    log = {"candidate": candidate, "accepted_to_ledger": ok, "reason": reason, "logged_at": datetime.now().isoformat()}
    with INTAKE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(log, ensure_ascii=False, sort_keys=True) + "\n")
    if not ok:
        raise ValueError(reason)
    evidence_id = next_id(EVIDENCE_PATH, "EV_PILOT")
    link_id = next_id(LINKS_PATH, "LINK_PILOT")
    evidence = {
        "evidence_id": evidence_id, "evidence_version": 1,
        "source_type": candidate["source_type"], "source_authority": candidate["source_authority"],
        "publisher": candidate.get("publisher"), "document_title": candidate.get("document_title"),
        "canonical_url": candidate["canonical_url"], "archive_url": candidate.get("archive_url"),
        "source_published_at": candidate.get("source_published_at"),
        "temporal_evidence_date": candidate["temporal_evidence_date"],
        "archive_capture_timestamp": candidate.get("archive_capture_timestamp"),
        "retrieved_at": candidate["retrieved_at"], "content_hash_sha256": candidate["content_hash_sha256"],
        "document_locator": candidate.get("document_locator"), "access_status": "retrieved",
        "admissibility_status": "pending_reviewer", "notes": candidate.get("notes"),
    }
    link = {
        "link_id": link_id, "fact_id": candidate["fact_id"], "evidence_id": evidence_id,
        "support_role": candidate.get("support_role", "candidate"),
        "supporting_excerpt": candidate["supporting_excerpt"],
        "subject_match": True, "predicate_match": True, "object_match": True,
        "semantic_verdict": "pending_reviewer",
        "temporal_field_name": candidate.get("temporal_field_name"),
        "temporal_raw_value": candidate.get("temporal_raw_value"),
        "valid_from": candidate.get("valid_from"), "valid_to": candidate.get("valid_to"),
        "temporal_verdict": "pending_reviewer", "leakage_verdict": candidate.get("leakage_verdict", "not_yet_checked"),
        "review_status": "pending", "reviewer_id": None,
    }
    with EVIDENCE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n")
    with LINKS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(link, ensure_ascii=False, sort_keys=True) + "\n")
    pending_count = len(read_jsonl(EVIDENCE_PATH))
    METRICS_PATH.write_text(json.dumps({
        "pilot_count": 50,
        "queue_status": "evidence_candidates_intaked",
        "candidate_evidence_rows": pending_count,
        "pending_reviewer_rows": pending_count,
        "evidence_verified": False,
        "model_b_merge_performed": False,
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"accepted_to_ledger": True, "evidence_id": evidence_id, "link_id": link_id, "status": "pending_reviewer"}, indent=2))


def check() -> None:
    pilot, schema = load_inputs()
    rows = build_queue()
    assert len(rows) == 50
    assert all(row["intake_status"] == "not_started" for row in rows)
    assert all(row["model_b_merge_allowed"] == "false" for row in rows)
    assert schema["cutoff_date"] == CUTOFF
    for path in (EVIDENCE_PATH, LINKS_PATH):
        assert path.exists()
    print("PASS pilot intake check: 50 locked IDs, append-only ledgers, no acceptance fields")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--build-queue", action="store_true")
    parser.add_argument("--candidate", type=Path, help="JSON file containing one manually prepared candidate")
    args = parser.parse_args()
    if args.check:
        check()
    if args.build_queue or not args.check and not args.candidate:
        write_queue(build_queue())
        METRICS_PATH.write_text(json.dumps({"pilot_count": 50, "queue_status": "not_started", "candidate_evidence_rows": 0, "evidence_verified": False, "model_b_merge_performed": False}, indent=2) + "\n", encoding="utf-8")
        print("Wrote pilot evidence work and review queues for 50 facts")
    if args.candidate:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        append_candidate(candidate)


if __name__ == "__main__":
    main()
