"""
pipeline_merged.py
==================
BCSBatighor GK — Full Pipeline  (pipeline-3  +  run_metrics_pipeline merged)

What changed vs pipeline-3.py
------------------------------
* LiveTelemetry  from run_metrics_pipeline now tracks every MCQ generation
  result automatically — no more manual rd.total_generations += len(...) etc.
* mcq_to_eval_dict  builds richer eval dicts (grounding/distractor/clarity scores).
* Stage 14 uses  telemetry.to_runtime_data()  instead of hand-wiring rd fields,
  so all 27 metrics receive proper inputs.
* JAS proxy  injected after full_report() — identical to run_metrics_pipeline.
* _print_diagnosis  health-check table printed after every run.
* No CorpusLoader, no bcs_questions_corpus.json, no pre-2023 filter.
  Works identically for BCS facts  AND  Somajgyaan facts — just change FACTS_JSON.

To switch dataset:
    FACTS_JSON  = "bcg_gk_facts.json"       # BCS
    FACTS_JSON  = "somajgyaan_facts.json"    # Somajgyaan
    MEMORY_DB   = "memory_somajgyaan.db"
    SNAPSHOT_FOLDER = "snapshots_somajgyaan"
    OUTPUT_METRICS  = "metrics_somajgyaan.json"

Team:
  Mohaiminul | Souvik | Saif | Galib | Simki
"""

# ---------------------------------------------------------------------------
# Env — load BEFORE anything else
# ---------------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv()

import json
import os
import random
import sys
import time
import datetime
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ── Step 1 & 2 ──────────────────────────────────────────────────────────────
from input_normalizer import InputNormalizer, NormalizedInput
from intent_builder   import IntentBuilder, Blueprint

# ── Downstream modules ───────────────────────────────────────────────────────
from kg_builder      import KnowledgeGraphBuilder
from episodic_store  import EpisodicMemory
from fact_quality    import FactQualityGate
from mcq_generator   import MCQGenerator, facts_from_kg
from mcq_quality     import MCQQualityEvaluator
from bcs_metrics     import BCSMetricsEvaluator, RuntimeData
from hf_client        import is_rate_limit_error
from rejection_taxonomy import RejectionCode, RejectionTally, DESCRIPTIONS as REJECTION_DESCRIPTIONS
                                          # §8.4/§10.2 — every stage below
                                          # (fact_quality, web_scraper,
                                          # mcq_generator, mcq_quality)
                                          # already tallies E-* codes
                                          # internally, but nothing ever
                                          # collected those tallies into the
                                          # final report. This import is
                                          # what STAGE 6b (added below) uses
                                          # to do that collection.
from web_scraper      import WebScraper, DDGS_AVAILABLE  # Step 5 — was never wired into the
                                          # pipeline before. Uses requests +
                                          # BeautifulSoup + ddgs search only
                                          # (see web_scraper.py imports) —
                                          # it never calls hf_client.call_llm,
                                          # so it does NOT touch your HF API
                                          # quota/budget at all.


# ===========================================================================
# Config — only change these to switch datasets
# ===========================================================================
# NOTE: your actual uploaded facts file is named
# "bcg_gk_facts_laneA_annotated.json", NOT "bcg_gk_facts.json" — the old
# name in this config never existed on disk, so Stage 1 would immediately
# hit the "[ERROR] ... not found" branch and exit. Fixed to match reality.
# Model B derived corpus: accepted static pre-cutoff evidence plus dynamic
# facts with verified validity starts. Rebuild it with
# accept_model_b_proposals.py after proposal changes.
FACTS_JSON          = "bcs_gk_facts_model_b.json"   # or somajgyaan_facts.json
BCS_CORPUS_JSON     = "bcs_questions_corpus.json"  # real historical BCS
                                                    # questions — feeds QSS
                                                    # (question similarity)
                                                    # and DDMS (difficulty
                                                    # distribution matching)
                                                    # in bcs_metrics.py.
                                                    # Set to None/"" to skip
                                                    # loading it (both
                                                    # metrics will then
                                                    # report None, same as
                                                    # today's behavior).
MEMORY_DB           = "memory.db"
SNAPSHOT_FOLDER     = "snapshots"
OUTPUT_METRICS      = "bcs_metrics_report.json"
MCQ_DIFFICULTY   = "medium"
EXTRACTION_DATE  = datetime.date.today().isoformat()

# Temporal cutoff t* for MCQ generation (guideline §3.2/§8.2). When set,
# facts_from_kg() only returns facts valid at this date, and drops facts
# whose supporting source was published after it. Set to None to disable
# cutoff filtering entirely (equivalent to the old, non-temporal behavior)
# — this matters today because bcg_gk_facts.json does not yet carry
# valid_from/valid_to/source_published_at, so most facts will come back
# as "unversioned" rather than "valid_at_cutoff" until that data exists.
# Example primary cutoff for the 45th BCS (19 May 2023) experiment:
#   CUTOFF_DATE = "2023-04-19"   # t_-30, guideline's recommended primary cutoff
#
# FIX: this was left as None, which silently ran the ENTIRE pipeline
# (including live web scraping of today's internet) with the temporal
# data-firewall OFF — web_scraper.py even logs a warning about this on
# every such run ("Do not use this mode for the frozen 2023 experiment").
# With no cutoff, freshly-scraped facts get mixed into the corpus with
# zero leakage screening, and TVR/PCLR always report null. Enabled here
# with the guideline's own recommended primary cutoff; change/clear this
# only for deliberate exploratory runs, not the default.
CUTOFF_DATE = "2023-04-19"
STRICT_TEMPORAL_GUARD = True
ALLOW_STATIC_SOURCE_EVIDENCE = True

# ── Web Scraper (Step 5) toggle ─────────────────────────────────────────────
# Previously imported nowhere in this pipeline — Stage 0c below wires it in.
# It does NOT call hf_client / the HF Inference API at all (it only does
# DuckDuckGo/Google search + requests/BeautifulSoup HTML scraping), so
# turning this on will NOT consume any of your Hugging Face quota. It will,
# however, make real outbound HTTP requests to the web and can be slow /
# rate-limited by DuckDuckGo itself — keep it off (False) for fast, free,
# fully-offline runs against your existing bcg_gk_facts_laneA_annotated.json.
ENABLE_WEB_SCRAPING       = True
WEB_SCRAPE_MAX_RESULTS    = 3     # top URLs fetched per generated query
WEB_SCRAPE_MIN_SOURCE_TIER = None # e.g. 3 to require tier-1..3 sources only
WEB_SCRAPE_ALLOW_UNDATED  = False # False = drop sources with no publish date
                                   # when a CUTOFF_DATE is active (§3.3)

MAX_FACTS_PER_TOPIC = 3
MIN_FACTS_PER_TOPIC = 1    # was 3 — now even topics with 1 fact can generate MCQs
MCQ_BUDGET          = 20

# Random-pick generation (replaces the old alphabetical-topic-order walk that
# starved late-sorted topics of budget). Set RANDOM_SEED to an int for a
# reproducible run (e.g. during testing); leave None for true randomness.
RANDOM_SEED  = None
# Hook for the future app layer: pass a set of topic names to restrict
# generation to (e.g. {"History", "Geography"}). None = all topics eligible,
# exactly like today. Wiring a UI to this is a follow-up, not part of this fix.
TOPIC_FILTER = None

HF_API_KEY = os.getenv("HF_API_KEY", "") or os.getenv("HF_API_TOKEN", "")


def _load_all_hf_keys() -> list:
    """
    All configured HF keys, in order: HF_API_KEY/HF_API_TOKEN first, then
    HF_API_KEY_1, HF_API_KEY_2, ... Shared by HFKeyManager (MCQ generation
    key rotation) and normalise_and_build_blueprints (blueprint-extraction
    key rotation — see intent_builder.py's LLMExtractor FIX) so both
    stages rotate across the SAME pool of keys instead of the blueprint
    stage being stuck on a single one.
    """
    candidates = []
    for name in (["HF_API_KEY", "HF_API_TOKEN"]
                 + [f"HF_API_KEY_{i}" for i in range(1, 10)]):
        val = os.getenv(name, "").strip()
        if val and val not in candidates:
            candidates.append(val)
    return candidates


# Cost estimate (adjust to match whatever model HF_MODEL points at —
# see hf_client.py. Hugging Face Inference Providers bill per-provider,
# so this is a rough estimate, not an exact invoice figure.)
TOKENS_PER_AGENT_CALL  = 1_200
COST_PER_1K_TOKENS_USD = 0.001

DEFAULT_QUESTIONS = [
    "বাংলাদেশের রাজধানী কী?",
    "পদ্মা সেতুর দৈর্ঘ্য কত কিলোমিটার?",
    "বাংলাদেশের প্রথম রাষ্ট্রপতি কে?",
    "মুক্তিযুদ্ধ কত সালে শুরু হয়?",
    "সুন্দরবন কোথায় অবস্থিত?",
    "বাংলাদেশের মোট জেলা কয়টি?",
    "শেখ মুজিবুর রহমান কবে জন্মগ্রহণ করেন?",
    "Bangladesh achieved independence in which year?",
    "জাতিসংঘের সদর দফতর কোথায়?",
    "বাংলাদেশের জিডিপি প্রবৃদ্ধির হার কত?",
]

