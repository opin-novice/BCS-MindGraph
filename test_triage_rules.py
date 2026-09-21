"""
test_triage_rules.py
====================
Regression tests for step-1b triage: deciding static/dynamic for facts whose
topic is on neither the static nor the dynamic topic list
(``verify_temporal_corpus.classify_unlisted_topic``).

Why these tests exist
---------------------
The class field is load-bearing. Model B admits a `static` fact on a verified
pre-cutoff SOURCE and a `dynamic` fact on a verified `valid_from`. So a
dynamic fact mislabelled `static` can enter the release corpus on evidence
that no longer supports the claim as the corpus states it -- the exact leak
the holdout exists to detect, and it would be silent.

TEST 1 and TEST 2 are the ones that must never be deleted: they pin that this
rule table cannot reach back and reclassify a fact the earlier pass already
decided, including the 69 in the frozen Model B seed.

    python test_triage_rules.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import verify_temporal_corpus as V

ROOT = Path(__file__).resolve().parent
CORPUS_PATH = ROOT / "bcs_gk_facts.json"
SEED_PATH = ROOT / "bcs_gk_facts_model_b.json"

CHECKS: list[bool] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    CHECKS.append(bool(condition))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" -- %s" % detail) if detail else ""))


def load(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["facts"] if isinstance(data, dict) and "facts" in data else data


def fact(**kw):
    base = {"fact_text": "", "topic": "Government", "relation": "STATED_AS"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
print("TEST 1: the rule table never reclassifies an already-decided fact")
# ---------------------------------------------------------------------------
corpus = load(CORPUS_PATH)
decided = [f for f in corpus if f.get("temporal_class") in ("static", "dynamic")]
flipped = [f["fact_uid"] for f in decided
           if V.triage_fact(f)["temporal_class"] != f["temporal_class"]]

# A human adjudication is SUPPOSED to disagree with the rule table -- that is
# what it is for. BCSGK-0315 is the live case: topic=Economy makes the blanket
# rule say dynamic, and the PI ruled it static because the topic here is a
# syllabus label, not a statement about the claim's temporality.
#
# So the invariant is not "nothing ever differs". It is: every fact that
# differs is backed by an approved adjudication, and nothing else moves. That
# is the stronger claim, and the one that would actually catch a rule change
# reaching back into settled facts.
adjudicated = {
    rec.get("fact_uid") for rec in V.load_class_adjudications()
    if rec.get("reviewer_status") == "approved"
}
unexplained = [uid for uid in flipped if uid not in adjudicated]
check("no unadjudicated fact changes class", not unexplained,
      "%d decided fact(s); unexplained flips: %s" % (len(decided), unexplained[:5]))
check("every flip is covered by an approved adjudication",
      all(uid in adjudicated for uid in flipped),
      "flipped: %s; adjudicated: %s"
      % (sorted(flipped), sorted(adjudicated)) if flipped else "no flips")
check("the corpus still holds facts of both classes",
      {f["temporal_class"] for f in decided} == {"static", "dynamic"})


# ---------------------------------------------------------------------------
print("TEST 2: the frozen Model B seed is untouched by this table")
# ---------------------------------------------------------------------------
if SEED_PATH.exists():
    seed = load(SEED_PATH)
    seed_flipped = [f.get("fact_uid") for f in seed
                    if V.triage_fact(f)["temporal_class"] != f.get("temporal_class")]
    check("no seed fact changes class", not seed_flipped,
          "%d seed fact(s); flipped: %s" % (len(seed), seed_flipped[:5]))
    check("no seed fact is unclassified",
          all(f.get("temporal_class") in ("static", "dynamic") for f in seed))
else:  # pragma: no cover - the seed is expected to be present
    check("seed file present", False, "%s is missing" % SEED_PATH.name)


# ---------------------------------------------------------------------------
print("TEST 3: dynamic wins whenever both a dynamic and a static cue fire")
# ---------------------------------------------------------------------------
# Founded-in-1962 (static cue S1) AND an open-ended count (dynamic cue D2).
# Admitting this as static on a pre-cutoff source would carry the 2,200 over
# the cutoff unchecked, so the dynamic reading has to win.
both = fact(fact_text="The university, established in 1992, affiliates over "
                      "2,200 colleges across the country.",
            topic="Education", relation="founded")
cls, basis = V.classify_unlisted_topic(both)
check("a fact matching both cues is dynamic", cls == "dynamic", basis[:90])
check("the basis names the dynamic rule that fired",
      V.rule_id_of(basis).startswith("D"), V.rule_id_of(basis))


# ---------------------------------------------------------------------------
print("TEST 4: S1 needs two signals -- a bare year is not enough")
# ---------------------------------------------------------------------------
# Rule 2: a year inside fact_text is just a number in a sentence.
year_only = fact(fact_text="The commission's report discusses the 1998 policy "
                           "in general terms.")
check("year with no event relation or verb is not static",
      V.classify_unlisted_topic(year_only)[0] == "unclassified")

verb_only = fact(fact_text="The academy was established in Dhaka.",
                 relation="founded")
check("event relation with no year is not static",
      V.classify_unlisted_topic(verb_only)[0] == "unclassified")

both_signals = fact(fact_text="The academy was established in Dhaka in 1959.",
                    relation="founded")
cls, basis = V.classify_unlisted_topic(both_signals)
check("event relation plus a year is static", cls == "static",
      V.rule_id_of(basis))


# ---------------------------------------------------------------------------
print("TEST 5: no rule guesses -- unmatched facts go to the reviewer queue")
# ---------------------------------------------------------------------------
unmatched = fact(fact_text="The Attorney General is the chief law officer.")
cls, basis = V.classify_unlisted_topic(unmatched)
check("an unmatched fact stays unclassified", cls == "unclassified")
check("the residual basis records no rule id", V.rule_id_of(basis) == "none")
check("the residual basis says it was referred, not decided",
      "reviewer queue" in basis)


# ---------------------------------------------------------------------------
print("TEST 6: every decision carries a rule id and a rationale")
# ---------------------------------------------------------------------------
unlisted = [f for f in corpus if f.get("temporal_class") == "unclassified"] or \
           [f for f in corpus
            if f.get("topic") not in V.STATIC_TOPICS | V.DYNAMIC_TOPICS]
no_basis = []
for f in unlisted:
    cls, basis = V.classify_unlisted_topic(f)
    if cls != "unclassified" and V.rule_id_of(basis) == "none":
        no_basis.append(f.get("fact_uid"))
check("no fact is classified without a named rule", not no_basis,
      "%d fact(s) checked; missing: %s" % (len(unlisted), no_basis[:5]))


# ---------------------------------------------------------------------------
print("TEST 7: labelled cases from the real corpus")
# ---------------------------------------------------------------------------
# Each of these was read by hand before being pinned here.
CASES = [
    ("dynamic", "survey edition",
     fact(fact_text="অর্থনৈতিক সমীক্ষা ২০২৫ অনুযায়ী, দেশে কমিউনিটি ক্লিনিকের সংখ্যা ১৪২৯৭টি।",
           topic="Bangladesh Affairs")),
    ("dynamic", "mutable superlative",
     fact(fact_text="Hazrat Shahjalal International Airport is the busiest "
                    "airport in Bangladesh.", topic="Infrastructure")),
    ("dynamic", "rotating designation",
     fact(fact_text="BNS Bangabandhu is the flagship of the Bangladesh Navy, "
                    "a guided missile frigate commissioned in 2001.",
           topic="Defense")),
    ("static", "dated treaty signing",
     fact(fact_text="The Chittagong Hill Tracts Peace Accord was signed on "
                    "2 December 1997.", topic="International Relations")),
    ("static", "constitutional provision",
     fact(fact_text="সংবিধানের ৬৫(১) অনুচ্ছেদ অনুযায়ী আইনপ্রণয়ন ক্ষমতা জাতীয় সংসদের উপর ন্যস্ত।",
           topic="Government", relation="enacted")),
    ("static", "ethnographic attribute",
     fact(fact_text="পাঙন উপজাতির ধর্ম ইসলাম।", topic="Bangladesh Affairs")),
]
for expected, label, f in CASES:
    got, basis = V.classify_unlisted_topic(f)
    check("%s -> %s" % (label, expected), got == expected,
          "got %s via %s" % (got, V.rule_id_of(basis)))


# ---------------------------------------------------------------------------
print("TEST 8: the adjudication ledger's gates")
# ---------------------------------------------------------------------------
# A human decision overrides the rules, so the gates on it are the only thing
# standing between one reviewer's note and a silent corpus edit.
corpus_fixture = [
    {"fact_uid": "F_LIVE", "temporal_class": "dynamic"},
    {"fact_uid": "F_FROZEN", "temporal_class": "static"},
]
FROZEN = {"F_FROZEN"}


def adj(**kw):
    base = {"fact_uid": "F_LIVE", "reviewer_status": "approved",
            "reviewer_id": "RA_1", "reason": "read as a regional claim",
            "previous_temporal_class": "dynamic", "temporal_class": "static",
            "adjudicated_at": "2026-09-16"}
    base.update(kw)
    return base


def gate(record):
    ok, skipped = V.applicable_adjudications(corpus_fixture, [record], FROZEN)
    return ok, skipped


ok, _ = gate(adj())
check("a complete approved record applies", set(ok) == {"F_LIVE"})

ok, notes = gate(adj(reviewer_status="pending_pi"))
check("a pending record does not apply", not ok, notes[0] if notes else "")

ok, notes = gate(adj(reason=None))
check("approved without a reason does not apply", not ok)

ok, notes = gate(adj(reviewer_id=None))
check("approved without a reviewer_id does not apply", not ok)

ok, notes = gate(adj(previous_temporal_class="unclassified"))
check("a stale record does not apply", not ok,
      "reviewer saw a class the corpus no longer holds")

ok, notes = gate(adj(fact_uid="F_FROZEN", previous_temporal_class="static",
                     temporal_class="dynamic"))
check("a frozen-seed fact cannot be adjudicated", not ok,
      notes[0] if notes else "")

ok, notes = gate(adj(temporal_class="unclassified"))
check("an adjudication cannot set 'unclassified'", not ok)

ok, notes = gate(adj(fact_uid="F_MISSING"))
check("a record for an unknown fact does not apply", not ok)

rejections = [gate(adj(reviewer_status="pending_pi"))[1],
              gate(adj(reason=None))[1],
              gate(adj(fact_uid="F_FROZEN", previous_temporal_class="static",
                       temporal_class="dynamic"))[1]]
check("every rejection is reported, never silent",
      all(len(r) == 1 for r in rejections))


# ---------------------------------------------------------------------------
print()
passed = sum(CHECKS)
print("test_triage_rules: %d/%d CHECK True -- %s"
      % (passed, len(CHECKS), "PASS" if passed == len(CHECKS) else "FAIL"))

# Guarded so pytest can import this module (it has no test_* functions --
# it runs its checks as import-time side effects) without pytest's
# collector dying on an unguarded module-level SystemExit. Standalone
# `python test_triage_rules.py` runs still exit with the real pass/fail code.
if __name__ == "__main__":
    sys.exit(0 if passed == len(CHECKS) else 1)
