"""
test_task2b_quality_gate.py
===========================
Task 2B regression check: does the upgraded quality gate reject the defects
Task 2A shipped?

Why this file exists
--------------------
The Task 2A smoke run produced 7 MCQs. The quality gate scored the Culture
batch 4/4 PASS. Four of those items were nevertheless unusable — misspelled
proper nouns, a mistranslated source term, a distractor that meant the same
thing as the correct answer, and two questions answered by the same entity
out of one fact.

Those 7 records are therefore the cheapest possible regression fixture: the
defects in them are known, catalogued (handover §9.1), and frozen on disk.
A checker that does not fail them is not working, and there is no point
spending a generation run to discover that.

This file makes NO model calls. Every Task 2B check is decidable from a
controlled table, which is the whole argument for putting them in the
rule-based screener instead of the LLM judge.

    python test_task2b_quality_gate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcq_quality import (
    HARD_FAILURE_CODES,
    RuleBasedScreener,
    find_near_duplicates,
)
from rejection_taxonomy import map_codes

# This file prints Bengali. A Windows console defaults to cp1252, where the
# first flagged misspelling raises UnicodeEncodeError and the run dies before
# reporting anything. Failing to render a character must not be mistaken for
# a failing check.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "model_b_workflow" / "task2a_smoke_run.jsonl"

NEW_CODES = {"MISSPELLING", "ASCII_DIGITS", "SYNONYM_DISTRACTOR",
             "MISTRANSLATION", "DROPPED_QUALIFIER", "NEAR_DUPLICATE"}

CHECKS: list[bool] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    CHECKS.append(bool(condition))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                           (" -- %s" % detail) if detail else ""))


def as_mcq_dict(row: dict) -> dict:
    """The JSONL provenance record, in the shape the gate expects."""
    return {
        "mcq_id":         row["mcq_id"],
        "fact_id":        row["kg_fact_id"],
        "question":       row["question"],
        "options":        {o["key"]: o["text"] for o in row["options"]},
        "correct_answer": row["correct_answer"],
        "difficulty":     row.get("difficulty", "medium"),
        "explanation":    row.get("explanation", ""),
    }


def as_supporting_fact(row: dict) -> dict:
    """The English seed fact the MCQ was generated from."""
    return {"fact_id": row["kg_fact_id"], "text": row.get("fact_text") or ""}


# ---------------------------------------------------------------------------
print("TEST 1: the Task 2A fixture is present and unchanged in shape")
# ---------------------------------------------------------------------------
check("the smoke-run record file exists", FIXTURE.exists(), FIXTURE.name)
rows = [json.loads(line) for line in
        FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
check("it holds the 7 Task 2A MCQs", len(rows) == 7, "%d record(s)" % len(rows))

screener = RuleBasedScreener()
facts = [as_supporting_fact(r) for r in rows]
mcqs = [as_mcq_dict(r) for r in rows]

results = {}
for mcq in mcqs:
    score, codes = screener.screen(mcq, facts)
    results[mcq["mcq_id"]] = [score, set(codes)]

duplicates = find_near_duplicates(mcqs)
for mcq_id in duplicates:
    results[mcq_id][1].add("NEAR_DUPLICATE")


# ---------------------------------------------------------------------------
print("TEST 2: the three catalogued misspellings are caught")
# ---------------------------------------------------------------------------
# পাদমা / বুরিগঙ্গা appear in OPTIONS only, not in the stem. A stem-only
# checker would report a clean batch here, which is why the screener scans
# options and explanation too.
for mcq_id, where in (("MCQ_89c2bdcc", "পাদমা, বুরিগঙ্গা in options"),
                      ("MCQ_2051ff97", "পাদমা, বুরিগঙ্গা in options"),
                      ("MCQ_fd5da7cf", "পৌহেলা in the stem"),
                      ("MCQ_795d94e5", "পৌহেলা in option ক")):
    check("MISSPELLING flagged on %s" % mcq_id,
          "MISSPELLING" in results[mcq_id][1], where)

clean_spelling = ("MCQ_f78a10b0", "MCQ_84efae05", "MCQ_21ac5280")
check("correctly spelled items are not flagged",
      all("MISSPELLING" not in results[m][1] for m in clean_spelling),
      ", ".join(clean_spelling))


# ---------------------------------------------------------------------------
print("TEST 2b: ASCII numerals in a Bengali MCQ (PI ruling, 2026-09-17)")
# ---------------------------------------------------------------------------
# MCQ_f78a10b0 asked a Bengali stem and offered 7 / 8 / 9 / 10 as options.
check("ASCII_DIGITS flagged on MCQ_f78a10b0",
      "ASCII_DIGITS" in results["MCQ_f78a10b0"][1], "options 7/8/9/10")

bn_numerals = {
    "mcq_id": "NUM_1", "fact_id": "F3",
    "question": "বাংলাদেশে প্রশাসনিক বিভাগ কতটি?",
    "options": {"ক": "৭", "খ": "৮", "গ": "৯", "ঘ": "১০"},
    "correct_answer": "খ", "difficulty": "easy", "explanation": "",
}
_, num_codes = screener.screen(bn_numerals, [])
check("Bengali numerals are not flagged", "ASCII_DIGITS" not in num_codes)

# The year items already use Bengali numerals, so the ruling must not sweep
# them up as collateral.
check("the Bengali-numeral year items stay clean",
      not any("ASCII_DIGITS" in results[m][1]
              for m in ("MCQ_84efae05", "MCQ_fd5da7cf")),
      "১৯৫০/১৯৫২/… and ১২/১৪/… এপ্রিল")

# The ruling names the stem and the options. An ASCII digit that appears only
# in the explanation is out of scope — stated as a test so the boundary is a
# decision on record rather than an oversight.
expl_only = dict(bn_numerals, mcq_id="NUM_2",
                 explanation="Source: 8 administrative divisions.")
_, expl_codes = screener.screen(expl_only, [])
check("an ASCII digit in the explanation alone is out of scope",
      "ASCII_DIGITS" not in expl_codes)


# ---------------------------------------------------------------------------
print("TEST 3: a synonym of the correct answer is rejected as a distractor")
# ---------------------------------------------------------------------------
# MCQ_795d94e5: correct answer পৌহেলা বৈশাখ, distractor গ = নববর্ষ.
# The old gate scored that distractor 0.75 and passed the item.
check("SYNONYM_DISTRACTOR flagged on MCQ_795d94e5",
      "SYNONYM_DISTRACTOR" in results["MCQ_795d94e5"][1],
      "correct=পৌহেলা বৈশাখ vs distractor=নববর্ষ")

# A misspelled correct answer must not let the collision through: the check
# normalises orthography before comparing.
synthetic = {
    "mcq_id": "SYN_1", "fact_id": "F1",
    "question": "বাংলা বর্ষবরণের প্রধান উৎসব কোনটি?",
    "options": {"ক": "পহেলা বৈশাখ", "খ": "বাংলা নববর্ষ",
                "গ": "দোল উৎসব", "ঘ": "বসন্ত উৎসব"},
    "correct_answer": "ক", "difficulty": "medium", "explanation": "",
}
_, syn_codes = screener.screen(synthetic, [])
check("a correctly spelled synonym pair is caught too",
      "SYNONYM_DISTRACTOR" in syn_codes, "পহেলা বৈশাখ = বাংলা নববর্ষ")

no_synonym = dict(synthetic, mcq_id="SYN_2",
                  options={"ক": "পহেলা বৈশাখ", "খ": "দোল উৎসব",
                           "গ": "বসন্ত উৎসব", "ঘ": "নবান্ন"})
_, ok_codes = screener.screen(no_synonym, [])
check("genuinely distinct options are not flagged",
      "SYNONYM_DISTRACTOR" not in ok_codes)


# ---------------------------------------------------------------------------
print("TEST 3b: a duplicate that repeats the correct answer is E-MULTI, "
      "not just E-DIST")
# ---------------------------------------------------------------------------
# Sanity-check fix: classify_duplicate_option() used to be dead code --
# FAILURE_CODE_MAP sent every DUPLICATE_OPTIONS to E-DIST regardless of
# which two options collided, so a real second-correct-answer defect
# (distractor exactly repeats the correct answer) was indistinguishable
# from two merely-similar wrong distractors.
second_correct = {
    "mcq_id": "SYN_3", "fact_id": "F1",
    "question": "বাংলা বর্ষবরণের প্রধান উৎসব কোনটি?",
    "options": {"ক": "পহেলা বৈশাখ", "খ": "পহেলা বৈশাখ",
                "গ": "দোল উৎসব", "ঘ": "বসন্ত উৎসব"},
    "correct_answer": "ক", "difficulty": "medium", "explanation": "",
}
_, multi_codes = screener.screen(second_correct, [])
check("a distractor identical to the correct answer is still DUPLICATE_OPTIONS",
      "DUPLICATE_OPTIONS" in multi_codes)
check("...and is additionally flagged as a second-correct-answer defect",
      "DUPLICATE_MATCHES_CORRECT_ANSWER" in multi_codes)
check("...which maps onto E-MULTI, not just E-DIST",
      "E-MULTI" in map_codes(list(multi_codes)), str(map_codes(list(multi_codes))))

two_wrong_dupes = {
    "mcq_id": "SYN_4", "fact_id": "F1",
    "question": "বাংলা বর্ষবরণের প্রধান উৎসব কোনটি?",
    "options": {"ক": "পহেলা বৈশাখ", "খ": "দোল উৎসব",
                "গ": "দোল উৎসব", "ঘ": "বসন্ত উৎসব"},
    "correct_answer": "ক", "difficulty": "medium", "explanation": "",
}
_, dist_codes = screener.screen(two_wrong_dupes, [])
check("two duplicate WRONG distractors are only E-DIST, not E-MULTI",
      "DUPLICATE_MATCHES_CORRECT_ANSWER" not in dist_codes
      and "E-MULTI" not in map_codes(list(dist_codes)))


# ---------------------------------------------------------------------------
print("TEST 4: the translation guard separates a bad word from a big claim")
# ---------------------------------------------------------------------------
# MCQ_795d94e5 rendered "secular" as বিশ্বাস্ত ("trustworthy"). Handover §9.1
# warns this reads like an ungrounded claim but is not one — the source fact
# does say "largest secular festival". Two defects, two codes, two repairs.
check("MISTRANSLATION flagged on MCQ_795d94e5",
      "MISTRANSLATION" in results["MCQ_795d94e5"][1], "secular -> বিশ্বাস্ত")
check("DROPPED_QUALIFIER flagged on MCQ_795d94e5",
      "DROPPED_QUALIFIER" in results["MCQ_795d94e5"][1],
      "বৃহত্তম ... উৎসব without ধর্মনিরপেক্ষ")

# Same source fact, a question about the date: no superlative, no term, so
# neither code may fire. This is the false-positive test that matters.
check("the date question from the same fact is left alone",
      not ({"MISTRANSLATION", "DROPPED_QUALIFIER"} & results["MCQ_fd5da7cf"][1]),
      "MCQ_fd5da7cf")

fixed = {
    "mcq_id": "TR_1", "fact_id": "F2",
    "question": "বাংলাদেশের বৃহত্তম ধর্মনিরপেক্ষ উৎসব কোনটি?",
    "options": {"ক": "পহেলা বৈশাখ", "খ": "দোল উৎসব",
                "গ": "বসন্ত উৎসব", "ঘ": "নবান্ন"},
    "correct_answer": "ক", "difficulty": "medium", "explanation": "",
}
tr_fact = [{"fact_id": "F2", "text": "Pohela Boishakh is celebrated on 14 April "
                                     "each year as the largest secular festival "
                                     "in Bangladesh."}]
_, tr_codes = screener.screen(fixed, tr_fact)
check("the correctly translated question passes the guard",
      not ({"MISTRANSLATION", "DROPPED_QUALIFIER"} & set(tr_codes)),
      str(sorted(tr_codes)))

# Without supporting facts the guard has nothing to compare against. It must
# skip, not guess — a caller that forgets the argument should lose one check,
# not gain a false accusation.
_, no_facts_codes = screener.screen(fixed, None)
check("the guard is skipped, not guessed, when no source fact is supplied",
      not ({"MISTRANSLATION", "DROPPED_QUALIFIER"} & set(no_facts_codes)))


# ---------------------------------------------------------------------------
print("TEST 5: two questions answered by the same entity from one fact")
# ---------------------------------------------------------------------------
# Stem similarity does not see this pair: the two stems share one token.
check("MCQ_2051ff97 is flagged as re-asking MCQ_89c2bdcc",
      duplicates.get("MCQ_2051ff97") == "MCQ_89c2bdcc", str(duplicates))
check("only the later item of the pair is flagged",
      "MCQ_89c2bdcc" not in duplicates,
      "the first occurrence is a legitimate question")
check("different questions from one fact are not flagged",
      not ({"MCQ_84efae05", "MCQ_21ac5280", "MCQ_fd5da7cf", "MCQ_795d94e5"}
           & set(duplicates)),
      "১৯৫২ vs ঢাকা, and ১৪ এপ্রিল vs পহেলা বৈশাখ, are distinct answers")


# ---------------------------------------------------------------------------
print("TEST 6: each new code fails the MCQ rather than nudging its score")
# ---------------------------------------------------------------------------
for code in sorted(NEW_CODES):
    check("%s is a hard failure" % code, code in HARD_FAILURE_CODES)

# Task 2A's own numbers are the argument: MCQ_795d94e5 scored 0.878 overall
# with a mistranslation and a second correct option in it. A defect that only
# moves a weighted average cannot be relied on to stop anything.
mapped = map_codes(list(HARD_FAILURE_CODES))
check("every hard-failure code maps onto the §10.2 taxonomy",
      not any(c.startswith("UNMAPPED:") for c in mapped), str(mapped))


# ---------------------------------------------------------------------------
print("TEST 7: every catalogued Task 2A defect fails the new gate")
# ---------------------------------------------------------------------------
defective = {
    "MCQ_f78a10b0": "ASCII numerals in a Bengali MCQ",
    "MCQ_89c2bdcc": "misspelled river names",
    "MCQ_2051ff97": "misspelled river names + re-asks MCQ_89c2bdcc",
    "MCQ_fd5da7cf": "পৌহেলা",
    "MCQ_795d94e5": "পৌহেলা + বিশ্বাস্ত + নববর্ষ distractor",
}
for mcq_id, why in defective.items():
    hard = results[mcq_id][1] & set(HARD_FAILURE_CODES)
    check("%s now fails" % mcq_id, bool(hard),
          "%s -> %s" % (why, sorted(hard)))

# The two clean Culture items must survive. If the new checks failed the whole
# batch, they would be measuring nothing.
for mcq_id in ("MCQ_84efae05", "MCQ_21ac5280"):
    check("%s is untouched by the new checks" % mcq_id,
          not (results[mcq_id][1] & NEW_CODES),
          str(sorted(results[mcq_id][1])))


# ---------------------------------------------------------------------------
print()
for mcq_id, (score, codes) in results.items():
    print("  %-14s format=%.3f  %s" % (mcq_id, score, sorted(codes) or "clean"))

print()
passed = sum(CHECKS)
print("test_task2b_quality_gate: %d/%d CHECK True -- %s"
      % (passed, len(CHECKS), "PASS" if passed == len(CHECKS) else "FAIL"))

# Guarded so pytest can import this module (it has no test_* functions --
# it runs its checks as import-time side effects) without pytest's
# collector dying on an unguarded module-level SystemExit. Standalone
# `python test_task2b_quality_gate.py` runs still exit with the real pass/fail code.
if __name__ == "__main__":
    sys.exit(0 if passed == len(CHECKS) else 1)