TOPIC_NAME_MAP: dict = {
    "History":               "History",
    "Geography":             "Geography",
    "Science":               "Science & Technology",
    "Economy":               "Economy",
    "Culture":               "Culture",
    "International":         "International Relations",
    "Government":            "Government",
    "Infrastructure":        "Infrastructure",
    "General":               "General",
    "Liberation War":        "Liberation War",
    "Constitution":          "Constitution",
    "Bangladesh Affairs":    "Bangladesh Affairs",
    "Constitution & Law":    "Constitution & Law",
    "International Relations": "International Relations",
    "Science & Technology":  "Science & Technology",
    "Appointments":          "Appointments",
    "Education":             "Education",
    "Sports":                "Sports",
    "Demography":            "Demography",
    "Flora & Fauna":         "Flora & Fauna",
    "Language":              "Language",
    "Defense":               "Defense",
    "Current Affairs":       "Current Affairs",
    "Climate":               "Climate",
}

BCS_BENCHMARK_TOPICS = list(set(TOPIC_NAME_MAP.values()))

# Difficulty metric thresholds (from run_metrics_pipeline)
METRIC_THRESHOLDS = {
    "FQS": 0.60, "GCS": 0.80, "RRR": 0.80, "TCS": 0.60, "FUS": 0.15,
    "MUS": 0.20, "LES": 0.50, "RIR": 0.50, "MSV": 0.90, "GA":  0.80,
    "DQS": 0.65, "CS":  0.75, "JAS": 0.70, "SCE": 0.40, "AES": 0.40,
    "FEU": 0.40, "TAS": 0.60, "DDMS":0.20, "CHP": 0.50, "EESR":0.80,
    "TVR": 0.80, "PCLR": 0.05,
}
LOWER_IS_BETTER = {"DCE", "DDMS", "CAM", "LSO", "PCLR"}


# ===========================================================================
# LiveTelemetry  (from run_metrics_pipeline — accurate metric computation)
# ===========================================================================

@dataclass
class LiveTelemetry:
    """
    Accumulates all RuntimeData fields as MCQ results come in.
    Call  .ingest_result(result)  after every MCQGenerator.generate_from_facts().
    """
    total_pipeline_runs:          int = 0
    successful_pipeline_runs:     int = 0
    total_agent_interactions:     int = 0
    episodes_assisted:            int = 0
    total_generations:            int = 0
    regenerated_mcqs:             int = 0
    improved_after_regen:         int = 0
    initial_failures:             int = 0
    corrected_failures:           int = 0
    regen_with_feedback:          int = 0
    regen_improved_with_feedback: int = 0
    low_quality_episodes_identified: int = 0
    low_quality_episodes_removed:    int = 0
    accepted_mcq_ids:             List[str] = field(default_factory=list)
    accepted_mcq_fact_ids:        List[str] = field(default_factory=list)
    # FIX (JAS): these used to be a tautological proxy — every accepted
    # MCQ got `_auto_judge_pass.append(True)` unconditionally (it's
    # already-accepted by construction, so this list was always all-True)
    # compared against `_secondary_judge_pass`, a near-always-true
    # threshold check on that SAME MCQ's own quality_score. Comparing a
    # constant to a near-constant produced JAS ≈ 1.0 in every run
    # regardless of actual judge reliability — even runs with dozens of
    # logged "Judge self-check mismatch" warnings still reported perfect
    # agreement. Replaced with the real per-episode self-check counters
    # from mcq_generator.py's JudgeAgent (LLM-reported verdict vs. the
    # deterministic recompute from its own dimension scores).
    _judge_self_check_total:      int = 0
    _judge_self_check_agreed:     int = 0

    def ingest_result(self, result) -> None:
        self.total_pipeline_runs += 1
        if result.accepted and result.mcqs:
            self.successful_pipeline_runs += 1

        self.total_agent_interactions += result.crj_rounds * 3

        round0   = [m for m in result.mcqs if m.regeneration_round == 0]
        after_r0 = [m for m in result.mcqs if m.regeneration_round > 0]

        est_r0_candidates   = max(len(round0) + (1 if result.crj_rounds > 1 else 0), 1)
        extra_rounds        = max(result.crj_rounds - 1, 0)
        est_regen_candidates = len(after_r0) + extra_rounds

        self.total_generations    += est_r0_candidates + est_regen_candidates
        self.regenerated_mcqs     += est_regen_candidates
        self.improved_after_regen += min(len(after_r0), est_regen_candidates)

        r0_failures = max(est_r0_candidates - len(round0), 0)
        self.initial_failures   += r0_failures
        self.corrected_failures += min(len(after_r0), r0_failures)

        if result.crj_rounds > 1 and result.rejection_reasons:
            regen_rounds = result.crj_rounds - 1
            self.regen_with_feedback          += regen_rounds
            self.regen_improved_with_feedback += min(len(after_r0), regen_rounds)

        for mcq in result.mcqs:
            self.accepted_mcq_ids.append(mcq.mcq_id)
            self.accepted_mcq_fact_ids.extend(result.fact_ids)

        self._judge_self_check_total  += getattr(result, "judge_self_check_total", 0)
        self._judge_self_check_agreed += getattr(result, "judge_self_check_agreed", 0)

    def jas_proxy(self) -> Optional[float]:
        """
        Judge self-consistency proxy: fraction of judge evaluations
        (across ALL candidate MCQs the judge scored this run, not just
        the accepted ones) where the LLM's self-reported overall_score
        and passed verdict matched the deterministic recompute from its
        own dimension scores. This is a proxy for judge RELIABILITY —
        NOT the guideline's official JAS definition (auto judge vs. a
        human expert), which stays correctly null in bcs_metrics.py's
        own judge_agreement_score() until real human_judge_decisions
        exist. Returns None if the judge was never invoked.
        """
        if self._judge_self_check_total == 0:
            return None
        return round(self._judge_self_check_agreed / self._judge_self_check_total, 4)

    def estimate_cost_usd(self) -> float:
        total_tokens = self.total_agent_interactions * TOKENS_PER_AGENT_CALL
        return round(total_tokens / 1_000 * COST_PER_1K_TOKENS_USD, 6)

    def to_runtime_data(self, elapsed: float, iterations_to_target: int = 1) -> RuntimeData:
        return RuntimeData(
            total_pipeline_runs          = self.total_pipeline_runs or 1,
            successful_pipeline_runs     = self.successful_pipeline_runs,
            total_cost_usd               = self.estimate_cost_usd(),
            total_processing_seconds     = elapsed,
            total_agent_interactions     = self.total_agent_interactions,
            episodes_assisted            = self.episodes_assisted,
            total_generations            = max(self.total_generations, 1),
            regenerated_mcqs             = self.regenerated_mcqs,
            improved_after_regen         = self.improved_after_regen,
            initial_failures             = self.initial_failures,
            corrected_failures           = self.corrected_failures,
            regen_with_feedback          = self.regen_with_feedback,
            regen_improved_with_feedback = self.regen_improved_with_feedback,
            low_quality_episodes_identified = self.low_quality_episodes_identified,
            low_quality_episodes_removed    = self.low_quality_episodes_removed,
            # Left empty deliberately — no real human review happened, so
            # bcs_metrics.py's official judge_agreement_score() correctly
            # returns None ("data not provided") rather than a fabricated
            # number. jas_proxy() above (based on real judge self-check
            # data) is what overwrites metrics_report["metrics"]["JAS"]
            # afterward — see the "Inject JAS proxy" step near the end of
            # main().
            human_judge_decisions        = [],
            auto_judge_decisions         = [],
            iterations_to_target         = iterations_to_target,
            difficulty_target_dist       = {"easy": 0.33, "medium": 0.34, "hard": 0.33},
            accepted_mcq_ids             = self.accepted_mcq_ids,
        )


# ===========================================================================
# mcq_to_eval_dict  (from run_metrics_pipeline — richer than pipeline-3 version)
# ===========================================================================

def mcq_to_eval_dict(mcq, difficulty: str, fact_temporal_lookup: Optional[dict] = None) -> dict:
    d = {
        "question":       mcq.question,
        "difficulty":     difficulty,
        "passed":         True,
        "overall_score":  mcq.quality_score,
        "scores": {
            "grounding_score":  getattr(mcq, "_grounding_score",  mcq.quality_score),
            "distractor_score": getattr(mcq, "_distractor_score", mcq.quality_score * 0.9),
            "clarity_score":    getattr(mcq, "_clarity_score",    mcq.quality_score * 0.95),
            "format_score":     1.0,
        },
    }
    # Only stamp temporal_status when a cutoff was actually active for this
    # run (fact_temporal_lookup is None otherwise) — see facts_from_kg()'s
    # as_of parameter and bcs_metrics.py's TVR/PCLR, which both key off
    # this field's presence to distinguish "no cutoff was used" from
    # "cutoff was used and everything happened to check out."
    if fact_temporal_lookup is not None:
        d["temporal_status"] = fact_temporal_lookup.get(mcq.fact_id, "NOT_IN_APPROVED_SET")
    return d


# ===========================================================================
# _print_diagnosis  (from run_metrics_pipeline — health check table)
# ===========================================================================

def print_diagnosis(metrics: dict):
    print("\n" + "=" * 65)
    print("  METRIC HEALTH CHECK")
    print("=" * 65)
    for key, val in metrics.items():
        if val is None:
            status = "⚠  NULL"
        elif isinstance(val, float):
            thr = METRIC_THRESHOLDS.get(key)
            if thr is None:
                status = "ℹ  (no threshold)"
            elif key in LOWER_IS_BETTER:
                status = "✓  OK" if val <= thr else f"✗  HIGH (>{thr})"
            else:
                status = "✓  OK" if val >= thr else f"✗  LOW  (<{thr})"
        else:
            status = "ℹ "
        val_str = f"{val:.4f}" if isinstance(val, float) else str(val)
        print(f"  {key:<6}  {val_str:<10}  {status}")
    print("=" * 65 + "\n")


