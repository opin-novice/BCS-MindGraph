"""CLI tool: export accepted MCQs from episodic memory as a dataset."""

import json
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bcs.pipeline.episodic_store import EpisodicMemory


def export_json(memory: EpisodicMemory, min_quality: float, limit: int, output: str):
    rows = memory.export_dataset(min_quality=min_quality, limit=limit, include_feedback=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"Exported {len(rows)} rows → {output}")


def export_csv(memory: EpisodicMemory, min_quality: float, limit: int, output: str):
    rows = memory.export_dataset(min_quality=min_quality, limit=limit, include_feedback=True)
    if not rows:
        print("No rows to export.")
        return
    fieldnames = list(rows[0].keys())
    with open(output, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row["options"] = json.dumps(row.get("options", {}), ensure_ascii=False)
            writer.writerow(row)
    print(f"Exported {len(rows)} rows → {output}")


def print_stats(memory: EpisodicMemory, min_quality: float, limit: int):
    rows = memory.export_dataset(min_quality=min_quality, limit=limit, include_feedback=True)
    if not rows:
        print("No MCQs found meeting criteria.")
        return

    topics: dict = {}
    difficulties: dict = {}
    score_sum = 0.0

    for r in rows:
        t = r.get("topic", "Unknown")
        topics[t] = topics.get(t, 0) + 1
        d = r.get("difficulty", "unknown")
        difficulties[d] = difficulties.get(d, 0) + 1
        score_sum += r.get("quality_score", 0.0)

    print(f"\nDataset Stats (min_quality={min_quality})")
    print(f"  Total MCQs: {len(rows)}")
    print(f"  Avg quality_score: {score_sum / len(rows):.3f}")
    print(f"\n  By topic:")
    for t, c in sorted(topics.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c}")
    print(f"\n  By difficulty:")
    for d, c in sorted(difficulties.items(), key=lambda x: -x[1]):
        print(f"    {d}: {c}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export MCQ dataset from episodic memory")
    parser.add_argument("--format", choices=["json", "csv", "stats"], default="stats")
    parser.add_argument("--output", default="mcq_dataset.json")
    parser.add_argument("--min-quality", type=float, default=0.70)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--db-path", default="runtime/memory.db")
    args = parser.parse_args()

    memory = EpisodicMemory(args.db_path)

    if args.format == "json":
        export_json(memory, args.min_quality, args.limit, args.output)
    elif args.format == "csv":
        export_csv(memory, args.min_quality, args.limit, args.output)
    else:
        print_stats(memory, args.min_quality, args.limit)

    memory.close()
