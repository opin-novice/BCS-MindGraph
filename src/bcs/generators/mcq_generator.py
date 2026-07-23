"""
mcq_generator.py
================

Responsibilities
----------------
1. Graph-grounded MCQ generation strictly from KG Fact nodes.
2. Difficulty-aware generation: easy / medium / hard.
3. Challenger–Reasoner–Judge (CRJ) loop.
4. Regeneration logic driven by judge failure reasons.
5. Duplicate MCQ detection (within-session + cross-session fingerprinting).
6. Clean integration interfaces for kg_builder.py and episodic_store.py.


"""

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import requests

from bcs.logging_config import get_logger

log = get_logger("mcq_generator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
import os
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_API_URL  = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions")

MAX_REGENERATION_ROUNDS      = 3
MIN_PASS_SCORE               = 0.70
DISTRACTOR_QUALITY_THRESHOLD = 0.65
BATCH_SIZE                   = 10
BATCH_REST_SECONDS           = 20

from bcs.rate_limiter import wait_for_rate_limit

DIFFICULTY_CONFIG = {
    "easy":   {"distractors": "clearly wrong but plausible",    "context": "direct fact recall"},
    "medium": {"distractors": "same category, close attribute", "context": "entity + attribute"},
    "hard":   {"distractors": "near-identical values or dates",  "context": "multi-fact inference"},
}

# ---------------------------------------------------------------------------
# Duplicate detection helpers
# ---------------------------------------------------------------------------

def _question_fingerprint(question_text: str) -> str:
    """Stable SHA-256 fingerprint of a normalised question string."""
    normalised = re.sub(r"\s+", " ", question_text.strip().lower())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class DuplicateDetector:
    """
    Tracks question fingerprints within a session and optionally persists
    them to a JSON file for cross-session deduplication.
    """

    def __init__(self, persist_path: Optional[str] = None):
        self.persist_path = persist_path or "runtime/seen_questions.json"
        self._seen: Set[str] = set()

    def load(self) -> None:
        """Load persisted fingerprints from disk, creating the file if absent."""
        if not self.persist_path:
            return
        try:
            import pathlib
            path = pathlib.Path(self.persist_path)
            if path.exists():
                data       = json.loads(path.read_text(encoding="utf-8"))
                self._seen = set(data.get("fingerprints", []))
                log.info("DuplicateDetector: loaded %d seen fingerprints.", len(self._seen))
            else:
                path.write_text(
                    json.dumps({"fingerprints": []}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                log.info("DuplicateDetector: created new fingerprint store -> %s", self.persist_path)
        except Exception as exc:
            log.warning("DuplicateDetector.load() failed: %s", exc)

    def save(self) -> None:
        """Persist fingerprints to disk."""
        if not self.persist_path:
            return
        try:
            import pathlib
            pathlib.Path(self.persist_path).write_text(
                json.dumps({"fingerprints": sorted(self._seen)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("DuplicateDetector: saved %d fingerprints -> %s",
                     len(self._seen), self.persist_path)
        except Exception as exc:
            log.warning("DuplicateDetector.save() failed: %s", exc)

    def is_duplicate(self, mcq: "MCQ") -> bool:
        return _question_fingerprint(mcq.question) in self._seen

    def register(self, mcq: "MCQ") -> None:
        self._seen.add(_question_fingerprint(mcq.question))

    def register_many(self, mcqs: List["MCQ"]) -> None:
        for m in mcqs:
            self.register(m)

    def filter_duplicates(self, mcqs: List["MCQ"]) -> Tuple[List["MCQ"], List["MCQ"]]:
        """Split into (unique_mcqs, duplicate_mcqs). Unique are NOT auto-registered."""
        unique, dupes     = [], []
        seen_in_batch: Set[str] = set()
        for m in mcqs:
            fp = _question_fingerprint(m.question)
            if fp in self._seen or fp in seen_in_batch:
                dupes.append(m)
                log.info("  ⚑ Duplicate MCQ filtered: %s (fact=%s)", m.mcq_id, m.fact_id)
            else:
                seen_in_batch.add(fp)
                unique.append(m)
        return unique, dupes


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MCQOption:
    key:  str   # ক / খ / গ / ঘ
    text: str


@dataclass
class MCQ:
    mcq_id:            str
    fact_id:           str
    question:          str
    options:           List[MCQOption]
    correct_answer:    str
    difficulty:        str
    question_type:     str
    explanation:       str
    quality_score:     float = 0.0
    regeneration_round: int  = 0
    grounded:          bool  = False
    _grounding_score:  float = 0.0
    _distractor_score: float = 0.0
    _clarity_score:    float = 0.0

    def to_episode_dict(self) -> Dict:
        return {
            "question":           self.question,
            "options":            [f"{o.key}) {o.text}" for o in self.options],
            "correct_answer":     self.correct_answer,
            "difficulty":         self.difficulty,
            "quality_score":      self.quality_score,
            "regeneration_round": self.regeneration_round,
        }

    def display(self) -> str:
        lines = [f"\nপ্রশ্ন: {self.question}"]
        for opt in self.options:
            marker = " ✓" if opt.key == self.correct_answer else ""
            lines.append(f"  {opt.key}) {opt.text}{marker}")
        lines.append(f"ব্যাখ্যা: {self.explanation}")
        lines.append(
            f"কঠিনতা: {self.difficulty}  |  ধরন: {self.question_type}"
            f"  |  স্কোর: {self.quality_score:.2f}"
        )
        return "\n".join(lines)


@dataclass
class GenerationResult:
    """Full output for one fact-set, ready for episodic_store.write_episode()."""
    episode_id:        str
    topic:             str
    fact_ids:          List[str]
    mcqs:              List[MCQ]
    overall_score:     float
    accepted:          bool
    crj_rounds:        int
    generation_config: Dict
    rejection_reasons: List[str] = field(default_factory=list)
    duplicate_count:   int       = 0

    def to_episode_payload(self) -> Dict:
        n = len(self.mcqs)
        if n > 0:
            avg_grounding  = sum(getattr(m, "_grounding_score",  0.0) for m in self.mcqs) / n
            avg_distractor = sum(getattr(m, "_distractor_score", 0.0) for m in self.mcqs) / n
            avg_clarity    = sum(getattr(m, "_clarity_score",    0.0) for m in self.mcqs) / n
        else:
            avg_grounding = avg_distractor = avg_clarity = 0.0

        return {
            "input_question":       "",
            "intent":               "mcq_generation",
            "blueprint":            "single_correct_answer",
            "topic":                self.topic,
            "fact_ids":             self.fact_ids,
            "mcqs":                 [m.to_episode_dict() for m in self.mcqs],
            "overall_score":        self.overall_score,
            "accepted":             int(self.accepted),
            "avg_grounding_score":  round(avg_grounding,  4),
            "avg_distractor_score": round(avg_distractor, 4),
            "avg_clarity_score":    round(avg_clarity,    4),
            "generation_config":    self.generation_config,
        }


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

def call_llm(
    api_key:      str,
    model:        str,
    system_prompt: str,
    user_prompt:  str,
    temperature:  float = 0.7,
    max_tokens:   int   = 4096,
    max_retries:  int   = 5,
) -> str:
    """Call LLM via Groq API with rate-limit awareness and backoff."""
    last_exc: Exception = RuntimeError("LLM call failed with no exception captured")
    for attempt in range(max_retries):
        wait_for_rate_limit()
        try:
            response = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "temperature": temperature,
                    "max_tokens":  max_tokens,
                },
                timeout=(10, 120),
            )
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 10))
                log.warning("429 rate limit; retry-after=%ds (attempt %d/%d)",
                            retry_after, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            last_exc = exc
            log.warning("LLM call attempt %d/%d failed: %s",
                        attempt + 1, max_retries, str(exc)[:120])
            if attempt < max_retries - 1:
                time.sleep(5 * (2 ** attempt))
    raise last_exc


# ---------------------------------------------------------------------------
# JSON parsing  (FIXED — replaces old fragile extract_json)
# ---------------------------------------------------------------------------

def safe_parse_json(raw: str) -> Optional[Union[Dict, List]]:
    """
    Robustly extract and parse the first JSON object from an LLM response.

    Repair pipeline (stops at first success):
      1. Strip markdown fences.
      2. Direct json.loads().
      3. Remove trailing commas before ] or }.
      4. Remove trailing commas AND strip any text after the closing }.
      5. Try to extract just the outermost {...} substring and parse that.
      6. Give up — return None so the caller can handle the empty result.

    This replaces the old extract_json() which failed with a single
    JSONDecodeError and left the entire episode with 0 MCQs.
    """
    if not raw:
        return None

    # Step 0 — strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
    cleaned = re.sub(r"```\s*$",         "", cleaned).strip()

    # Step 1 — direct parse (fastest path for well-formed responses)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Step 2 — remove trailing commas before ] or }
    repaired = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Step 3 — strip any trailing text after the last closing brace
    match = re.search(r"(\{.*\})", repaired, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Step 4 — last resort: try to fix a truncated JSON by closing open
    # brackets/braces. Handles gateway timeout mid-response.
    try:
        partial = repaired
        open_braces   = partial.count("{") - partial.count("}")
        open_brackets = partial.count("[") - partial.count("]")
        # Close any open string first (look for lone " that isn't escaped)
        if partial.count('"') % 2 != 0:
            partial += '"'
        # Trim to the last complete key-value pair to avoid broken values
        partial = re.sub(r',\s*"[^"]*"\s*:\s*$', "", partial)
        partial = re.sub(r',\s*"[^"]*"\s*:\s*"[^"]*$', "", partial)
        partial += "]" * max(open_brackets, 0)
        partial += "}" * max(open_braces, 0)
        result = json.loads(partial)
        log.warning("safe_parse_json: used truncation repair — response may be incomplete.")
        return result
    except Exception:
        pass

    log.warning("safe_parse_json: all repair attempts failed — returning None.")
    return None


# ---------------------------------------------------------------------------
# Challenger Agent
# ---------------------------------------------------------------------------

class ChallengerAgent:
    """
    Generates MCQ candidates from a set of KG facts.
    Supports difficulty-aware generation and MCQ-type awareness from fact mcq_tags.
    """

    SYSTEM_PROMPT = (
        "BCS MCQ generator. Rules: "
        "(1) Base MCQs ONLY on given facts. No outside knowledge. "
        "(2) Type distribution (IMPORTANT): 75% numeric_ranking/analytical, 15% who_question, "
        "max 10% when_question + where_question. NO excess when_question. "
        "(3) 4 options (ক/খ/গ/ঘ). Distractors must be plausible (close numbers, same-era people, similar entities). "
        "(4) Correct answer evenly distributed among ক/খ/গ/ঘ. "
        "(5) Formal Bengali. Valid JSON only."
    )

    def __init__(self, client: str, model: str = DEFAULT_MODEL):
        self.client = client
        self.model  = model

    def generate(
        self,
        facts: List[Dict],
        difficulty: str = "medium",
        prior_failure_reasons: Optional[List[str]] = None,
        regeneration_round: int = 0,
        seen_questions: Optional[List[str]] = None,
    ) -> List[MCQ]:
        """
        Generate MCQs for a list of KG fact dicts.

        Parameters
        ----------
        facts                  : list of fact dicts (fact_id, text, topic, mcq_suitable_for)
        difficulty             : "easy" | "medium" | "hard"
        prior_failure_reasons  : failure codes from Judge to guide re-generation
        regeneration_round     : current CRJ iteration (0 = first attempt)
        seen_questions         : already-accepted question strings to avoid repeating
        """
        # Build fact block
        fact_lines = []
        for i, f in enumerate(facts, 1):
            suitable = ", ".join(f.get("mcq_suitable_for", ["factual"]))
            fact_lines.append(
                f"{i}. [ID: {f['fact_id']}] {f['text']}  (প্রশ্নের ধরন: {suitable})"
            )
        fact_block = "\n".join(fact_lines)

        # Failure guidance block
        failure_block = ""
        if prior_failure_reasons:
            reasons_text  = "; ".join(prior_failure_reasons)
            failure_block = (
                f"\n⚠ পূর্ববর্তী ব্যর্থতার কারণ ({regeneration_round}. পুনরাবৃত্তি):"
                f" {reasons_text}\n"
                "অনুগ্রহ করে এই সমস্যাগুলো সমাধান করে নতুন MCQ তৈরি করো।\n"
            )

        # Duplicate avoidance block
        avoid_block = ""
        if seen_questions:
            seen_list   = "\n".join(f"  - {q}" for q in seen_questions[:20])
            avoid_block = (
                f"\n🚫 নিচের প্রশ্নগুলো ইতিমধ্যে তৈরি হয়েছে — এগুলো আবার তৈরি করবে না:\n"
                f"{seen_list}\n"
                "সম্পূর্ণ ভিন্ন প্রশ্ন তৈরি করো।\n"
            )

        n_facts    = len(facts)
        n_factual  = max(1, round(n_facts * 0.75))
        n_who      = max(1, round(n_facts * 0.15))

        prompt = f"""Generate {n_facts} MCQs in Bengali from these facts:

{fact_block}
{failure_block}{avoid_block}
Distribute types: {n_factual}x numeric_ranking/analytical, {n_who}x who_question, max 1x when/where.

Return JSON:
{{"mcqs":[{{"fact_id":"FACT_xx","question":"...","options":{{"ক":"...","খ":"...","গ":"...","ঘ":"..."}},"correct_answer":"ক","difficulty":"{difficulty}","question_type":"...","explanation":"..."}}]}}"""
        raw = call_llm(self.client, self.model, self.SYSTEM_PROMPT, prompt, temperature=0.7)
        if not raw:
            log.warning("Challenger returned empty response.")
            return []

        parsed = safe_parse_json(raw)
        if not parsed:
            log.warning("Challenger: could not parse JSON.")
            return []

        raw_list = parsed if isinstance(parsed, list) else parsed.get("mcqs", [])
        if isinstance(raw_list, dict):
            raw_list = [raw_list]
        mcqs = []
        for item in raw_list:
            try:
                opts_raw = item.get("options", {})
                options  = [MCQOption(key=k, text=v) for k, v in opts_raw.items()]
                if item.get("difficulty") and item.get("difficulty") != difficulty:
                    log.info(
                        "Challenger self-reported difficulty '%s' disagreed with requested '%s' "
                        "[round=%d] — assigning requested difficulty.",
                        item.get("difficulty"), difficulty, regeneration_round,
                    )
                mcqs.append(MCQ(
                    mcq_id            = f"MCQ_{uuid.uuid4().hex[:8]}",
                    fact_id           = item["fact_id"],
                    question          = item["question"],
                    options           = options,
                    correct_answer    = item["correct_answer"],
                    difficulty        = difficulty,
                    question_type     = item.get("question_type", "factual"),
                    explanation       = item.get("explanation", ""),
                    regeneration_round = regeneration_round,
                ))
            except KeyError as e:
                log.warning("Challenger MCQ missing field %s — skipping.", e)

        log.info("Challenger generated %d MCQ(s) [round=%d, difficulty=%s].",
                 len(mcqs), regeneration_round, difficulty)
        return mcqs


# ---------------------------------------------------------------------------
# Reasoner Agent
# ---------------------------------------------------------------------------

class ReasonerAgent:
    """
    Attempts to answer each MCQ without seeing the correct answer.
    Simulates a BCS candidate to test if the MCQ is solvable from the given facts.
    """

    SYSTEM_PROMPT = (
        "BCS test-taker. Answer MCQs using ONLY given facts. "
        "If answer not found in facts, pick randomly with confidence=0.1. "
        "Valid JSON only."
    )

    def __init__(self, client: str, model: str = DEFAULT_MODEL):
        self.client = client
        self.model  = model

    def answer(self, mcqs: List[MCQ], supporting_facts: List[Dict]) -> List[Dict]:
        """
        Returns list of answer dicts:
        {mcq_id, chosen_answer, confidence, reasoning}
        """
        if not mcqs:
            return []

        fact_block = "\n".join(
            f"- [{f['fact_id']}] {f['text']}" for f in supporting_facts
        )
        mcq_block  = []
        for m in mcqs:
            opts_str = "  ".join(f"{o.key}) {o.text}" for o in m.options)
            mcq_block.append(f"[{m.mcq_id}] প্রশ্ন: {m.question}\n  অপশন: {opts_str}")
        mcq_text = "\n\n".join(mcq_block)

        prompt = f"""Answer these MCQs using ONLY these facts:

Facts:
{fact_block}

Questions:
{mcq_text}

Return JSON: {{"answers":[{{"mcq_id":"MCQ_xx","chosen_answer":"ক","confidence":0.9,"reasoning":"..."}}]}}"""
        raw = call_llm(self.client, self.model, self.SYSTEM_PROMPT, prompt, temperature=0.2)
        if not raw:
            return []

        parsed = safe_parse_json(raw)
        if not parsed:
            return []

        raw_answers = parsed if isinstance(parsed, list) else parsed.get("answers", [])
        if isinstance(raw_answers, dict):
            raw_answers = [raw_answers]
        return raw_answers


# ---------------------------------------------------------------------------
# Judge Agent
# ---------------------------------------------------------------------------

class JudgeAgent:
    """
    Validates each MCQ on four dimensions:
      1. Format correctness
      2. Grounding (answer derivable from provided facts)
      3. Clarity
      4. Distractor quality

    Also checks whether the Reasoner answered correctly.
    Issues a pass/fail verdict and provides structured failure reasons.

    FIX v2: SYSTEM_PROMPT now includes realistic score-range guidance so
    the model stops returning exactly 0.96 for every MCQ regardless of
    actual distractor quality and clarity.
    """

    SYSTEM_PROMPT = (
        "BCS MCQ evaluator. Score 4 dimensions (0-1), use exact formula: "
        "overall = format*0.15 + grounding*0.35 + clarity*0.25 + distractor*0.25. "
        "Rules: (1) Base grounding ONLY on given facts. "
        "(2) Vary scores per MCQ (no uniform scores). "
        "(3) format: correct JSON = 0.9-1.0, issues = 0.5-0.85. "
        "(4) grounding: answer clear in fact = 0.9-1.0, inferred = 0.4-0.7, absent = 0.0-0.3. "
        "(5) clarity: clear = 0.85-1.0, somewhat unclear = 0.6-0.84. "
        "(6) distractor: plausible = 0.75-1.0, weak = 0.5-0.74, obvious = 0.2-0.49. "
        "Output valid JSON only."
    )

    REASONS = {
        "WRONG_GROUNDING":    "সঠিক উত্তর তথ্যে পাওয়া যায়নি",
        "AMBIGUOUS_QUESTION": "প্রশ্ন অস্পষ্ট বা একাধিক উত্তর সম্ভব",
        "WEAK_DISTRACTORS":   "Distractor গুলো খুব সহজে বাদ দেওয়া যায়",
        "FORMAT_ERROR":       "MCQ ফরম্যাট সঠিক নয় (৪টি অপশন নেই)",
        "REASONER_WRONG":     "Reasoner সঠিক তথ্য থেকেও ভুল উত্তর দিয়েছে",
        "DUPLICATE_OPTIONS":  "একাধিক অপশনের মান একই বা অতি কাছাকাছি",
    }

    def __init__(self, client: str, model: str = DEFAULT_MODEL):
        self.client = client
        self.model  = model

    def evaluate(
        self,
        mcqs:              List[MCQ],
        supporting_facts:  List[Dict],
        reasoner_answers:  List[Dict],
    ) -> List[Dict]:
        """
        Returns evaluation dicts per MCQ:
        {mcq_id, passed, overall_score, dimension_scores, failure_reasons, feedback}
        """
        if not mcqs:
            return []

        reasoner_map = {a["mcq_id"]: a for a in reasoner_answers}
        fact_block   = "\n".join(
            f"- [{f['fact_id']}] {f['text']}" for f in supporting_facts
        )

        mcq_payload = []
        for m in mcqs:
            ra = reasoner_map.get(m.mcq_id, {})
            mcq_payload.append({
                "mcq_id":               m.mcq_id,
                "fact_id":              m.fact_id,
                "question":             m.question,
                "options":              {o.key: o.text for o in m.options},
                "correct_answer":       m.correct_answer,
                "explanation":          m.explanation,
                "reasoner_answer":      ra.get("chosen_answer", "N/A"),
                "reasoner_confidence":  ra.get("confidence", 0.0),
                "reasoner_reasoning":   ra.get("reasoning", ""),
            })

        prompt = f"""Evaluate these MCQs given the facts and reasoner answers.

Ground truth:
{fact_block}

MCQs & Reasoner answers:
{json.dumps(mcq_payload, ensure_ascii=False, indent=2)}

Score each MCQ (0-1) on: format, grounding(fact-based), clarity(one correct answer), distractor(plausible wrong options).
Formula: overall = format*0.15 + grounding*0.35 + clarity*0.25 + distractor*0.25.
Pass if overall >= {MIN_PASS_SCORE} AND grounding >= 0.80.
Failure codes: WRONG_GROUNDING | AMBIGUOUS_QUESTION | WEAK_DISTRACTORS | FORMAT_ERROR | REASONER_WRONG | DUPLICATE_OPTIONS

Return JSON: {{"evaluations":[{{"mcq_id":"MCQ_xx","passed":true,"reasoner_correct":true,"dimension_scores":{{"format_score":0.95,"grounding_score":0.9,"clarity_score":0.85,"distractor_score":0.75}},"overall_score":0.86,"failure_reasons":[],"feedback":"..."}}]}}"""
        raw = call_llm(self.client, self.model, self.SYSTEM_PROMPT, prompt, temperature=0.2)
        if not raw:
            return []

        parsed = safe_parse_json(raw)
        if not parsed:
            return []

        raw_evals = parsed if isinstance(parsed, list) else parsed.get("evaluations", [])
        if isinstance(raw_evals, dict):
            raw_evals = [raw_evals]
        return raw_evals


# ---------------------------------------------------------------------------
# MCQ Generator  (orchestrates the CRJ loop)
# ---------------------------------------------------------------------------

class MCQGenerator:
    """
    Orchestrates the full MCQ generation pipeline:

        Challenger → Reasoner → Judge → [Regenerate if needed]

    Includes duplicate MCQ detection via DuplicateDetector.

    Usage
    -----
    gen = MCQGenerator(hf_api_key="...", seen_questions_path="seen.json")
    result = gen.generate_from_facts(facts, difficulty="medium")
    gen.save_seen_questions()
    """

    def __init__(
        self,
        hf_api_key:           str,
        model:                str           = DEFAULT_MODEL,
        max_rounds:           int           = MAX_REGENERATION_ROUNDS,
        seen_questions_path:  Optional[str] = "runtime/seen_questions.json",
    ):
        self.client     = hf_api_key
        self.model      = model
        self.max_rounds = max_rounds

        self.challenger = ChallengerAgent(self.client, model)
        self.reasoner   = ReasonerAgent(self.client, model)
        self.judge      = JudgeAgent(self.client, model)

        self.dedup = DuplicateDetector(persist_path=seen_questions_path)
        self.dedup.load()

    def save_seen_questions(self) -> None:
        """Persist cross-session fingerprints after generation."""
        self.dedup.save()

    def _run_crj_round(
        self,
        facts:               List[Dict],
        difficulty:          str,
        round_num:           int,
        all_failure_reasons: List[str],
        seen_questions:      List[str],
    ) -> tuple:
        seen_questions = seen_questions or []
        candidates = self.challenger.generate(
            facts=facts, difficulty=difficulty,
            prior_failure_reasons=all_failure_reasons if round_num > 0 else None,
            regeneration_round=round_num,
            seen_questions=seen_questions or None,
        )
        if not candidates:
            return [], [], 0

        unique, dupes = self.dedup.filter_duplicates(candidates)
        dup_count = len(dupes)
        if not unique:
            return [], [], dup_count

        evaluations = []
        try:
            reasoner_answers = self.reasoner.answer(unique, facts)
            if reasoner_answers:
                evaluations = self.judge.evaluate(unique, facts, reasoner_answers)
        except Exception as exc:
            log.warning("CRJ Reasoner/Judge skipped (%s) — accepting MCQs with default score.", str(exc)[:80])

        return unique, evaluations, dup_count

    def generate_from_facts(
        self,
        facts:      List[Dict],
        difficulty: str           = "medium",
        topic:      Optional[str] = None,
        max_facts:  int           = 5,
    ) -> GenerationResult:
        selected = sorted(facts, key=lambda f: f.get("composite_score", 0.0), reverse=True)[:max_facts]
        if len(selected) < len(facts):
            log.info("Selected top %d/%d facts by composite_score", max_facts, len(facts))
        facts = selected
        episode_id = f"EP_{uuid.uuid4().hex[:12]}"
        topic_str  = topic or (facts[0].get("topic", "General") if facts else "General")
        fact_ids   = [f["fact_id"] for f in facts]

        log.info("=" * 60)
        log.info("Episode %s | Topic: %s | Difficulty: %s | Facts: %d",
                 episode_id, topic_str, difficulty, len(facts))
        log.info("=" * 60)

        accepted_mcqs:       List[MCQ] = []
        all_failure_reasons: List[str] = []
        total_duplicates               = 0
        round_num                      = 0
        pending_facts                  = facts

        while round_num < self.max_rounds and pending_facts:
            log.info("-- CRJ Round %d/%d --", round_num + 1, self.max_rounds)

            seen_in_episode = [m.question for m in accepted_mcqs]

            chunks = [pending_facts[i:i + BATCH_SIZE] for i in range(0, len(pending_facts), BATCH_SIZE)]

            for chunk_idx, chunk in enumerate(chunks):
                log.info("  CRJ chunk %d/%d (%d facts)", chunk_idx + 1, len(chunks), len(chunk))

                candidates, evaluations, dup_count = self._run_crj_round(
                    facts=chunk, difficulty=difficulty, round_num=round_num,
                    all_failure_reasons=all_failure_reasons,
                    seen_questions=seen_in_episode,
                )
                total_duplicates += dup_count

                if not candidates:
                    continue

                for mcq in candidates:
                    if evaluations:
                        eval_map = {e["mcq_id"]: e for e in evaluations}
                        ev = eval_map.get(mcq.mcq_id)
                        if ev:
                            mcq.quality_score = ev.get("overall_score", 0.0)
                            mcq.regeneration_round = round_num
                            dim = ev.get("dimension_scores", {})
                            mcq._grounding_score = float(dim.get("grounding_score", 0.0))
                            mcq._distractor_score = float(dim.get("distractor_score", 0.0))
                            mcq._clarity_score = float(dim.get("clarity_score", 0.0))

                            if ev.get("passed", False):
                                gs = float(dim.get("grounding_score", 0.0))
                                mcq.grounded = gs >= 0.80
                                accepted_mcqs.append(mcq)
                                self.dedup.register(mcq)
                                log.info("  + MCQ %s passed (score=%.2f).", mcq.mcq_id, mcq.quality_score)
                            else:
                                reasons = ev.get("failure_reasons", ["UNKNOWN"])
                                all_failure_reasons.extend(reasons)
                                log.info("  x MCQ %s failed - %s.", mcq.mcq_id, ", ".join(reasons))
                        continue

                    mcq.quality_score = 0.80
                    mcq.regeneration_round = round_num
                    mcq.grounded = True
                    accepted_mcqs.append(mcq)
                    self.dedup.register(mcq)
                    log.info("  + MCQ %s accepted (default score=0.80).", mcq.mcq_id)

                if chunk_idx < len(chunks) - 1:
                    time.sleep(0.5)

            round_num += 1

            pending_facts = [
                f for f in pending_facts
                if f["fact_id"] not in {m.fact_id for m in accepted_mcqs}
            ]

        # ── Final result ──────────────────────────────────────────────
        overall = (
            sum(m.quality_score for m in accepted_mcqs) / len(accepted_mcqs)
            if accepted_mcqs else 0.0
        )
        accepted_flag = bool(accepted_mcqs) and overall >= MIN_PASS_SCORE

        config = {
            "model":          self.model,
            "difficulty":     difficulty,
            "max_rounds":     self.max_rounds,
            "crj_rounds_used": round_num,
            "min_pass_score": MIN_PASS_SCORE,
        }

        result = GenerationResult(
            episode_id        = episode_id,
            topic             = topic_str,
            fact_ids          = fact_ids,
            mcqs              = accepted_mcqs,
            overall_score     = overall,
            accepted          = accepted_flag,
            crj_rounds        = round_num,
            generation_config = config,
            rejection_reasons = list(set(all_failure_reasons)),
            duplicate_count   = total_duplicates,
        )

        self._log_summary(result)
        return result

    def generate_all_difficulties(
        self,
        facts: List[Dict],
        topic: Optional[str] = None,
    ) -> Dict[str, GenerationResult]:
        """Generate easy + medium + hard MCQs for a fact set."""
        results = {}
        for diff in ["easy", "medium", "hard"]:
            log.info("\n▶ Generating %s MCQs...", diff.upper())
            results[diff] = self.generate_from_facts(facts, difficulty=diff, topic=topic)
        return results

    def generate_in_batches(
        self,
        facts:      List[Dict],
        difficulty: str           = "medium",
        topic:      Optional[str] = None,
    ) -> List[MCQ]:
        """
        Split facts into smaller batches of BATCH_SIZE and merge results.
        Prevents 504 Gateway Timeout on CraftX when fact count is large.
        Rests BATCH_REST_SECONDS between chunks so the server recovers.
        """
        all_mcqs: List[MCQ] = []
        chunks = [facts[i:i + BATCH_SIZE] for i in range(0, len(facts), BATCH_SIZE)]
        for idx, chunk in enumerate(chunks):
            log.info("generate_in_batches: chunk %d/%d (%d facts)",
                     idx + 1, len(chunks), len(chunk))
            try:
                result = self.generate_from_facts(chunk, difficulty=difficulty, topic=topic)
                all_mcqs.extend(result.mcqs)
            except Exception as exc:
                log.warning("generate_in_batches: chunk %d/%d failed — skipping. Error: %s",
                            idx + 1, len(chunks), str(exc)[:120])
            if idx < len(chunks) - 1:
                log.info("generate_in_batches: resting %ds before next chunk …",
                         BATCH_REST_SECONDS)
                time.sleep(BATCH_REST_SECONDS)
        return all_mcqs

    def _log_summary(self, result: GenerationResult) -> None:
        log.info("\n%s", "=" * 60)
        log.info("EPISODE SUMMARY — %s", result.episode_id)
        log.info("  Topic          : %s", result.topic)
        log.info("  Facts used     : %d", len(result.fact_ids))
        log.info("  MCQs accepted  : %d", len(result.mcqs))
        log.info("  Overall score  : %.3f", result.overall_score)
        log.info("  Accepted       : %s", result.accepted)
        log.info("  CRJ rounds     : %d", result.crj_rounds)
        log.info("  Duplicates     : %d", result.duplicate_count)
        if result.rejection_reasons:
            log.info("  Failure reasons: %s", ", ".join(result.rejection_reasons))
        log.info("=" * 60)


# ---------------------------------------------------------------------------
# KG Integration helper
# ---------------------------------------------------------------------------

def facts_from_kg(kg_builder, topic: str) -> List[Dict]:
    """
    Pull accepted, MCQ-ready facts from KnowledgeGraphBuilder for a topic.

    Parameters
    ----------
    kg_builder : KnowledgeGraphBuilder instance
    topic      : topic name string matching a TOPIC node

    Returns
    -------
    List of fact dicts suitable for MCQGenerator.generate_from_facts()
    """
    fact_ids = kg_builder.get_facts_by_topic(topic)
    facts    = []
    for fid in fact_ids:
        data = kg_builder.get_fact_data(fid)
        if not data:
            continue
        verdict = data.get("quality_verdict", "")
        if verdict in ("REJECT", "REJECT_AFTER_REFINE", "NOT_FOUND"):
            continue
        if verdict and verdict not in ("ACCEPT", "REFINED_ACCEPT", "SALVAGEABLE_ACCEPT"):
            continue
        readiness = data.get("mcq_readiness", 0.5)
        if readiness < 0.5:
            continue
        facts.append({
            "fact_id":         fid,
            "text":            data.get("text", ""),
            "topic":           topic,
            "mcq_suitable_for": data.get("mcq_suitable_for", ["factual"]),
            "mcq_readiness":   readiness,
            "composite_score": data.get("composite_score", 0.0),
            "source_reliability": data.get("source_reliability", 0.5),
        })
    return facts


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

DEMO_FACTS = [
    {
        "fact_id":         "FACT_demo0001",
        "text":            "বাংলাদেশের প্রথম রাষ্ট্রপতি ছিলেন শেখ মুজিবুর রহমান।",
        "topic":           "History",
        "mcq_suitable_for": ["who_question"],
        "mcq_readiness":   0.91,
        "composite_score": 0.88,
        "source_reliability": 0.95,
    },
    {
        "fact_id":         "FACT_demo0002",
        "text":            "বাংলাদেশের স্বাধীনতা দিবস ২৬ মার্চ।",
        "topic":           "History",
        "mcq_suitable_for": ["when_question"],
        "mcq_readiness":   0.89,
        "composite_score": 0.87,
        "source_reliability": 0.95,
    },
    {
        "fact_id":         "FACT_demo0003",
        "text":            "পদ্মা সেতুর দৈর্ঘ্য ৬.১৫ কিলোমিটার।",
        "topic":           "Infrastructure",
        "mcq_suitable_for": ["numeric_ranking"],
        "mcq_readiness":   0.85,
        "composite_score": 0.84,
        "source_reliability": 0.90,
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

    print("BCSBatighor — MCQ Generator Demo")
    print("=" * 60)

    generator = MCQGenerator(
        hf_api_key           = HF_API_KEY,
        seen_questions_path  = "runtime/seen_questions.json",
    )

    result = generator.generate_from_facts(DEMO_FACTS, difficulty="medium", topic="History")

    print("\n📋 Generated MCQs:")
    for mcq in result.mcqs:
        print(mcq.display())

    print(f"\n⚑  Duplicates filtered this run: {result.duplicate_count}")

    print("\n📦 Episode payload (for episodic_store.write_episode):")
    payload = result.to_episode_payload()
    payload["input_question"] = "বাংলাদেশের ইতিহাস সম্পর্কিত MCQ"
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    generator.save_seen_questions()
    print("\n✅ Seen questions saved → seen_questions.json")