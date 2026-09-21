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
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
load_dotenv()

from bcs.logging_config import get_logger
from bcs.rate_limiter import wait_for_rate_limit

log = get_logger("mcq_quality")

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

DEFAULT_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_API_URL = os.getenv(
    "GROQ_API_URL",
    "https://api.groq.com/openai/v1/chat/completions",
)
MAX_LLM_RETRIES = 3


# ---------------------------------------------------------------------------
# LLM call + JSON parsing (same pattern as the generator modules — the
# previous version of this file imported call_llm/extract_json from
# medq_question_generator, which broke when the package was reorganized
# under bcs.generators.* and no longer matched that module's actual API
# (safe_parse_json, not extract_json). Self-contained here instead.)
# ---------------------------------------------------------------------------

def call_llm(
    api_key:     str,
    model:       str,
    system:      str,
    user:        str,
    api_url:     str   = DEFAULT_API_URL,
    temperature: float = 0.2,
    max_tokens:  int   = 2048,
    max_retries: int   = MAX_LLM_RETRIES,
) -> Optional[str]:
    payload = {
        "model":       model,
        "messages":    [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    for attempt in range(max_retries):
        wait_for_rate_limit()
        try:
            resp = requests.post(
                api_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=(10, 90),
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            log.warning("LLM call attempt %d/%d failed: %s", attempt + 1, max_retries, str(exc)[:120])
            if attempt < max_retries - 1:
                time.sleep(5 * (2 ** attempt))
    return None


def safe_parse_json(raw: str) -> Optional[Dict]:
    """Strip markdown fences / trailing commas and parse the first JSON object found."""
    if not raw:
        return None
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = re.sub(r"```\s*$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    repaired = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\})", repaired, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    log.warning("safe_parse_json: all repair attempts failed.")
    return None

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
    evaluated_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

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
# Rule-based pre-screening (fast, no LLM)
# ---------------------------------------------------------------------------

class RuleBasedScreener:
    """
    Fast structural checks run before the LLM evaluator.
    Catches obvious formatting failures cheaply.
    """

    EXPECTED_OPTION_KEYS = {"ক", "খ", "গ", "ঘ"}
    BANGLA_CHAR_RE = re.compile(r"[\u0980-\u09FF]")

    def screen(self, mcq_dict: Dict) -> Tuple[float, List[str]]:
        """
        Returns (format_score 0–1, list of failure codes).
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

        return max(0.0, round(score, 3)), list(set(failures))


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
    ) -> MCQEvaluation:
        """
        Evaluate a single MCQ dict against its supporting KG facts.

        mcq_dict expected keys:
            mcq_id, fact_id, question, options (dict ক/খ/গ/ঘ),
            correct_answer, difficulty, explanation
        topic : topic label (used for get_topic_pass_rate grouping)
        """
        mcq_id     = mcq_dict.get("mcq_id", f"MCQ_{uuid.uuid4().hex[:8]}")
        fact_id    = mcq_dict.get("fact_id", "FACT_unknown")
        difficulty = mcq_dict.get("difficulty", "medium")

        # ── Step 1: Rule-based ──────────────────────────────────────
        format_score, rule_failures = self.screener.screen(mcq_dict)

        # ── Step 2: LLM evaluation ──────────────────────────────────
        llm_result = self.llm_eval.evaluate(mcq_dict, supporting_facts)

        grounding_score  = float(llm_result.get("grounding_score",  0.0))
        clarity_score    = float(llm_result.get("clarity_score",    0.0))
        distractor_score = float(llm_result.get("distractor_score", 0.0))

        llm_failures  = llm_result.get("failure_codes", [])
        feedback      = llm_result.get("feedback", "")
        suggestions   = llm_result.get("suggestions", [])

        # ── Step 3: Hard floor checks ───────────────────────────────
        extra_failures = []
        if grounding_score < GROUNDING_MIN:
            extra_failures.append("WRONG_GROUNDING")
        if distractor_score < DISTRACTOR_MIN:
            if "WEAK_DISTRACTORS" not in llm_failures:
                extra_failures.append("WEAK_DISTRACTORS")

        all_failures = list(set(rule_failures + llm_failures + extra_failures))

        scores = DimensionScores(
            format_score=format_score,
            grounding_score=grounding_score,
            clarity_score=clarity_score,
            distractor_score=distractor_score,
        )

        passed = (
            scores.composite >= PASS_THRESHOLD
            and grounding_score >= GROUNDING_MIN
            and not any(f in all_failures for f in ("FORMAT_ERROR", "WRONG_GROUNDING"))
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
    ) -> QualityReport:
        """
        Evaluate a batch of MCQ dicts and return a QualityReport.

        Parameters
        ----------
        mcqs             : list of mcq_dicts (same format as evaluate_one)
        supporting_facts : list of KG fact dicts used to generate these MCQs
        topic            : topic label for the report
        difficulty       : difficulty level label for the report
        """
        log.info("Evaluating batch of %d MCQs [topic=%s, difficulty=%s]",
                 len(mcqs), topic, difficulty)

        # Pass topic to evaluate_one so grouping works correctly
        evaluations = [self.evaluate_one(m, supporting_facts, topic=topic) for m in mcqs]

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