# ===========================================================================
# Hugging Face Key Manager  (was CraftXKeyManager — same rotation logic,
# just pointed at HF_API_KEY* env vars and hf_client.py's rate-limit list)
# ===========================================================================

class HFKeyManager:

    def __init__(self):
        self.keys = self._load_keys()
        self._idx = 0
        self._exhausted: set = set()
        if not self.keys:
            raise RuntimeError(
                "[HFKeyManager] No valid HF_API_KEY found in .env.\n"
                "  Add HF_API_KEY=... (or HF_API_TOKEN=...) or "
                "HF_API_KEY_1=... etc. for multiple tokens.\n"
                "  Create a token at https://huggingface.co/settings/tokens"
            )
        print(f"[HFKeyManager] {len(self.keys)} key(s) loaded.")

    def _load_keys(self) -> list:
        return _load_all_hf_keys()

    @property
    def current_key(self) -> str:
        return self.keys[self._idx]

    def is_rate_limit_error(self, exc: Exception) -> bool:
        return is_rate_limit_error(exc)

    def rotate(self) -> bool:
        self._exhausted.add(self._idx)
        available = [i for i in range(len(self.keys)) if i not in self._exhausted]
        if not available:
            print("[HFKeyManager] ⚠  All keys exhausted.")
            return False
        self._idx = available[0]
        return True

    def build_generator(self, **kwargs) -> MCQGenerator:
        return MCQGenerator(hf_api_key=self.current_key, **kwargs)

    def build_evaluator(self) -> MCQQualityEvaluator:
        return MCQQualityEvaluator(hf_api_key=self.current_key)


# ===========================================================================
# Rate-limit-aware generation helpers  (unchanged from pipeline-3)
# ===========================================================================

def generate_with_fallback(key_manager, ready_facts, difficulty, topic,
                           max_key_rotations=5):
    for _ in range(max_key_rotations + 1):
        generator = key_manager.build_generator(seen_questions_path="seen_questions.json")
        try:
            result = generator.generate_from_facts(
                ready_facts, difficulty=difficulty, topic=topic,
            )
            generator.save_seen_questions()
            return result
        except Exception as exc:
            if key_manager.is_rate_limit_error(exc):
                print(f"\n  [⚠] Rate limit: {exc}")
                if not key_manager.rotate():
                    return None
                time.sleep(2)
            else:
                raise
    return None


def evaluate_batch_with_fallback(key_manager, mcq_dicts, supporting_facts,
                                 topic, difficulty, max_key_rotations=5,
                                 kg_builder=None, t_cutoff="2023-04-19"):
    for _ in range(max_key_rotations + 1):
        evaluator = key_manager.build_evaluator()
        try:
            return evaluator, evaluator.evaluate_batch(
                mcqs=mcq_dicts,
                supporting_facts=supporting_facts,
                topic=topic,
                difficulty=difficulty,
                kg_builder=kg_builder,
                t_cutoff=t_cutoff,
            )
        except Exception as exc:
            if key_manager.is_rate_limit_error(exc):
                if not key_manager.rotate():
                    return None, None
                time.sleep(2)
            else:
                raise
    return None, None


# ===========================================================================
# Shared helpers
# ===========================================================================

def banner(text: str):
    width = 65
    print("\n" + "=" * width)
    print(f"  {text}")
    print("=" * width)


def load_bcs_corpus_benchmarks(path):
    """
    Load the real historical BCS question corpus (bcs_questions_corpus.json)
    and derive the two benchmark inputs bcs_metrics.py's full_report() needs
    but was never being given:

      bcs_benchmark_questions : list[str]
          question_bn text from every corpus entry — feeds QSS (lexical
          similarity between generated MCQs and real BCS questions).
      bcs_difficulty_dist     : dict[str, float]
          normalised easy/medium/hard proportions across the corpus —
          feeds DDMS (how closely generated difficulty mix matches the
          real historical exam mix).

    Returns (benchmark_questions, difficulty_dist). On any failure (file
    missing, bad JSON, empty questions list) returns ([], None) so callers
    degrade gracefully to the old behaviour — QSS/DDMS report None instead
    of crashing the pipeline over a missing/optional corpus file.
    """
    if not path or not os.path.exists(path):
        print(f"  [WARNING] BCS corpus not found at '{path}' — "
              f"QSS and DDMS will report None (no benchmark data).")
        return [], None

    try:
        with open(path, "r", encoding="utf-8") as f:
            corpus = json.load(f)
        questions = corpus.get("questions", [])
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"  [WARNING] Could not parse BCS corpus '{path}': {e} — "
              f"QSS and DDMS will report None.")
        return [], None

    if not questions:
        print(f"  [WARNING] BCS corpus '{path}' has no questions — "
              f"QSS and DDMS will report None.")
        return [], None

    benchmark_questions = [
        q["question_bn"] for q in questions if q.get("question_bn")
    ]

    diff_counts = defaultdict(int)
    for q in questions:
        d = q.get("difficulty")
        if d:
            diff_counts[d] += 1
    total = sum(diff_counts.values())
    difficulty_dist = (
        {k: round(v / total, 4) for k, v in diff_counts.items()}
        if total > 0 else None
    )

    print(f"  BCS corpus loaded ← {path}  "
          f"({len(benchmark_questions)} question(s) for QSS, "
          f"difficulty_dist={difficulty_dist} for DDMS)")

    return benchmark_questions, difficulty_dist


def select_best_facts(facts: list, n: int = MAX_FACTS_PER_TOPIC) -> list:
    def _score(f):
        dims = [f.get("language_confidence", 1.0), f.get("extraction_confidence", 1.0),
                f.get("source_reliability", 1.0),  f.get("temporal_freshness", 1.0)]
        return sum(dims) / len(dims)
    return sorted(facts, key=_score, reverse=True)[:n]


def _prioritize_facts_for_blueprint(ready_facts: list, topic: str,
                                     blueprints_by_topic: dict) -> list:
    """
    FIX (TAS / blueprint alignment): reorder a topic's ready facts so
    ones actually matching a blueprint's entities for that topic come
    first, WITHOUT reintroducing the deterministic-top-N-by-score
    problem the random-sampling fix was added to avoid. Facts within
    each bucket (matched / unmatched) are still shuffled; only the
    matched bucket is given priority. If a topic has no blueprint
    entities to check against, this is a no-op (returns facts as-is).
    """
    bps = blueprints_by_topic.get(topic, [])
    entities = set()
    for bp in bps:
        entities.update(getattr(bp, "entities", None) or [])
    if not entities:
        return ready_facts

    matched, other = [], []
    for f in ready_facts:
        text = f.get("text", "")
        if any(e and e in text for e in entities):
            matched.append(f)
        else:
            other.append(f)
    random.shuffle(matched)
    random.shuffle(other)
    return matched + other


def _get_fact_topic(kg: KnowledgeGraphBuilder, fact_id: str) -> str:
    for _, tgt, edata in kg.graph.edges(fact_id, data=True):
        if edata.get("relation") == "ABOUT":
            node = kg.graph.nodes.get(tgt, {})
            if node.get("type") == "TOPIC":
                return node.get("name", "")
    return ""


def normalise_and_build_blueprints(questions, hf_api_key=None):
    normalizer = InputNormalizer()
    # FIX: pass every configured key (not just HF_API_KEY) so blueprint
    # extraction can rotate across keys the same way MCQ generation does —
    # see intent_builder.py's LLMExtractor and _load_all_hf_keys() above.
    builder    = IntentBuilder(hf_api_key=_load_all_hf_keys())
    pairs = []
    for raw in questions:
        ni = normalizer.normalize(raw)
        if not ni.normalized_text:
            print(f"  [Step 1] ⚠  Skipping empty input: {repr(raw[:40])}")
            continue
        for w in ni.warnings:
            print(f"  [Step 1] ⚠  {w}  (input: {repr(raw[:40])})")
        bp = builder.build_blueprint(ni)
        bp.topic = TOPIC_NAME_MAP.get(bp.topic, bp.topic)
        pairs.append((ni, bp))
    return pairs


# ===========================================================================
# Main pipeline
# ===========================================================================

