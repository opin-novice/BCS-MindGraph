"""
task2c_pilot_batch_run.py
=========================
Task 2C -- Full Pilot Batch Generation across all 11 topics of the frozen
Model B seed.

This script executes Task 2C:
1. Verifies frozen seed sha256 checksum against model_b_release_manifest.json.
2. Runs strict temporal guard across all 11 topics at cutoff 2023-04-19.
3. Runs FactQualityGate to score and classify MCQ suitability.
4. Retrieves MCQ-ready facts across all 11 topics at the cutoff.
5. Invokes MCQGenerator (Qwen2.5-72B-Instruct) with updated prompt constraints.
6. Evaluates all generated MCQs using RuleBasedScreener and MCQQualityEvaluator.
7. Logs detailed provenance records to model_b_workflow/task2c_pilot_batch.jsonl
   and summary manifest to model_b_workflow/task2c_pilot_batch_manifest.json.

    python task2c_pilot_batch_run.py --dry-run
    python task2c_pilot_batch_run.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import inspect
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os                                                  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "model_b_workflow"

DEFAULT_TAG = "task2c_pilot"
CUTOFF = "2023-04-19"
SEED_FILE = "bcs_gk_facts_model_b.json"


def run_paths(tag: str) -> tuple[Path, Path]:
    return (OUT_DIR / ("%s_batch.jsonl" % tag),
            OUT_DIR / ("%s_manifest.json" % tag))


def prompt_fingerprint() -> dict:
    import mcq_generator as mg

    out = {}
    for name, cls in (("challenger", mg.ChallengerAgent),
                      ("reasoner", mg.ReasonerAgent),
                      ("judge", mg.JudgeAgent)):
        try:
            src = inspect.getsource(cls)
            out[name] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        except OSError:
            out[name] = None
    return out


def build_kg():
    from kg_builder import KnowledgeGraphBuilder

    seed = json.loads((ROOT / SEED_FILE).read_text(encoding="utf-8"))
    kg = KnowledgeGraphBuilder()
    by_fid = {}
    topics = {}
    for raw in seed:
        fid = kg.insert_fact_pipeline(
            fact_text=raw["fact_text"],
            subject_entities=[tuple(p) for p in raw.get("subject_entities") or []],
            object_entities=[tuple(p) for p in raw.get("object_entities") or []],
            topic=raw["topic"],
            source_url=raw.get("source_url") or "",
            publisher=raw.get("publisher", ""),
            relation=raw.get("relation"),
            valid_from=raw.get("valid_from"),
            valid_to=raw.get("valid_to"),
            source_published_at=raw.get("source_published_at"),
            source_tier=raw.get("source_tier"),
            status=raw.get("status", "accepted"),
            temporal_class=raw.get("temporal_class"),
            temporal_evidence_status=raw.get("temporal_evidence_status"),
            temporal_evidence_date=raw.get("temporal_evidence_date"),
            temporal_evidence_source_url=raw.get("temporal_evidence_source_url"),
            temporal_evidence_snapshot_hash=raw.get("temporal_evidence_snapshot_hash"),
        )
        for key in ("temporal_class", "temporal_evidence_status",
                    "temporal_evidence_date", "temporal_evidence_source_url",
                    "temporal_evidence_snapshot_hash"):
            if key in raw:
                kg.update_fact_attribute(fid, key, raw[key])
        by_fid[fid] = raw
        topics.setdefault(raw["topic"], []).append(fid)
    return kg, by_fid, topics, seed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts-per-topic", type=int, default=3,
                    help="max facts per topic to use for generation")
    ap.add_argument("--difficulty", default="medium",
                    choices=["easy", "medium", "hard"])
    ap.add_argument("--dry-run", action="store_true",
                    help="run every gate but stop before model calls")
    ap.add_argument("--tag", default=DEFAULT_TAG,
                    help="output tag; writes <tag>_batch.jsonl and <tag>_manifest.json")
    args = ap.parse_args(argv)

    RUN_LOG, RUN_MANIFEST = run_paths(args.tag)

    started = dt.datetime.now().isoformat(timespec="seconds")
    t0 = time.time()

    # -- Gate 1: Check seed sha256 -----------------------------------------
    seed_sha = hashlib.sha256((ROOT / SEED_FILE).read_bytes()).hexdigest()
    manifest = json.loads(
        (ROOT / "model_b_release_manifest.json").read_text(encoding="utf-8"))
    expected_sha = manifest["accepted_seed"]["sha256"]
    if seed_sha != expected_sha:
        print("ABORT: seed sha256 does not match the release manifest.")
        print("  on disk : %s" % seed_sha)
        print("  manifest: %s" % expected_sha)
        return 1
    print("[1/6] seed checksum matches release manifest")

    kg, by_fid, topics, seed = build_kg()
    print("[2/6] seed loaded: %d facts across %d topics" % (len(seed), len(topics)))

    # -- Gate 2: Fact quality pipeline --------------------------------------
    from fact_quality import FactQualityGate
    FactQualityGate(kg).run_quality_pipeline(
        extraction_date=dt.date.today().isoformat())
    print("[3/6] fact quality gate complete")

    # -- Gate 3: Strict temporal guard across all topics ---------------------
    violations = {}
    for topic in topics:
        bad = kg.strict_temporal_guard(topic, CUTOFF,
                                       allow_static_source_evidence=True)
        if bad:
            violations[topic] = bad
    if violations:
        print("ABORT: strict temporal guard failed: %s" % violations)
        return 1
    print("[4/6] strict temporal guard PASS across %d topics" % len(topics))

    # -- Retrieval across all topics ----------------------------------------
    from mcq_generator import MCQGenerator, facts_from_kg
    from mcq_quality import RuleBasedScreener
    from rejection_taxonomy import map_codes

    topic_batches = {}
    total_facts_offered = 0
    total_facts_available = 0

    print("[5/6] retrieving MCQ-ready facts across all topics:")
    sorted_topics = sorted(topics.keys())
    for topic in sorted_topics:
        ready = facts_from_kg(kg, topic, as_of=CUTOFF,
                              allow_static_source_evidence=True)
        batch = ready[:args.facts_per_topic]
        total_facts_available += len(ready)
        if batch:
            # Check for unversioned leakage
            leaked = [f["fact_id"] for f in batch
                      if f.get("temporal_status") == "unversioned"]
            if leaked:
                print("ABORT: unversioned fact(s) in topic %r: %s" % (topic, leaked))
                return 1
            topic_batches[topic] = (ready, batch)
            total_facts_offered += len(batch)
            print("        %-22s : %d available, using %d" % (topic, len(ready), len(batch)))

    model_id = os.getenv("HF_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    provenance_common = {
        "run_tag": args.tag,
        "run_started_at": started,
        "cutoff": CUTOFF,
        "seed_file": SEED_FILE,
        "seed_sha256": seed_sha,
        "seed_release_id": manifest.get("release_id"),
        "difficulty": args.difficulty,
        "model_id": model_id,
        "hf_provider": os.getenv("HF_PROVIDER", "auto"),
        "prompt_fingerprint": prompt_fingerprint(),
        "generator_module_sha256": hashlib.sha256(
            (ROOT / "mcq_generator.py").read_bytes()).hexdigest()[:16],
    }

    if args.dry_run:
        print("[6/6] --dry-run: stopping before model calls")
        summary_dry = {
            **provenance_common,
            "topics_count": len(topic_batches),
            "total_facts_offered": total_facts_offered,
            "total_facts_available": total_facts_available,
        }
        print(json.dumps(summary_dry, ensure_ascii=False, indent=2))
        return 0

    api_key = (os.getenv("HF_API_KEY") or os.getenv("HF_API_TOKEN")
               or os.getenv("HF_TOKEN") or "").strip()
    if not api_key:
        print("ABORT: no HF key in environment. Generation not attempted.")
        return 1

    generator = MCQGenerator(hf_api_key=api_key, model=model_id,
                             seen_questions_path="seen_questions.json")
    screener = RuleBasedScreener()

    print("[6/6] starting pilot batch generation across %d topics..." % len(topic_batches))

    all_mcq_rows = []
    topic_summaries = {}
    rejection_code_counts = {}

    total_mcqs_generated = 0
    total_mcqs_accepted = 0
    total_screener_passed = 0

    for topic, (ready, batch) in topic_batches.items():
        print("\n  --> Topic: %s (%d facts)" % (topic, len(batch)))
        t_start = time.time()
        result = generator.generate_from_facts(
            batch, difficulty=args.difficulty, topic=topic)
        t_elapsed = round(time.time() - t_start, 1)

        mcqs = result.mcqs
        total_mcqs_generated += len(mcqs)
        accepted_count = len(mcqs)
        total_mcqs_accepted += accepted_count

        # Screen each MCQ through RuleBasedScreener
        mcq_dicts = [{
            "mcq_id": m.mcq_id, "fact_id": m.fact_id,
            "question": m.question,
            "options": {o.key: o.text for o in m.options},
            "correct_answer": m.correct_answer,
            "difficulty": m.difficulty,
            "question_type": m.question_type,
            "explanation": m.explanation,
        } for m in mcqs]

        seed_by_text = {f["fact_id"]: f for f in batch}
        screener_passed_topic = 0

        for mcq, mcq_dict in zip(mcqs, mcq_dicts):
            screener_score, screener_codes = screener.screen(mcq_dict, batch)
            screener_pass = screener_score >= 0.70 and not any(
                c in ("MISSPELLING", "ASCII_DIGITS", "SYNONYM_DISTRACTOR",
                      "MISTRANSLATION", "DROPPED_QUALIFIER", "NEAR_DUPLICATE")
                for c in screener_codes
            )
            if screener_pass:
                screener_passed_topic += 1
                total_screener_passed += 1

            for code in screener_codes:
                rejection_code_counts[code] = rejection_code_counts.get(code, 0) + 1

            kg_fact = seed_by_text.get(mcq.fact_id, {})
            raw = by_fid.get(mcq.fact_id, {})

            row = {
                "record_type": "%s_generated_mcq" % args.tag,
                **provenance_common,
                "topic": topic,
                "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "generation_seconds": t_elapsed,
                "episode_id": result.episode_id,
                "mcq_id": mcq.mcq_id,
                "kg_fact_id": mcq.fact_id,
                "seed_fact_uid": raw.get("fact_uid"),
                "seed_corpus_id": raw.get("_corpus_id"),
                "fact_text": raw.get("fact_text"),
                "temporal_status": kg_fact.get("temporal_status"),
                "temporal_class": raw.get("temporal_class"),
                "temporal_evidence_status": raw.get("temporal_evidence_status"),
                "temporal_evidence_date": raw.get("temporal_evidence_date"),
                "temporal_evidence_source_url": raw.get("temporal_evidence_source_url"),
                "temporal_evidence_snapshot_hash": raw.get("temporal_evidence_snapshot_hash"),
                "valid_from": raw.get("valid_from"),
                "valid_to": raw.get("valid_to"),
                "question": mcq.question,
                "options": [{"key": o.key, "text": o.text} for o in mcq.options],
                "correct_answer": mcq.correct_answer,
                "question_type": mcq.question_type,
                "explanation": mcq.explanation,
                "self_reported_difficulty": mcq.self_reported_difficulty,
                "generator_scores": {
                    "quality_score": round(mcq.quality_score, 3),
                    "grounded": mcq.grounded,
                    "grounding": round(mcq._grounding_score, 3),
                    "distractor": round(mcq._distractor_score, 3),
                    "clarity": round(mcq._clarity_score, 3),
                    "regeneration_round": mcq.regeneration_round,
                },
                "screener_verdict": {
                    "screener_score": round(screener_score, 3),
                    "passed": screener_pass,
                    "failure_codes": screener_codes,
                },
                "accepted_for_benchmark": False,
                "acceptance_note": "Task 2C Pilot Batch output.",
            }
            all_mcq_rows.append(row)

        topic_summaries[topic] = {
            "facts_used": len(batch),
            "mcqs_generated": len(mcqs),
            "screener_passed": screener_passed_topic,
            "seconds": t_elapsed,
        }
        print("      %d MCQ(s) generated, %d passed quality screener in %.1fs"
              % (len(mcqs), screener_passed_topic, t_elapsed))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("w", encoding="utf-8") as fh:
        for row in all_mcq_rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    mapped_taxonomy = map_codes(list(rejection_code_counts.keys()))

    acceptance_rate = round(
        (total_screener_passed / total_mcqs_generated * 100)
        if total_mcqs_generated > 0 else 0.0, 1
    )

    run_manifest = {
        "record_type": "%s_manifest" % args.tag,
        **provenance_common,
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "wall_seconds": round(time.time() - t0, 1),
        "total_topics": len(topic_batches),
        "facts_offered": total_facts_offered,
        "facts_available_at_cutoff": total_facts_available,
        "total_mcqs_generated": total_mcqs_generated,
        "total_mcqs_screener_passed": total_screener_passed,
        "acceptance_rate_pct": acceptance_rate,
        "topic_summaries": topic_summaries,
        "rejection_taxonomy": {
            "code_counts": rejection_code_counts,
            "mapped_taxonomy_codes": mapped_taxonomy,
        },
        "temporal_firewall": {
            "unversioned_in_batch": 0,
            "enforced_by": "mcq_generator.generate_from_facts & strict_temporal_guard",
        },
        "seed_modified": False,
        "accepted_for_benchmark": False,
    }
    RUN_MANIFEST.write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    generator.save_seen_questions()

    print("\n" + "=" * 65)
    print("  TASK 2C PILOT BATCH SUMMARY")
    print("=" * 65)
    print("  Total Topics Handled    : %d" % len(topic_batches))
    print("  Total Facts Offered     : %d (out of %d available)"
          % (total_facts_offered, total_facts_available))
    print("  Total MCQs Generated    : %d" % total_mcqs_generated)
    print("  Screener Passed MCQs    : %d" % total_screener_passed)
    print("  Acceptance Rate         : %.1f%%" % acceptance_rate)
    print("  Rejection Taxonomy      : %s" % (rejection_code_counts or "Zero Defect"))
    print("  Total Wall Time         : %.1fs" % (time.time() - t0))
    print("\n  Output files:")
    print("    JSONL Records : %s" % RUN_LOG)
    print("    Manifest File : %s" % RUN_MANIFEST)
    print("=" * 65)

    return 0


if __name__ == "__main__":
    sys.exit(main())
