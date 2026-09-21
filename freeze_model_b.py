"""Freeze and reconcile the current Model B seed release."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_PATH = ROOT / "bcs_gk_facts.json"
MODEL_B_PATH = ROOT / "bcs_gk_facts_model_b.json"
MANIFEST_PATH = ROOT / "model_b_acceptance_manifest.json"
OUTPUT_PATH = ROOT / "model_b_release_manifest.json"
CUTOFF_DATE = "2023-04-19"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    model_b = json.loads(MODEL_B_PATH.read_text(encoding="utf-8"))
    acceptance = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    raw_uids = {fact["fact_uid"] for fact in raw}
    accepted_uids = {fact["fact_uid"] for fact in model_b}
    excluded_uids = {
        fact["fact_uid"] for fact in raw
        if fact.get("holdout_eligible") is False
    }
    recoverable_uids = raw_uids - accepted_uids - excluded_uids

    assert len(raw) == 616, f"unexpected raw corpus size: {len(raw)}"
    assert len(model_b) == 69, f"unexpected Model B size: {len(model_b)}"
    assert len(excluded_uids) == 20, f"unexpected permanent exclusions: {len(excluded_uids)}"
    assert not accepted_uids & excluded_uids, "accepted/excluded UID overlap"
    assert len(recoverable_uids) == 527, f"unexpected recoverable count: {len(recoverable_uids)}"
    assert acceptance["accepted_count"] == len(model_b)

    release = {
        "release_id": "model-b-seed-2026-09-16",
        "frozen_at": date.today().isoformat(),
        "cutoff_date": CUTOFF_DATE,
        "raw_corpus": {"file": RAW_PATH.name, "count": len(raw), "sha256": sha256(RAW_PATH)},
        "accepted_seed": {
            "file": MODEL_B_PATH.name,
            "count": len(model_b),
            "static_count": sum(f.get("temporal_class") == "static" for f in model_b),
            "dynamic_count": sum(f.get("temporal_class") == "dynamic" for f in model_b),
            "sha256": sha256(MODEL_B_PATH),
        },
        "permanently_holdout_excluded": {
            "count": len(excluded_uids),
            "fact_uids": sorted(excluded_uids),
        },
        "recoverable_resourcing_backlog": {
            "count": len(recoverable_uids),
            "fact_uids": sorted(recoverable_uids),
        },
        "reconciliation": f"{len(raw)} = {len(model_b)} accepted + {len(excluded_uids)} permanently excluded + {len(recoverable_uids)} recoverable",
        "source_acceptance_manifest_sha256": sha256(MANIFEST_PATH),
    }
    OUTPUT_PATH.write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "release_id": release["release_id"],
        "raw": len(raw),
        "accepted": len(model_b),
        "permanently_excluded": len(excluded_uids),
        "recoverable": len(recoverable_uids),
        "sha256": release["accepted_seed"]["sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
