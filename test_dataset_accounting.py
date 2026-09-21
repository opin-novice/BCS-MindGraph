"""Regression checks for the frozen Model B dataset partitions."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    raw = json.loads((ROOT / "bcs_gk_facts.json").read_text(encoding="utf-8"))
    seed = json.loads((ROOT / "bcs_gk_facts_model_b.json").read_text(encoding="utf-8"))
    backlog = read_jsonl(ROOT / "model_b_workflow" / "recoverable_backlog.jsonl")
    excluded = read_jsonl(ROOT / "model_b_workflow" / "permanently_excluded.jsonl")
    triage = read_jsonl(ROOT / "model_b_workflow" / "triage_decisions.jsonl")

    raw_ids = {fact["fact_uid"] for fact in raw}
    seed_ids = {fact["fact_uid"] for fact in seed}
    backlog_ids = {row["fact_id"] for row in backlog}
    excluded_ids = {row["fact_id"] for row in excluded}

    assert len(raw) == 616
    assert len(seed) == 69
    assert len(excluded) == 20
    assert len(backlog) == 527
    assert seed_ids.isdisjoint(excluded_ids)
    assert seed_ids.isdisjoint(backlog_ids)
    assert excluded_ids.isdisjoint(backlog_ids)
    assert seed_ids | excluded_ids | backlog_ids == raw_ids
    assert seed_ids | excluded_ids | backlog_ids == raw_ids
    assert len(triage) == sum(
        row["suggested_temporal_class"] == "needs_clarification" for row in backlog
    )
    assert len(read_jsonl(ROOT / "model_b_workflow" / "pilot_batch.jsonl")) == 50
    print("PASS dataset accounting: 616 = 69 + 20 + 527; zero partition overlap")
    print(f"PASS triage queue: {len(triage)} clarification records")
    print("PASS pilot batch: 50 records; seed merge: false")


if __name__ == "__main__":
    main()
