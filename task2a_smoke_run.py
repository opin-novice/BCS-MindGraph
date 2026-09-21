"""
task2a_smoke_run.py
===================
Task 2A -- one bounded, fully-logged generation batch over the verified
Model B seed.

This is a smoke run, not the experiment. It exists to answer one question:
does a fact travel from the frozen seed, through the guard and the quality
gate, into a generated MCQ, with its lineage intact at every step?

Bounded on purpose
------------------
One topic, ``--facts`` facts (default 2), one difficulty. Small enough that
every generated item can be read by a person, and cheap enough to re-run.
Expand only after the provenance record is confirmed correct.

What is recorded per MCQ
------------------------
seed fact uid, topic, cutoff, temporal_status, evidence url/date, model id
and provider, prompt fingerprint, timestamps, the generator's own judge
scores, and the independent quality-gate verdict. An MCQ whose lineage
cannot be reconstructed from that record is not usable as benchmark output,
so the record is the deliverable as much as the question is.

    python task2a_smoke_run.py --topic Geography --facts 2
    python task2a_smoke_run.py --dry-run     # everything except the model call
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

DEFAULT_TAG = "task2a"


def run_paths(tag: str) -> tuple:
    """
    (jsonl, manifest) for a run tag.

    The log is APPENDED to and the manifest is OVERWRITTEN, so a second run
    under the same tag rewrites the first run's manifest and leaves its MCQ
    records behind with no manifest describing them. The Task 2A records are
    also the regression fixture for test_task2b_quality_gate.py, and a run
    that grew that file would break the fixture silently. Hence a tag: each
    run writes its own pair, and `task2a` stays exactly as the PI signed it
    off.
    """
    return (OUT_DIR / ("%s_smoke_run.jsonl" % tag),
            OUT_DIR / ("%s_smoke_run_manifest.json" % tag))

CUTOFF = "2023-04-19"
SEED_FILE = "bcs_gk_facts_model_b.json"


def prompt_fingerprint() -> dict:
    """
    A content hash of each agent's prompt code.

    The project has no prompt version string, and inventing one that nobody
    updates would be worse than none. Hashing the source means the recorded
    version changes exactly when the prompts change.
    """
    import mcq_generator as mg

    out = {}
    for name, cls in (("challenger", mg.ChallengerAgent),
                      ("reasoner", mg.ReasonerAgent),
                      ("judge", mg.JudgeAgent)):
        try:
            src = inspect.getsource(cls)
            out[name] = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        except OSError:                                    # pragma: no cover
            out[name] = None
    return out


def build_kg():
    """Load the frozen seed exactly as main-pipeline.py does."""
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
    ap.add_argument("--topic", default="Geography")
    ap.add_argument("--facts", type=int, default=2,
                    help="how many facts to generate from (keep this small)")
    ap.add_argument("--difficulty", default="medium",
                    choices=["easy", "medium", "hard"])
    ap.add_argument("--dry-run", action="store_true",
                    help="run every gate but stop before the model call")
    ap.add_argument("--tag", default=DEFAULT_TAG,
                    help="output tag; writes <tag>_smoke_run.jsonl and "
                         "<tag>_smoke_run_manifest.json. Use a new tag per "
                         "run so an earlier run's manifest is not overwritten.")
    args = ap.parse_args(argv)

    RUN_LOG, RUN_MANIFEST = run_paths(args.tag)

    started = dt.datetime.now().isoformat(timespec="seconds")
    t0 = time.time()

    # -- Gate 1: the seed is the one that was signed off --------------------
    seed_sha = hashlib.sha256((ROOT / SEED_FILE).read_bytes()).hexdigest()
    manifest = json.loads(
        (ROOT / "model_b_release_manifest.json").read_text(encoding="utf-8"))
    expected_sha = manifest["accepted_seed"]["sha256"]
    if seed_sha != expected_sha:
        print("ABORT: seed sha256 does not match the release manifest.")
        print("  on disk : %s" % seed_sha)
        print("  manifest: %s" % expected_sha)
        return 1
    print("[1/6] seed checksum matches the release manifest")

    kg, by_fid, topics, seed = build_kg()
    print("[2/6] seed loaded: %d facts, %d topics" % (len(seed), len(topics)))

    # -- Gate 2: quality gate, which is what sets mcq_readiness -------------
    from fact_quality import FactQualityGate

    FactQualityGate(kg).run_quality_pipeline(
        extraction_date=dt.date.today().isoformat())
    print("[3/6] fact quality gate complete")

    # -- Gate 3: strict temporal guard, every topic -------------------------
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

    # -- Retrieval, through the generator's own bridge ----------------------
    from mcq_generator import MCQGenerator, facts_from_kg

    if args.topic not in topics:
        print("ABORT: topic %r not in the seed. Available: %s"
              % (args.topic, ", ".join(sorted(topics))))
        return 1

    ready = facts_from_kg(kg, args.topic, as_of=CUTOFF,
                          allow_static_source_evidence=True)
    batch = ready[:args.facts]
    if not batch:
        print("ABORT: no MCQ-ready facts for topic %r at the cutoff" % args.topic)
        return 1

    # Belt and braces. The firewall inside generate_from_facts would catch
    # this too, but failing here names the topic and the retrieval call that
    # produced the bad batch, which is what a reader needs.
    leaked = [f["fact_id"] for f in batch
              if f.get("temporal_status") == "unversioned"]
    if leaked:
        print("ABORT: unversioned fact(s) in the generation batch: %s" % leaked)
        return 1
    print("[5/6] retrieved %d MCQ-ready fact(s) for %r; using %d"
          % (len(ready), args.topic, len(batch)))
    for f in batch:
        print("        %s  [%s]  %s"
              % (f["fact_id"], f.get("temporal_status"), f["text"][:70]))

    model_id = os.getenv("HF_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    provenance_common = {
        "run_tag": args.tag,
        "run_started_at": started,
        "cutoff": CUTOFF,
        "seed_file": SEED_FILE,
        "seed_sha256": seed_sha,
        "seed_release_id": manifest.get("release_id"),
        "topic": args.topic,
        "difficulty": args.difficulty,
        "model_id": model_id,
        "hf_provider": os.getenv("HF_PROVIDER", "auto"),
        "prompt_fingerprint": prompt_fingerprint(),
        "generator_module_sha256": hashlib.sha256(
            (ROOT / "mcq_generator.py").read_bytes()).hexdigest()[:16],
    }

    if args.dry_run:
        print("[6/6] --dry-run: stopping before the model call")
        print(json.dumps(provenance_common, ensure_ascii=False, indent=2))
        return 0

    api_key = (os.getenv("HF_API_KEY") or os.getenv("HF_API_TOKEN")
               or os.getenv("HF_TOKEN") or "").strip()
    if not api_key:
        print("ABORT: no HF key in the environment. Generation not attempted.")
        return 1

    generator = MCQGenerator(hf_api_key=api_key, model=model_id,
                             seen_questions_path="seen_questions.json")
    print("[6/6] generating (topic=%s, difficulty=%s, facts=%d)..."
          % (args.topic, args.difficulty, len(batch)))

    gen_started = time.time()
    result = generator.generate_from_facts(
        batch, difficulty=args.difficulty, topic=args.topic)
    gen_seconds = round(time.time() - gen_started, 1)
    print("       %d MCQ(s) accepted in %.1fs" % (len(result.mcqs), gen_seconds))

    # -- Independent quality gate over the generated batch ------------------
    quality_by_mcq = {}
    quality_summary = None
    if result.mcqs:
        try:
            from mcq_quality import MCQQualityEvaluator

            # `options` must be a dict keyed by ক/খ/গ/ঘ -- RuleBasedScreener
            # calls options.keys() and options.values() on it. A list of
            # "ক) text" strings raises AttributeError inside the screener,
            # which the caller then reports as an evaluation failure rather
            # than as the shape error it is. Same construction as
            # main-pipeline.py's quality stage.
            mcq_dicts = [{
                "mcq_id": m.mcq_id, "fact_id": m.fact_id,
                "question": m.question,
                "options": {o.key: o.text for o in m.options},
                "correct_answer": m.correct_answer,
                "difficulty": m.difficulty,
                "question_type": m.question_type,
                "explanation": m.explanation,
            } for m in result.mcqs]
            report = MCQQualityEvaluator(api_key, model_id).evaluate_batch(
                mcq_dicts, batch, topic=args.topic,
                difficulty=args.difficulty)
            quality_summary = {
                "report_id": report.report_id, "total": report.total,
                "passed": report.passed, "failed": report.failed,
            }
            for ev in getattr(report, "evaluations", []) or []:
                quality_by_mcq[ev.mcq_id] = {
                    "passed": ev.passed,
                    "overall_score": round(ev.overall_score, 3),
                    "grounding": round(ev.scores.grounding_score, 3),
                    "clarity": round(ev.scores.clarity_score, 3),
                    "distractor": round(ev.scores.distractor_score, 3),
                    "format": round(ev.scores.format_score, 3),
                }
            print("       quality gate: %d/%d passed"
                  % (report.passed, report.total))
        except Exception as exc:
            # A failed evaluator must not be mistaken for a clean batch.
            quality_summary = {"error": "%s: %s" % (type(exc).__name__,
                                                    str(exc)[:200])}
            print("       quality gate FAILED to run: %s" % quality_summary["error"])

    # -- One provenance record per generated MCQ ----------------------------
    seed_by_text = {f["fact_id"]: f for f in batch}
    rows = []
    for mcq in result.mcqs:
        kg_fact = seed_by_text.get(mcq.fact_id, {})
        raw = by_fid.get(mcq.fact_id, {})
        rows.append({
            "record_type": "%s_generated_mcq" % args.tag,
            **provenance_common,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "generation_seconds": gen_seconds,
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
            "quality_gate_verdict": quality_by_mcq.get(mcq.mcq_id),
            "accepted_for_benchmark": False,
            "acceptance_note": (
                "Smoke-run output. Not benchmark data: one topic, one "
                "difficulty, no human review of the generated items."
            ),
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    run_manifest = {
        "record_type": "%s_smoke_run_manifest" % args.tag,
        **provenance_common,
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "wall_seconds": round(time.time() - t0, 1),
        "facts_offered": len(batch),
        "facts_available_at_cutoff": len(ready),
        "seed_fact_uids": [by_fid.get(f["fact_id"], {}).get("fact_uid")
                           for f in batch],
        "mcqs_accepted": len(result.mcqs),
        "overall_score": round(getattr(result, "overall_score", 0.0), 3),
        "quality_gate": quality_summary,
        "rejection_tally": (result.rejection_report
                            if hasattr(result, "rejection_report") else None),
        "temporal_firewall": {
            "unversioned_in_batch": 0,
            "enforced_by": "mcq_generator.generate_from_facts "
                           "(TemporalGuardViolation)",
        },
        "seed_modified": False,
        "accepted_for_benchmark": False,
    }
    RUN_MANIFEST.write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print("\nwrote %d provenance record(s) -> %s" % (len(rows), RUN_LOG.name))
    print("wrote %s" % RUN_MANIFEST.name)
    for mcq in result.mcqs:
        print(mcq.display())
    generator.save_seen_questions()
    return 0


if __name__ == "__main__":
    sys.exit(main())
