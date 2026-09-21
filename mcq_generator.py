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
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from hf_client import call_llm, is_rate_limit_error, DEFAULT_MODEL as HF_DEFAULT_MODEL
from rejection_taxonomy import map_codes, classify_duplicate_mcq, RejectionTally

log = logging.getLogger("mcq_generator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MODEL    = HF_DEFAULT_MODEL   # e.g. "Qwen/Qwen2.5-72B-Instruct" — see hf_client.py

MAX_REGENERATION_ROUNDS      = 3
MIN_PASS_SCORE               = 0.70
DISTRACTOR_QUALITY_THRESHOLD = 0.65
BATCH_SIZE                   = 5   # safe for 72B on CraftX — avoids 504 timeouts
BATCH_REST_SECONDS           = 20  # pause between chunks so server doesn't choke

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


class TemporalGuardViolation(RuntimeError):
    """
    Raised when a fact with no verified temporal standing reaches generation.

    Subclasses RuntimeError so callers already catching the pipeline's own
    guard failure keep working.
    """


class DuplicateDetector:
    """
    Tracks question fingerprints within a session and optionally persists
    them to a JSON file for cross-session deduplication.
    """

    def __init__(self, persist_path: Optional[str] = "seen_questions.json"):
        self.persist_path = persist_path
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
                log.info("DuplicateDetector: created new fingerprint store → %s", self.persist_path)
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
            log.info("DuplicateDetector: saved %d fingerprints → %s",
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


Option = MCQOption  # convenient alias


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
    # What the model itself labeled this question's difficulty as,
    # BEFORE it got overwritten to match the requested difficulty below.
    # `difficulty` always holds the requested/target level (needed so
    # topic/budget accounting stays consistent); this field preserves
    # the model's own assessment so a mismatch isn't silently lost.
    self_reported_difficulty: Optional[str] = None
    supporting_fact_ids:      List[str] = field(default_factory=list)
    evidence_ids:             List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.supporting_fact_ids and self.fact_id:
            self.supporting_fact_ids = [self.fact_id]
        if not self.evidence_ids and self.fact_id:
            self.evidence_ids = [f"EVID_{self.fact_id}"]

    def to_episode_dict(self) -> Dict:
        return {
            "question":           self.question,
            "options":            [f"{o.key}) {o.text}" for o in self.options],
            "correct_answer":     self.correct_answer,
            "difficulty":         self.difficulty,
            "quality_score":      self.quality_score,
            "regeneration_round": self.regeneration_round,
            "supporting_fact_ids": self.supporting_fact_ids,
            "evidence_ids":       self.evidence_ids,
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
    rejection_codes:   List[str] = field(default_factory=list)
    # rejection_reasons (JudgeAgent's internal vocabulary) translated onto
    # the guideline's §10.2 taxonomy, PLUS one E-DUP entry per duplicate
    # MCQ dropped by DuplicateDetector — duplicates previously only showed
    # up as a bare count (`duplicate_count`) with no taxonomy code at all.
    # How many accepted MCQs had a self-reported difficulty that disagreed
    # with the requested one. The `difficulty` label on each accepted MCQ
    # is always the requested one regardless — this is visibility into how
    # often that label doesn't match what the model itself thought it
    # wrote, since forcing the label doesn't change the content.
    difficulty_mismatch_count: int = 0
    # Exact per-MCQ rejection tally for THIS episode only (guideline §8.4
    # "report rejection rate"), i.e. RejectionTally().report() captured
    # before the tally that produced it goes out of scope.
    #
    # `rejection_codes` above is presence-only — it's built from
    # map_codes(all_failure_reasons), and map_codes() de-duplicates, so
    # two MCQs that both failed with E-DIST collapse into a single
    # "E-DIST" entry in that list. This field keeps the real counts
    # (e.g. {"E-DIST": 2, "E-UNSUP": 1, ...}) plus the derived
    # _total_items / _total_rejections / _rejection_rate for this
    # episode specifically — not the generator's cross-episode running
    # total on self.rejection_tally, which already existed but was
    # never attached to the result object main() actually receives.
    rejection_tally: Dict[str, object] = field(default_factory=dict)
    # Real judge self-check agreement for THIS episode (see JudgeAgent
    # self_check_total/self_check_agreed) — feeds a genuine JAS in
    # main-pipeline.py's LiveTelemetry, replacing the old tautological
    # proxy that always came out ~1.0 regardless of actual judge quality.
    judge_self_check_total:  int = 0
    judge_self_check_agreed: int = 0

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
            "rejection_tally":      self.rejection_tally,
        }


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
# call_llm() now lives in hf_client.py (Hugging Face Inference API) and is
# imported above. Kept as a module-level name here too so existing callers
# doing `from mcq_generator import call_llm` (e.g. mcq_quality.py) keep working.

# ---------------------------------------------------------------------------
# JSON parsing  (FIXED — replaces old fragile extract_json)
# ---------------------------------------------------------------------------

def safe_parse_json(raw: str) -> Optional[Dict]:
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
        "তুমি একজন BCS পরীক্ষার প্রশ্নপত্র প্রণয়নকারী বিশেষজ্ঞ, "
        "যার ১৫+ বছরের অভিজ্ঞতা আছে। "
        "শুধুমাত্র দেওয়া তথ্যের উপর ভিত্তি করে প্রামাণিক BCS-মানের MCQ তৈরি করো। "

        # ── Rule 1: Type distribution ─────────────────────────────────
        "প্রশ্নের ধরন বিতরণ — এটি সবচেয়ে গুরুত্বপূর্ণ নিয়ম: "
        "প্রতিটি ব্যাচে ৭৫% প্রশ্ন হবে numeric_ranking বা analytical ধরনের "
        "(সংখ্যা, অনুচ্ছেদ নম্বর, শতাংশ, মন্ত্রণালয়, সংজ্ঞা, বৈশিষ্ট্য, উপাদান বিষয়ক); "
        "১৫% হবে who_question; "
        "when_question ও where_question মিলিয়ে সর্বোচ্চ ১০%। "
        "when_question অতিরিক্ত ব্যবহার সম্পূর্ণ নিষিদ্ধ। "

        # ── Rule 2: BCS distractor style ─────────────────────────────
        "Distractor নিয়ম (BCS-মান): "
        "(ক) সংখ্যা প্রশ্নে: কাছাকাছি সংখ্যা ব্যবহার করো "
        "(যেমন ২১৯/২২১/২২৩/২২৫ অথবা ৯৫/৯৬/৯৭/৯৮)। "
        "(খ) প্রতিষ্ঠান/মন্ত্রণালয় প্রশ্নে: একই ধরনের প্রতিষ্ঠান ব্যবহার করো। "
        "(গ) ব্যক্তি প্রশ্নে: একই যুগের একই ভূমিকার ব্যক্তি ব্যবহার করো। "
        "কখনো সহজে বাদ দেওয়া যায় এমন distractor দেবে না। "

        # ── Rule 3: BCS stem patterns ─────────────────────────────────
        "BCS প্রশ্নের কাঠামো: নিচের যেকোনো ধরন ব্যবহার করো — "
        "'X কোন অনুচ্ছেদে উল্লেখ আছে?', "
        "'X কোন মন্ত্রণালয়ের আওতাভুক্ত?', "
        "'নিচের কোনটি X-এর উপাদান?', "
        "'X-এর ক্ষেত্রে কত শতাংশ/কতটি?', "
        "'X কোন শাসনামলের নিদর্শন?'। "

        # ── Rule 4: Spelling and translation (Task 2B) ────────────────
        # Task 2A shipped পৌহেলা / বুরিগঙ্গা / পাদমা and rendered the source
        # word "secular" as বিশ্বাস্ত. The quality gate now rejects all four
        # outright, so stating the constraint here is cheaper than paying
        # for a regeneration round.
        "বানান ও অনুবাদ (বাধ্যতামূলক): "
        "(ক) প্রমিত বাংলা বানান ব্যবহার করো — প্রশ্ন, অপশন ও ব্যাখ্যা সব জায়গায়। "
        "বিশেষ্য ও নদী/স্থান/উৎসবের নাম সঠিক বানানে লেখো "
        "(পহেলা বৈশাখ, বুড়িগঙ্গা, পদ্মা — পৌহেলা/বুরিগঙ্গা/পাদমা ভুল)। "
        "(খ) সংখ্যা বাংলা অঙ্কে লেখো (৮, ১৪, ১৯৫২ — 8/14/1952 নয়)। "
        "(গ) ইংরেজি তথ্য থেকে অনুবাদ করার সময় মূল পরিভাষা ঠিক রাখো — "
        "secular = ধর্মনিরপেক্ষ। তথ্যে কোনো বিশেষণ দিয়ে দাবিটি সীমিত করা থাকলে "
        "(যেমন 'largest secular festival') সেই বিশেষণ প্রশ্নেও রাখতে হবে; "
        "বাদ দিলে প্রশ্নটি তথ্যের চেয়ে বড় দাবি করে। "

        # ── Rule 5: A distractor must be WRONG (Task 2B) ──────────────
        "সমার্থক distractor সম্পূর্ণ নিষিদ্ধ: কোনো অপশন সঠিক উত্তরের সমার্থক, "
        "বিকল্প নাম, সংক্ষিপ্ত রূপ বা অনুবাদ হতে পারবে না — সেটি দ্বিতীয় সঠিক "
        "উত্তর হয়ে যায়। উদাহরণ: সঠিক উত্তর 'পহেলা বৈশাখ' হলে 'নববর্ষ' বা "
        "'বাংলা নববর্ষ' distractor হিসেবে দেওয়া যাবে না। "
        "প্রতিটি distractor এমন হতে হবে যা দেওয়া তথ্য অনুযায়ী স্পষ্টভাবে ভুল। "

        # ── Rule 6: One question per fact-claim (Task 2B) ─────────────
        "একই তথ্য থেকে একাধিক MCQ বানালে প্রতিটির সঠিক উত্তর আলাদা হতে হবে। "
        "একই উত্তরকে ভিন্ন ভাষায় দুবার জিজ্ঞাসা করা নকল প্রশ্ন হিসেবে বাতিল হবে। "

        "অন্যান্য নিয়ম: "
        "(১) সঠিক উত্তর সমানভাবে ক/খ/গ/ঘ তে বিতরণ করো। "
        "(২) পরিমার্জিত ও আনুষ্ঠানিক বাংলায় প্রশ্ন লেখো। "
        "সর্বদা valid JSON ফরম্যাটে উত্তর দাও।"
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

        # Concrete difficulty rubric. Previously "difficulty" was only
        # ever passed as a JSON template value for the model to echo back
        # — it was never told what actually makes a question easy, medium,
        # or hard, which is why the model's own self-reported difficulty
        # routinely disagreed with what was requested (see the log's
        # "self-reported difficulty disagreed with requested" warnings).
        # Forcing the label afterward doesn't change the content, so the
        # rubric below is what should actually make the levels differ.
        difficulty_rubric = {
            "easy": (
                "সহজ (easy): উত্তর তথ্যে সরাসরি ও স্পষ্টভাবে উল্লেখিত থাকবে, প্রশ্ন সরল "
                "সরাসরি জিজ্ঞাসা হবে, distractor গুলো স্পষ্টভাবে ভুল হবে (দূরবর্তী সংখ্যা/নাম)।"
            ),
            "medium": (
                "মাঝারি (medium): উত্তরের জন্য তথ্যের একাধিক অংশ যুক্ত করতে হতে পারে, "
                "distractor গুলো কাছাকাছি হবে (সংখ্যায় ছোট পার্থক্য, একই শ্রেণির নাম) "
                "যাতে সরাসরি অনুমান করে বাদ দেওয়া না যায়।"
            ),
            "hard": (
                "কঠিন (hard): সূক্ষ্ম পার্থক্য বা কম-প্রচলিত বিবরণের উপর ভিত্তি করে হবে, "
                "distractor গুলো অত্যন্ত কাছাকাছি ও বিভ্রান্তিকর হবে (একই ধরনের সংখ্যা/নাম/সময়কাল), "
                "এবং প্রশ্নে একাধিক শর্ত বা নির্দিষ্ট বিবরণ (যেমন সঠিক তারিখ, নির্দিষ্ট অনুচ্ছেদ) থাকতে পারে।"
            ),
        }.get(difficulty, "")

        prompt = f"""
তথ্যসমূহ (এই তথ্য থেকেই MCQ তৈরি করতে হবে):
{fact_block}
{failure_block}{avoid_block}
কাঙ্ক্ষিত কঠিনতা স্তর — {difficulty}:
{difficulty_rubric}
এই ব্যাচের প্রতিটি MCQ অবশ্যই উপরের "{difficulty}" রুব্রিক অনুযায়ী তৈরি করতে হবে,
শুধু "difficulty" ফিল্ডে "{difficulty}" লিখলেই হবে না — প্রশ্ন ও distractor-এর
প্রকৃত কাঠিন্য সেই স্তরের সাথে মিলতে হবে।

নিয়মাবলী:
1. প্রতিটি MCQ অবশ্যই দেওয়া তথ্যের উপর ভিত্তি করে হতে হবে — কোনো বাইরের জ্ঞান ব্যবহার করবে না।
2. প্রতিটি MCQ-তে ৪টি অপশন (ক, খ, গ, ঘ) থাকবে।
3. সঠিক উত্তর অবশ্যই তথ্যে স্পষ্টভাবে উল্লেখিত হতে হবে।
4. Distractor: সংখ্যা হলে কাছাকাছি সংখ্যা, ব্যক্তি হলে একই যুগের ব্যক্তি, প্রতিষ্ঠান হলে একই ধরনের প্রতিষ্ঠান — সহজে বাদ দেওয়া যাবে না।
5. fact_id ব্যবহার করো প্রতিটি MCQ কোন তথ্য থেকে এসেছে তা চিহ্নিত করতে।
6. question_type বিতরণ এই ব্যাচে ({n_facts}টি তথ্য):
   → {n_factual}টি: numeric_ranking অথবা analytical
     (সংখ্যা/অনুচ্ছেদ/শতাংশ/মন্ত্রণালয়/উপাদান/বৈশিষ্ট্য বিষয়ক)
   → {n_who}টি: who_question
   → সর্বোচ্চ ১টি: when_question অথবা where_question (উভয় মিলে)
   question_type মান: who_question / when_question / where_question / numeric_ranking / analytical

JSON ফরম্যাটে উত্তর দাও:
{{
  "mcqs": [
    {{
      "fact_id": "FACT_xxxxxxxx",
      "question": "MCQ প্রশ্ন বাংলায়",
      "options": {{
        "ক": "অপশন ১",
        "খ": "অপশন ২",
        "গ": "অপশন ৩",
        "ঘ": "অপশন ৪"
      }},
      "correct_answer": "খ",
      "difficulty": "{difficulty}",
      "question_type": "numeric_ranking",
      "explanation": "কেন এই উত্তর সঠিক (তথ্য থেকে উদ্ধৃত করে)"
    }}
  ]
}}
"""
        raw = call_llm(self.client, self.model, self.SYSTEM_PROMPT, prompt, temperature=0.7)
        if not raw:
            log.warning("Challenger returned empty response.")
            return []

        parsed = safe_parse_json(raw)
        if not parsed:
            log.warning("Challenger: could not parse JSON.")
            return []

        mcqs = []
        for item in parsed.get("mcqs", []):
            try:
                opts_raw = item.get("options", {})
                options  = [MCQOption(key=k, text=v) for k, v in opts_raw.items()]
                self_reported = item.get("difficulty")
                if self_reported and self_reported != difficulty:
                    log.info(
                        "Challenger self-reported difficulty '%s' disagreed with requested '%s' "
                        "[round=%d] — assigning requested difficulty, keeping self-report on record.",
                        self_reported, difficulty, regeneration_round,
                    )
                mcqs.append(MCQ(
                    mcq_id            = f"MCQ_{uuid.uuid4().hex[:8]}",
                    fact_id           = item["fact_id"],
                    question          = item["question"],
                    options           = options,
                    correct_answer    = item["correct_answer"],
                    difficulty        = difficulty,
                    self_reported_difficulty = self_reported,
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
        "তুমি একজন BCS পরীক্ষার্থী। "
        "শুধুমাত্র দেওয়া তথ্যের উপর ভিত্তি করে প্রশ্নের উত্তর দাও — "
        "নিজের সাধারণ জ্ঞান ব্যবহার করো না। "
        "যদি তথ্যে উত্তর না পাওয়া যায়, confidence = 0.1 দিয়ে যেকোনো অপশন বেছে নাও। "
        "সর্বদা valid JSON ফরম্যাটে উত্তর দাও।"
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

        prompt = f"""
নিচের তথ্যগুলো পড়ো এবং প্রতিটি প্রশ্নের উত্তর দাও।
শুধুমাত্র দেওয়া তথ্যের উপর ভিত্তি করে উত্তর দাও।

তথ্যসমূহ:
{fact_block}

প্রশ্নসমূহ:
{mcq_text}

JSON ফরম্যাটে উত্তর দাও:
{{
  "answers": [
    {{
      "mcq_id": "MCQ_xxxxxxxx",
      "chosen_answer": "খ",
      "confidence": 0.85,
      "reasoning": "কেন এই উত্তর বেছে নিলাম"
    }}
  ]
}}
"""
        raw = call_llm(self.client, self.model, self.SYSTEM_PROMPT, prompt, temperature=0.2)
        if not raw:
            return []

        parsed = safe_parse_json(raw)
        if not parsed:
            return []

        return parsed.get("answers", [])


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
        "তুমি একজন কঠোর BCS MCQ মূল্যায়নকারী বিশেষজ্ঞ। "
        "তোমার কাজ: চারটি মাত্রায় MCQ মূল্যায়ন করা — "
        "format, grounding, clarity, distractor। "

        # ── Core scoring rules ────────────────────────────────────────
        "গুরুত্বপূর্ণ নিয়ম: "
        "(১) grounding_score শুধুমাত্র দেওয়া তথ্যের উপর ভিত্তি করে দাও — "
        "নিজের জ্ঞান ব্যবহার করবে না। "
        "(২) overall_score = (format×0.15)+(grounding×0.35)+(clarity×0.25)+(distractor×0.25) "
        "— এই সূত্র ছাড়া অন্য কোনো গণনা করবে না। "
        "(৩) সন্দেহ হলে কঠোর স্কোর দাও। "

        # ── FIX: realistic score ranges ───────────────────────────────
        "স্কোর সীমা (এটি অবশ্যই মানতে হবে): "
        "সব MCQ এর একই স্কোর (যেমন ০.৯৬) দেওয়া ভুল — প্রতিটি MCQ আলাদাভাবে মূল্যায়ন করো। "
        "format_score: JSON কাঠামো সঠিক হলে ০.৯০-১.০; সমস্যা থাকলে ০.৫০-০.৮৫। "
        "grounding_score: সঠিক উত্তর তথ্যে স্পষ্ট থাকলে ০.৯০-১.০; "
        "অনুমান লাগলে ০.৪০-০.৭০; তথ্যে নেই হলে ০.০-০.৩০ (WRONG_GROUNDING ফ্ল্যাগ করো)। "
        "clarity_score: প্রশ্ন সম্পূর্ণ স্পষ্ট ০.৮৫-১.০; "
        "কিছুটা অস্পষ্ট ০.৬০-০.৮৪; অনেক অস্পষ্ট ০.৩০-০.৫৯। "
        "distractor_score: কাছাকাছি সংখ্যা/একই শ্রেণির distractor ০.৭৫-১.০; "
        "প্রশংসনীয় কিন্তু সহজ ০.৫০-০.৭৪; সহজে বাদ দেওয়া যায় ০.২০-০.৪৯ "
        "(WEAK_DISTRACTORS ফ্ল্যাগ করো)। "

        "সর্বদা valid JSON ফরম্যাটে উত্তর দাও।"
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
        # Self-check tally (FIX): how often the LLM's own reported
        # overall_score/passed verdict actually matched the deterministic
        # recompute in _verify_and_correct(). This is real evidence of
        # judge reliability/consistency — previously computed and logged
        # as a warning per-MCQ but then thrown away, while the pipeline's
        # JAS ("Judge Agreement Score") metric was instead built from a
        # tautological proxy (accepted-MCQs' hardcoded auto-pass vs a
        # near-always-true secondary threshold on the SAME accepted
        # MCQ's own score), which is why JAS reported a misleading 1.0
        # even in runs full of self-check mismatches. GenerationResult
        # now exposes these two counters so main-pipeline.py can compute
        # a genuine agreement rate instead.
        self.self_check_total  = 0
        self.self_check_agreed = 0

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

        prompt = f"""
নিচের MCQ গুলো মূল্যায়ন করো। প্রতিটি MCQ-এর জন্য চারটি মাত্রায় স্কোর দাও।

তথ্যসমূহ (Ground truth):
{fact_block}

MCQ ও Reasoner এর উত্তর:
{json.dumps(mcq_payload, ensure_ascii=False, indent=2)}

মূল্যায়নের মাত্রা (প্রতিটি 0.0–1.0):
1. format_score        — ৪টি সঠিক অপশন, বাংলায় প্রশ্ন, সঠিক কাঠামো
2. grounding_score     — সঠিক উত্তর দেওয়া তথ্যে স্পষ্টভাবে আছে কিনা
3. clarity_score       — প্রশ্ন স্পষ্ট, একটিমাত্র সঠিক উত্তর সম্ভব
4. distractor_score    — ভুল অপশনগুলো বিভ্রান্তিকর কিন্তু নির্দিষ্টভাবে ভুল

Reasoner সঠিক উত্তর দিলে reasoner_correct = true।

স্কোর নির্দেশিকা:
- format_score:    ভালো ০.৯০-১.০ | সমস্যা ০.৫০-০.৮৫
- grounding_score: স্পষ্ট ০.৯০-১.০ | অনুমান ০.৪০-০.৭০ | নেই ০.০-০.৩০
- clarity_score:   স্পষ্ট ০.৮৫-১.০ | কিছুটা ০.৬০-০.৮৪ | অস্পষ্ট ০.৩০-০.৫৯
- distractor_score: কঠিন ০.৭৫-১.০ | মাঝারি ০.৫০-০.৭৪ | সহজ ০.২০-০.৪৯

অনুমোদনের নিয়ম:
overall_score = (format × 0.15) + (grounding × 0.35) + (clarity × 0.25) + (distractor × 0.25)
- passed = true যদি overall_score ≥ {MIN_PASS_SCORE} এবং grounding_score ≥ 0.80

failure_reasons হবে এই কোডগুলোর মধ্যে যেকোনো:
WRONG_GROUNDING, AMBIGUOUS_QUESTION, WEAK_DISTRACTORS, FORMAT_ERROR, REASONER_WRONG, DUPLICATE_OPTIONS

JSON ফরম্যাটে উত্তর দাও:
{{
  "evaluations": [
    {{
      "mcq_id": "MCQ_xxxxxxxx",
      "passed": true,
      "reasoner_correct": true,
      "dimension_scores": {{
        "format_score":    0.95,
        "grounding_score": 0.90,
        "clarity_score":   0.85,
        "distractor_score": 0.75
      }},
      "overall_score": 0.86,
      "failure_reasons": [],
      "feedback": "মূল্যায়নকারীর মন্তব্য"
    }}
  ]
}}
"""
        raw = call_llm(self.client, self.model, self.SYSTEM_PROMPT, prompt, temperature=0.2)
        if not raw:
            return []

        parsed = safe_parse_json(raw)
        if not parsed:
            return []

        evaluations = parsed.get("evaluations", [])
        return [self._verify_and_correct(ev) for ev in evaluations]

    # Weights match the formula stated verbatim in SYSTEM_PROMPT above —
    # kept as a named constant so the check and the prompt can't silently
    # drift apart.
    _WEIGHTS = {"format_score": 0.15, "grounding_score": 0.35,
                "clarity_score": 0.25, "distractor_score": 0.25}

    def _verify_and_correct(self, ev: Dict) -> Dict:
        """
        Independently recompute overall_score and passed from the LLM's own
        dimension_scores, rather than trusting the LLM's self-reported
        overall_score/passed values as-is.

        Why this matters: the SYSTEM_PROMPT tells the model the exact
        formula and pass rule, but nothing previously verified the model
        actually followed it — an LLM arithmetic slip (e.g. reporting
        passed=true while overall_score is really below MIN_PASS_SCORE, or
        vice versa) would flow straight through into `accepted_mcqs` and
        from there into generated_mcqs.json and episodic memory, since this
        Judge's `passed` flag is the actual accept/reject gate in
        generate_from_facts(). This recomputes both fields deterministically
        in Python and overwrites whatever the LLM reported, logging a
        warning when the two disagreed so drift is visible rather than
        silent.
        """
        dim = ev.get("dimension_scores", {})
        try:
            recomputed = sum(
                float(dim.get(key, 0.0)) * weight
                for key, weight in self._WEIGHTS.items()
            )
        except (TypeError, ValueError):
            recomputed = 0.0

        grounding = float(dim.get("grounding_score", 0.0) or 0.0)
        recomputed_passed = recomputed >= MIN_PASS_SCORE and grounding >= 0.80

        llm_overall = ev.get("overall_score")
        llm_passed  = ev.get("passed")
        mismatch = (
            llm_passed != recomputed_passed
            or llm_overall is None
            or abs(float(llm_overall) - recomputed) > 0.01
        )
        self.self_check_total += 1
        if mismatch:
            log.warning(
                "Judge self-check mismatch for %s — LLM said overall=%s/passed=%s, "
                "recomputed overall=%.4f/passed=%s. Using recomputed values.",
                ev.get("mcq_id", "?"), llm_overall, llm_passed,
                recomputed, recomputed_passed,
            )
        else:
            self.self_check_agreed += 1

        ev["overall_score"] = round(recomputed, 4)
        ev["passed"] = recomputed_passed
        ev["judge_self_check_agreed"] = not mismatch
        return ev


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
        seen_questions_path:  Optional[str] = "seen_questions.json",
        kg_builder:           Optional[Any] = None,
        cutoff_date:          str           = "2023-04-19",
    ):
        self.client      = hf_api_key
        self.model       = model
        self.max_rounds  = max_rounds
        self.kg_builder  = kg_builder
        self.cutoff_date = cutoff_date

        self.challenger = ChallengerAgent(self.client, model)
        self.reasoner   = ReasonerAgent(self.client, model)
        self.judge      = JudgeAgent(self.client, model)

        self.dedup = DuplicateDetector(persist_path=seen_questions_path)
        self.dedup.load()

        # Cross-episode rejection tally (guideline §8.4 "report rejection
        # rate") — accumulates across every generate_from_facts() call made
        # through this generator instance, not just the last episode.
        self.rejection_tally = RejectionTally()

    def save_seen_questions(self) -> None:
        """Persist cross-session fingerprints after generation."""
        self.dedup.save()

    def generate_from_facts(
        self,
        facts:      List[Dict],
        difficulty: str           = "medium",
        topic:      Optional[str] = None,
        kg_builder: Optional[Any] = None,
    ) -> GenerationResult:
        """
        Full CRJ pipeline for a list of KG fact dicts.

        Parameters
        ----------
        facts      : KG fact dicts — must have: fact_id, text, topic, mcq_suitable_for
        difficulty : "easy" | "medium" | "hard"
        topic      : topic string (uses facts[0]['topic'] if None)
        kg_builder : KnowledgeGraphBuilder instance (optional override)
        """
        if kg_builder is not None:
            self.kg_builder = kg_builder
        # ------------------------------------------------------------------
        # Temporal firewall. This is the last point before a model sees any
        # fact text, and the only one that catches every caller.
        #
        # facts_from_kg() STAMPS temporal_status; it does not filter on it.
        # main-pipeline.py is safe only because it runs
        # KnowledgeGraphBuilder.strict_temporal_guard() first and raises
        # before reaching here. Any other caller -- a notebook, a new script,
        # a baseline harness -- gets no protection from that, and on the
        # current seed 46 unversioned facts would walk straight into
        # generation. Refuse them here instead of trusting each caller to
        # remember the guard.
        #
        # Facts carry no temporal_status at all when no cutoff was active
        # (facts_from_kg only stamps it when as_of is given), so the
        # non-temporal path is untouched: absence means "no cutoff", which is
        # a different thing from "cutoff ran and this fact failed it".
        #
        # Interval violations are already handled upstream --
        # get_facts_by_topic_as_of drops a fact whose validity interval does
        # not cover the cutoff -- so `unversioned` is the one status that can
        # still arrive here.
        unversioned = [f.get("fact_id") for f in facts
                       if f.get("temporal_status") == "unversioned"]
        if unversioned:
            raise TemporalGuardViolation(
                "Refusing to generate from %d unversioned fact(s): %s. "
                "A fact marked `unversioned` has no verified validity "
                "interval and no accepted pre-cutoff source evidence, so "
                "nothing establishes that it was true at the cutoff. Run "
                "KnowledgeGraphBuilder.strict_temporal_guard() before "
                "generation, or retrieve with "
                "allow_static_source_evidence=True if these are Model B "
                "static facts."
                % (len(unversioned), unversioned[:10])
            )

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

        # Per-episode tally (exact per-MCQ counts for THIS call only).
        # self.rejection_tally keeps accumulating across the generator's
        # whole lifetime as before — this one is local so its .report()
        # can be captured onto GenerationResult before it goes out of scope.
        episode_tally = RejectionTally()

        # FIX (#4, root cause of JAS staying ~1.0 despite logged mismatches):
        # self.judge is one JudgeAgent shared across every generate_from_facts()
        # call this generator makes (all topics/episodes in the run), so
        # self.judge.self_check_total/self_check_agreed are CUMULATIVE
        # counters, not per-episode ones. The old code copied those raw
        # cumulative snapshots onto every episode's GenerationResult, and
        # main-pipeline.py's LiveTelemetry.ingest_result() then SUMMED that
        # snapshot across every episode — i.e. it added the running total
        # again and again, wildly over-counting both numerator and
        # denominator in a way that (because both grow together) stayed
        # close to whatever the early-episode agreement ratio was,
        # masking later mismatches almost entirely. Snapshot the counters
        # here, before this episode's judge calls, so we can report the
        # per-episode DELTA instead of the running cumulative total.
        _self_check_total_before  = self.judge.self_check_total
        _self_check_agreed_before = self.judge.self_check_agreed

        while round_num < self.max_rounds and pending_facts:
            log.info("── CRJ Round %d/%d ──", round_num + 1, self.max_rounds)

            seen_in_episode = [m.question for m in accepted_mcqs]

            # ① Challenger
            candidates = self.challenger.generate(
                facts                 = pending_facts,
                difficulty            = difficulty,
                prior_failure_reasons = all_failure_reasons if round_num > 0 else None,
                regeneration_round    = round_num,
                seen_questions        = seen_in_episode or None,
            )
            if not candidates:
                log.warning("Round %d: Challenger produced no MCQs.", round_num + 1)
                break

            # Duplicate filter
            unique_candidates, dupes = self.dedup.filter_duplicates(candidates)
            total_duplicates += len(dupes)
            if dupes:
                log.info("  Filtered %d duplicate MCQ(s) in round %d.", len(dupes), round_num + 1)
            if not unique_candidates:
                log.warning("Round %d: All candidates were duplicates.", round_num + 1)
                round_num += 1
                continue

            # ② Reasoner
            reasoner_answers = self.reasoner.answer(unique_candidates, pending_facts)

            # ③ Judge
            evaluations = self.judge.evaluate(unique_candidates, pending_facts, reasoner_answers)
            eval_map    = {e["mcq_id"]: e for e in evaluations}

            newly_accepted: List[MCQ] = []
            accepted_fact_ids: set   = set()

            for mcq in unique_candidates:
                ev = eval_map.get(mcq.mcq_id)
                if not ev:
                    log.warning("No evaluation for MCQ %s — treating as failed.", mcq.mcq_id)
                    continue

                mcq.quality_score      = ev.get("overall_score", 0.0)
                mcq.regeneration_round = round_num

                dim = ev.get("dimension_scores", {})
                mcq._grounding_score  = float(dim.get("grounding_score",  0.0))
                mcq._distractor_score = float(dim.get("distractor_score", 0.0))
                mcq._clarity_score    = float(dim.get("clarity_score",    0.0))

                # ── Task 6.3: Integrated distractor plausibility & ambiguity screening gate ──
                screener_failures = self._screen_candidate_distractors(mcq, pending_facts)
                if screener_failures:
                    ev["passed"] = False
                    ev.setdefault("failure_reasons", []).extend(screener_failures)
                    log.info("  ✗ MCQ %s rejected by distractor plausibility gate: %s",
                             mcq.mcq_id, ", ".join(screener_failures))

                if ev.get("passed", False):
                    gs           = float(dim.get("grounding_score", 0.0))
                    mcq.grounded = gs >= 0.80
                    newly_accepted.append(mcq)
                    accepted_fact_ids.add(mcq.fact_id)
                    self.rejection_tally.add([])
                    episode_tally.add([])
                    log.info("  ✓ MCQ %s passed (score=%.2f).", mcq.mcq_id, mcq.quality_score)
                else:
                    reasons = ev.get("failure_reasons", ["UNKNOWN"])
                    all_failure_reasons.extend(reasons)
                    mapped = map_codes(reasons)
                    self.rejection_tally.add(mapped)
                    episode_tally.add(mapped)
                    log.info("  ✗ MCQ %s failed — %s.", mcq.mcq_id, ", ".join(reasons))

            for _dupe in dupes:
                self.rejection_tally.add([classify_duplicate_mcq()])
                episode_tally.add([classify_duplicate_mcq()])

            self.dedup.register_many(newly_accepted)
            accepted_mcqs.extend(newly_accepted)

            pending_facts = [
                f for f in pending_facts
                if f["fact_id"] not in accepted_fact_ids
            ]
            round_num += 1

            if not pending_facts:
                log.info("All facts have accepted MCQs — stopping CRJ loop.")
                break

        # ── Cross-MCQ option-set collision filter (FIX) ─────────────────
        # DuplicateDetector above only fingerprints exact question TEXT,
        # so two MCQs built from the same fact that ask about different
        # attributes (different stem, different correct_answer) but reuse
        # the exact same 4 option values sail through untouched — even
        # though a test-taker who sees both can answer-by-elimination
        # across the pair. Keep the higher-scoring MCQ per
        # (fact_id, option-value-set) and drop the rest as E-DUP.
        optionset_collisions = 0
        seen_option_sets: Dict[Tuple[str, frozenset], "MCQ"] = {}
        deduped_accepted: List[MCQ] = []
        for mcq in sorted(accepted_mcqs, key=lambda m: -m.quality_score):
            key = (mcq.fact_id, frozenset(o.text for o in mcq.options))
            if key in seen_option_sets:
                optionset_collisions += 1
                log.warning(
                    "Option-set collision: MCQ %s shares fact %s and all option "
                    "values with already-accepted MCQ %s — dropping the "
                    "lower-scoring one.",
                    mcq.mcq_id, mcq.fact_id, seen_option_sets[key].mcq_id,
                )
                self.rejection_tally.add([classify_duplicate_mcq()])
                episode_tally.add([classify_duplicate_mcq()])
                continue
            seen_option_sets[key] = mcq
            deduped_accepted.append(mcq)
        if optionset_collisions:
            # Restore original relative order rather than leaving the
            # score-sorted order from the dedup pass above.
            kept_ids = {m.mcq_id for m in deduped_accepted}
            accepted_mcqs = [m for m in accepted_mcqs if m.mcq_id in kept_ids]
            total_duplicates += optionset_collisions

        # ── Final result ──────────────────────────────────────────────
        overall = (
            sum(m.quality_score for m in accepted_mcqs) / len(accepted_mcqs)
            if accepted_mcqs else 0.0
        )
        accepted_flag = bool(accepted_mcqs) and overall >= MIN_PASS_SCORE

        mismatch_count = sum(
            1 for m in accepted_mcqs
            if m.self_reported_difficulty and m.self_reported_difficulty != difficulty
        )

        config = {
            "model":          self.model,
            "difficulty":     difficulty,
            "max_rounds":     self.max_rounds,
            "crj_rounds_used": round_num,
            "min_pass_score": MIN_PASS_SCORE,
            "difficulty_mismatch_count": mismatch_count,
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
            rejection_codes   = (
                map_codes(all_failure_reasons)
                + ([classify_duplicate_mcq()] if total_duplicates else [])
            ),
            difficulty_mismatch_count = mismatch_count,
            rejection_tally    = episode_tally.report(),
            # Per-episode delta, not the raw cumulative snapshot — see
            # FIX (#4) note above where these were captured.
            judge_self_check_total  = self.judge.self_check_total  - _self_check_total_before,
            judge_self_check_agreed = self.judge.self_check_agreed - _self_check_agreed_before,
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

    def generate_temporal_distractors(
        self,
        kg_builder: Any,
        fact_id: Optional[str] = None,
        topic: Optional[str] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Instance method wrapping top-level generate_temporal_distractors."""
        return generate_temporal_distractors(
            kg_builder=kg_builder,
            fact_id=fact_id,
            topic=topic,
            t_cutoff=getattr(self, "cutoff_date", "2023-04-19"),
            limit=limit,
        )

    def generate_semantic_confusers(
        self,
        kg_builder: Any,
        fact_id: Optional[str] = None,
        entity_name: Optional[str] = None,
        entity_subtype: Optional[str] = None,
        topic: Optional[str] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Instance method wrapping top-level generate_semantic_confusers."""
        return generate_semantic_confusers(
            kg_builder=kg_builder,
            fact_id=fact_id,
            entity_name=entity_name,
            entity_subtype=entity_subtype,
            topic=topic,
            t_cutoff=getattr(self, "cutoff_date", "2023-04-19"),
            limit=limit,
        )

    def _screen_candidate_distractors(
        self,
        mcq: MCQ,
        supporting_facts: List[Dict],
    ) -> List[str]:
        """
        Sub-task 6.3: In-pipeline distractor plausibility and ambiguity screener.
        Applies RuleBasedScreener and check_distractor_plausibility to reject
        distractors that are synonymous, duplicate, or simultaneously valid at t*.
        """
        try:
            from mcq_quality import RuleBasedScreener
            screener = RuleBasedScreener()
            mcq_payload = {
                "mcq_id": mcq.mcq_id,
                "fact_id": mcq.fact_id,
                "question": mcq.question,
                "options": {o.key: o.text for o in mcq.options},
                "correct_answer": mcq.correct_answer,
                "explanation": mcq.explanation,
            }
            score, failures = screener.screen(
                mcq_payload,
                supporting_facts=supporting_facts,
                kg_builder=getattr(self, "kg_builder", None),
                t_cutoff=getattr(self, "cutoff_date", "2023-04-19"),
            )
            # Filter for distractor-relevant failure codes
            blockers = [
                f for f in failures
                if f in (
                    "DISTRACTOR_VALID_AT_CUTOFF",
                    "SYNONYM_DISTRACTOR",
                    "DUPLICATE_OPTIONS",
                    "WEAK_DISTRACTORS",
                )
            ]
            return blockers
        except Exception as exc:
            log.debug("Distractor candidate screening skipped: %s", exc)
            return []

    def screen_mcq(
        self,
        mcq_dict: Dict,
        supporting_facts: Optional[List[Dict]] = None,
    ) -> Tuple[float, List[str]]:
        """
        Public screening helper exposing RuleBasedScreener with this generator's
        active kg_builder and cutoff_date.
        """
        from mcq_quality import RuleBasedScreener
        screener = RuleBasedScreener()
        return screener.screen(
            mcq_dict,
            supporting_facts=supporting_facts,
            kg_builder=getattr(self, "kg_builder", None),
            t_cutoff=getattr(self, "cutoff_date", "2023-04-19"),
        )


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
        if result.rejection_codes:
            log.info("  Rejection codes: %s", ", ".join(result.rejection_codes))
        if result.rejection_tally:
            counts = {
                k: v for k, v in result.rejection_tally.items()
                if not str(k).startswith("_") and v
            }
            if counts:
                log.info("  Rejection tally: %s", counts)
        if result.difficulty_mismatch_count:
            log.info(
                "  Difficulty     : %d/%d accepted MCQ(s) were relabeled to the "
                "requested level — the model's own content assessment disagreed.",
                result.difficulty_mismatch_count, len(result.mcqs),
            )
        if result.rejection_reasons:
            log.info("  Failure reasons: %s", ", ".join(result.rejection_reasons))
        log.info("=" * 60)


# ---------------------------------------------------------------------------
# KG Integration helper
# ---------------------------------------------------------------------------

def facts_from_kg(kg_builder, topic: str, as_of: Optional[str] = None,
                  enforce_source_cutoff: bool = True,
                  allow_static_source_evidence: bool = False) -> List[Dict]:
    """
    Pull accepted, MCQ-ready facts from KnowledgeGraphBuilder for a topic.

    Parameters
    ----------
    kg_builder : KnowledgeGraphBuilder instance
    topic      : topic name string matching a TOPIC node
    as_of      : str / datetime.date, optional
        Cutoff time t*. When given, only facts whose valid interval
        covers t* survive, and (if enforce_source_cutoff) facts whose
        supporting source was published after t* are dropped too —
        this is the guideline §8.2 "Temporal Retriever" contract: the
        generator must only ever see facts valid under the declared
        cutoff. When omitted (the default), behaves exactly as before
        with no temporal filtering, so existing callers are unaffected.
    enforce_source_cutoff : bool
        Passed through to KnowledgeGraphBuilder.get_facts_by_topic_as_of()
        when as_of is given. See that method's docstring.
    allow_static_source_evidence : bool
        Model B opt-in for accepted, pre-cutoff source evidence supporting
        static facts.

    Returns
    -------
    List of fact dicts suitable for MCQGenerator.generate_from_facts().
    When as_of is given, each dict also carries `temporal_status`,
    `valid_from`, and `valid_to` so downstream verification/audit code
    can tell an interval-checked fact from an "unversioned" one that
    only passed because it had no recorded interval to violate.
    """
    if as_of is not None:
        candidates = kg_builder.get_facts_by_topic_as_of(
            topic, as_of_date=as_of, enforce_source_cutoff=enforce_source_cutoff,
            allow_static_source_evidence=allow_static_source_evidence,
        )
        fact_ids        = [c["fact_id"] for c in candidates]
        temporal_lookup = {c["fact_id"]: c for c in candidates}
    else:
        fact_ids        = kg_builder.get_facts_by_topic(topic)
        temporal_lookup = {}

    facts = []
    for fid in fact_ids:
        data = kg_builder.get_fact_data(fid)
        if not data:
            continue
        verdict = data.get("quality_verdict", "REJECT")
        if verdict in ("REJECT", "REJECT_AFTER_REFINE", "NOT_FOUND"):
            continue
        readiness = data.get("mcq_readiness", 0.0)
        if readiness < 0.5:
            continue
        fact_dict = {
            "fact_id":         fid,
            "text":            data.get("text", ""),
            "topic":           topic,
            "mcq_suitable_for": data.get("mcq_suitable_for", ["factual"]),
            "mcq_readiness":   readiness,
            "composite_score": data.get("composite_score", 0.0),
            "source_reliability": data.get("source_reliability", 0.5),
        }
        if as_of is not None:
            tinfo = temporal_lookup.get(fid, {})
            fact_dict["temporal_status"] = tinfo.get("temporal_status", "unversioned")
            fact_dict["valid_from"]      = tinfo.get("valid_from")
            fact_dict["valid_to"]        = tinfo.get("valid_to")
        facts.append(fact_dict)
    return facts


# ---------------------------------------------------------------------------
# Task 6.1: Past-State & Outdated Entity Distractor Generator
# ---------------------------------------------------------------------------

def generate_temporal_distractors(
    kg_builder: Any,
    fact_id: Optional[str] = None,
    topic: Optional[str] = None,
    t_cutoff: str = "2023-04-19",
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """
    Extract outdated past-state facts/entities valid at t < t_cutoff
    (where valid_to <= t_cutoff) to construct plausibly deceptive
    temporally contrastive distractors (Sub-task 6.1).

    Parameters
    ----------
    kg_builder : KnowledgeGraphBuilder or dict/list
        Knowledge Graph instance or fact index.
    fact_id : str, optional
        Target fact ID for which contrastive distractors are requested.
    topic : str, optional
        Topic string to filter distractors.
    t_cutoff : str
        Temporal cutoff date string (default "2023-04-19").
    limit : int
        Maximum number of outdated distractors to return.

    Returns
    -------
    List[Dict[str, Any]]
        List of distractor dictionaries.
    """
    if kg_builder is None:
        return []

    cutoff_key = t_cutoff.replace("-", "")[:8]
    outdated_distractors: List[Dict[str, Any]] = []
    seen_texts: set = set()

    # Target fact details if provided
    target_obj = None
    target_subj = None
    target_rel = None
    target_topic = topic

    if fact_id and hasattr(kg_builder, "get_bitemporal_tuple"):
        target_tuple = kg_builder.get_bitemporal_tuple(fact_id)
        if target_tuple:
            target_obj = target_tuple.get("object")
            target_subj = target_tuple.get("subject")
            target_rel = target_tuple.get("relation")
            if not target_topic:
                target_topic = target_tuple.get("topic")

    # 1. First priority: Version chain history for target subject/relation.
    #    get_fact_history() returns raw node dicts which don't carry an "object"
    #    field. We resolve the actual object entity name via get_bitemporal_tuple()
    #    so that outdated_entity contains the entity name ("Justice B"), not
    #    the full fact text.
    if target_subj and target_rel and hasattr(kg_builder, "get_fact_history"):
        subj_name = target_subj if isinstance(target_subj, str) else target_subj[0]
        history = kg_builder.get_fact_history(subj_name, target_rel)
        for h_fact in history:
            h_fid = h_fact.get("fact_id")
            vt = h_fact.get("valid_to")
            if not vt:
                continue
            vt_key = str(vt).replace("-", "")[:8]
            if vt_key <= cutoff_key:
                # Prefer resolved entity name from bitemporal tuple
                obj_text = None
                if h_fid and hasattr(kg_builder, "get_bitemporal_tuple"):
                    bt = kg_builder.get_bitemporal_tuple(h_fid)
                    if bt:
                        raw_obj = bt.get("object")
                        if isinstance(raw_obj, list):
                            obj_text = raw_obj[0] if raw_obj else None
                        else:
                            obj_text = raw_obj
                # Fallback to node text if tuple resolution failed
                if not obj_text:
                    obj_text = h_fact.get("object") or h_fact.get("text")

                if obj_text and obj_text != target_obj and str(obj_text) not in seen_texts:
                    seen_texts.add(str(obj_text))
                    outdated_distractors.append({
                        "distractor_text": str(obj_text),
                        "outdated_entity": str(obj_text),
                        "valid_from": h_fact.get("valid_from"),
                        "valid_to": vt,
                        "source_fact_id": h_fid,
                        "relation": target_rel,
                        "temporal_class": "outdated_past_state",
                    })
                    if len(outdated_distractors) >= limit:
                        return outdated_distractors

    # 2. Second priority: All bitemporal tuples or raw graph scan with valid_to <= t_cutoff.
    #    get_all_bitemporal_tuples() returns dicts that already include "object" (entity name)
    #    from OBJECT_IS edge resolution inside get_bitemporal_tuple(). For a direct graph scan
    #    we similarly resolve via get_bitemporal_tuple() to get the entity name, not raw text.
    all_tuples = []
    if hasattr(kg_builder, "get_all_bitemporal_tuples"):
        all_tuples = kg_builder.get_all_bitemporal_tuples()
    elif hasattr(kg_builder, "graph"):
        for node_id, data in kg_builder.graph.nodes(data=True):
            if data.get("type") == "FACT":
                vt = data.get("valid_to")
                if vt:
                    # Resolve entity name via bitemporal tuple
                    bt = kg_builder.get_bitemporal_tuple(node_id) if hasattr(kg_builder, "get_bitemporal_tuple") else {}
                    all_tuples.append({
                        "fact_id": node_id,
                        "text": data.get("text", ""),
                        "topic": data.get("topic"),
                        "relation": data.get("relation"),
                        "valid_from": data.get("valid_from"),
                        "valid_to": vt,
                        "object": (bt or {}).get("object"),
                        "subject": (bt or {}).get("subject"),
                    })
    elif isinstance(kg_builder, list):
        all_tuples = kg_builder

    for item in all_tuples:
        vt = item.get("valid_to")
        if not vt:
            continue
        vt_key = str(vt).replace("-", "")[:8]
        if vt_key <= cutoff_key:
            # Skip if topic doesn't match
            if target_topic and item.get("topic") and item.get("topic").lower() != target_topic.lower():
                continue

            # Prefer resolved entity name; fall back to text
            raw_obj = item.get("object")
            if isinstance(raw_obj, list):
                obj_val = raw_obj[0] if raw_obj else None
            else:
                obj_val = raw_obj
            if not obj_val:
                obj_val = item.get("text") or item.get("fact_text")

            if not obj_val or obj_val == target_obj or str(obj_val) in seen_texts:
                continue

            # Skip facts where the fact_id is the anchor fact itself
            if item.get("fact_id") == fact_id:
                continue

            seen_texts.add(str(obj_val))
            outdated_distractors.append({
                "distractor_text": str(obj_val),
                "outdated_entity": str(obj_val),
                "valid_from": item.get("valid_from"),
                "valid_to": vt,
                "source_fact_id": item.get("fact_id"),
                "relation": item.get("relation"),
                "temporal_class": "outdated_past_state",
            })
            if len(outdated_distractors) >= limit:
                break

    return outdated_distractors


# ---------------------------------------------------------------------------
# Task 6.2: Near-Synonym & Semantic Confuser Distractor Generator
# ---------------------------------------------------------------------------

DOMAIN_CONFUSER_TAXONOMY: Dict[str, Dict[str, Any]] = {
    "RIVERS": {
        "subtype": "RIVER",
        "entities": [
            "পদ্মা", "মেঘনা", "যমুনা", "ব্রহ্মপুত্র", "কর্ণফুলী", "সুরমা",
            "বুড়িগঙ্গা", "তিস্তা", "কুশিয়ারা", "মধুমতি", "গোমতী", "ধলেশ্বরী",
        ],
        "aliases": ["river", "নদী", "ভৌগোলিক", "geography"],
    },
    "HISTORICAL_DATES": {
        "subtype": "DATE",
        "entities": [
            "৭ মার্চ", "২৫ মার্চ", "২৬ মার্চ", "১০ এপ্রিল", "১৭ এপ্রিল",
            "২১ নভেম্বর", "১৪ ডিসেম্বর", "১৬ ডিসেম্বর", "২১ ফেব্রুয়ারি", "১৫ আগস্ট",
        ],
        "aliases": ["date", "দিবস", "তারিখ", "সাল", "history", "মুক্তিযুদ্ধ"],
    },
    "CONSTITUTIONAL_ROLES": {
        "subtype": "POSITION",
        "entities": [
            "প্রধান বিচারপতি", "রাষ্ট্রপতি", "প্রধানমন্ত্রী", "স্পিকার",
            "প্রধান নির্বাচন কমিশনার", "অ্যাটর্নি জেনারেল", "কম্পট্রোলার অ্যান্ড অডিটর জেনারেল",
        ],
        "aliases": ["position", "পদবী", "বিচারপতি", "কমিশনার", "constitution", "governance"],
    },
    "DIVISIONS_AND_CITIES": {
        "subtype": "CITY",
        "entities": [
            "ঢাকা", "চট্টগ্রাম", "রাজশাহী", "খুলনা", "বরিশাল",
            "সিলেট", "রংপুর", "ময়মনসিংহ", "কুমিল্লা", "বগুড়া",
        ],
        "aliases": ["division", "বিভাগ", "জেলা", "শহর", "place", "city", "location"],
    },
    "NATIONAL_SYMBOLS": {
        "subtype": "SYMBOL",
        "entities": [
            "জাতীয় পতাকা", "জাতীয় সংগীত", "জাতীয় প্রতীক", "জাতীয় স্মৃতিসৌধ",
            "শহীদ মিনার", "জাতীয় ফুল শাপলা", "জাতীয় ফল কাঁঠাল", "জাতীয় পশু রয়েল বেঙ্গল টাইগার",
        ],
        "aliases": ["symbol", "প্রতীক", "চিহ্ন", "culture"],
    },
    "DYNASTIES_EMPIRES": {
        "subtype": "DYNASTY",
        "entities": [
            "মৌর্য সাম্রাজ্য", "গুপ্ত সাম্রাজ্য", "পাল বংশ", "সেন বংশ",
            "মুঘল সাম্রাজ্য", "সুলতানি আমল", "ইলিয়াস শাহী বংশ",
        ],
        "aliases": ["dynasty", "সাম্রাজ্য", "বংশ", "আমল", "history"],
    },
    "SECTOR_COMMANDERS": {
        "subtype": "PERSON",
        "entities": [
            "মেজর জিয়াউর রহমান", "মেজর কে এম শফিউল্লাহ", "মেজর খালেদ মোশাররফ",
            "মেজর সি আর দত্ত", "ক্যাপ্টেন এ টি এম হায়দার", "উইং কমান্ডার এম কে বাশার",
        ],
        "aliases": ["commander", "কমান্ডার", "সেক্টর", "liberation war"],
    },
    "HISTORIC_MOVEMENTS": {
        "subtype": "EVENT",
        "entities": [
            "ভাষা আন্দোলন", "যুক্তফ্রন্ট নির্বাচন", "ছয় দফা আন্দোলন",
            "উনসত্তরের গণঅভ্যুত্থান", "একাত্তরের মুক্তিযুদ্ধ", "অসহযোগ আন্দোলন",
        ],
        "aliases": ["movement", "আন্দোলন", "অভ্যুত্থান", "যুদ্ধ", "history"],
    },
}


def generate_semantic_confusers(
    kg_builder: Any,
    fact_id: Optional[str] = None,
    entity_name: Optional[str] = None,
    entity_subtype: Optional[str] = None,
    topic: Optional[str] = None,
    t_cutoff: str = "2023-04-19",
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """
    Generate near-synonym and semantic confuser distractors using domain-specific
    taxonomy and knowledge graph entity embedding/neighborhood overlap (Sub-task 6.2).

    Parameters
    ----------
    kg_builder : KnowledgeGraphBuilder or Any
        Knowledge Graph instance or graph container.
    fact_id : str, optional
        Anchor fact ID from which target object/subject and topic are resolved.
    entity_name : str, optional
        Target entity name (correct answer or focal entity). If omitted and
        fact_id is provided, resolved from the fact's object/subject.
    entity_subtype : str, optional
        Entity subtype (e.g. 'PERSON', 'RIVER', 'CITY', 'DATE').
    topic : str, optional
        Topic string to filter or contextualize confusers.
    t_cutoff : str
        Temporal cutoff date string (default '2023-04-19').
    limit : int
        Maximum number of confusers to return.

    Returns
    -------
    List[Dict[str, Any]]
        List of confuser distractor dictionaries.
    """
    if kg_builder is None:
        return []

    target_entity = entity_name
    target_subtype = entity_subtype
    target_topic = topic

    # Resolve from fact_id if available
    if fact_id and hasattr(kg_builder, "get_bitemporal_tuple"):
        target_tuple = kg_builder.get_bitemporal_tuple(fact_id)
        if target_tuple:
            if not target_entity:
                raw_obj = target_tuple.get("object")
                if isinstance(raw_obj, list):
                    target_entity = raw_obj[0] if raw_obj else None
                else:
                    target_entity = raw_obj
                if not target_entity:
                    raw_subj = target_tuple.get("subject")
                    if isinstance(raw_subj, list):
                        target_entity = raw_subj[0] if raw_subj else None
                    else:
                        target_entity = raw_subj
            if not target_topic:
                target_topic = target_tuple.get("topic")

    # If entity subtype still unknown, look up in graph
    if target_entity and not target_subtype and hasattr(kg_builder, "graph") and hasattr(kg_builder, "_normalize_name"):
        norm_id = f"ENTITY_{kg_builder._normalize_name(str(target_entity))}"
        if kg_builder.graph.has_node(norm_id):
            target_subtype = kg_builder.graph.nodes[norm_id].get("subtype")

    def _normalize(s: Any) -> str:
        import unicodedata
        t = unicodedata.normalize("NFC", str(s or "")).strip().lower()
        return re.sub(r"\s+", " ", t)

    target_norm = _normalize(target_entity) if target_entity else ""

    # Known synonym sets to exclude exact synonyms of target (preventing multi-correct answers)
    synonym_blacklist: Set[str] = set()
    if target_norm:
        try:
            from mcq_quality import SYNONYM_CLASSES
            for s_cls in SYNONYM_CLASSES:
                norm_cls = {_normalize(w) for w in s_cls}
                if target_norm in norm_cls:
                    synonym_blacklist.update(norm_cls)
        except Exception:
            pass

    confusers: List[Dict[str, Any]] = []
    seen_names: Set[str] = ({target_norm} | synonym_blacklist) if target_norm else set()

    # -----------------------------------------------------------------------
    # Channel 1: Knowledge Graph Entity Neighborhood Overlap
    # -----------------------------------------------------------------------
    if target_entity and hasattr(kg_builder, "get_entity_neighborhood"):
        nb = kg_builder.get_entity_neighborhood(str(target_entity), radius=2, as_of_date=t_cutoff)
        if nb and "connected_entities" in nb:
            # Sort connected entities by shortest path distance (closest first)
            sorted_conn = sorted(nb["connected_entities"], key=lambda x: x.get("distance", 99))
            for ce in sorted_conn:
                c_name = ce.get("name")
                c_sub = ce.get("subtype")
                c_norm = _normalize(c_name)
                if not c_name or c_norm in seen_names:
                    continue
                # If target_subtype is specified, prefer matching subtype
                if target_subtype and c_sub and c_sub != target_subtype:
                    continue
                seen_names.add(c_norm)
                confusers.append({
                    "distractor_text": c_name,
                    "confuser_entity": c_name,
                    "subtype": c_sub or target_subtype,
                    "generation_method": "kg_neighborhood",
                    "distance": ce.get("distance", 2),
                    "topic": target_topic,
                    "temporal_class": "semantic_confuser",
                })
                if len(confusers) >= limit:
                    return confusers

    # Also scan KG graph for same-subtype entities in the same topic or snapshot
    if len(confusers) < limit and hasattr(kg_builder, "graph"):
        for nid, ndata in kg_builder.graph.nodes(data=True):
            if ndata.get("type") == "ENTITY":
                e_name = ndata.get("name")
                e_sub = ndata.get("subtype")
                e_norm = _normalize(e_name)
                if not e_name or e_norm in seen_names:
                    continue
                if target_subtype and e_sub != target_subtype:
                    continue
                seen_names.add(e_norm)
                confusers.append({
                    "distractor_text": e_name,
                    "confuser_entity": e_name,
                    "subtype": e_sub,
                    "generation_method": "kg_subtype_overlap",
                    "distance": 3,
                    "topic": target_topic,
                    "temporal_class": "semantic_confuser",
                })
                if len(confusers) >= limit:
                    return confusers

    # -----------------------------------------------------------------------
    # Channel 2: Domain-Specific Taxonomy Clusters
    # -----------------------------------------------------------------------
    matched_clusters = []
    for cluster_name, cluster_data in DOMAIN_CONFUSER_TAXONOMY.items():
        c_sub = cluster_data.get("subtype")
        c_entities = cluster_data.get("entities", [])
        c_aliases = cluster_data.get("aliases", [])
        # Match by:
        # 1. Target entity in cluster entities
        # 2. Subtype matches
        # 3. Topic matches cluster aliases
        is_match = False
        if target_norm and any(_normalize(ent) == target_norm for ent in c_entities):
            is_match = True
        elif target_subtype and c_sub == target_subtype:
            is_match = True
        elif target_topic and any(alias.lower() in str(target_topic).lower() for alias in c_aliases):
            is_match = True

        if is_match:
            matched_clusters.append((cluster_name, cluster_data))

    for c_name, c_data in matched_clusters:
        for candidate in c_data.get("entities", []):
            cand_norm = _normalize(candidate)
            if cand_norm in seen_names:
                continue
            seen_names.add(cand_norm)
            confusers.append({
                "distractor_text": candidate,
                "confuser_entity": candidate,
                "subtype": c_data.get("subtype"),
                "generation_method": "domain_taxonomy",
                "cluster": c_name,
                "distance": 1,
                "topic": target_topic or c_name,
                "temporal_class": "semantic_confuser",
            })
            if len(confusers) >= limit:
                return confusers

    return confusers



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
        seen_questions_path  = "seen_questions.json",
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