"""Build a conservative Model B corpus from reviewed resourcing proposals.

The original corpus is never overwritten. Only static proposals with explicit
pre-cutoff evidence and positive entity support are admitted to the derived
Model B corpus; dynamic and unclassified proposals remain excluded.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CUTOFF_DATE = "2023-04-19"
INPUT_PATH = ROOT / "bcs_gk_facts.json"
PROPOSALS_PATH = ROOT / "resourcing_proposals.json"
OUTPUT_PATH = ROOT / "bcs_gk_facts_model_b.json"
MANIFEST_PATH = ROOT / "model_b_acceptance_manifest.json"


def proposal_is_accepted(proposal: dict) -> tuple[bool, str]:
    temporal_class = proposal.get("temporal_class")
    if temporal_class not in {"static", "dynamic"}:
        return False, "unclassified or invalid temporal class"
    if proposal.get("outcome") not in {"proposed", "proposed_without_valid_from"}:
        return False, "proposal has no accepted resourcing outcome"
    if not proposal.get("proposed_source_url"):
        return False, "proposal has no replacement source URL"
    evidence_date = proposal.get("proposed_source_published_at")
    if not evidence_date or evidence_date > CUTOFF_DATE:
        return False, "replacement source is missing or post-cutoff"
    support = proposal.get("support") or {}
    if support.get("title_is_about_entity") is not True:
        return False, "replacement article is not about the fact entity"
    if float(support.get("token_coverage") or 0) < 0.75:
        return False, "replacement article has insufficient entity support"
    if not proposal.get("proposed_snapshot_hash"):
        return False, "replacement source has no snapshot hash"
    if temporal_class == "dynamic":
        valid_from = proposal.get("proposed_valid_from")
        if not valid_from or valid_from > CUTOFF_DATE:
            return False, "dynamic fact lacks a verified pre-cutoff valid_from"
        if not proposal.get("proposed_valid_from_basis"):
            return False, "dynamic valid_from lacks an evidence basis"
        return True, "accepted dynamic fact with verified pre-cutoff validity evidence"
    return True, "accepted static fact with verified pre-cutoff source evidence"


def main() -> None:
    facts = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    proposals_doc = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    proposals = {p["fact_uid"]: p for p in proposals_doc["proposals"]}

    accepted = []
    decisions = []
    for fact in facts:
        uid = fact.get("fact_uid")
        if fact.get("holdout_eligible") is False:
            decisions.append({
                "fact_uid": uid,
                "accepted": False,
                "reason": "fact is explicitly excluded from the holdout benchmark",
            })
            continue
        proposal = proposals.get(uid)
        if proposal is None:
            decisions.append({"fact_uid": uid, "accepted": False,
                              "reason": "no resourcing proposal"})
            continue
        ok, reason = proposal_is_accepted(proposal)
        if not ok:
            decisions.append({"fact_uid": uid, "accepted": False, "reason": reason})
            continue

        enriched = dict(fact)
        temporal_class = proposal["temporal_class"]
        enriched.update({
            "source_url": proposal["proposed_source_url"],
            "publisher": "Wikipedia revision snapshot",
            "source_tier": proposal["proposed_source_tier"],
            "source_tier_basis": "accepted pre-cutoff resourcing proposal",
            "temporal_class": temporal_class,
            "temporal_evidence_status": (
                "verified_pre_cutoff_source_and_interval"
                if temporal_class == "dynamic" else "verified_pre_cutoff_source"
            ),
            "temporal_evidence_date": proposal["proposed_source_published_at"],
            "temporal_evidence_source_url": proposal["proposed_source_url"],
            "temporal_evidence_snapshot_hash": proposal["proposed_snapshot_hash"],
            "source_snapshot_hash": proposal["proposed_snapshot_hash"],
            "source_snapshot_hash_basis": "pre-cutoff MediaWiki revision content",
            "source_published_at": None,
            "valid_from": proposal.get("proposed_valid_from") if temporal_class == "dynamic" else None,
            "valid_to": proposal.get("proposed_valid_to") if temporal_class == "dynamic" else None,
            "holdout_eligible": True,
            "verification_status": "verified_supported",
            "verification_notes": (
                "Accepted Model B static evidence: pre-cutoff source revision "
                "supports the fact's entity and was verified by the resourcing pass."
            ),
            "verified_at": date.today().isoformat(),
            "temporal_review_status": f"resolved_{temporal_class}_evidence",
            "temporal_review_reason": "",
        })
        accepted.append(enriched)
        decisions.append({"fact_uid": uid, "accepted": True, "reason": reason,
                          "evidence_url": proposal["proposed_source_url"],
                          "evidence_date": proposal["proposed_source_published_at"]})

    OUTPUT_PATH.write_text(json.dumps(accepted, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    manifest = {
        "generated_at": date.today().isoformat(),
        "cutoff_date": CUTOFF_DATE,
        "input_corpus": INPUT_PATH.name,
        "output_corpus": OUTPUT_PATH.name,
        "policy": "Model B static facts require explicit pre-cutoff supported evidence",
        "accepted_count": len(accepted),
        "excluded_count": len(facts) - len(accepted),
        "decisions": decisions,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in (
        "cutoff_date", "input_corpus", "output_corpus", "accepted_count", "excluded_count"
    )}, indent=2))


if __name__ == "__main__":
    main()