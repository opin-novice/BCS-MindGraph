"""

Responsibilities
----------------
1. Multi-dimensional MCQ quality scoring.
2. BCS-alignment checks against corpus patterns.
3. Quality improvement log with per-dimension breakdowns.
4. Integration hooks for Saif's EpisodicMemory diagnostics.
5. Batch quality reporting for Sadia's BCS corpus evaluation.

Dimensions
----------
  format_score        (20%) — structure, option count, Bengali text
  grounding_score     (35%) — answer traceable to KG fact
  clarity_score       (25%) — unambiguous, single correct answer
  distractor_score    (20%) — distractors plausible but clearly wrong

Standalone Usage
----------------
    python mcq_quality.py
"""

import json
import logging
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hf_client import DEFAULT_MODEL
from rejection_taxonomy import map_codes, RejectionTally

log = logging.getLogger("mcq_quality")

# ---------------------------------------------------------------------------
# Dimension weights (must sum to 1.0)
# ---------------------------------------------------------------------------
DIMENSION_WEIGHTS = {
    "format_score":     0.20,
    "grounding_score":  0.35,
    "clarity_score":    0.25,
    "distractor_score": 0.20,
}

# Thresholds
PASS_THRESHOLD     = 0.70
GROUNDING_MIN      = 0.80   # hard floor — factual accuracy is non-negotiable
DISTRACTOR_MIN     = 0.55

