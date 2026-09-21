"""Add auditable provenance and temporal-review metadata to the BCS facts corpus."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
CORPUS_PATH = ROOT / "bcs_gk_facts.json"
REPORT_PATH = ROOT / "temporal_metadata_review_report.json"
OBSERVED_AT = date.today().isoformat()

SOURCE_TIER_MAP = {
    "gov.bd": 1,
    "mil.bd": 1,
    "edu.bd": 1,
    "ac.bd": 1,
    "org.bd": 1,
    "un.org": 2,
    "worldbank.org": 2,
    "who.int": 2,
    "imf.org": 2,
    "unesco.org": 2,
    "reliefweb.int": 2,
    "banglapedia.org": 3,
    "nationalgeographic.com": 3,
    "ethnologue.com": 3,
    "icc-cricket.com": 3,
    "espncricinfo.com": 3,
    "reuters.com": 4,
    "bbc.com": 4,
    "bdnews24.com": 4,
    "lonelyplanet.com": 4,
    "blogspot.com": 5,
}

YEAR_RE = re.compile(r"(?<!\d)(?:1[0-9]{3}|20[0-2][0-9])(?!\d)")


def source_tier(url: str) -> tuple[int, str]:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    for domain, tier in SOURCE_TIER_MAP.items():
        if host == domain or host.endswith("." + domain):
            return tier, "domain matched project source-tier policy"
    return 4, "unmapped domain; conservative default, requires review"


def source_hash(url: str, text: str) -> str:
    return hashlib.sha256(f"{url}|{text}".encode("utf-8")).hexdigest()[:16]


def main() -> None:
    facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    review_ids = []
    tier_counts: dict[str, int] = {}
    explicit_year_count = 0

    for index, fact in enumerate(facts, start=1):
        text = str(fact.get("fact_text", ""))
        url = str(fact.get("source_url", ""))
        tier, tier_basis = source_tier(url)
        years = sorted(set(YEAR_RE.findall(text)))
        if years:
            explicit_year_count += 1

        fact["relation"] = fact.get("relation") or "STATED_AS"
        fact["source_tier"] = fact.get("source_tier") or tier
        fact["source_tier_basis"] = tier_basis
        fact["source_snapshot_hash"] = source_hash(url, text)
        fact["observed_at"] = fact.get("observed_at") or OBSERVED_AT

        # Do not promote an event year to valid_from or confuse it with
        # source publication date. Keep candidates visible for human review.
        fact["valid_from"] = fact.get("valid_from")
        fact["valid_to"] = fact.get("valid_to")
        fact["source_published_at"] = fact.get("source_published_at")
        fact["temporal_candidate_years"] = years
        fact["temporal_review_status"] = (
            "needs_source_date_verification"
            if not fact["source_published_at"]
            else "needs_validity_review"
        )
        fact["temporal_review_reason"] = (
            "No verified source publication date is present; explicit fact years "
            "are retained as candidates only."
        )
        review_ids.append(fact.get("fact_id", f"CORPUS_INDEX_{index:04d}"))
        tier_counts[str(tier)] = tier_counts.get(str(tier), 0) + 1

    CORPUS_PATH.write_text(
        json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "corpus": CORPUS_PATH.name,
        "observed_at": OBSERVED_AT,
        "total_facts": len(facts),
        "facts_with_source_published_at": sum(
            bool(f.get("source_published_at")) for f in facts
        ),
        "facts_with_valid_from": sum(bool(f.get("valid_from")) for f in facts),
        "facts_with_explicit_year_candidates": explicit_year_count,
        "facts_requiring_temporal_review": len(review_ids),
        "source_tier_counts": tier_counts,
        "strict_cutoff_ready": False,
        "strict_cutoff_blocker": (
            "No verified source publication dates or validity intervals were present "
            "in the input corpus; no dates were invented."
        ),
        "review_fact_ids": review_ids,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "review_fact_ids"}, indent=2))


if __name__ == "__main__":
    main()