"""
test_cutoff.py
==============
Standalone smoke test for the temporal cutoff fix — needs NO API key,
NO internet, and doesn't touch bcg_gk_facts.json. Just proves
kg_builder.py's cutoff-aware retrieval and mcq_generator.py's
facts_from_kg() wiring behave correctly, in isolation, before you spend
any API budget running the full pipeline.

Run:  python test_cutoff.py
Expect: every line starting "CHECK" should say True.
"""

from kg_builder import KnowledgeGraphBuilder

kg = KnowledgeGraphBuilder()

# Fact valid 2020 -> open-ended, sourced from an official, pre-cutoff page.
fid_valid = kg.insert_fact_pipeline(
    fact_text="A holds Office X", subject_entities=[("A", "PERSON")],
    object_entities=[("Office X", "ORGANIZATION")], topic="Government",
    source_url="https://bbs.gov.bd/a", publisher="BBS",
    relation="holds_position", valid_from="2020-01-01",
    source_published_at="2020-02-01", source_tier=1,
)
kg.update_fact_attribute(fid_valid, "quality_verdict", "ACCEPT")
kg.update_fact_attribute(fid_valid, "mcq_readiness", 0.9)

# Fact with no temporal fields at all (this is what your CURRENT
# bcg_gk_facts.json entries look like today).
fid_legacy = kg.insert_fact_pipeline(
    fact_text="Bangladesh's capital is Dhaka",
    subject_entities=[("Bangladesh", "COUNTRY")],
    object_entities=[("Dhaka", "CITY")], topic="Government",
    source_url="https://en.wikipedia.org/x", publisher="Wikipedia",
)
kg.update_fact_attribute(fid_legacy, "quality_verdict", "ACCEPT")
kg.update_fact_attribute(fid_legacy, "mcq_readiness", 0.9)

# Fact whose ONLY evidence was published after the cutoff -> must be
# excluded entirely when a cutoff is active (this is the leakage check).
fid_leak = kg.insert_fact_pipeline(
    fact_text="Some 2024 announcement", subject_entities=[("C", "PERSON")],
    object_entities=[("Y", "ORGANIZATION")], topic="Government",
    source_url="https://dailystar.net/z", publisher="Daily Star",
    source_published_at="2024-06-01", source_tier=4,
)
kg.update_fact_attribute(fid_leak, "quality_verdict", "ACCEPT")
kg.update_fact_attribute(fid_leak, "mcq_readiness", 0.9)

print("=" * 60)
print("TEST 1 — no cutoff (as_of=None): should return all 3 facts")
print("=" * 60)
no_cutoff = kg.get_facts_by_topic_as_of("Government", as_of_date=None)
ids = {d["fact_id"] for d in no_cutoff}
print("  facts returned:", len(no_cutoff))
print("  CHECK all 3 present:", ids == {fid_valid, fid_legacy, fid_leak})

print()
print("=" * 60)
print("TEST 2 — cutoff = 2022-01-01: should drop the 2024-sourced fact")
print("=" * 60)
cutoff_result = kg.get_facts_by_topic_as_of("Government", as_of_date="2022-01-01")
for d in cutoff_result:
    print(f"  {d['fact_id']}  status={d['temporal_status']}  text={d['text'][:40]}")
ids2 = {d["fact_id"] for d in cutoff_result}
print("  CHECK leaked fact excluded:", fid_leak not in ids2)
print("  CHECK valid fact included:", fid_valid in ids2)
print("  CHECK legacy fact included as 'unversioned':",
      any(d["fact_id"] == fid_legacy and d["temporal_status"] == "unversioned"
          for d in cutoff_result))

print()
print("=" * 60)
print("TEST 3 — facts_from_kg() end-to-end (what the pipeline actually calls)")
print("=" * 60)
try:
    from mcq_generator import facts_from_kg
    ready = facts_from_kg(kg, "Government", as_of="2022-01-01")
    print("  facts returned:", [(f["fact_id"], f.get("temporal_status")) for f in ready])
    print("  CHECK leaked fact excluded here too:",
          fid_leak not in [f["fact_id"] for f in ready])