# Failure codes that fail an MCQ outright, whatever the composite score is.
# The first three predate Task 2B; the rest are the language and duplication
# defects Task 2A shipped past a 4/4 PASS verdict (handover §9.1).
HARD_FAILURE_CODES = (
    "FORMAT_ERROR",
    "WRONG_GROUNDING",
    "POST_CUTOFF_LEAKAGE_RISK",
    "MISSPELLING",
    "ASCII_DIGITS",
    "SYNONYM_DISTRACTOR",
    "MISTRANSLATION",
    "DROPPED_QUALIFIER",
    "NEAR_DUPLICATE",
    # Task 6.2: a distractor still valid at t* is a second correct answer.
    "DISTRACTOR_VALID_AT_CUTOFF",
    # Task 7.1: Automated Factuality Verification Engine codes.
    "FACTUALITY_HALLUCINATION",
    "UNVERIFIED_EVIDENCE",
    "TEMPORAL_CUTOFF_VIOLATION",
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DimensionScores:
    format_score:     float = 0.0
    grounding_score:  float = 0.0
    clarity_score:    float = 0.0
    distractor_score: float = 0.0

    @property
    def composite(self) -> float:
        return (
            self.format_score     * DIMENSION_WEIGHTS["format_score"]
            + self.grounding_score  * DIMENSION_WEIGHTS["grounding_score"]
            + self.clarity_score    * DIMENSION_WEIGHTS["clarity_score"]
            + self.distractor_score * DIMENSION_WEIGHTS["distractor_score"]
        )

    def to_dict(self) -> Dict:
        return {
            "format_score":     round(self.format_score,     3),
            "grounding_score":  round(self.grounding_score,  3),
            "clarity_score":    round(self.clarity_score,    3),
            "distractor_score": round(self.distractor_score, 3),
            "composite":        round(self.composite,        3),
        }


@dataclass
class MCQEvaluation:
    """Full evaluation record for one MCQ."""
    eval_id:       str
    mcq_id:        str
    fact_id:       str
    question:      str
    difficulty:    str
    topic:         str            # ← NEW: store topic so get_topic_pass_rate() works correctly
    scores:        DimensionScores
    passed:        bool
    failure_codes: List[str]      # e.g. ["WEAK_DISTRACTORS", "AMBIGUOUS_QUESTION"]
    feedback:      str
    suggestions:   List[str]
    temporal_status: Optional[str] = None
    # One of:
    #   "valid_at_cutoff"  — fact's interval was checked and covers t*
    #   "unversioned"      — fact has no valid_from/valid_to, wasn't checked
    #   "NOT_IN_APPROVED_SET" — fact_id isn't among the cutoff-approved
    #                        supporting_facts at all; a leakage-risk signal,
    #                        see guideline §13.2 PCLR
    #   None               — no cutoff was in effect for this evaluation
    evaluated_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    near_duplicate_of: Optional[str] = None
    # Task 2B: the mcq_id this item re-asks, when the batch-level check
    # found one. Recorded rather than only coded, so a reviewer can see
    # WHICH earlier question it collides with without re-deriving it.
    rejection_codes: List[str] = field(default_factory=list)
    # `failure_codes` translated onto the guideline's §10.2 taxonomy
    # (E-TIME/E-LEAK/E-UNSUP/E-MULTI/E-DIST/E-AMB/E-STYLE/E-DUP/E-KG/E-SRC)
    # via rejection_taxonomy.map_codes(). Kept alongside the original
    # internal codes rather than replacing them, so existing consumers of
    # `failure_codes` (get_failed_fact_ids, improvement log, etc.) keep
    # working unchanged.

    @property
    def overall_score(self) -> float:
        return self.scores.composite

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["scores"] = self.scores.to_dict()
        d["overall_score"] = round(self.overall_score, 3)
        return d


@dataclass
class QualityReport:
    """Aggregate quality report for a batch of MCQs."""
    report_id:     str
    topic:         str
    difficulty:    str
    total:         int
    passed:        int
    failed:        int
    avg_score:     float
    avg_grounding: float
    avg_distractor:float
    pass_rate:     float
    evaluations:   List[MCQEvaluation]
    generated_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary_str(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"Quality Report — {self.report_id}\n"
            f"  Topic      : {self.topic}\n"
            f"  Difficulty : {self.difficulty}\n"
            f"  Total MCQs : {self.total}\n"
            f"  Passed     : {self.passed}  ({self.pass_rate*100:.1f}%)\n"
            f"  Avg Score  : {self.avg_score:.3f}\n"
            f"  Avg Ground : {self.avg_grounding:.3f}\n"
            f"  Avg Dist.  : {self.avg_distractor:.3f}\n"
            f"{'='*60}"
        )

# ---------------------------------------------------------------------------
# Task 2B — controlled language tables (no LLM, no fuzzy guessing)
#
# Task 2A's smoke run produced four defects that the gate scored as PASS:
# misspelled proper nouns, a mistranslated source term, a distractor that
# was a synonym of the correct answer, and two questions that re-asked the
# same fact. All four are deterministic to detect, so they are caught here
# rather than by hoping an LLM judge notices them.
#
# Every table below is a CONTROLLED list, deliberately short. A checker
# that guessed at Bengali spelling would reject correct forms, which is the
# worse failure of the two: a rejected good MCQ is invisible, an accepted
# bad MCQ is at least auditable. Add entries only when a real run produces
# the error.
# ---------------------------------------------------------------------------

#: Wrong form -> standard form. These three are the misspellings the Task 2A
#: batch actually produced (handover §9.1).
BANGLA_ORTHOGRAPHY: Dict[str, str] = {
    "পৌহেলা":   "পহেলা",
    "বুরিগঙ্গা":  "বুড়িগঙ্গা",
    "পাদমা":    "পদ্মা",
}

#: Surface forms that name the same thing. A distractor in the same class as
#: the correct answer is not a distractor — it is a second correct option,
#: which is why it maps onto the taxonomy's E-MULTI rather than E-DIST.
SYNONYM_CLASSES: Tuple[frozenset, ...] = (
    frozenset({"পহেলা বৈশাখ", "পয়লা বৈশাখ", "বাংলা নববর্ষ", "নববর্ষ", "বর্ষবরণ"}),
)

#: Source-term translation guard. `en` is matched against the English
#: supporting fact; the MCQ's Bengali text is then checked for a known wrong
#: rendering (MISTRANSLATION) and, where the English term qualifies a
#: superlative, for that qualifier having been dropped (DROPPED_QUALIFIER).
#:
#: The two are kept apart on purpose. Handover §9.1 warns that Task 2A's
#: "বৃহত্তম ... উৎসব" looked ungrounded but was in fact a translation bug —
#: the fact does say "largest secular festival". A garbled word and a claim
#: broader than its evidence need different repairs, so they get different
#: codes.
TRANSLATION_GUARD: Tuple[Dict[str, object], ...] = (
    {
        "term":     "secular",
        "en":       r"\bsecular\b",
        "required": ("ধর্মনিরপেক্ষ",),
        "wrong":    ("বিশ্বাস্ত", "বিশ্বস্ত", "ধার্মিক", "ধর্মীয়", "পবিত্র"),
        "qualified_superlative": r"\b(largest|biggest|greatest)\s+secular\b",
        "bn_superlatives": ("বৃহত্তম", "সর্ববৃহৎ", "সর্ববৃহত্তম", "সবচেয়ে বড়"),
    },
)

#: Tokens too generic to identify an answer. Two MCQs from one fact whose
#: answers share only "নদী" are not necessarily the same question; two that
#: share "বুড়িগঙ্গা" are.
GENERIC_ANSWER_TOKENS = frozenset({
    "নদী", "নদীর", "তীরে", "তীর", "শহর", "শহরে", "সাল", "বছর",
    "তারিখ", "উৎসব", "জেলা", "বিভাগ", "এলাকা",
})

#: Minimum token length for the near-duplicate answer match. Prefix matching
#: at this length absorbs Bengali inflection (নদী/নদীর) without colliding
#: short tokens such as numerals.
_MIN_ANSWER_TOKEN = 4


def normalise_bn(text: str) -> str:
    """Case-folded, whitespace-collapsed, punctuation-stripped NFC form."""
    t = unicodedata.normalize("NFC", text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" ।.,;:!?\"'()[]-—")


def correct_orthography(text: str) -> str:
    """Rewrite every known misspelling in `text` to its standard form."""
    out = text or ""
    for wrong, right in BANGLA_ORTHOGRAPHY.items():
        out = out.replace(wrong, right)
    return out


def find_misspellings(text: str) -> List[str]:
    """Known wrong forms present in `text`, in table order."""
    haystack = unicodedata.normalize("NFC", text or "")
    return [w for w in BANGLA_ORTHOGRAPHY if w in haystack]


def _synonym_class_of(option_text: str) -> Optional[int]:
    key = normalise_bn(correct_orthography(option_text))
    for i, cls in enumerate(SYNONYM_CLASSES):
        if key in cls:
            return i
    return None


def _answer_tokens(answer_text: str) -> List[str]:
    """Content tokens of an answer string, for near-duplicate matching."""
    norm = normalise_bn(correct_orthography(answer_text))
    return [t for t in norm.split()
            if len(t) >= _MIN_ANSWER_TOKEN and t not in GENERIC_ANSWER_TOKENS]


def _answers_name_the_same_thing(a: str, b: str) -> bool:
    """True if two answer strings share a content token (prefix-tolerant)."""
    for ta in _answer_tokens(a):
        for tb in _answer_tokens(b):
            if ta.startswith(tb) or tb.startswith(ta):
                return True
    return False


def find_near_duplicates(mcqs: List[Dict]) -> Dict[str, str]:
    """
    Batch-level check: which MCQs re-ask what an earlier MCQ in the same
    batch already asked?

    Task 2A produced "নিচের কোনটি বাংলাদেশের রাজধানীর অবস্থান নির্দেশ করে?"
    and "বাংলাদেশের রাজধানী কোন নদীর কাছে অবস্থিত?" from one fact. Their
    stems share a single token, so stem similarity does not see it; what
    makes them the same question is that both are answered by বুড়িগঙ্গা out
    of the same fact. That is the signal used here: same `fact_id`, and
    correct answers naming the same entity.

    Returns {duplicate_mcq_id: first_mcq_id}. Only the later item is
    flagged — the first occurrence is a legitimate question.
    """
    seen: List[Tuple[str, str, str]] = []      # (fact_id, correct_text, mcq_id)
    flagged: Dict[str, str] = {}
    for m in mcqs:
        mcq_id  = m.get("mcq_id", "")
        fact_id = m.get("fact_id", "")
        options = m.get("options", {}) or {}
        correct = options.get(m.get("correct_answer", ""), "")
        if not correct:
            continue
        for prev_fact, prev_correct, prev_id in seen:
            if prev_fact == fact_id and _answers_name_the_same_thing(correct, prev_correct):
                flagged[mcq_id] = prev_id
                break
        else:
            seen.append((fact_id, correct, mcq_id))
    return flagged


# ---------------------------------------------------------------------------
# Task 6.3: Distractor Plausibility & Ambiguity Screener
# ---------------------------------------------------------------------------

def check_distractor_plausibility(
    mcq_dict: Dict,
    kg_builder: object,
    t_cutoff: str = "2023-04-19",
) -> List[str]:
    """
    Verify that no distractor option is simultaneously valid at ``t_cutoff``
    in the knowledge graph (acceptance criterion: "reject distractors that
    are synonymous or simultaneously valid at t*").

    Strategy
    --------
    1. Resolve anchor fact's subject and relation via ``get_bitemporal_tuple``
       or node attributes if ``fact_id`` is supplied.
    2. For each non-correct distractor option text, check if any entity in the
       KG matching this text is the object of a fact with the SAME subject
       and relation (or linked to the same subject entity) that is valid
       at ``t_cutoff`` (valid_from <= t_cutoff < valid_to or open interval).
       If so, the distractor is simultaneously valid at t*, creating a
       multiple-correct-answer defect (E-MULTI).
    3. If anchor fact or relation is unknown, checks if the distractor entity
       is asserted in any active fact connected to any entity in the question stem.

    Parameters
    ----------
    mcq_dict   : MCQ dict (question, options, correct_answer, fact_id, …).
    kg_builder : KnowledgeGraphBuilder instance. If None or missing the
                 ``graph`` attribute, the check is skipped (returns []).
    t_cutoff   : ISO date string, default "2023-04-19".

    Returns
    -------
    List of failure codes — [] if clean, or ["DISTRACTOR_VALID_AT_CUTOFF"].
    """
    if kg_builder is None or not hasattr(kg_builder, "graph"):
        return []

    options     = mcq_dict.get("options", {}) or {}
    correct_key = mcq_dict.get("correct_answer", "")
    if not options or not correct_key:
        return []

    cutoff_key = t_cutoff.replace("-", "")[:8]

    def _norm(s: Any) -> str:
        import unicodedata as _ud
        t = _ud.normalize("NFC", str(s or "")).strip().lower()
        return re.sub(r"\s+", " ", t)

    distractor_texts = [
        _norm(text)
        for key, text in options.items()
        if key != correct_key and text
    ]
    if not distractor_texts:
        return []

    g = kg_builder.graph

    # Resolve target subject and relation from anchor fact
    target_subj = None
    target_rel = None
    anchor_fact_id = mcq_dict.get("fact_id")

    if anchor_fact_id and hasattr(kg_builder, "get_bitemporal_tuple"):
        bt = kg_builder.get_bitemporal_tuple(anchor_fact_id)
        if bt:
            target_subj = bt.get("subject")
            target_rel = bt.get("relation")

    if not target_subj and anchor_fact_id and g.has_node(anchor_fact_id):
        fdata = g.nodes[anchor_fact_id]
        target_rel = fdata.get("relation")
        for src, _, edata in g.in_edges(anchor_fact_id, data=True):
            if edata.get("relation") == "SUBJECT_OF":
                target_subj = g.nodes.get(src, {}).get("name")
                break

    target_subj_norm = _norm(
        target_subj if isinstance(target_subj, str)
        else (target_subj[0] if isinstance(target_subj, list) and target_subj else "")
    )

    from kg_builder import _date_key

    def _fact_valid_at_cutoff(fid: str) -> bool:
        fdata = g.nodes.get(fid)
        if not fdata:
            return False
        vf = fdata.get("valid_from")
        vt = fdata.get("valid_to")
        if vf is None and vt is None:
            return True
        vf_key = _date_key(vf) if vf else ""
        vt_key = _date_key(vt) if vt else "9999-99-99"
        return vf_key <= cutoff_key < vt_key

    # Collect facts for each distractor name
    for dist_norm in distractor_texts:
        for node_id, ndata in g.nodes(data=True):
            if ndata.get("type") != "ENTITY":
                continue
            if _norm(ndata.get("name", "")) != dist_norm:
                continue

            # Check facts where this entity is the object
            for src, _, edata in g.in_edges(node_id, data=True):
                if edata.get("relation") != "OBJECT_IS":
                    continue
                if g.nodes.get(src, {}).get("type") != "FACT":
                    continue
                if src == anchor_fact_id:
                    continue  # Anchor fact itself

                fact_data = g.nodes[src]
                fact_rel = fact_data.get("relation")

                # Find subject of this candidate fact
                cand_subj = None
                for s_src, _, s_edata in g.in_edges(src, data=True):
                    if s_edata.get("relation") == "SUBJECT_OF":
                        cand_subj = g.nodes.get(s_src, {}).get("name")
                        break
                cand_subj_norm = _norm(cand_subj)

                # Match criteria:
                # If target subject & relation are known, must match both
                # Otherwise, if stem mentions the candidate subject, match
                matches_scope = False
                if target_subj_norm and target_rel:
                    if cand_subj_norm == target_subj_norm and fact_rel == target_rel:
                        matches_scope = True
                elif target_subj_norm and cand_subj_norm == target_subj_norm:
                    matches_scope = True
                elif not target_subj_norm and cand_subj_norm and cand_subj_norm in _norm(mcq_dict.get("question", "")):
                    matches_scope = True

                if matches_scope and _fact_valid_at_cutoff(src):
                    log.info(
                        "check_distractor_plausibility: distractor '%s' is simultaneously "
                        "valid for subject '%s' relation '%s' at %s via fact %s — DISTRACTOR_VALID_AT_CUTOFF",
                        dist_norm, cand_subj or target_subj, fact_rel, t_cutoff, src,
                    )
                    return ["DISTRACTOR_VALID_AT_CUTOFF"]

    return []


# ---------------------------------------------------------------------------
# Task 7.1: Automated Factuality Verification Engine
# ---------------------------------------------------------------------------

@dataclass
class FactualityVerificationResult:
    """Outcome of verifying one MCQ against bitemporal KG evidence."""
    mcq_id:         str
    fact_id:        str
    verified:       bool
    failure_codes:  List[str] = field(default_factory=list)
    detail:         str = ""

    def to_dict(self) -> Dict:
        return {
            "mcq_id":        self.mcq_id,
            "fact_id":       self.fact_id,
            "verified":      self.verified,
            "failure_codes": self.failure_codes,
            "detail":        self.detail,
        }


class FactualityVerificationEngine:
    """
    Cross-verifies generated MCQ options against bitemporal KG evidence.

    Checks (in order):
      1. **Existence**: the cited ``fact_id`` must exist in the KG's
         approved snapshot. If missing → ``UNVERIFIED_EVIDENCE``.
      2. **Temporal validity**: the fact's ``valid_to`` must extend past
         ``t_cutoff`` (or be open-ended). An expired fact (``valid_to <=
         t_cutoff``) → ``TEMPORAL_CUTOFF_VIOLATION``.
      3. **Source provenance**: the fact's ``source_published_at`` or
         ``valid_from`` must not post-date ``t_cutoff``.  If it does →
         ``POST_CUTOFF_LEAKAGE_RISK`` (already a hard failure from the
         existing pipeline; included here for completeness).
      4. **Textual grounding**: the correct answer text must appear in the
         fact's stored text, subject, or object.  Failure →
         ``FACTUALITY_HALLUCINATION``.

    The engine is deliberately conservative: when a KG builder is not
    supplied or a fact has no temporal metadata, the check is skipped (not
    failed), because the pipeline ran without bitemporal enrichment and
    failing it would punish legacy facts that never had those fields.

    Usage::

        engine = FactualityVerificationEngine(kg_builder, t_cutoff="2023-04-19")
        result = engine.verify(mcq_dict)
        if not result.verified:
            all_failures.extend(result.failure_codes)
    """

    def __init__(
        self,
        kg_builder: object,
        t_cutoff: str = "2023-04-19",
    ):
        self.kg_builder = kg_builder
        self.t_cutoff = t_cutoff
        self._cutoff_key = t_cutoff.replace("-", "")[:8]

    def verify(self, mcq_dict: Dict) -> FactualityVerificationResult:
        """
        Verify a single MCQ's factuality against the bitemporal KG.
        """
        mcq_id = mcq_dict.get("mcq_id", "UNKNOWN")
        fact_id = mcq_dict.get("fact_id", "")
        failures: List[str] = []
        details: List[str] = []

        if not self.kg_builder or not hasattr(self.kg_builder, "graph"):
            return FactualityVerificationResult(
                mcq_id=mcq_id, fact_id=fact_id, verified=True,
                detail="KG builder not available; skipped.",
            )

        g = self.kg_builder.graph

        # ── Check 1: Existence ─────────────────────────────────────
        if not fact_id or not g.has_node(fact_id):
            failures.append("UNVERIFIED_EVIDENCE")
            details.append(
                f"fact_id={fact_id!r} not found in KG snapshot."
            )
            return FactualityVerificationResult(
                mcq_id=mcq_id, fact_id=fact_id, verified=False,
                failure_codes=failures, detail=" | ".join(details),
            )

        fdata = g.nodes[fact_id]

        # ── Check 2: Temporal validity ─────────────────────────────
        valid_to = fdata.get("valid_to")
        if valid_to is not None:
            vt_key = str(valid_to).replace("-", "")[:8]
            if vt_key <= self._cutoff_key:
                failures.append("TEMPORAL_CUTOFF_VIOLATION")
                details.append(
                    f"Fact expired: valid_to={valid_to} <= t*={self.t_cutoff}."
                )

        # ── Check 3: Source provenance ─────────────────────────────
        src_pub = fdata.get("source_published_at")
        valid_from = fdata.get("valid_from")
        if src_pub is not None:
            sp_key = str(src_pub).replace("-", "")[:8]
            if sp_key > self._cutoff_key:
                failures.append("POST_CUTOFF_LEAKAGE_RISK")
                details.append(
                    f"Source published post-cutoff: {src_pub} > {self.t_cutoff}."
                )
        if valid_from is not None:
            vf_key = str(valid_from).replace("-", "")[:8]
            if vf_key > self._cutoff_key:
                if "POST_CUTOFF_LEAKAGE_RISK" not in failures:
                    failures.append("POST_CUTOFF_LEAKAGE_RISK")
                details.append(
                    f"valid_from post-cutoff: {valid_from} > {self.t_cutoff}."
                )

        # ── Check 4: Textual grounding (correct answer vs fact) ────
        options = mcq_dict.get("options", {}) or {}
        correct_key = mcq_dict.get("correct_answer", "")
        correct_text = (options.get(correct_key, "") or "").strip()

        if correct_text:
            # Gather all text associated with the fact node
            fact_text = str(fdata.get("text", "") or fdata.get("fact_text", "") or "")
            fact_subj = ""
            fact_obj = ""

            # Resolve subject and object entities from edges
            for src, _, edata in g.in_edges(fact_id, data=True):
                rel = edata.get("relation", "")
                if rel == "SUBJECT_OF":
                    fact_subj = str(g.nodes.get(src, {}).get("name", ""))
                elif rel == "HAS_FACT":
                    fact_subj = str(g.nodes.get(src, {}).get("name", ""))
            for _, tgt, edata in g.out_edges(fact_id, data=True):
                rel = edata.get("relation", "")
                if rel == "OBJECT_IS":
                    fact_obj = str(g.nodes.get(tgt, {}).get("name", ""))

            evidence_pool = " ".join([fact_text, fact_subj, fact_obj]).lower()
            answer_norm = correct_text.lower().strip()

            if answer_norm and answer_norm not in evidence_pool:
                # Also try individual tokens (for multi-word answers where
                # the fact stores the entity in a slightly different form)
                answer_tokens = [t for t in answer_norm.split() if len(t) >= 3]
                if answer_tokens and not any(t in evidence_pool for t in answer_tokens):
                    failures.append("FACTUALITY_HALLUCINATION")
                    details.append(
                        f"Correct answer {correct_text!r} not grounded "
                        f"in fact text/subject/object."
                    )

        verified = len(failures) == 0
        return FactualityVerificationResult(
            mcq_id=mcq_id, fact_id=fact_id, verified=verified,
            failure_codes=failures, detail=" | ".join(details),
        )


def verify_factuality(
    mcq_dict: Dict,
    kg_builder: object,
    t_cutoff: str = "2023-04-19",
) -> FactualityVerificationResult:
    """
    Module-level convenience wrapper around ``FactualityVerificationEngine``.

    Returns a :class:`FactualityVerificationResult` for a single MCQ.
    When ``kg_builder`` is None or lacks a ``graph`` attribute, the check
    is trivially passed (``verified=True``, no failure codes).
    """
    engine = FactualityVerificationEngine(kg_builder, t_cutoff)
    return engine.verify(mcq_dict)


# ---------------------------------------------------------------------------
# Rule-based pre-screening (fast, no LLM)
# ---------------------------------------------------------------------------


class RuleBasedScreener:
    """
    Fast structural checks run before the LLM evaluator.
    Catches obvious formatting failures cheaply.

    Task 2B added three language-level checks to the same pass — spelling,
    synonym distractors, and source-term translation. They live here, and
    not in the LLM evaluator, because all three are decidable from a
    controlled table: they cost nothing, never vary between runs, and a
    judge model demonstrably missed all three on the Task 2A batch while
    scoring it 4/4 PASS.
    """

    EXPECTED_OPTION_KEYS = {"ক", "খ", "গ", "ঘ"}
    BANGLA_CHAR_RE = re.compile(r"[ঀ-৿]")

    #: Task 2B (PI ruling, 2026-09-17): a standard Bengali MCQ carries only
    #: Bengali numerals. Task 2A's MCQ_f78a10b0 offered 7/8/9/10 inside a
    #: Bengali stem. Checked over the stem and the options — the two places
    #: the ruling names — and not the explanation.
    ASCII_DIGIT_RE = re.compile(r"[0-9]")

    def screen(
        self,
        mcq_dict: Dict,
        supporting_facts: Optional[List[Dict]] = None,
        kg_builder: Optional[object] = None,
        t_cutoff: str = "2023-04-19",
    ) -> Tuple[float, List[str]]:
        """
        Returns (format_score 0–1, list of failure codes).

        ``supporting_facts`` is optional so existing callers keep working. It
        is only needed by the translation guard, which compares the Bengali
        MCQ against the English source fact; without it that one check is
        skipped rather than guessed at.

        ``kg_builder`` and ``t_cutoff`` are optional (Task 6.2). When provided,
        the Distractor Plausibility & Ambiguity Screener is activated: any
        distractor option whose entity is still simultaneously valid in the KG
        at ``t_cutoff`` is flagged as ``DISTRACTOR_VALID_AT_CUTOFF`` (hard
        failure, same severity as ``SYNONYM_DISTRACTOR``).
        """
        score = 1.0
        failures = []

        options = mcq_dict.get("options", {})
        question = mcq_dict.get("question", "")
        correct  = mcq_dict.get("correct_answer", "")

        # ── Option count ────────────────────────────────────────────
        if set(options.keys()) != self.EXPECTED_OPTION_KEYS:
            score -= 0.40
            failures.append("FORMAT_ERROR")

        # ── Correct answer is a valid key ───────────────────────────
        if correct not in self.EXPECTED_OPTION_KEYS:
            score -= 0.30
            failures.append("FORMAT_ERROR")

        # ── Contains Bengali text ───────────────────────────────────
        if not self.BANGLA_CHAR_RE.search(question):
            score -= 0.20
            failures.append("FORMAT_ERROR")

        # ── Duplicate options ────────────────────────────────────────
        values = list(options.values())
        if len(set(v.strip().lower() for v in values)) < len(values):
            score -= 0.25
            failures.append("DUPLICATE_OPTIONS")
            # Task 19.3 / SS10.2: a duplicate that specifically matches the
            # *correct* answer's text is a second-correct-answer defect
            # (E-MULTI), not merely a weak/redundant distractor (E-DIST).
            # classify_duplicate_option() carries that distinction; wire it
            # in here instead of letting every duplicate fall through to
            # the same generic code regardless of which options collided.
            correct_text = options.get(correct, "").strip().lower()
            other_values = [v.strip().lower() for k, v in options.items() if k != correct]
            if correct_text and correct_text in other_values:
                from rejection_taxonomy import classify_duplicate_option
                if classify_duplicate_option(True) == "E-MULTI":
                    failures.append("DUPLICATE_MATCHES_CORRECT_ANSWER")

        # ── Distractor diversity (all difficulty levels) ─────────────
        # At least 2 of the 3 distractors must differ by more than
        # 3 characters from the correct option to count as diverse.
        # This catches near-identical options that inflate DCE even on easy MCQs.
        correct_text = options.get(correct, "").strip().lower()
        distractors  = [v.strip().lower() for k, v in options.items() if k != correct]

        def _char_diff(a: str, b: str) -> int:
            """Count characters in `a` not present at the same position in `b`."""
            return sum(1 for ca, cb in zip(a.ljust(len(b)), b.ljust(len(a))) if ca != cb)

        diverse = sum(1 for d in distractors if _char_diff(d, correct_text) > 3)
        if diverse < 2:
            score -= 0.20
            failures.append("WEAK_DISTRACTORS")

        # ── Question length sanity ───────────────────────────────────
        if len(question.strip()) < 10:
            score -= 0.15
            failures.append("FORMAT_ERROR")

        # ── Task 2B (খ.1): standard Bengali spelling ─────────────────
        # Checked across stem, options and explanation: Task 2A misspelled
        # বুড়িগঙ্গা in the options only, where a stem-only check misses it.
        mcq_text = " ".join(
            [question, mcq_dict.get("explanation", "") or ""] + list(options.values())
        )
        if find_misspellings(mcq_text):
            score -= 0.15
            failures.append("MISSPELLING")

        # ── Task 2B (PI ruling): Bengali numerals only ───────────────
        # Deliberately narrower than `mcq_text` above: the stem and the
        # options are what a candidate reads on the paper.
        graded_text = " ".join([question] + list(options.values()))
        if self.ASCII_DIGIT_RE.search(graded_text):
            score -= 0.15
            failures.append("ASCII_DIGITS")

        # ── Task 2B (খ.2): a distractor that is a second correct answer ──
        # Task 2A offered "নববর্ষ" against a correct answer of "পহেলা বৈশাখ".
        # Orthography is normalised first, so a misspelled correct answer
        # still collides with a correctly spelled synonym.
        raw_correct = options.get(correct, "")
        if raw_correct:
            correct_key  = normalise_bn(correct_orthography(raw_correct))
            correct_cls  = _synonym_class_of(raw_correct)
            for key, text in options.items():
                if key == correct:
                    continue
                if normalise_bn(correct_orthography(text)) == correct_key or (
                    correct_cls is not None and _synonym_class_of(text) == correct_cls
                ):
                    score -= 0.30
                    failures.append("SYNONYM_DISTRACTOR")
                    break

        # ── Task 6.2: Distractor Plausibility & Ambiguity Screener ────
        # Only runs when a KG builder is provided. Checks if any distractor
        # is simultaneously valid in the KG at t_cutoff (= a second correct
        # answer), which is a harder violation than a weak distractor.
        if kg_builder is not None:
            plausibility_failures = check_distractor_plausibility(
                mcq_dict, kg_builder, t_cutoff
            )
            if plausibility_failures:
                score -= 0.30
                failures.extend(plausibility_failures)

        # ── Task 2B (গ): source-term translation guard ──────────────
        failures.extend(self._translation_failures(mcq_dict, mcq_text, supporting_facts))
        if "MISTRANSLATION" in failures:
            score -= 0.25
        if "DROPPED_QUALIFIER" in failures:
            score -= 0.25

        return max(0.0, round(score, 3)), list(set(failures))

    # ------------------------------------------------------------------

    @staticmethod
    def _source_text(mcq_dict: Dict, supporting_facts: Optional[List[Dict]]) -> str:
        """
        The English source text this MCQ was generated from.

        Prefers the fact the MCQ cites. Falls back to the whole batch only
        when the cited fact_id is absent — an MCQ citing a fact outside the
        approved set is a leakage signal that evaluate_one() already raises
        separately, and it should not silently disable this guard too.
        """
        if not supporting_facts:
            return ""
        fact_id = mcq_dict.get("fact_id")
        matched = [f for f in supporting_facts if f.get("fact_id") == fact_id]
        pool = matched or supporting_facts
        return " ".join(str(f.get("text") or f.get("fact_text") or "") for f in pool)

    def _translation_failures(
        self,
        mcq_dict: Dict,
        mcq_text: str,
        supporting_facts: Optional[List[Dict]],
    ) -> List[str]:
        """MISTRANSLATION / DROPPED_QUALIFIER against the English source."""
        source = self._source_text(mcq_dict, supporting_facts)
        if not source:
            return []

        found: List[str] = []
        bn = unicodedata.normalize("NFC", mcq_text)
        for guard in TRANSLATION_GUARD:
            if not re.search(str(guard["en"]), source, re.IGNORECASE):
                continue

            # A known-wrong rendering of the term is present in the MCQ.
            if any(w in bn for w in guard["wrong"]):
                found.append("MISTRANSLATION")

            # The English superlative is qualified ("largest secular
            # festival") but the Bengali keeps the superlative and drops the
            # qualifier — a claim broader than the evidence supports.
            pattern = guard.get("qualified_superlative")
            if (
                pattern
                and re.search(str(pattern), source, re.IGNORECASE)
                and any(s in bn for s in guard["bn_superlatives"])
                and not any(r in bn for r in guard["required"])
            ):
                found.append("DROPPED_QUALIFIER")
        return found


# ---------------------------------------------------------------------------
# LLM-based Quality Evaluator
# ---------------------------------------------------------------------------

class LLMQualityEvaluator:
    """
    Uses Qwen to evaluate grounding, clarity, and distractor quality.
    Called only for MCQs that pass the RuleBasedScreener.
    """

    SYSTEM_PROMPT = (
        "তুমি একজন BCS MCQ মানদণ্ড বিশেষজ্ঞ, যার দায়িত্ব হলো MCQ-এর "
        "তথ্যগত নির্ভুলতা, স্পষ্টতা এবং Distractor মান যাচাই করা। "
        "শুধুমাত্র দেওয়া KG তথ্যের বিপরীতে মূল্যায়ন করো — "
        "নিজের জ্ঞান থেকে সিদ্ধান্ত নেবে না। "
        "কঠোর মানদণ্ড প্রয়োগ করো: সন্দেহ হলে নিচু স্কোর দাও। "
        "সর্বদা valid JSON ফরম্যাটে উত্তর দাও।"
    )

    def __init__(self, client: str, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model

    def evaluate(self, mcq_dict: Dict, supporting_facts: List[Dict]) -> Dict:
        """
        Returns dict with grounding_score, clarity_score, distractor_score,
        failure_codes, feedback, suggestions.
        """
        fact_block = "\n".join(
            f"- [{f['fact_id']}] {f['text']}" for f in supporting_facts
        )
        options_str = "\n".join(
            f"  {k}) {v}" for k, v in mcq_dict.get("options", {}).items()
        )
        correct = mcq_dict.get("correct_answer", "")

        prompt = f"""
নিচের MCQ-টি তিনটি মাত্রায় মূল্যায়ন করো।

প্রশ্ন: {mcq_dict.get("question")}
অপশন:
{options_str}
সঠিক উত্তর: {correct}
ব্যাখ্যা: {mcq_dict.get("explanation", "")}

সহায়ক তথ্যসমূহ (KG থেকে):
{fact_block}

মূল্যায়নের মাত্রা (0.0–1.0):

1. grounding_score (0.0–1.0)
   - সঠিক উত্তর কি দেওয়া তথ্যে স্পষ্টভাবে উল্লেখিত? (0.8+)
   - উত্তরটি কি অনুমান নয়, তথ্যনির্ভর? (0.6+)
   - ব্যাখ্যা কি তথ্য থেকে উদ্ধৃত? (0.4+)

2. clarity_score (0.0–1.0)
   - প্রশ্নটি কি একটিমাত্র সঠিক উত্তর নির্দেশ করে?
   - প্রশ্নটি কি স্পষ্ট ও সংক্ষিপ্ত?
   - প্রশ্নটি কি BCS পরীক্ষার্থীর জন্য বোধগম্য?

3. distractor_score (0.0–1.0)
   - ভুল অপশনগুলো কি একই শ্রেণির? (0.8+)
   - ভুল অপশনগুলো কি সহজেই বাদ দেওয়া যায় না? (0.6+)
   - ভুল অপশনগুলো কি বাস্তবসম্মত? (0.4+)
   - অপশনগুলো কি একে অপরের সাথে মিলে যায় না (DUPLICATE_OPTIONS)? (0.4+)

ব্যর্থতার কোড (প্রযোজ্য হলে):
WRONG_GROUNDING, AMBIGUOUS_QUESTION, WEAK_DISTRACTORS, DUPLICATE_OPTIONS

JSON ফরম্যাটে উত্তর দাও:
{{
  "grounding_score": 0.90,
  "clarity_score": 0.85,
  "distractor_score": 0.75,
  "failure_codes": [],
  "feedback": "মূল্যায়নকারীর সামগ্রিক মন্তব্য",
  "suggestions": ["উন্নতির পরামর্শ ১", "উন্নতির পরামর্শ ২"]
}}
"""
        # NOTE: mcq_generator.py's old extract_json() was renamed to
        # safe_parse_json() when its JSON-repair logic was hardened —
        # this import previously still said `extract_json`, which
        # would raise ImportError the first time this path ran.
        from mcq_generator import call_llm, safe_parse_json
        raw = call_llm(
            self.client, self.model,
            self.SYSTEM_PROMPT, prompt,
            temperature=0.2, max_tokens=1024,
        )
        parsed = safe_parse_json(raw) if raw else None
        if not parsed:
            return {
                "grounding_score":  0.0,
                "clarity_score":    0.0,
                "distractor_score": 0.0,
                "failure_codes":    ["FORMAT_ERROR"],
                "feedback":         "LLM evaluation failed.",
                "suggestions":      [],
            }
        return parsed


# ---------------------------------------------------------------------------
# Main Quality Evaluator (combines rule-based + LLM)
# ---------------------------------------------------------------------------

class MCQQualityEvaluator:
    """
    Full quality evaluation pipeline:
      1. Rule-based structural screening (fast)
      2. LLM grounding + clarity + distractor scoring (deep)
      3. Verdict and structured report
    """

    def __init__(self, hf_api_key: str, model: str = DEFAULT_MODEL):
        self.client   = hf_api_key          # keep as string for call_llm()
        self.llm_eval = LLMQualityEvaluator(hf_api_key, model)
        self.screener  = RuleBasedScreener()
        self.llm_eval  = LLMQualityEvaluator(self.client, model)
        self._log: List[MCQEvaluation] = []     # quality improvement log

    # ------------------------------------------------------------------
    # Single MCQ evaluation
    # ------------------------------------------------------------------

    def evaluate_one(
        self,
        mcq_dict: Dict,
        supporting_facts: List[Dict],
        topic: str = "General",     # ← NEW: pass topic explicitly
        kg_builder: Optional[object] = None,
        t_cutoff: str = "2023-04-19",
    ) -> MCQEvaluation:
        """
        Evaluate a single MCQ dict against its supporting KG facts.

        mcq_dict expected keys:
            mcq_id, fact_id, question, options (dict ক/খ/গ/ঘ),
            correct_answer, difficulty, explanation
        topic : topic label (used for get_topic_pass_rate grouping)
        kg_builder : KnowledgeGraphBuilder instance (optional, for distractor plausibility screening)
        t_cutoff : cutoff date string (default "2023-04-19")
        """
        mcq_id     = mcq_dict.get("mcq_id", f"MCQ_{uuid.uuid4().hex[:8]}")
        fact_id    = mcq_dict.get("fact_id", "FACT_unknown")
        difficulty = mcq_dict.get("difficulty", "medium")

        # ── Step 1: Rule-based ──────────────────────────────────────
        format_score, rule_failures = self.screener.screen(
            mcq_dict, supporting_facts, kg_builder=kg_builder, t_cutoff=t_cutoff
        )

        # ── Step 2: LLM evaluation ──────────────────────────────────
        llm_result = self.llm_eval.evaluate(mcq_dict, supporting_facts)

        grounding_score  = float(llm_result.get("grounding_score",  0.0))
        clarity_score    = float(llm_result.get("clarity_score",    0.0))
        distractor_score = float(llm_result.get("distractor_score", 0.0))

        llm_failures  = llm_result.get("failure_codes", [])
        feedback      = llm_result.get("feedback", "")
        suggestions   = llm_result.get("suggestions", [])

        # ── Step 2b: Temporal cutoff check (guideline §13.2: TVR/PCLR) ──
        # supporting_facts only carries temporal_status when facts_from_kg()
        # was called with a cutoff (as_of=...). If none of the supporting
        # facts have that key, no cutoff was active for this run and we
        # skip temporal checking entirely — old callers see no behavior
        # change.
        cutoff_active = any("temporal_status" in f for f in supporting_facts)
        temporal_status = None
        if cutoff_active:
            facts_by_id = {f.get("fact_id"): f for f in supporting_facts}
            matched = facts_by_id.get(fact_id)
            if matched is None:
                # The MCQ cites a fact_id that isn't in the cutoff-approved
                # set at all. Under this architecture that can only happen
                # if the generator drifted from its allowed evidence — the
                # exact failure mode §8.2 and PCLR are designed to catch.
                temporal_status = "NOT_IN_APPROVED_SET"
            else:
                temporal_status = matched.get("temporal_status", "unversioned")

        # ── Step 3: Hard floor checks ───────────────────────────────
        extra_failures = []
        if grounding_score < GROUNDING_MIN:
            extra_failures.append("WRONG_GROUNDING")
        if distractor_score < DISTRACTOR_MIN:
            if "WEAK_DISTRACTORS" not in llm_failures:
                extra_failures.append("WEAK_DISTRACTORS")
        if temporal_status == "NOT_IN_APPROVED_SET":
            # Hard failure: a leakage-risk signal, not a style nit.
            extra_failures.append("POST_CUTOFF_LEAKAGE_RISK")
        elif temporal_status == "unversioned":
            # Soft flag only: the fact was allowed through (it may simply
            # not have valid_from/valid_to recorded yet), but its temporal
            # validity was never actually verified against t*. Doesn't
            # fail the MCQ — just makes the caveat visible in reporting.
            extra_failures.append("TEMPORAL_UNVERSIONED")

        # ── Step 2c: Factuality Verification Engine (Task 7.1 & 7.2 / §10.2 Standard) ──
        if kg_builder is not None:
            fact_res = verify_factuality(mcq_dict, kg_builder=kg_builder, t_cutoff=t_cutoff)
            if not fact_res.verified:
                for fc in fact_res.failure_codes:
                    if fc not in extra_failures:
                        extra_failures.append(fc)

        all_failures = list(set(rule_failures + llm_failures + extra_failures))

        # ── Step 3b: canonical rejection-taxonomy codes (guideline §10.2) ──
        rejection_codes = map_codes(all_failures) if all_failures else []

        scores = DimensionScores(
            format_score=format_score,
            grounding_score=grounding_score,
            clarity_score=clarity_score,
            distractor_score=distractor_score,
        )

        # Task 2B: the four language failures are hard failures, not score
        # deductions. Task 2A's batch scored 0.838–0.878 composite WITH a
        # mistranslation and a second correct option in it — a defect that
        # only moves a weighted average cannot be relied on to stop
        # anything, so these are listed alongside FORMAT_ERROR instead.
        passed = (
            scores.composite >= PASS_THRESHOLD
            and grounding_score >= GROUNDING_MIN
            and not any(f in all_failures for f in HARD_FAILURE_CODES)
        )

        evaluation = MCQEvaluation(
            eval_id=f"EVAL_{uuid.uuid4().hex[:8]}",
            mcq_id=mcq_id,
            fact_id=fact_id,
            question=mcq_dict.get("question", ""),
            difficulty=difficulty,
            topic=topic,                   # ← stored correctly now
            scores=scores,
            passed=passed,
            failure_codes=all_failures,
            feedback=feedback,
            suggestions=suggestions,
            temporal_status=temporal_status,
            rejection_codes=rejection_codes,
        )

        self._log.append(evaluation)
        status = "✓ PASS" if passed else "✗ FAIL"
        log.info("  MCQ %s — %s (%.3f)", mcq_id, status, scores.composite)
        return evaluation

    # ------------------------------------------------------------------
    # Batch evaluation
    # ------------------------------------------------------------------

    def evaluate_batch(
        self,
        mcqs: List[Dict],
        supporting_facts: List[Dict],
        topic: str = "General",
        difficulty: str = "medium",
        kg_builder: Optional[object] = None,
        t_cutoff: str = "2023-04-19",
    ) -> QualityReport:
        """
        Evaluate a batch of MCQ dicts and return a QualityReport.

        Parameters
        ----------
        mcqs             : list of mcq_dicts (same format as evaluate_one)
        supporting_facts : list of KG fact dicts used to generate these MCQs
        topic            : topic label for the report
        difficulty       : difficulty level label for the report
        kg_builder       : KnowledgeGraphBuilder instance (optional, for distractor plausibility screening)
        t_cutoff         : cutoff date string (default "2023-04-19")
        """
        log.info("Evaluating batch of %d MCQs [topic=%s, difficulty=%s]",
                 len(mcqs), topic, difficulty)

        # Pass topic, kg_builder, and t_cutoff to evaluate_one so grouping and screening work correctly
        evaluations = [
            self.evaluate_one(
                m, supporting_facts, topic=topic,
                kg_builder=kg_builder, t_cutoff=t_cutoff,
            )
            for m in mcqs
        ]

        # ── Task 2B: batch-level near-duplicate pass ────────────────────
        # Only decidable once the whole batch is in hand, so it runs here
        # rather than in evaluate_one. Task 2A generated two questions from
        # one fact that were answered by the same entity; both passed,
        # because nothing in a per-item check can see the other item.
        duplicates = find_near_duplicates(mcqs)
        for ev in evaluations:
            first_id = duplicates.get(ev.mcq_id)
            if not first_id:
                continue
            ev.near_duplicate_of = first_id
            if "NEAR_DUPLICATE" not in ev.failure_codes:
                ev.failure_codes.append("NEAR_DUPLICATE")
            ev.rejection_codes = map_codes(ev.failure_codes)
            ev.passed = False
            log.info("  MCQ %s re-asks %s (same fact, same answer) — NEAR_DUPLICATE",
                     ev.mcq_id, first_id)

        passed   = [e for e in evaluations if e.passed]
        failed   = [e for e in evaluations if not e.passed]
        total    = len(evaluations)
        avg_sc   = sum(e.overall_score  for e in evaluations) / total if total else 0.0
        avg_gr   = sum(e.scores.grounding_score  for e in evaluations) / total if total else 0.0
        avg_di   = sum(e.scores.distractor_score for e in evaluations) / total if total else 0.0
        avg_cl   = sum(e.scores.clarity_score    for e in evaluations) / total if total else 0.0
        avg_fmt  = sum(e.scores.format_score     for e in evaluations) / total if total else 0.0

        report = QualityReport(
            report_id=f"RPT_{uuid.uuid4().hex[:8]}",
            topic=topic,
            difficulty=difficulty,
            total=total,
            passed=len(passed),
            failed=len(failed),
            avg_score=round(avg_sc, 3),
            avg_grounding=round(avg_gr, 3),
            avg_distractor=round(avg_di, 3),
            pass_rate=round(len(passed) / total, 3) if total else 0.0,
            evaluations=evaluations,
        )

        # ── metrics_export: ready-to-use dict for bcs_metrics.full_report() ──
        # Pass this as the `mcq_evaluations` argument — bcs_metrics reads
        # 'scores' sub-dict for GA, DQS, CS and 'passed' for MSV.
        report.metrics_export = [
            {
                "mcq_id":     e.mcq_id,
                "question":   e.question,
                "difficulty": e.difficulty,
                "passed":     e.passed,
                "temporal_status": e.temporal_status,
                "failure_codes":   e.failure_codes,
                "scores": {
                    "grounding_score":  round(e.scores.grounding_score,  4),
                    "distractor_score": round(e.scores.distractor_score, 4),
                    "clarity_score":    round(e.scores.clarity_score,    4),
                    "format_score":     round(e.scores.format_score,     4),
                },
            }
            for e in evaluations
        ]

        log.info(report.summary_str())
        return report

    # ------------------------------------------------------------------
    # Quality improvement log
    # ------------------------------------------------------------------

    def get_improvement_log(self) -> List[Dict]:
        """
        Returns all evaluations recorded in this session as dicts.
        Suitable for JSON serialisation or passing to EpisodicMemory diagnostics.
        """
        return [e.to_dict() for e in self._log]

    def save_improvement_log(self, path: str = "quality_improvement_log.json") -> None:
        """Save the full improvement log to JSON."""
        log_data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_evaluated": len(self._log),
            "pass_rate": (
                round(sum(1 for e in self._log if e.passed) / len(self._log), 3)
                if self._log else 0.0
            ),
            "evaluations": self.get_improvement_log(),
        }
        Path(path).write_text(
            json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Improvement log saved → %s (%d entries)", path, len(self._log))

    # ------------------------------------------------------------------
    # Integration helpers for Saif's EpisodicMemory
    # ------------------------------------------------------------------

    def get_failed_fact_ids(self) -> List[str]:
        """
        Returns fact IDs that repeatedly appear in failed evaluations.
        Mirrors the interface used by EpisodicMemory.get_failed_facts().
        Useful for local session diagnostics before merging with episodic_store.
        """
        failed_facts = [e.fact_id for e in self._log if not e.passed]
        counts = Counter(failed_facts)
        return [fid for fid, _ in counts.most_common()]

    def get_topic_pass_rate(self) -> Dict[str, float]:
        """
        Topic-level pass rates from the current session log.
        Complements EpisodicMemory.get_high_performing_topics().

        FIX: now correctly groups by topic (was incorrectly grouping by difficulty).
        """
        topic_data: Dict[str, List[bool]] = defaultdict(list)
        for e in self._log:
            topic_data[e.topic].append(e.passed)   # ← fixed: was e.difficulty
        return {
            topic: round(sum(v) / len(v), 3)
            for topic, v in topic_data.items() if v
        }

    def get_difficulty_pass_rate(self) -> Dict[str, float]:
        """
        Difficulty-level pass rates from the current session log.
        (Separate method so both topic and difficulty breakdowns are available.)
        """
        diff_data: Dict[str, List[bool]] = defaultdict(list)
        for e in self._log:
            diff_data[e.difficulty].append(e.passed)
        return {
            diff: round(sum(v) / len(v), 3)
            for diff, v in diff_data.items() if v
        }

    def get_rejection_taxonomy_report(self) -> Dict[str, object]:
        """
        Guideline §8.4 / §10.2: "report rejection rate", broken down by the
        canonical taxonomy (E-TIME/E-LEAK/E-UNSUP/E-MULTI/E-DIST/E-AMB/
        E-STYLE/E-DUP/E-KG/E-SRC). This is the per-run table the paper's
        verification/rejection-pipeline section needs — separate from
        get_failed_fact_ids()/get_topic_pass_rate(), which report on
        internal codes and don't map onto the taxonomy the paper uses.
        """
        tally = RejectionTally()
        for e in self._log:
            tally.add(e.rejection_codes)
        return tally.report()

    # ------------------------------------------------------------------
    # BCS corpus alignment (for Sadia's evaluation benchmarks)
    # ------------------------------------------------------------------

    def score_against_corpus(
        self,
        generated_mcqs: List[Dict],
        corpus_questions: List[str],
    ) -> Dict:
        """
        Rough similarity check between generated MCQ stems and BCS historical questions.
        Uses token-level Jaccard similarity (no LLM needed).

        Parameters
        ----------
        generated_mcqs    : list of mcq dicts with 'question' key
        corpus_questions  : list of BCS historical question strings

        Returns
        -------
        Dict with avg_similarity, coverage, difficulty_distribution
        """
        def tokenize(text: str) -> set:
            return set(re.findall(r"[\u0980-\u09FF\w]+", text.lower()))

        def jaccard(a: set, b: set) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / len(a | b)

        similarities = []
        for gen in generated_mcqs:
            gen_tokens = tokenize(gen.get("question", ""))
            best = max(
                (jaccard(gen_tokens, tokenize(cq)) for cq in corpus_questions),
                default=0.0,
            )
            similarities.append(best)

        avg_sim = round(sum(similarities) / len(similarities), 3) if similarities else 0.0
        covered = sum(1 for s in similarities if s > 0.3)

        diff_dist: Dict[str, int] = {}
        for m in generated_mcqs:
            d = m.get("difficulty", "unknown")
            diff_dist[d] = diff_dist.get(d, 0) + 1

        return {
            "avg_similarity":          avg_sim,
            "coverage_rate":           round(covered / len(generated_mcqs), 3) if generated_mcqs else 0.0,
            "difficulty_distribution": diff_dist,
            "total_generated":         len(generated_mcqs),
            "total_corpus":            len(corpus_questions),
        }


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

DEMO_MCQS = [
    {
        "mcq_id": "MCQ_demo0001",
        "fact_id": "FACT_demo0001",
        "question": "বাংলাদেশের প্রথম রাষ্ট্রপতি কে ছিলেন?",
        "options": {
            "ক": "জিয়াউর রহমান",
            "খ": "শেখ মুজিবুর রহমান",
            "গ": "এরশাদ",
            "ঘ": "বঙ্গবন্ধু আবু সাঈদ চৌধুরী",
        },
        "correct_answer": "খ",
        "difficulty": "easy",
        "question_type": "who_question",
        "explanation": "শেখ মুজিবুর রহমান বাংলাদেশের প্রথম রাষ্ট্রপতি ছিলেন।",
    },
    {
        "mcq_id": "MCQ_demo0002",
        "fact_id": "FACT_demo0002",
        "question": "বাংলাদেশের স্বাধীনতা দিবস কত তারিখে?",
        "options": {
            "ক": "১৬ ডিসেম্বর",
            "খ": "১৫ আগস্ট",
            "গ": "২৬ মার্চ",
            "ঘ": "২১ ফেব্রুয়ারি",
        },
        "correct_answer": "গ",
        "difficulty": "easy",
        "question_type": "when_question",
        "explanation": "বাংলাদেশের স্বাধীনতা দিবস ২৬ মার্চ।",
    },
]

DEMO_FACTS = [
    {
        "fact_id": "FACT_demo0001",
        "text": "বাংলাদেশের প্রথম রাষ্ট্রপতি ছিলেন শেখ মুজিবুর রহমান।",
        "topic": "History",
    },
    {
        "fact_id": "FACT_demo0002",
        "text": "বাংলাদেশের স্বাধীনতা দিবস ২৬ মার্চ।",
        "topic": "History",
    },
]

if __name__ == "__main__":
    import os
    try:
        from google.colab import userdata
        HF_API_KEY = userdata.get("HF_API_KEY")
    except Exception:
        HF_API_KEY = os.environ.get("HF_API_KEY", "")

    if not HF_API_KEY:
        print("❌  HF_API_KEY not set. Export it or add to Colab Secrets.")
        raise SystemExit(1)

    print("BCSBatighor — MCQ Quality Evaluator Demo")
    print("=" * 60)

    evaluator = MCQQualityEvaluator(hf_api_key=HF_API_KEY)

    report = evaluator.evaluate_batch(
        mcqs=DEMO_MCQS,
        supporting_facts=DEMO_FACTS,
        topic="History",
        difficulty="easy",
    )

    print(report.summary_str())
    print("\nDetailed evaluations:")
    for ev in report.evaluations:
        status = "✓ PASS" if ev.passed else "✗ FAIL"
        print(f"  [{status}] {ev.mcq_id}  score={ev.overall_score:.3f}  failures={ev.failure_codes}")
        if ev.suggestions:
            print(f"    Suggestions: {ev.suggestions}")

    evaluator.save_improvement_log("quality_improvement_log.json")
    print("\nFailed fact IDs:", evaluator.get_failed_fact_ids())
    print("Topic pass rates:", evaluator.get_topic_pass_rate())
    print("Difficulty pass rates:", evaluator.get_difficulty_pass_rate())   # ← new