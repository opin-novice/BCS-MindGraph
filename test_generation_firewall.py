"""
test_generation_firewall.py
===========================
The last gate before a model sees a fact
(``mcq_generator.MCQGenerator.generate_from_facts``).

Why this exists
---------------
``facts_from_kg()`` stamps ``temporal_status`` but does not filter on it.
main-pipeline.py is safe only because it calls ``strict_temporal_guard()``
first and raises before generation. Nothing stopped a different caller -- a
notebook, a baseline harness, a new script -- from skipping that step, and on
the current seed 46 unversioned facts would have reached the generator.

These tests pin the refusal, and pin that it does not fire on the paths it
should leave alone.

    python test_generation_firewall.py
"""

from __future__ import annotations

import sys

from mcq_generator import MCQGenerator, TemporalGuardViolation

CHECKS: list[bool] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    CHECKS.append(bool(condition))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" -- %s" % detail) if detail else ""))


def fact(fid: str, **kw) -> dict:
    base = {
        "fact_id": fid,
        "text": "বাংলাদেশের রাজধানী ঢাকা।",
        "topic": "Geography",
        "mcq_suitable_for": ["factual"],
        "mcq_readiness": 0.9,
    }
    base.update(kw)
    return base


# A generator instance is needed to reach the method, but the firewall fires
# before any agent is called, so no key and no network are involved.
gen = MCQGenerator(hf_api_key="not-used-by-these-tests")


def generate(facts):
    return gen.generate_from_facts(facts, difficulty="medium", topic="Geography")


# ---------------------------------------------------------------------------
print("TEST 1: an unversioned fact is refused")
# ---------------------------------------------------------------------------
try:
    generate([fact("FACT_a", temporal_status="unversioned")])
    check("generation refuses an unversioned fact", False, "no exception")
except TemporalGuardViolation as exc:
    check("generation refuses an unversioned fact", True)
    check("the error names the offending fact", "FACT_a" in str(exc))
    check("the error says what to do about it",
          "strict_temporal_guard" in str(exc))
except Exception as exc:                                   # pragma: no cover
    check("generation refuses an unversioned fact", False,
          "%s raised instead" % type(exc).__name__)


# ---------------------------------------------------------------------------
print("TEST 2: one bad fact in a good batch still stops the batch")
# ---------------------------------------------------------------------------
# Refusing the batch rather than silently dropping the fact: a caller that
# asked for five facts and got four without being told has a quiet hole.
try:
    generate([
        fact("FACT_ok1", temporal_status="static_valid_at_cutoff"),
        fact("FACT_ok2", temporal_status="valid_at_cutoff"),
        fact("FACT_bad", temporal_status="unversioned"),
    ])
    check("a mixed batch is refused", False, "no exception")
except TemporalGuardViolation as exc:
    check("a mixed batch is refused", True)
    check("only the offending fact is named",
          "FACT_bad" in str(exc) and "FACT_ok1" not in str(exc))


# ---------------------------------------------------------------------------
print("TEST 3: the guard subclasses RuntimeError")
# ---------------------------------------------------------------------------
# main-pipeline.py already raises RuntimeError for its own guard failure, so
# anything catching that keeps working.
check("TemporalGuardViolation is a RuntimeError",
      issubclass(TemporalGuardViolation, RuntimeError))


# ---------------------------------------------------------------------------
print("TEST 4: the firewall does not fire where it should not")
# ---------------------------------------------------------------------------
# No cutoff was active: facts_from_kg stamps no temporal_status at all.
# Absence means "no cutoff was used", which is not the same as "the cutoff
# ran and this fact failed it". Blocking that would break every
# non-temporal caller.
try:
    generate([fact("FACT_no_cutoff")])
    check("a fact with no temporal_status is not blocked", True)
except TemporalGuardViolation:
    check("a fact with no temporal_status is not blocked", False,
          "blocked the no-cutoff path")
except Exception:
    # Any later failure (no API key, network) means the firewall let it
    # through, which is the only thing under test here.
    check("a fact with no temporal_status is not blocked", True,
          "passed the firewall, failed later as expected without a key")

for status in ("static_valid_at_cutoff", "valid_at_cutoff"):
    try:
        generate([fact("FACT_%s" % status, temporal_status=status)])
        check("%s is allowed through" % status, True)
    except TemporalGuardViolation:
        check("%s is allowed through" % status, False, "wrongly blocked")
    except Exception:
        check("%s is allowed through" % status, True,
              "passed the firewall")

try:
    generate([])
    check("an empty batch is not treated as a violation", True)
except TemporalGuardViolation:
    check("an empty batch is not treated as a violation", False)
except Exception:
    check("an empty batch is not treated as a violation", True)


# ---------------------------------------------------------------------------
print()
passed = sum(CHECKS)
print("test_generation_firewall: %d/%d CHECK True -- %s"
      % (passed, len(CHECKS), "PASS" if passed == len(CHECKS) else "FAIL"))
sys.exit(0 if passed == len(CHECKS) else 1)