def main():
    pipeline_start = time.time()

    print("\n+" + "-" * 63 + "+")
    print("|  BCSBatighor GK — Full Pipeline (Merged)                     |")
    print("|  Step1 → Step2 → KG → Quality → MCQ → Memory → 27 Metrics   |")
    print("|  Mohaiminul | Souvik | Saif | Galib | Simki                  |")
    print("+" + "-" * 63 + "+")
    print(f"\n  Dataset : {FACTS_JSON}")
    print(f"  Memory  : {MEMORY_DB}")
    print(f"  Output  : {OUTPUT_METRICS}\n")

    raw_questions = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_QUESTIONS

    # ── Load real BCS corpus benchmarks for QSS / DDMS (Stage 14) ─────────────
    # Previously loaded nowhere — full_report() got no bcs_benchmark_questions
    # or bcs_difficulty_dist, so both metrics silently reported None every run
    # even though this file was sitting on disk unused.
    bcs_benchmark_questions, bcs_difficulty_dist = load_bcs_corpus_benchmarks(
        BCS_CORPUS_JSON
    )

    # ── Initialise LiveTelemetry (replaces manual rd field tracking) ──────────
    telemetry  = LiveTelemetry()
    eval_dicts: list = []   # richer MCQ eval dicts for metrics

    # ======================================================================
    # STAGE 0a — Input & Normalization
    # ======================================================================
    banner("STAGE 0a — Input & Normalization (Step 1 · Mohaiminul)")

    print(f"  Processing {len(raw_questions)} raw question(s)...\n")
    ni_bp_pairs = normalise_and_build_blueprints(
        questions=raw_questions, hf_api_key=HF_API_KEY,
    )
    if not ni_bp_pairs:
        print("  [ERROR] All inputs empty. Exiting.")
        return

    print(f"\n  {'#':<4} {'Script':<8} {'Lang':<6} {'Q?':<5} {'Words':<6}  Normalized (60 chars)")
    print(f"  {'-'*4} {'-'*8} {'-'*6} {'-'*5} {'-'*6}  {'-'*40}")
    for idx, (ni, _) in enumerate(ni_bp_pairs, 1):
        q_flag = "✓" if ni.has_question_marker else "✗"
        print(f"  {idx:<4} {ni.script_type:<8} {ni.detected_language:<6} "
              f"{q_flag:<5} {ni.word_count:<6}  {ni.normalized_text[:60]}")

    # ======================================================================
    # STAGE 0b — Intent / Blueprint Building
    # ======================================================================
    banner("STAGE 0b — Intent / Blueprint Building (Step 2 · Mohaiminul)")

    blueprints_by_topic: dict = {}
    for ni, bp in ni_bp_pairs:
        blueprints_by_topic.setdefault(bp.topic, []).append(bp)

    print(f"\n  {'#':<4} {'Topic':<22} {'Intent':<18} {'Type':<20}  Method")
    print(f"  {'-'*4} {'-'*22} {'-'*18} {'-'*20}  {'-'*10}")
    for idx, (ni, bp) in enumerate(ni_bp_pairs, 1):
        print(f"  {idx:<4} {bp.topic:<22} {bp.intent:<18} "
              f"{bp.question_type:<20}  {bp.extraction_method}")

    blueprint_topics = set(blueprints_by_topic.keys())
    print(f"\n  Topics identified: {sorted(blueprint_topics)}")

    blueprints_path = "blueprints.json"
    with open(blueprints_path, "w", encoding="utf-8") as f:
        json.dump(
            [{"raw_question": ni.raw_text, "normalized_text": ni.normalized_text,
              "script_type": ni.script_type, "detected_language": ni.detected_language,
              "has_question_marker": ni.has_question_marker, "word_count": ni.word_count,
              "warnings": ni.warnings, **bp.to_dict()}
             for ni, bp in ni_bp_pairs],
            f, ensure_ascii=False, indent=2,
        )
    print(f"  Blueprints saved → {blueprints_path}")

    # ======================================================================
    # STAGE 0c — Web Scraping (Step 5) — freshly wired in, was orphaned
    # ======================================================================
    banner("STAGE 0c — Web Search & Scraping (Step 5)")

    scraped_fact_dicts: List[dict] = []
    scraper: Optional[WebScraper]  = None  # stays None when scraping is off,
                                            # so STAGE 6b below can safely
                                            # check `if scraper is not None`
                                            # instead of a NameError.

    if not ENABLE_WEB_SCRAPING:
        print("  ENABLE_WEB_SCRAPING = False — skipping (no HF quota, no network "
              "calls used). Set it to True in the config block to turn this on.")
    else:
        cutoff_date_obj = (
            datetime.date.fromisoformat(CUTOFF_DATE) if CUTOFF_DATE else None
        )
        # NOTE: google_api_key/google_cse_id are passed explicitly (not left
        # to WebScraper's os.environ fallback) so it's obvious at the call
        # site that Google is the only backend available if `ddgs` isn't
        # installed. Previously these were never passed here at all, so if
        # `from ddgs import DDGS` failed (e.g. only the old
        # `duckduckgo-search` package was installed), scraping had ZERO
        # working search backend even when GOOGLE_API_KEY/GOOGLE_CSE_ID
        # were set in the environment — see web_scraper.py's DDGS_AVAILABLE
        # comment for the root cause.
        google_api_key = os.environ.get("GOOGLE_API_KEY", "")
        google_cse_id  = os.environ.get("GOOGLE_CSE_ID", "")
        scraper = WebScraper(
            cutoff_date=cutoff_date_obj,
            allow_undated=WEB_SCRAPE_ALLOW_UNDATED,
            max_results_per_query=WEB_SCRAPE_MAX_RESULTS,
            min_source_tier=WEB_SCRAPE_MIN_SOURCE_TIER,
            google_api_key=google_api_key,
            google_cse_id=google_cse_id,
        )
        if not DDGS_AVAILABLE and not (google_api_key and google_cse_id):
            print("  ⚠ WARNING: no search backend available — `ddgs` is not "
                  "installed AND GOOGLE_API_KEY/GOOGLE_CSE_ID are not set. "
                  "Every topic below will return 0 URLs. Run "
                  "`pip install ddgs` or set the Google env vars.")

        # FIX (#1, root cause): this used to scrape only the FIRST blueprint
        # seen for each topic ("one scrape per topic, not per blueprint"),
        # on the theory that it avoided re-searching the same topic twice.
        # But different blueprints under the same topic are NOT the same
        # query — e.g. Geography had three: "capital of Bangladesh",
        # "where is Sundarbans", "total districts" — and skipping the
        # second/third meant Sundarbans and district-count never got
        # searched for AT ALL. That's the real reason Geography and
        # International Relations came back with 0 scraped facts: not a
        # downstream topic-ordering/sampling issue (both of those are
        # already blueprint-prioritized below), but that most of the
        # blueprint questions were never even searched for. Now every
        # blueprint gets its own scrape_for_blueprint() call — one call
        # per BLUEPRINT (a topic with 3 blueprints does 3 searches, not 1),
        # which is the granularity the guideline's query-generation step
        # (§5) actually intends.
        for ni, bp in ni_bp_pairs:
            print(f"\n  Scraping for topic: {bp.topic}  "
                  f"(query: \"{bp.english_query or bp.bangla_query}\")")
            try:
                result = scraper.scrape_for_blueprint(bp)
                facts_from_scrape = result.as_fact_dicts()
                scraped_fact_dicts.extend(facts_from_scrape)
                print(f"    URLs searched : {len(result.urls_searched)}")
                print(f"    Sentences     : {len(result.sentences)} "
                      f"(raw={result.total_raw})")
                print(f"    Errors        : {len(result.errors)}")
                leak = result.leakage_report()
                print(f"    Leakage report: accepted={leak['accepted']} "
                      f"rejected={leak['rejected_post_cutoff_or_undated']} "
                      f"archived_fallback={leak['archived_fallback_used']}")
            except Exception as exc:
                print(f"    [WARNING] Scrape failed for topic {bp.topic}: {exc}")

        print(f"\n  Total scraped facts collected: {len(scraped_fact_dicts)}")

    # ======================================================================
    # STAGE 1 — Load Raw Facts into KG
    # ======================================================================
    banner("STAGE 1 — Load Raw Facts into Knowledge Graph (Souvik)")

    kg = KnowledgeGraphBuilder()

    if not os.path.exists(FACTS_JSON):
        print(f"  [ERROR] {FACTS_JSON} not found.")
        return

    with open(FACTS_JSON, "r", encoding="utf-8") as f:
        raw_facts = json.load(f)

    print(f"  Loaded {len(raw_facts)} raw facts from {FACTS_JSON}")

    if scraped_fact_dicts:
        print(f"  + {len(scraped_fact_dicts)} freshly scraped fact(s) from Stage 0c")
        raw_facts = raw_facts + scraped_fact_dicts

    prioritised = [f for f in raw_facts if f.get("topic") in blueprint_topics]
    remaining   = [f for f in raw_facts if f.get("topic") not in blueprint_topics]
    if prioritised:
        print(f"\n  Blueprint-matched facts : {len(prioritised)}")
        print(f"  Other facts             : {len(remaining)}")

    fact_ids    = []
    topics_seen = {}

    for raw in (prioritised + remaining):
        raw["topic"] = TOPIC_NAME_MAP.get(raw.get("topic", "General"), raw.get("topic", "General"))
        fid = kg.insert_fact_pipeline(
            fact_text        = raw["fact_text"],
            subject_entities = raw["subject_entities"],
            object_entities  = raw["object_entities"],
            topic            = raw["topic"],
            source_url       = raw["source_url"],
            publisher        = raw.get("publisher", ""),
            quality_scores   = {
                "language_confidence":   raw.get("language_confidence",   1.0),
                "extraction_confidence": raw.get("extraction_confidence", 1.0),
                "source_reliability":    raw.get("source_reliability",    1.0),
                "temporal_freshness":    raw.get("temporal_freshness",    1.0),
            },
            relation          = raw.get("relation"),
            valid_from        = raw.get("valid_from"),
            valid_to          = raw.get("valid_to"),
            source_published_at = raw.get("source_published_at"),
            source_tier       = raw.get("source_tier"),
            status             = raw.get("status", "accepted"),
            # Model B: a timeless (static) fact is proved by dated evidence
            # rather than a validity interval. All of these must reach the KG
            # or the static route can never fire.
            temporal_class                  = raw.get("temporal_class"),
            temporal_evidence_status        = raw.get("temporal_evidence_status"),
            temporal_evidence_date          = raw.get("temporal_evidence_date"),
            temporal_evidence_source_url    = raw.get("temporal_evidence_source_url"),
            temporal_evidence_snapshot_hash = raw.get("temporal_evidence_snapshot_hash"),
        )
        for key in (
            "temporal_class", "temporal_evidence_status", "temporal_evidence_date",
            "temporal_evidence_source_url", "temporal_evidence_snapshot_hash",
            "holdout_eligible", "verification_status", "verified_at",
        ):
            if key in raw:
                kg.update_fact_attribute(fid, key, raw[key])
        fact_ids.append(fid)
        topics_seen.setdefault(raw["topic"], []).append(fid)

    print(f"\n  KG loaded: {len(fact_ids)} facts across {len(topics_seen)} topic(s):")
    for t, fids in topics_seen.items():
        marker = " ★" if t in blueprint_topics else ""
        print(f"    [{t}]{marker} — {len(fids)} fact(s)")
    kg.summary()

    if CUTOFF_DATE and STRICT_TEMPORAL_GUARD:
        blocked_topics = {}
        for topic in sorted(topics_seen.keys()):
            violating = kg.strict_temporal_guard(
                topic, CUTOFF_DATE,
                allow_static_source_evidence=ALLOW_STATIC_SOURCE_EVIDENCE,
            )
            if violating:
                blocked_topics[topic] = violating[:10]
        if blocked_topics:
            raise RuntimeError(
                "Strict temporal benchmark guard failed. The following topics still "
                "contain unversioned facts under the active cutoff and cannot be used "
                f"for generation: {blocked_topics}. "
                "All facts must carry valid temporal metadata before the benchmark run can continue."
            )

    # ======================================================================
    # STAGE 2 — Fact Quality Gate
    # ======================================================================
    banner("STAGE 2 — Fact Quality Gate (Galib)")

    gate           = FactQualityGate(kg)
    quality_report = gate.run_quality_pipeline(extraction_date=EXTRACTION_DATE)
    dedup_report   = quality_report["deduplication"]
    summary        = quality_report["summary"]

    print(f"\n  Facts surviving quality gate : {summary['facts_remaining']}")
    print(f"  Accepted                     : {summary['accepted']}")
    print(f"  Refined                      : {summary['refined']}")
    print(f"  Rejected                     : {summary['rejected']}")
    print(f"  Duplicates merged            : {summary['duplicates_merged']}")

    # ======================================================================
    # STAGE 3 — Save Quality Report
    # ======================================================================
    banner("STAGE 3 — Save Quality Report")

    report_text = gate.generate_quality_report()
    with open("quality_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)
    print("  Quality report saved → quality_report.txt")

    # ======================================================================
    # STAGE 4 — Link Question Nodes to Facts in KG
    # ======================================================================
    banner("STAGE 4 — Generate and Link Question Nodes in KG")

    surviving_fact_ids = [
        nid for nid, d in kg.graph.nodes(data=True)
        if d.get("type") == "FACT"
    ]

    question_ids       = []
    questions_by_topic = {}
    blueprint_lookup   = dict(blueprints_by_topic)   # mutable copy

    for fact_id in surviving_fact_ids:
        data = kg.get_fact_data(fact_id)
        if data is None:
            continue
        text       = data.get("text", "")
        mcq_types  = data.get("mcq_suitable_for", [])
        fact_topic = _get_fact_topic(kg, fact_id)
        if not fact_topic:
            continue

        user_questions = blueprint_lookup.get(fact_topic, [])
        if user_questions:
            q_text = user_questions.pop(0).normalized_question
        elif "who_question"     in mcq_types:
            q_text = f"Who is associated with: {text[:60]}?"
        elif "when_question"    in mcq_types:
            q_text = f"When did the event occur: {text[:60]}?"
        elif "where_question"   in mcq_types:
            q_text = f"Where is this located: {text[:60]}?"
        elif "numeric_ranking"  in mcq_types:
            q_text = f"What is the ranking or number: {text[:60]}?"
        else:
            q_text = f"What is the fact about: {text[:60]}?"

        qid = kg.add_question(text=q_text, question_type="generated",
                              topic=fact_topic, source_fact_ids=[fact_id])
        question_ids.append(qid)
        questions_by_topic.setdefault(fact_topic, []).append(qid)

    print(f"  Generated {len(question_ids)} question node(s) linked to facts")
    for t, qids in questions_by_topic.items():
        print(f"    [{t}] — {len(qids)} question(s)")
    kg.summary()

    # ======================================================================
    # STAGE 5 — MCQ Generation
    # ======================================================================
    # ======================================================================
    # STAGE 5 — MCQ Generation
    # ======================================================================
    banner("STAGE 5 — MCQ Generation (Simki) with HF Key Failover")

    mcq_results_by_topic = {}
    mcq_available        = False
    key_manager          = None

    try:
        key_manager   = HFKeyManager()
        mcq_available = True
    except RuntimeError as e:
        print(f"  [WARNING] {e}")
        print("  MCQ generation skipped — add HF_API_KEY to .env to enable.")

    if mcq_available:
        if RANDOM_SEED is not None:
            random.seed(RANDOM_SEED)

        topics_with_facts = set()
        for fid in surviving_fact_ids:
            t = _get_fact_topic(kg, fid)
            if t:
                topics_with_facts.add(t)

        if TOPIC_FILTER:
            topics_with_facts &= set(TOPIC_FILTER)

        # FIX (TAS / blueprint alignment): random order across ALL topics
        # meant the topics actually asked about in blueprints.json had no
        # better chance of getting budget than topics nobody asked about
        # — e.g. a run could (and did) spend its whole MCQ_BUDGET on
        # Culture/Infrastructure/Liberation War while Geography and
        # International Relations, which the blueprints specifically
        # asked about (capital, Sundarbans, UN HQ, etc.), got zero MCQs.
        # That's what TAS ("Topic Alignment Score") was flagging as LOW.
        # Fix: shuffle blueprint-matched topics and everything-else
        # SEPARATELY, then always walk blueprint topics first. This keeps
        # the earlier fix's randomization (no more deterministic
        # alphabetical starvation) while guaranteeing the topics someone
        # actually asked about get first claim on the budget.
        blueprint_ordered = [t for t in topics_with_facts if t in blueprint_topics]
        other_ordered      = [t for t in topics_with_facts if t not in blueprint_topics]
        random.shuffle(blueprint_ordered)
        random.shuffle(other_ordered)
        ordered_topics = blueprint_ordered + other_ordered

        for i, topic in enumerate(ordered_topics):

            # ── Budget check — stop once MCQ limit is reached ──────────────
            if len(eval_dicts) >= MCQ_BUDGET:
                print(f"\n  [Budget] Reached {MCQ_BUDGET} MCQ limit — "
                      f"stopping generation. ({len(ordered_topics) - i} topic(s) skipped)")
                break

            # Cycle difficulty across generation batches (easy / medium / hard)
            difficulty = ["easy", "medium", "hard"][i % 3]

            ready_facts = facts_from_kg(
                kg, topic, as_of=CUTOFF_DATE,
                allow_static_source_evidence=ALLOW_STATIC_SOURCE_EVIDENCE,
            )
            if not ready_facts:
                print(f"  [{topic}] — no MCQ-ready facts"
                      f"{f' valid as of {CUTOFF_DATE}' if CUTOFF_DATE else ''}, skipping.")
                continue
            if CUTOFF_DATE:
                unversioned = sum(1 for f in ready_facts if f.get("temporal_status") == "unversioned")
                if unversioned:
                    print(f"  [{topic}] — ⚠ {unversioned}/{len(ready_facts)} fact(s) have no "
                          f"valid_from/valid_to and were NOT interval-checked against {CUTOFF_DATE} "
                          f"(only their source date, if known, was checked).")
            # Remaining budget for this topic — computed BEFORE sampling now.
            # Previously this was computed after the MAX_FACTS_PER_TOPIC
            # sample and never used to size that sample, so a topic could
            # (and regularly did) hand MAX_FACTS_PER_TOPIC facts to
            # generate_with_fallback even with only 1-2 slots of budget
            # left. Since each fact yields AT MOST one accepted MCQ (a fact
            # is removed from `pending_facts` the moment it gets an
            # accepted MCQ — see generate_from_facts), capping the fact
            # count to `remaining` caps the MCQs that topic can possibly
            # produce, so nothing gets generated (and paid for) only to be
            # thrown away by the eval_dicts cap below.
            remaining = MCQ_BUDGET - len(eval_dicts)
            sample_size = min(MAX_FACTS_PER_TOPIC, remaining)

            # FIX (TAS / blueprint alignment): bias sampling toward facts
            # that actually mention this topic's blueprint entities
            # (e.g. "পদ্মা সেতু" for the Infrastructure blueprint), so the
            # generated MCQs are more likely to be about what was asked,
            # not just any random fact tagged with the same topic name.
            # Still random within each priority bucket — see
            # _prioritize_facts_for_blueprint — so this does NOT
            # reintroduce the old deterministic top-N-by-score problem.
            ready_facts = _prioritize_facts_for_blueprint(ready_facts, topic, blueprints_by_topic)

            if len(ready_facts) > sample_size:
                pool_size   = len(ready_facts)
                ready_facts = ready_facts[:sample_size]
                print(f"  [{topic}] — sampled {sample_size} of "
                      f"{pool_size} ready fact(s), prioritizing blueprint-"
                      f"relevant ones "
                      f"(min of per-topic cap {MAX_FACTS_PER_TOPIC} and "
                      f"remaining budget {remaining}).")

            print(f"\n  [{difficulty.upper():6}] [{topic}] "
                  f"{len(ready_facts)} fact(s) → generating MCQs "
                  f"(budget remaining: {remaining})...")

            try:
                result = generate_with_fallback(
                    key_manager=key_manager, ready_facts=ready_facts,
                    difficulty=difficulty, topic=topic,
                )
            except Exception as exc:
                print(f"  [{topic}] ✗ Error: {exc}")
                traceback.print_exc()
                result = None

            if result is None:
                print(f"  [{topic}] — skipped (keys exhausted or error).")
                telemetry.total_pipeline_runs += 1
                continue

            # ── Ingest into LiveTelemetry ────────────────────────────────────
            telemetry.ingest_result(result)

            # Fact -> temporal_status lookup for this topic's ready_facts,
            # used to stamp each MCQ's eval dict (only meaningful when
            # CUTOFF_DATE is set; None otherwise so TVR/PCLR correctly
            # report "not computed" rather than a misleading value).
            fact_temporal_lookup = (
                {f["fact_id"]: f.get("temporal_status", "unchecked") for f in ready_facts}
                if CUTOFF_DATE else None
            )

            # ── Build richer eval dicts (respect budget cap) ─────────────────
            # This should now be a no-op safety net rather than the thing
            # doing the real capping — since ready_facts was already sized
            # to `remaining`, result.mcqs should already fit inside the
            # budget. It's kept here in case a topic somehow still returns
            # more MCQs than facts fed in (shouldn't happen, but silently
            # over-counting the report would be worse than silently
            # trimming it).
            overflow = 0
            for mcq in result.mcqs:
                if len(eval_dicts) >= MCQ_BUDGET:
                    overflow += 1
                    continue
                eval_dicts.append(mcq_to_eval_dict(mcq, difficulty, fact_temporal_lookup))
            if overflow:
                # Trim mcq_results_by_topic too, so generated_mcqs.json and
                # bcs_metrics_report.json never disagree on the MCQ count
                # again (previously mcq_results_by_topic always kept the
                # full uncapped list even when eval_dicts was capped).
                result.mcqs = result.mcqs[: len(result.mcqs) - overflow]
                print(f"  [{topic}] ⚠ {overflow} MCQ(s) generated beyond the "
                      f"budget cap were discarded from both eval_dicts and "
                      f"the saved MCQ file (this should be rare now that "
                      f"fact sampling respects remaining budget).")

            mcq_results_by_topic[topic] = result
            print(f"  [{topic}] ✓ {len(result.mcqs)} MCQ(s) accepted | "
                  f"score={result.overall_score:.3f} | rounds={result.crj_rounds} | "
                  f"total so far: {len(eval_dicts)}/{MCQ_BUDGET}")

        total_mcqs = sum(len(r.mcqs) for r in mcq_results_by_topic.values())
        print(f"\n  Total MCQs generated : {total_mcqs}")
        print(f"  Total eval dicts     : {len(eval_dicts)} (budget cap: {MCQ_BUDGET})")
        print(f"  Topics covered       : {len(mcq_results_by_topic)}")

        # Save generated MCQs to file
        all_mcqs_output = []
        for topic, result in mcq_results_by_topic.items():
            for mcq in result.mcqs:
                all_mcqs_output.append({
                    "mcq_id":         mcq.mcq_id,
                    "topic":          topic,
                    "question":       mcq.question,
                    "options":        {o.key: o.text for o in mcq.options},
                    "correct_answer": mcq.correct_answer,
                    "difficulty":     mcq.difficulty,
                    "question_type":  mcq.question_type,
                    "explanation":    mcq.explanation,
                    "fact_id":        mcq.fact_id,
                    "quality_score":  mcq.quality_score,
                })
        with open("generated_mcqs.json", "w", encoding="utf-8") as f:
            json.dump(all_mcqs_output, f, ensure_ascii=False, indent=2)
        print(f"  MCQs saved → generated_mcqs.json")

    # ======================================================================
    # STAGE 6 — MCQ Quality Evaluation
    # ======================================================================
    banner("STAGE 6 — MCQ Quality Evaluation (Simki)")

    all_quality_evaluations = []
    quality_evaluator       = None

    if not mcq_available:
        print("  [SKIPPED] MCQ quality evaluation requires HF_API_KEY.")
    elif not mcq_results_by_topic:
        print("  [SKIPPED] No MCQs were generated.")
    else:
        for topic, result in mcq_results_by_topic.items():
            if not result.mcqs:
                continue
            mcq_dicts = [
                {"mcq_id": mcq.mcq_id, "fact_id": mcq.fact_id,
                 "question": mcq.question,
                 "options": {o.key: o.text for o in mcq.options},
                 "correct_answer": mcq.correct_answer,
                 "difficulty": mcq.difficulty,
                 "question_type": mcq.question_type,
                 "explanation": mcq.explanation}
                for mcq in result.mcqs
            ]
            supporting_facts = facts_from_kg(
                kg, topic, as_of=CUTOFF_DATE,
                allow_static_source_evidence=ALLOW_STATIC_SOURCE_EVIDENCE,
            )
            try:
                quality_evaluator, batch_report = evaluate_batch_with_fallback(
                    key_manager=key_manager, mcq_dicts=mcq_dicts,
                    supporting_facts=supporting_facts,
                    topic=topic, difficulty=MCQ_DIFFICULTY,
                    kg_builder=kg, t_cutoff=CUTOFF_DATE,
                )
            except Exception as exc:
                print(f"  [{topic}] ✗ Evaluation error: {exc}")
                batch_report = None

            if batch_report:
                all_quality_evaluations.extend(batch_report.evaluations)
                # Not printed here — mcq_quality.py already logs
                # batch_report.summary_str() via log.info() right after it
                # builds the report, so printing it again here produced
                # every quality report twice, back to back, in the log.

        if quality_evaluator:
            quality_evaluator.save_improvement_log("quality_improvement_log.json")
            print("  MCQ quality log saved → quality_improvement_log.json")

    accepted_mcq_fact_ids = [
        ev.fact_id for ev in all_quality_evaluations if ev.passed
    ]

    if not accepted_mcq_fact_ids:
     accepted_mcq_fact_ids = telemetry.accepted_mcq_fact_ids

    # ======================================================================
    # STAGE 6b — Rejection Taxonomy Report (guideline §8.4 / §10.2)
    # ======================================================================
    # rejection_taxonomy.py itself was always correctly imported and called
    # by fact_quality.py, web_scraper.py, mcq_generator.py and
    # mcq_quality.py — each of those stages already produces canonical E-*
    # codes and even keeps a live RejectionTally while it runs. But every
    # one of those tally objects lived on a per-stage / per-batch object
    # (a fresh WebScraper, MCQGenerator, or MCQQualityEvaluator, rebuilt on
    # every HF key rotation) that main() never read from and then threw
    # away — so the taxonomy the paper's §10.2 rejection-rate table needs
    # never made it into bcs_metrics_report.json or any saved file. This
    # stage is the fix: it pulls the E-* evidence that IS still reachable
    # after each stage finished, from data structures main() already keeps
    # around, and combines them into one taxonomy report.
    #
    # Sources, in order of granularity:
    #   - fact_quality (Stage 2)   : dedup_report["rejection_report"] —
    #                                exact per-decision E-KG counts.
    #   - web_scraper   (Stage 0c) : scraper.rejection_tally — exact
    #                                per-source-decision E-LEAK/E-SRC counts
    #                                (only present if ENABLE_WEB_SCRAPING).
    #   - mcq_quality   (Stage 6)  : all_quality_evaluations[i].rejection_codes
    #                                — exact per-MCQ E-UNSUP/E-DIST/E-AMB/
    #                                E-STYLE/E-MULTI counts (this list is
    #                                accumulated across every topic/batch in
    #                                main(), so it survives evaluator reuse).
    #   - mcq_generator (Stage 5)  : mcq_results_by_topic[t].rejection_codes
    #                                — the CRJ judge loop's own tally object
    #                                doesn't survive past generate_from_facts(),
    #                                so this is a de-duplicated per-topic-
    #                                episode PRESENCE signal (which E-* codes
    #                                occurred in that episode), not a raw
    #                                per-candidate-MCQ count. Kept in its own
    #                                sub-report below so it is never silently
    #                                mixed with the exact per-MCQ counts above.
    banner("STAGE 6b — Rejection Taxonomy Report (§8.4 / §10.2)")

    rejection_stage_reports: Dict[str, dict] = {}

    # -- fact_quality (KG dedup / E-KG) --------------------------------
    gate_rejection_report = dedup_report.get("rejection_report")
    if gate_rejection_report:
        rejection_stage_reports["fact_quality_gate"] = gate_rejection_report

    # -- web_scraper (source acquisition / E-LEAK, E-SRC) --------------
    if scraper is not None:
        rejection_stage_reports["web_scraping"] = scraper.rejection_tally.report()

    # -- mcq_generator CRJ judge loop (per-topic-episode presence) -----
    gen_tally = RejectionTally()
    for _topic, _result in mcq_results_by_topic.items():
        gen_tally.add(_result.rejection_codes)
    if mcq_results_by_topic:
        rejection_stage_reports["mcq_generation_judge_episodes"] = gen_tally.report()

    # -- mcq_quality Stage 6 evaluator (exact per-MCQ counts) -----------
    qual_tally = RejectionTally()
    for _ev in all_quality_evaluations:
        qual_tally.add(_ev.rejection_codes)
    if all_quality_evaluations:
        rejection_stage_reports["mcq_quality_evaluation"] = qual_tally.report()

    # -- Grand total: sum of E-* code counts across every stage above ---
    # (denominators/"_total_items" differ in meaning per stage — a source
    # decision, an episode, an individual MCQ — so they are NOT summed;
    # only the canonical code counts are, which is what §10.2 asks for.)
    grand_counts: Dict[str, int] = {code: 0 for code in RejectionCode.ALL}
    unmapped_counts: Dict[str, int] = {}
    for rep in rejection_stage_reports.values():
        for code in RejectionCode.ALL:
            grand_counts[code] += rep.get(code, 0)
        for k, v in rep.items():
            if isinstance(k, str) and k.startswith("UNMAPPED:"):
                unmapped_counts[k] = unmapped_counts.get(k, 0) + v

    rejection_taxonomy_report = {
        "by_stage":            rejection_stage_reports,
        "combined_code_counts": {**grand_counts, **unmapped_counts},
        "combined_total_rejections": sum(grand_counts.values()) + sum(unmapped_counts.values()),
    }

    print("\n  Rejection taxonomy (guideline §10.2) — combined counts across all stages:")
    for code in RejectionCode.ALL:
        n = grand_counts.get(code, 0)
        if n:
            print(f"    {code:<8} {REJECTION_DESCRIPTIONS[code]:<45} n={n}")
    for k, v in unmapped_counts.items():
        print(f"    {k:<8} (no taxonomy mapping yet){'':<20} n={v}")
    print(f"    Total rejections logged across all stages: "
          f"{rejection_taxonomy_report['combined_total_rejections']}")

    with open("rejection_taxonomy_report.json", "w", encoding="utf-8") as f:
        json.dump(rejection_taxonomy_report, f, ensure_ascii=False, indent=2)
    print("  Rejection taxonomy report saved → rejection_taxonomy_report.json")

    # ======================================================================
    # STAGE 7 — Write Episodes to Episodic Memory
    # ======================================================================
    banner("STAGE 7 — Episodic Memory — Write Episodes (Saif)")

    if os.path.exists(MEMORY_DB):
        os.remove(MEMORY_DB)

    mem         = EpisodicMemory(MEMORY_DB)
    episode_ids = []
    topic_to_episode_id: dict = {}   # FIX (#9) — needed to attach rejections below
    facts_by_topic: dict = {}

    for fid in surviving_fact_ids:
        t = _get_fact_topic(kg, fid)
        if t:
            facts_by_topic.setdefault(t, []).append(fid)

    for topic, topic_facts in facts_by_topic.items():
        if not topic_facts:
            continue
        topic_bps      = blueprints_by_topic.get(topic, [])
        episode_input_q = (topic_bps[0].normalized_question if topic_bps
                           else f"BCS pipeline question for topic: {topic}")
        episode_intent  = topic_bps[0].intent    if topic_bps else "pipeline_auto"
        episode_bp      = topic_bps[0].question_type if topic_bps else "single_correct_answer"

        if topic in mcq_results_by_topic:
            result  = mcq_results_by_topic[topic]
            payload = result.to_episode_payload()
            payload["input_question"] = episode_input_q
            payload["intent"]         = episode_intent
            payload["blueprint"]      = episode_bp
            # Explicit marker so downstream stages (retrieval, judge
            # feedback, forgetting) can tell a real, judge-evaluated
            # episode apart from a placeholder one. Previously this was
            # left unset and only placeholders set generation_config,
            # which worked by accident (absence == real) but made it easy
            # for a future edit to silently reintroduce the mislabeling
            # bug fixed below.
            payload.setdefault("generation_config", {})
            payload["generation_config"]["source"] = "pipeline_real"
            eid = mem.write_episode(**payload)
        else:
            readiness_scores = [
                kg.get_fact_data(fid).get("mcq_readiness", 0.5)
                for fid in topic_facts if kg.get_fact_data(fid)
            ]
            avg_readiness = (sum(readiness_scores) / len(readiness_scores)
                             if readiness_scores else 0.0)
            placeholder_mcqs = []
            for fid in topic_facts[:3]:
                fdata = kg.get_fact_data(fid)
                if fdata:
                    placeholder_mcqs.append({
                        "question": f"MCQ from: {fdata.get('text','')[:50]}",
                        "options": ["Option A","Option B","Option C","Option D"],
                        "correct_answer": "Option A",
                        "difficulty": "medium",
                        "quality_score": fdata.get("mcq_readiness", 0.5),
                        "regeneration_round": 0,
                    })
            eid = mem.write_episode(
                input_question=episode_input_q, intent=episode_intent,
                blueprint=episode_bp, topic=topic, fact_ids=topic_facts,
                mcqs=placeholder_mcqs, overall_score=round(avg_readiness, 2),
                accepted=1 if avg_readiness >= 0.5 else 0,
                generation_config={"source": "pipeline_fallback", "version": "1.0"},
            )
        episode_ids.append(eid)
        topic_to_episode_id[topic] = eid

    print(f"\n  {len(episode_ids)} episode(s) written to {MEMORY_DB}")

    # ── FIX (#9): actually populate rejection_logs with real per-fact ──
    # failure data. Stage 6b already computed the taxonomy counts and
    # `all_quality_evaluations` already holds the exact fact_id/topic/
    # feedback for every failed MCQ — nothing previously fed that into
    # episodic memory, which is why rejection_logs stayed at 0 rows and
    # Stage 10's diagnostics ([A] Failed Facts, [C] MCQ-level Failed Fact
    # IDs) always reported "None" even when real rejections happened.
    logged_rejections = 0
    for ev in all_quality_evaluations:
        if ev.passed:
            continue
        eid = topic_to_episode_id.get(ev.topic)
        if not eid:
            continue
        reason = ", ".join(ev.rejection_codes or ev.failure_codes or ["UNKNOWN"])
        mem.log_rejection(
            episode_id=eid, fact_id=ev.fact_id,
            reason=reason, judge_feedback=ev.feedback,
        )
        logged_rejections += 1

    if logged_rejections:
        print(f"  Logged {logged_rejections} rejection record(s) into "
              f"rejection_logs (fact-level detail for Stage 10 diagnostics).")

    # ======================================================================
    # STAGE 8 — Experience Retrieval
    # ======================================================================
    banner("STAGE 8 — Experience Retrieval (Saif)")

    # Previously this hardcoded `list(facts_by_topic.keys())[:5]` — an
    # arbitrary first-5-by-dict-order slice unrelated to which topics
    # actually got real MCQ generation this run. That silently dropped
    # topics (e.g. it skipped "Government" in a 6-topic run) and fed a
    # skewed count into episodes_assisted, which MUS/LES are built from.
    # Retrieval should run over every topic that actually has a real,
    # judge-evaluated episode worth finding — i.e. mcq_results_by_topic —
    # not a fixed-size slice of whatever topic happened to sort first.
    episodes_assisted = 0
    for topic in mcq_results_by_topic.keys():
        retrieved = mem.retrieve_similar_episodes(topic=topic, min_score=0.5)
        print(f"  [{topic}] — {len(retrieved)} high-quality episode(s) found")
        if retrieved:
            episodes_assisted += len(retrieved)

    telemetry.episodes_assisted = episodes_assisted   # feeds MUS / LES metrics

    # ======================================================================
    # STAGE 9 — Judge Feedback Loop
    # ======================================================================
    banner("STAGE 9 — Judge Feedback Loop (Saif)")

    # NOTE: these are diagnostic-only counts over *episodes* (score < 0.5 /
    # score in [0.40, 0.5)) — they do NOT represent an actual regeneration
    # event, so they must NOT be merged into telemetry.initial_failures /
    # telemetry.corrected_failures. Those two fields are already populated
    # by LiveTelemetry.ingest_result() from real CRJ regeneration rounds at
    # the MCQ level, and feed bcs_metrics.py's SCE (Self-Correction
    # Efficiency = corrected_failures / initial_failures). Adding this
    # unrelated episode-level score-bucket count on top used to silently
    # corrupt SCE with numbers that never went through any regeneration —
    # kept separate here so SCE keeps meaning what its docstring says.
    episode_low_quality_count = 0
    episode_borderline_count  = 0
    episode_placeholder_flagged_count = 0

    for eid in episode_ids:
        detail = mem.get_episode_detail(eid)
        if not detail:
            continue
        score = detail["overall_score"]
        if score >= 0.5:
            continue

        # A placeholder episode's overall_score is an average of the
        # underlying facts' mcq_readiness — it was never actually judged,
        # because generation never ran for that topic (budget cap). Prior
        # to this fix, a low-readiness placeholder and a genuinely
        # judge-rejected MCQ episode were logged identically as "rejected
        # via judge feedback," which misrepresents what happened for any
        # topic MCQ generation never reached.
        try:
            gen_config = json.loads(detail.get("generation_config") or "{}")
        except (TypeError, ValueError):
            gen_config = {}
        is_placeholder = gen_config.get("source") == "pipeline_fallback"

        if is_placeholder:
            episode_placeholder_flagged_count += 1
            mem.update_episode(
                episode_id=eid, accepted=0, overall_score=score,
                rejection_reason="LOW_READINESS_PLACEHOLDER",
                judge_feedback=(
                    f"Placeholder episode — avg fact mcq_readiness "
                    f"{score:.3f} below 0.5. No MCQ was ever generated or "
                    f"judged for this topic this run."
                ),
            )
        else:
            episode_low_quality_count += 1
            mem.update_episode(
                episode_id=eid, accepted=0, overall_score=score,
                rejection_reason="LOW_QUALITY_SCORE",
                judge_feedback=f"Score {score:.3f} below threshold of 0.5.",
            )
            if score >= 0.40:
                episode_borderline_count += 1

    print(f"\n  {episode_low_quality_count} episode(s) rejected via judge feedback")
    print(f"  {episode_placeholder_flagged_count} placeholder episode(s) flagged "
          f"as low-readiness (never generated/judged this run)")
    print(f"  {episode_borderline_count} episode(s) flagged as potentially correctable "
          f"(diagnostic only — not counted toward SCE)")

    # ======================================================================
    # STAGE 10 — Diagnostics
    # ======================================================================
    banner("STAGE 10 — Diagnostics (Saif + Simki)")

    print("\n  [A] Failed Facts:")
    failed_facts = mem.get_failed_facts(top_n=10)
    if failed_facts:
        for ff in failed_facts:
            fdata = kg.get_fact_data(ff["fact_id"])
            preview = fdata.get("text", "")[:50] if fdata else ""
            print(f"    {ff['fact_id'][:16]} | rejections={ff['rejection_count']}"
                  f" | \"{preview}\"")
    else:
        print("    None")

    print("\n  [B] Topic Performance:")
    for tp in mem.get_high_performing_topics():
        print(f"    {tp['topic']:<22} | avg_score={tp['avg_score']:.4f} | "
              f"acc_rate={tp['acceptance_rate']:.4f} | count={tp['question_count']}")

    if all_quality_evaluations:
        # FIX (#9): `quality_evaluator` here was whatever the LAST topic's
        # evaluate_batch_with_fallback() call happened to construct — a
        # fresh MCQQualityEvaluator per topic (and per HF key rotation),
        # each with its own private `_log`. Reading get_failed_fact_ids()
        # off it only ever showed the last topic processed, so a failure
        # from an earlier topic (e.g. Infrastructure's MCQ_6904f7a1 in one
        # run) was invisible here even though it's sitting right there in
        # `all_quality_evaluations`, which main() already accumulates
        # across every topic/batch. Read from that instead.
        print("\n  [C] MCQ-level Failed Fact IDs:")
        mcq_failed = [ev.fact_id for ev in all_quality_evaluations if not ev.passed]
        print("    " + (", ".join(mcq_failed) if mcq_failed else "None"))

    # ======================================================================
    # STAGE 11 — Feedback Loop: Diagnostics → Quality Gate
    # ======================================================================
    banner("STAGE 11 — Feedback Loop: Diagnostics → Quality Gate (Galib)")

    re_evaluated = 0
    for ff in failed_facts:
        fid = ff["fact_id"]
        if kg.graph.has_node(fid):
            result = gate.evaluate_and_act(fid)
            print(f"  Re-evaluated {fid[:16]} → {result['action']}")
            re_evaluated += 1
    print(f"\n  Re-evaluated {re_evaluated} problematic fact(s)")

    # ======================================================================
    # STAGE 12 — Adaptive Growth Recommendations
    # ======================================================================
    banner("STAGE 12 — Adaptive Growth Recommendations (Souvik)")

    recommendations = kg.analyze_topic_density(
        fact_threshold=5, question_threshold=3, unmapped_threshold=2,
    )
    if recommendations:
        for topic_id, action, reason in recommendations:
            tname = kg.graph.nodes.get(topic_id, {}).get("name", topic_id)
            print(f"  [{tname}] → {action} | {reason}")
    else:
        print("  No expansion recommendations at current thresholds.")

    print("\n  Topic Statistics:")
    for tid, stats in kg.get_topic_stats().items():
        tname = kg.graph.nodes.get(tid, {}).get("name", tid)
        print(f"    {tname:<22} | facts={stats['fact_count']} "
              f"| questions={stats['question_count']} "
              f"| unmapped_f={stats['unmapped_facts']} "
              f"| unmapped_q={stats.get('unmapped_questions',0)}")

    # ======================================================================
    # STAGE 13 — Selective Forgetting
    # ======================================================================
    banner("STAGE 13 — Selective Forgetting (Saif)")

    low_q_identified = sum(
        1 for eid in episode_ids
        if (d := mem.get_episode_detail(eid)) and d["overall_score"] < 0.3
    )
    # Dry run and live run must use the SAME thresholds — otherwise the
    # "preview" doesn't preview what the live call actually does. This was
    # previously 0.5/90d for the dry run vs 0.3/365d live, which is why a
    # dry run could report "1 episode would be pruned" and the live run
    # would then correctly prune 0 (its decay was above the live
    # threshold, just below the dry run's looser one).
    FORGET_DECAY_THRESHOLD = 0.3
    FORGET_MAX_AGE_DAYS    = 365

    print("  Dry run:")
    mem.forget_old_episodes(
        decay_threshold=FORGET_DECAY_THRESHOLD,
        max_age_days=FORGET_MAX_AGE_DAYS, dry_run=True,
    )
    print("\n  Live prune:")
    pruned = mem.forget_old_episodes(
        decay_threshold=FORGET_DECAY_THRESHOLD,
        max_age_days=FORGET_MAX_AGE_DAYS,
    )
    print(f"  Pruned {pruned} episode(s)")

    # Feed into telemetry (overrides any estimate from ingest_result)
    telemetry.low_quality_episodes_identified = low_q_identified
    telemetry.low_quality_episodes_removed    = pruned

    # ======================================================================
    # STAGE 14 — BCS Metrics Evaluation (all 27 metrics)
    # ======================================================================
    banner("STAGE 14 — BCS Metrics Evaluation (all 27 metrics)")

    elapsed = round(time.time() - pipeline_start, 2)

    # Build RuntimeData from LiveTelemetry (accurate — no manual guessing)
    iterations_to_target = max(
        (r.crj_rounds for r in mcq_results_by_topic.values()), default=1
    ) or 1
    rd = telemetry.to_runtime_data(elapsed=elapsed,
                                   iterations_to_target=iterations_to_target)

    metrics_evaluator = BCSMetricsEvaluator(
        kg=kg, memory=mem, min_facts_per_topic=MIN_FACTS_PER_TOPIC,
    )

    metrics_report = metrics_evaluator.full_report(
        dedup_report            = dedup_report,
        mcq_evaluations         = eval_dicts,        # richer dicts from mcq_to_eval_dict
        runtime_data            = rd,
        bcs_benchmark_topics    = BCS_BENCHMARK_TOPICS,
        bcs_benchmark_questions = bcs_benchmark_questions,  # ← now wired: activates QSS
        bcs_difficulty_dist     = bcs_difficulty_dist,      # ← now wired: activates DDMS
        target_topics           = list(topics_seen.keys()),
        accepted_mcq_fact_ids   = accepted_mcq_fact_ids,
    )

    # Inject JAS proxy (FIX: now real judge self-check agreement —
    # LLM-reported verdict vs. deterministic recompute — not the old
    # tautological accepted-vs-accepted comparison. See jas_proxy().)
    jas_value = telemetry.jas_proxy()
    if jas_value is not None:
        metrics_report["metrics"]["JAS"] = jas_value

    print(metrics_report["summary"])

    # Save metrics
    metrics_out = {
        "timestamp":            metrics_report["timestamp"],
        "dataset":              FACTS_JSON,
        "total_facts_loaded":   len(fact_ids),
        "total_mcqs_generated": len(eval_dicts),
        "cost_estimate_usd":    telemetry.estimate_cost_usd(),
        "metrics":              metrics_report["metrics"],
        # §8.4/§10.2 — combined-only view here; the full per-stage
        # breakdown lives in rejection_taxonomy_report.json (Stage 6b).
        "rejection_taxonomy": {
            "combined_code_counts":      rejection_taxonomy_report["combined_code_counts"],
            "combined_total_rejections": rejection_taxonomy_report["combined_total_rejections"],
        },
    }
    with open(OUTPUT_METRICS, "w", encoding="utf-8") as f:
        json.dump(metrics_out, f, ensure_ascii=False, indent=2, default=str)
    print(f"  Metrics report saved → {OUTPUT_METRICS}")

    # Health check table (from run_metrics_pipeline)
    print_diagnosis(metrics_report["metrics"])

    # ======================================================================
    # STAGE 15 — Save KG Snapshot
    # ======================================================================
    banner("STAGE 15 — Save KG Snapshot (Souvik)")

    snapshot_path = kg.save_snapshot(SNAPSHOT_FOLDER)
    kg.summary()

    # ======================================================================
    # FINAL SUMMARY
    # ======================================================================
    banner("PIPELINE COMPLETE — Final Summary")

    mem.summary()

    print(f"\n  Dataset          : {FACTS_JSON}")
    print(f"  Facts loaded     : {len(fact_ids)}")
    print(f"  Topics           : {len(topics_seen)}")
    print(f"  MCQs generated   : {len(eval_dicts)}")
    print(f"  Cost estimate    : ${telemetry.estimate_cost_usd():.6f}")
    print(f"  Total time       : {elapsed}s")
    print(f"\n  Output files:")
    print(f"    Metrics report      : {OUTPUT_METRICS}")
    print(f"    Rejection taxonomy  : rejection_taxonomy_report.json")
    print(f"    Blueprints          : {blueprints_path}")
    print(f"    Quality report      : quality_report.txt")
    print(f"    MCQs                : generated_mcqs.json")
    print(f"    Episodic DB         : {MEMORY_DB}")
    print(f"    KG Snapshot         : {snapshot_path}")

    mem.close()

    print("\n+" + "-" * 63 + "+")
    print("|  Merged pipeline complete. All 27 metrics computed.          |")
    print("|  Mohaiminul | Souvik | Saif | Galib | Simki                  |")
    print("+" + "-" * 63 + "+\n")


if __name__ == "__main__":
    main()