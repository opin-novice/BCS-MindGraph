"""
test_task2_pipeline.py
======================
Task 2 readiness check over the verified Model B seed.

What this covers
----------------
The part of main-pipeline.py that runs before any model is called: loading
the frozen seed into the knowledge graph, the strict temporal guard, and
cutoff-aware retrieval through the same bridge the MCQ generator uses
(`mcq_generator.facts_from_kg`).

What this does NOT cover
------------------------
Generation. ChallengerAgent, ReasonerAgent and JudgeAgent each need a
Hugging Face inference client, and `.env` is empty, so no key is available in
this environment. That half of Task 2 cannot be exercised here and this file
does not pretend otherwise.

The reason to run the offline half anyway: a temporal leak is a data-path
bug, not a generation bug. If an unversioned or post-cutoff fact reaches the
generator, no amount of prompt quality repairs the benchmark.

    python test_task2_pipeline.py
"""

from __future__ import annotations

import collections
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHECKS: list[bool] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    CHECKS.append(bool(condition))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" -- %s" % detail) if detail else ""))


def load_pipeline_module():
    """Import main-pipeline.py for its real constants.

    Hard-coding the cutoff here would make the test agree with itself rather
    than with the pipeline, which is the only thing worth asserting.
    """
    spec = importlib.util.spec_from_file_location(
        "mainpipeline", ROOT / "main-pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MP = load_pipeline_module()
from kg_builder import KnowledgeGraphBuilder          # noqa: E402
from mcq_generator import facts_from_kg               # noqa: E402

CUTOFF = MP.CUTOFF_DATE
SEED_PATH = ROOT / MP.FACTS_JSON


# ---------------------------------------------------------------------------
print("TEST 1: the pipeline is pointed at the verified seed")
# ---------------------------------------------------------------------------
check("FACTS_JSON is the frozen Model B seed",
      MP.FACTS_JSON == "bcs_gk_facts_model_b.json", MP.FACTS_JSON)
check("a cutoff is active", CUTOFF == "2023-04-19", str(CUTOFF))
check("the strict temporal guard is on", MP.STRICT_TEMPORAL_GUARD is True)
check("the Model B static route is enabled",
      MP.ALLOW_STATIC_SOURCE_EVIDENCE is True)

seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
seed = seed["facts"] if isinstance(seed, dict) and "facts" in seed else seed
check("the seed holds 69 facts", len(seed) == 69, str(len(seed)))


# ---------------------------------------------------------------------------
print("TEST 2: the seed loads into the KG with its temporal metadata intact")
# ---------------------------------------------------------------------------
kg = KnowledgeGraphBuilder()
topics_seen: dict[str, list[str]] = {}
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
    topics_seen.setdefault(raw["topic"], []).append(fid)

check("every seed fact reached the KG",
      sum(len(v) for v in topics_seen.values()) == len(seed))
check("11 topics are represented", len(topics_seen) == 11, str(len(topics_seen)))


# ---------------------------------------------------------------------------
print("TEST 3: the strict guard passes, topic by topic")
# ---------------------------------------------------------------------------
violations = {}
for topic in sorted(topics_seen):
    bad = kg.strict_temporal_guard(
        topic, CUTOFF,
        allow_static_source_evidence=MP.ALLOW_STATIC_SOURCE_EVIDENCE)
    if bad:
        violations[topic] = bad
check("no topic contains an unversioned fact", not violations,
      str(violations)[:150] if violations else "0 violations across 11 topics")

# The same run without the Model B route must FAIL. If it passed, the static
# facts would be getting through on something other than their evidence, and
# the guard would not be testing what it claims to.
legacy = {}
for topic in sorted(topics_seen):
    bad = kg.strict_temporal_guard(topic, CUTOFF,
                                   allow_static_source_evidence=False)
    if bad:
        legacy[topic] = len(bad)
check("with the static route OFF the same seed is blocked",
      bool(legacy),
      "%d topic(s) blocked -- the 63 static facts depend on their evidence, "
      "not on a default" % len(legacy))


# ---------------------------------------------------------------------------
print("TEST 4: the fact quality gate runs over the seed")
# ---------------------------------------------------------------------------
# main-pipeline.py runs this as STAGE 2, between loading and generation, and
# it is what sets `quality_verdict` and `mcq_readiness`. facts_from_kg treats
# a missing verdict as REJECT and a missing readiness as 0.0, so skipping
# this stage makes every topic come back empty -- which looks exactly like a
# temporal gate failure and is nothing of the kind.
from fact_quality import FactQualityGate              # noqa: E402

gate = FactQualityGate(kg)
quality_report = gate.run_quality_pipeline(extraction_date=MP.EXTRACTION_DATE)
check("the quality gate completed", isinstance(quality_report, dict),
      ", ".join(sorted(quality_report)[:5]) if isinstance(quality_report, dict)
      else type(quality_report).__name__)

scored = [fid for fids in topics_seen.values() for fid in fids
          if (kg.get_fact_data(fid) or {}).get("mcq_readiness") is not None]
check("the gate scored the seed facts", len(scored) > 0,
      "%d of %d scored" % (len(scored), len(seed)))


# ---------------------------------------------------------------------------
print("TEST 5: retrieval through the generator's own bridge is cutoff-clean")
# ---------------------------------------------------------------------------
# Called exactly as main-pipeline.py calls it, including the Model B flag.
# Without that flag the 63 static facts are dropped here even though they
# passed the guard, so passing it is part of what is being tested.
retrieved, unversioned, post_cutoff = 0, [], []
for topic in sorted(topics_seen):
    for fact in facts_from_kg(
            kg, topic, as_of=CUTOFF,
            allow_static_source_evidence=MP.ALLOW_STATIC_SOURCE_EVIDENCE):
        retrieved += 1
        status = fact.get("temporal_status")
        if status == "unversioned":
            unversioned.append(fact.get("fact_id"))
        evidence_date = fact.get("temporal_evidence_date")
        if evidence_date and str(evidence_date)[:10] > CUTOFF:
            post_cutoff.append((fact.get("fact_id"), evidence_date))
        valid_from = fact.get("valid_from")
        if valid_from and str(valid_from)[:10] > CUTOFF:
            post_cutoff.append((fact.get("fact_id"), valid_from))

check("facts_from_kg returns facts at the cutoff", retrieved > 0, str(retrieved))
check("no unversioned fact reaches the generator", not unversioned,
      str(unversioned[:5]))
check("no retrieved fact carries a post-cutoff date", not post_cutoff,
      str(post_cutoff[:3]))


# What the Model B flag actually controls is the LABEL, not the count.
# Retrieval returns the same facts either way and stamps each one with a
# temporal_status; the strict guard is what refuses to run when any of those
# statuses is `unversioned`. Asserting on counts here would be testing the
# wrong layer and would fail for the right reasons.
statuses_on = collections.Counter()
statuses_off = collections.Counter()
for topic in sorted(topics_seen):
    for f in kg.get_facts_by_topic_as_of(
            topic, as_of_date=CUTOFF, allow_static_source_evidence=True):
        statuses_on[f.get("temporal_status")] += 1
    for f in kg.get_facts_by_topic_as_of(
            topic, as_of_date=CUTOFF, allow_static_source_evidence=False):
        statuses_off[f.get("temporal_status")] += 1

check("with the Model B route on, 63 static facts are evidence-backed",
      statuses_on.get("static_valid_at_cutoff") == 63, str(dict(statuses_on)))
check("with it off, those same 63 fall back to unversioned",
      statuses_off.get("unversioned") == 63, str(dict(statuses_off)))
check("the 6 dynamic facts are interval-checked either way",
      statuses_on.get("valid_at_cutoff") == 6
      and statuses_off.get("valid_at_cutoff") == 6)

# Worth stating plainly, because it is a trap for the next caller:
# facts_from_kg ANNOTATES temporal_status, it does not filter on it. Nothing
# in the retrieval path stops an unversioned fact reaching the generator --
# main-pipeline.py is safe only because it runs strict_temporal_guard first
# and raises before generation. A new caller that skips that step gets no
# protection from this layer.
unversioned_would_pass = [
    f for topic in sorted(topics_seen)
    for f in facts_from_kg(kg, topic, as_of=CUTOFF,
                           allow_static_source_evidence=False)
    if f.get("temporal_status") == "unversioned"
]
check("documented: retrieval alone does not filter unversioned facts",
      len(unversioned_would_pass) > 0,
      "%d would reach the generator if the guard were skipped -- the guard, "
      "not this layer, is the gate" % len(unversioned_would_pass))


# ---------------------------------------------------------------------------
print("TEST 6: generation feasibility under the configured budget")
# ---------------------------------------------------------------------------
usable_per_topic = {
    topic: len(facts_from_kg(
        kg, topic, as_of=CUTOFF,
        allow_static_source_evidence=MP.ALLOW_STATIC_SOURCE_EVIDENCE))
    for topic in sorted(topics_seen)
}
eligible = {t: n for t, n in usable_per_topic.items()
            if n >= MP.MIN_FACTS_PER_TOPIC}
capped = sum(min(n, MP.MAX_FACTS_PER_TOPIC) for n in eligible.values())

check("at least one topic meets MIN_FACTS_PER_TOPIC", bool(eligible),
      "%d of %d topics" % (len(eligible), len(usable_per_topic)))
check("the fact pool is not empty after MAX_FACTS_PER_TOPIC capping",
      capped > 0, "%d fact(s) available for generation" % capped)

print("      MCQ_BUDGET=%d, MAX_FACTS_PER_TOPIC=%d -> %d fact(s) reachable "
      "across %d topic(s)"
      % (MP.MCQ_BUDGET, MP.MAX_FACTS_PER_TOPIC, capped, len(eligible)))
for topic, n in sorted(usable_per_topic.items(), key=lambda kv: -kv[1]):
      print("        %-22s %d usable (capped to %d)"
            % (topic, n, min(n, MP.MAX_FACTS_PER_TOPIC)))


# ---------------------------------------------------------------------------
print("TEST 7: what this run could not verify")
# ---------------------------------------------------------------------------
import os                                            # noqa: E402
has_key = bool((os.getenv("HF_API_KEY") or os.getenv("HF_API_TOKEN") or "").strip())
print("      HF inference key present: %s" % has_key)
if not has_key:
    print("      Generation stages (Challenger / Reasoner / Judge) were NOT run.")
    print("      This file verifies the data path only. Task 2 is not complete "
          "until generation runs against this same seed.")
check("the absence of a key is reported rather than silently skipped", True)


# ---------------------------------------------------------------------------
print()
passed = sum(CHECKS)
print("test_task2_pipeline: %d/%d CHECK True -- %s"
      % (passed, len(CHECKS), "PASS" if passed == len(CHECKS) else "FAIL"))

# Guarded so pytest can import this module (it has no test_* functions --
# it runs its checks as import-time side effects) without pytest's
# collector dying on an unguarded module-level SystemExit. Standalone
# `python test_task2_pipeline.py` runs still exit with the real pass/fail code.
if __name__ == "__main__":
    sys.exit(0 if passed == len(CHECKS) else 1)