except ModuleNotFoundError as e:
    print(f"  SKIPPED — mcq_generator.py couldn't import ({e}).")
    print("  This means a dependency (e.g. huggingface_hub, or an internal")
    print("  'bcs' package) is missing, NOT a problem with the cutoff fix.")
    print("  TEST 1 and TEST 2 above already prove the core logic works.")

print()
print("=" * 60)
print("TEST 4 — strict benchmark gate should drop unversioned facts under cutoff")
print("=" * 60)
try:
    import importlib.util
    import pathlib

    module_path = pathlib.Path(__file__).with_name("main-pipeline.py")
    spec = importlib.util.spec_from_file_location("main_pipeline", module_path)
    main_pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_pipeline)
    print("  CHECK strict benchmark gate exists:", hasattr(main_pipeline, "STRICT_TEMPORAL_GUARD"))
    violations = kg.strict_temporal_guard("Government", "2022-01-01")
    print("  CHECK strict gate flags unversioned facts:", fid_legacy in violations)
    print("  CHECK strict gate allows cutoff-compliant facts:", fid_valid not in violations)
except Exception as e:
    print(f"  FAIL — strict benchmark gate not implemented yet: {e}")

print()
print("=" * 60)
print("TEST 5 — Model B static-source evidence policy")
print("=" * 60)
fid_static = kg.insert_fact_pipeline(
    fact_text="Bangladesh's capital is Dhaka", subject_entities=[("Bangladesh", "COUNTRY")],
    object_entities=[("Dhaka", "CITY")], topic="Government",
    source_url="https://en.wikipedia.org/wiki/Dhaka", publisher="Wikipedia",
)
kg.update_fact_attribute(fid_static, "quality_verdict", "ACCEPT")
kg.update_fact_attribute(fid_static, "mcq_readiness", 0.9)
kg.update_fact_attribute(fid_static, "temporal_class", "static")
kg.update_fact_attribute(fid_static, "temporal_evidence_status", "verified_pre_cutoff_source")
kg.update_fact_attribute(fid_static, "temporal_evidence_date", "2023-04-05")
kg.update_fact_attribute(fid_static, "temporal_evidence_source_url", "https://en.wikipedia.org/w/index.php?oldid=1")
kg.update_fact_attribute(fid_static, "temporal_evidence_snapshot_hash", "snapshot-hash")

fid_dynamic = kg.insert_fact_pipeline(
    fact_text="The current office holder is Person X", subject_entities=[("Person X", "PERSON")],
    object_entities=[("Office X", "ORGANIZATION")], topic="Government",
    source_url="https://en.wikipedia.org/wiki/Office_X", publisher="Wikipedia",
)
kg.update_fact_attribute(fid_dynamic, "temporal_class", "dynamic")

model_b = kg.get_facts_by_topic_as_of(
    "Government", as_of_date="2023-04-19", allow_static_source_evidence=True,
)
model_b_ids = {fact["fact_id"] for fact in model_b}
model_b_status = {fact["fact_id"]: fact["temporal_status"] for fact in model_b}
print("  CHECK accepted static evidence included:", fid_static in model_b_ids)
print("  CHECK static evidence status is explicit:",
      model_b_status.get(fid_static) == "static_valid_at_cutoff")
print("  CHECK unverified legacy fact excluded:", fid_legacy not in model_b_ids)
print("  CHECK dynamic unversioned fact excluded:", fid_dynamic not in model_b_ids)
default_results = kg.get_facts_by_topic_as_of(
    "Government", as_of_date="2023-04-19",
)
default_status = {fact["fact_id"]: fact["temporal_status"] for fact in default_results}
print("  CHECK default policy remains legacy-unversioned:",
      default_status.get(fid_static) == "unversioned")

