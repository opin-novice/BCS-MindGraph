"""Derive dynamic valid_from values from cached structured source evidence only."""

from __future__ import annotations

import json
from pathlib import Path

from resource_corpus import _cache_get, valid_from_from_infobox

ROOT = Path(__file__).resolve().parent
CUTOFF_DATE = "2023-04-19"
CORPUS_PATH = ROOT / "bcs_gk_facts.json"
PROPOSALS_PATH = ROOT / "resourcing_proposals.json"


def main() -> None:
    facts = {fact["fact_uid"]: fact for fact in json.loads(
        CORPUS_PATH.read_text(encoding="utf-8")
    )}
    document = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    updated = 0
    checked = 0

    for proposal in document["proposals"]:
        if proposal.get("temporal_class") != "dynamic":
            continue
        if not proposal.get("proposed_source_url") or not proposal.get("resolved_title"):
            continue
        checked += 1
        cached = _cache_get(proposal.get("wiki_lang", "en"), proposal["resolved_title"])
        if not cached or cached.get("error") or not cached.get("content"):
            continue
        fact = facts.get(proposal.get("fact_uid"))
        if not fact:
            continue
        valid_from, basis = valid_from_from_infobox(
            fact, cached["content"], proposal.get("support") or {}
        )
        if not valid_from or valid_from > CUTOFF_DATE:
            continue
        proposal["proposed_valid_from"] = valid_from
        proposal["proposed_valid_from_basis"] = basis
        proposal["outcome"] = "proposed"
        updated += 1

    PROPOSALS_PATH.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"dynamic_checked": checked, "dynamic_validity_added": updated}, indent=2))


if __name__ == "__main__":
    main()
