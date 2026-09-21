"""
rejection_taxonomy.py
======================
Single shared rejection taxonomy for the BCS-GK pipeline, per the
faculty guideline §8.4 / §10.2 / §19.3:

    "Cap revision rounds ... and report rejection rate."          (§8.4)
    "10.2 Rejection taxonomy to log: E-TIME, E-LEAK, E-UNSUP,
     E-MULTI, E-DIST, E-AMB, E-STYLE, E-DUP, E-KG, E-SRC."         (§10.2)
    "Verifier: ... explicit errors for multiple-correct-option
     cases."                                                       (§19.3)

Problem this fixes
-------------------
Every stage of the pipeline already produces a rejection reason, but each
stage invented its own free-text vocabulary:

    RuleBasedScreener (mcq_quality.py)  -> FORMAT_ERROR, DUPLICATE_OPTIONS,
                                            WEAK_DISTRACTORS
    LLMQualityEvaluator (mcq_quality.py)-> WRONG_GROUNDING, AMBIGUOUS_QUESTION,
                                            WEAK_DISTRACTORS, DUPLICATE_OPTIONS
    JudgeAgent (mcq_generator.py)       -> WRONG_GROUNDING, AMBIGUOUS_QUESTION,
                                            WEAK_DISTRACTORS, FORMAT_ERROR,
                                            REASONER_WRONG, DUPLICATE_OPTIONS
    MCQQualityEvaluator temporal check  -> POST_CUTOFF_LEAKAGE_RISK,
                                            TEMPORAL_UNVERSIONED
    DuplicateDetector (mcq_generator.py)-> (no code at all — just dropped)
    CutoffPolicy (web_scraper.py)       -> free-text `reason` strings
    fact_quality.py dedup               -> (no code at all)

None of that vocabulary is the paper's required taxonomy, so nothing in
this codebase can currently produce the §10.2 rejection-rate table a
reviewer will expect. This module is the *one place* that maps every
existing internal code onto the ten canonical labels, so every stage can
keep its own detailed internal reasons AND log a paper-ready code.

Usage
-----
    from rejection_taxonomy import map_codes, RejectionCode, RejectionTally

    canonical = map_codes(["WRONG_GROUNDING", "WEAK_DISTRACTORS"])
    # -> ["E-UNSUP", "E-DIST"]

    tally = RejectionTally()
    tally.add(canonical)
    ...
    print(tally.report())
    # {"E-UNSUP": 3, "E-DIST": 5, ..., "_total_rejections": 8, "_total_seen": 40}
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Dict, Iterable, List, Optional

log = logging.getLogger("rejection_taxonomy")


# ---------------------------------------------------------------------------
# The ten canonical codes (guideline §10.2, verbatim)
# ---------------------------------------------------------------------------

class RejectionCode:
    E_TIME  = "E-TIME"   # fact not valid at target time
    E_LEAK  = "E-LEAK"   # post-cutoff source/evidence detected
    E_UNSUP = "E-UNSUP"  # answer not supported by stored evidence
    E_MULTI = "E-MULTI"  # multiple options correct
    E_DIST  = "E-DIST"   # implausible or type-inconsistent distractor
    E_AMB   = "E-AMB"    # ambiguous/underspecified stem
    E_STYLE = "E-STYLE"  # not BCS-like
    E_DUP   = "E-DUP"    # duplicate / too close to previous item
    E_KG    = "E-KG"     # graph conflict unresolved
    E_SRC   = "E-SRC"    # source below required evidence tier

    # PRD / §10.2 Category aliases
    E_FACT  = "E-FACT"   # alias for E-UNSUP (factuality / ungroundedness violation)
    E_TEMP  = "E-TEMP"   # alias for E-TIME (temporal cutoff violation)
    E_AMBIG = "E-AMBIG"  # alias for E-AMB (ambiguous / underspecified stem)

    ALL = (E_TIME, E_LEAK, E_UNSUP, E_MULTI, E_DIST, E_AMB,
           E_STYLE, E_DUP, E_KG, E_SRC)


DESCRIPTIONS: Dict[str, str] = {
    RejectionCode.E_TIME:  "Fact not valid at target time t*",
    RejectionCode.E_LEAK:  "Post-cutoff source/evidence detected",
    RejectionCode.E_UNSUP: "Answer not supported by stored evidence",
    RejectionCode.E_MULTI: "Multiple options correct",
    RejectionCode.E_DIST:  "Implausible or type-inconsistent distractor",
    RejectionCode.E_AMB:   "Ambiguous/underspecified stem",
    RejectionCode.E_STYLE: "Not BCS-like",
    RejectionCode.E_DUP:   "Duplicate / too close to a previous item",
    RejectionCode.E_KG:    "Graph conflict unresolved",
    RejectionCode.E_SRC:   "Source below required evidence tier",
    RejectionCode.E_FACT:  "Factual hallucination or unverified claim",
    RejectionCode.E_TEMP:  "Temporal cutoff violation",
    RejectionCode.E_AMBIG: "Ambiguous or underspecified stem",
}


# ---------------------------------------------------------------------------
# Mapping: every internal free-text code this codebase already produces,
# onto the canonical taxonomy above.
# ---------------------------------------------------------------------------

FAILURE_CODE_MAP: Dict[str, str] = {
    # mcq_quality.RuleBasedScreener / LLMQualityEvaluator
    "FORMAT_ERROR":              RejectionCode.E_STYLE,
    "DUPLICATE_OPTIONS":         RejectionCode.E_DIST,
    "DUPLICATE_MATCHES_CORRECT_ANSWER": RejectionCode.E_MULTI,
    "WEAK_DISTRACTORS":          RejectionCode.E_DIST,
    "WRONG_GROUNDING":           RejectionCode.E_UNSUP,
    "AMBIGUOUS_QUESTION":        RejectionCode.E_AMB,

    # mcq_quality.MCQQualityEvaluator temporal hard/soft flags
    "POST_CUTOFF_LEAKAGE_RISK":  RejectionCode.E_LEAK,
    "TEMPORAL_UNVERSIONED":      RejectionCode.E_TIME,

    # mcq_quality.RuleBasedScreener — Task 2B language and duplication checks
    "MISSPELLING":               RejectionCode.E_STYLE,
    "ASCII_DIGITS":              RejectionCode.E_STYLE,
    "SYNONYM_DISTRACTOR":        RejectionCode.E_MULTI,
    "MISTRANSLATION":            RejectionCode.E_STYLE,
    "DROPPED_QUALIFIER":         RejectionCode.E_UNSUP,
    "NEAR_DUPLICATE":            RejectionCode.E_DUP,

    # mcq_generator.JudgeAgent-only reasons
    "REASONER_WRONG":            RejectionCode.E_UNSUP,

    # explicit multi-correct signal, if a caller ever emits it directly
    "MULTIPLE_CORRECT":          RejectionCode.E_MULTI,

    # Task 6.2/6.3: Distractor Plausibility & Ambiguity Screener
    "DISTRACTOR_VALID_AT_CUTOFF": RejectionCode.E_MULTI,

    # Task 7.1: Automated Factuality Verification Engine codes
    "FACTUALITY_HALLUCINATION":  RejectionCode.E_UNSUP,
    "UNVERIFIED_EVIDENCE":       RejectionCode.E_UNSUP,
    "UNSUPPORTED_FACT":          RejectionCode.E_UNSUP,
    "TEMPORAL_CUTOFF_VIOLATION": RejectionCode.E_TIME,
    "UNRELIABLE_SOURCE":         RejectionCode.E_SRC,
    "GRAPH_CONFLICT":            RejectionCode.E_KG,

    # Canonical code self-mappings and aliases
    "E-FACT":                    RejectionCode.E_UNSUP,
    "E_FACT":                    RejectionCode.E_UNSUP,
    "E-TEMP":                    RejectionCode.E_TIME,
    "E_TEMP":                    RejectionCode.E_TIME,
    "E-AMBIG":                   RejectionCode.E_AMB,
    "E_AMBIG":                   RejectionCode.E_AMB,
    "E-TIME":                    RejectionCode.E_TIME,
    "E-LEAK":                    RejectionCode.E_LEAK,
    "E-UNSUP":                   RejectionCode.E_UNSUP,
    "E-MULTI":                   RejectionCode.E_MULTI,
    "E-DIST":                    RejectionCode.E_DIST,
    "E-AMB":                     RejectionCode.E_AMB,
    "E-STYLE":                   RejectionCode.E_STYLE,
    "E-DUP":                     RejectionCode.E_DUP,
    "E-KG":                      RejectionCode.E_KG,
    "E-SRC":                     RejectionCode.E_SRC,
}


def map_codes(internal_codes: Iterable[str]) -> List[str]:
    """
    Translate a list of internal failure codes (any vocabulary already in
    use across mcq_quality.py / mcq_generator.py) into the canonical
    taxonomy. Order-preserving, de-duplicated.

    Unknown codes are NOT dropped silently: they come back prefixed with
    ``UNMAPPED:`` and a warning is logged, so a new failure string added
    somewhere in the pipeline shows up as a visible gap in the rejection
    report instead of disappearing.
    """
    seen: List[str] = []
    for code in internal_codes:
        mapped = FAILURE_CODE_MAP.get(code)
        if mapped is None:
            log.warning("rejection_taxonomy: no mapping for internal code %r "
                        "— add it to FAILURE_CODE_MAP.", code)
            mapped = f"UNMAPPED:{code}"
        if mapped not in seen:
            seen.append(mapped)
    return seen


def classify_duplicate_option(duplicate_value_is_correct_answer: bool) -> str:
    """
    A duplicated option value is either:
      - a plain distractor-quality problem (E-DIST), or
      - an actual multiple-correct-option case (E-MULTI), if the
        duplicated text matches the *correct* answer's text.
    """
    return RejectionCode.E_MULTI if duplicate_value_is_correct_answer else RejectionCode.E_DIST


def classify_duplicate_mcq() -> str:
    """A whole MCQ matched an earlier/seen question fingerprint (§10.2 E-DUP)."""
    return RejectionCode.E_DUP


def classify_kg_conflict() -> str:
    """A KG-level conflict (unresolved duplicate/contradictory fact) (§10.2 E-KG)."""
    return RejectionCode.E_KG


def classify_source_decision(decision: str, reason: str,
                              source_tier: Optional[int] = None,
                              min_required_tier: Optional[int] = None) -> Optional[str]:
    """
    Map a web_scraper.CutoffPolicy / WebScraper source-level decision onto
    the taxonomy. Returns None for "accepted" (nothing to log).

    decision : "accepted" | "rejected" | "archived_fallback" | "needs_archive"
    reason   : the free-text reason string already produced by CutoffPolicy
    source_tier / min_required_tier : if a tier floor is enforced
        (guideline §19.3 "cutoff violations automatically blocked"; tier
        floors are the acquisition-side counterpart), a source below the
        floor is E-SRC even if it isn't a temporal violation.
    """
    if decision == "accepted":
        return None

    reason_lower = (reason or "").lower()
    is_temporal_reason = any(
        kw in reason_lower
        for kw in ("cutoff", "published", "modified", "publication date")
    )

    if (min_required_tier is not None and source_tier is not None
            and source_tier > min_required_tier and not is_temporal_reason):
        # Higher tier number = lower quality per web_scraper.infer_source_tier
        # (tier 1 = official ... tier 5 = secondary). Below-floor tier and
        # NOT a temporal issue -> E-SRC.
        return RejectionCode.E_SRC

    # Default: any cutoff-policy rejection is a leakage-prevention decision.
    return RejectionCode.E_LEAK


# ---------------------------------------------------------------------------
# Tally / reporting helper — the piece needed to actually "report
# rejection rate" (§8.4) instead of just logging codes one at a time.
# ---------------------------------------------------------------------------

class RejectionTally:
    """
    Accumulates canonical rejection codes across a run (one generation
    episode, one evaluation batch, or a whole pipeline invocation) and
    produces the summary table the paper needs.
    """

    def __init__(self):
        self._counts: Counter = Counter()
        self._total_seen = 0
        self._items_rejected = 0

    def add(self, codes: Iterable[str], is_rejection: bool = True) -> None:
        """
        Register one item's outcome.

        codes        : canonical E-* codes for this item (empty if it passed)
        is_rejection : whether this item counts toward the denominator of
                       "how many items were evaluated" even if `codes` is
                       empty (pass True once per MCQ/source/etc. regardless
                       of pass/fail; this method itself only increments the
                       numerator for non-empty `codes`).
        """
        self._total_seen += 1
        codes = list(codes)
        if codes:
            self._items_rejected += 1
        for c in codes:
            self._counts[c] += 1

    def report(self) -> Dict[str, object]:
        total_rejections = sum(self._counts.values())
        out: Dict[str, object] = {code: self._counts.get(code, 0) for code in RejectionCode.ALL}
        # Surface any UNMAPPED: codes too, so they aren't silently dropped
        # from the report the way an unrecognised free-text reason used to be.
        for code, n in self._counts.items():
            if code.startswith("UNMAPPED:"):
                out[code] = n
        out["_total_items"] = self._total_seen
        out["_total_rejections"] = total_rejections
        out["_items_rejected"] = self._items_rejected
        # _rejection_rate is the guideline-8.4 rate: the fraction of items
        # that failed at least once, not the fraction of individual codes
        # (one item can carry multiple codes, e.g. E-DIST + E-AMB, which
        # must not push the rate above 1.0).
        out["_rejection_rate"] = (
            round(self._items_rejected / self._total_seen, 4) if self._total_seen else None
        )
        return out

    def summary_str(self) -> str:
        rep = self.report()
        lines = ["Rejection taxonomy report (guideline §10.2):"]
        for code in RejectionCode.ALL:
            n = rep.get(code, 0)
            if n:
                lines.append(f"  {code:<8} {DESCRIPTIONS[code]:<45} n={n}")
        unmapped = {k: v for k, v in rep.items() if isinstance(k, str) and k.startswith("UNMAPPED:")}
        for k, v in unmapped.items():
            lines.append(f"  {k:<8} (no taxonomy mapping yet)                    n={v}")
        lines.append(f"  Total items evaluated : {rep['_total_items']}")
        lines.append(f"  Items rejected        : {rep['_items_rejected']}")
        lines.append(f"  Total rejection codes : {rep['_total_rejections']}")
        lines.append(f"  Rejection rate        : {rep['_rejection_rate']}")
        return "\n".join(lines)