print()
print("=" * 60)
print("TEST 6 — Model B: static fact proved by pre-cutoff source evidence")
print("=" * 60)

kgb = KnowledgeGraphBuilder()

# A TIMELESS fact. It has no valid_from/valid_to and never will — asking
# when "Dhaka is the capital" became true is a category error. Model B
# proves it with a source that demonstrably existed at or before t*.
fid_static = kgb.insert_fact_pipeline(
    fact_text="Dhaka is the capital of Bangladesh",
    subject_entities=[("Bangladesh", "COUNTRY")],
    object_entities=[("Dhaka", "CITY")], topic="Geography",
    source_url="https://en.wikipedia.org/w/index.php?oldid=111",
    publisher="Wikipedia", relation="located_in",
    temporal_class="static",
    temporal_evidence_status="verified_pre_cutoff_source",
    temporal_evidence_date="2023-04-10",
    temporal_evidence_source_url="https://en.wikipedia.org/w/index.php?oldid=111",
    temporal_evidence_snapshot_hash="a" * 64,
)

# Same shape, but the evidence POSTDATES the cutoff -> leakage, must drop.
fid_static_late = kgb.insert_fact_pipeline(
    fact_text="Some post-cutoff static claim",
    subject_entities=[("X", "ORGANIZATION")],
    object_entities=[("Y", "CITY")], topic="Geography",
    source_url="https://en.wikipedia.org/w/index.php?oldid=222",
    publisher="Wikipedia", relation="located_in",
    temporal_class="static",
    temporal_evidence_status="verified_pre_cutoff_source",
    temporal_evidence_date="2024-08-01",
    temporal_evidence_source_url="https://en.wikipedia.org/w/index.php?oldid=222",
    temporal_evidence_snapshot_hash="b" * 64,
)

# Claims to be static but carries NO evidence. The label alone must never
# be enough, otherwise every misclassification becomes a silent leak.
fid_static_bare = kgb.insert_fact_pipeline(
    fact_text="Unevidenced static claim",
    subject_entities=[("P", "ORGANIZATION")],
    object_entities=[("Q", "CITY")], topic="Geography",
    source_url="https://example.org/none", publisher="Example",
    relation="located_in", temporal_class="static",
)

for _fid in (fid_static, fid_static_late, fid_static_bare):
    kgb.update_fact_attribute(_fid, "quality_verdict", "ACCEPT")
    kgb.update_fact_attribute(_fid, "mcq_readiness", 0.9)

res = kgb.get_facts_by_topic_as_of("Geography", as_of_date="2023-04-19",
                                   allow_static_source_evidence=True)
by_id = {d["fact_id"]: d for d in res}

print("  CHECK evidenced static fact accepted:",
      by_id.get(fid_static, {}).get("temporal_status") == "static_valid_at_cutoff")
print("  CHECK post-cutoff static evidence dropped:", fid_static_late not in by_id)
# retrieval drops it entirely; TEST 7 then proves the guard still sees it
print("  CHECK unevidenced static fact NOT retrievable:",
      fid_static_bare not in by_id)

print()
print("=" * 60)
print("TEST 7 — REGRESSION: strict guard must not pass by HIDING violations")
print("=" * 60)
print("  An earlier revision dropped unversioned facts from the result set")
print("  when Model B was enabled. The guard finds breaches by looking for")
print("  exactly those records, so it reported PASS on a corpus full of")
print("  unversioned facts. Both checks below must be True.")

violations = kgb.strict_temporal_guard("Geography", "2023-04-19",
                                       allow_static_source_evidence=True)
print("  CHECK unevidenced static fact IS reported as a violation:",
      fid_static_bare in violations)
print("  CHECK evidenced static fact is NOT a violation:",
      fid_static not in violations)

print()
print("If every CHECK above says True, the cutoff fix is working correctly.")