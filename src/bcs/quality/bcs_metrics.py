

import math
import re
import datetime
import sqlite3
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


# Token splitter for QSS lexical overlap. We must NOT use ``\w+``: in Bengali,
# vowel-sign marks (matras, Unicode Mn) are not matched by ``\w``, so
# ``re.findall(r"\w+", "বাংলাদেশের রাজধানী")`` shatters each word into single
# consonants ("ব","ল","দ","শ"…), making Jaccard overlap meaningless. Bengali
# words are whitespace-delimited, so we split on whitespace + punctuation
# (including the Bengali danda ।/॥) and keep each word — matras included —
# intact. Works identically for English.
_QSS_SPLIT = re.compile(
    r"[\s।॥।॥.,?;:!()\[\]{}\"'`’“”…\-–—_/\\|<>@#%^&*+=~]+"
)


def _qss_tokenize(text: str) -> set:
    if not text:
        return set()
    return {t for t in _QSS_SPLIT.split(text.lower()) if t}


# ---------------------------------------------------------------------------
# RuntimeData — caller fills this in during / after a pipeline run
# ---------------------------------------------------------------------------

class RuntimeData:
    """
    Lightweight container for live pipeline telemetry that the metrics
    evaluator cannot compute by itself.

    All fields are optional — metrics that depend on missing fields will
    return None and emit a warning rather than crash.

    Attributes
    ----------
    total_pipeline_runs : int
        How many full pipeline runs were attempted.
    successful_pipeline_runs : int
        Runs that ended with at least one accepted MCQ.
    total_cost_usd : float
        Total API / compute cost in USD for this evaluation window.
    total_processing_seconds : float
        Wall-clock time for the same window.
    total_agent_interactions : int
        Number of challenger–reasoner–judge loop calls.
    episodes_assisted : int
        Generations where a retrieved episode contributed to acceptance.
    total_generations : int
        Total MCQ generation attempts (before acceptance/rejection).
    regenerated_mcqs : int
        MCQs that went through at least one regeneration round.
    improved_after_regen : int
        Regenerated MCQs that were accepted after regen (previously failed).
    initial_failures : int
        MCQs that failed on their first generation attempt.
    corrected_failures : int
        Of those, how many were eventually corrected by the loop.
    regen_with_feedback : int
        Regenerations that received explicit judge feedback.
    regen_improved_with_feedback : int
        Of those, how many improved after that feedback.
    low_quality_episodes_identified : int
        Episodes the memory system flagged for forgetting.
    low_quality_episodes_removed : int
        Of those, how many were actually removed / decayed.
    human_judge_decisions : list of bool
        Human expert accept/reject decisions (True = accept).
    auto_judge_decisions : list of bool
        Corresponding automatic judge decisions (same order).
    iterations_to_target : int
        Iterations the system needed to reach its quality target.
    difficulty_target_dist : dict
        Intended difficulty distribution e.g. {"easy":0.3,"medium":0.5,"hard":0.2}
    accepted_mcq_ids : list of str
        IDs of MCQs accepted by the judge (used for FUS cross-check).
    """

    def __init__(
        self,
        total_pipeline_runs: int = 0,
        successful_pipeline_runs: int = 0,
        total_cost_usd: float = 0.0,
        total_processing_seconds: float = 0.0,
        total_agent_interactions: int = 0,
        episodes_assisted: int = 0,
        total_generations: int = 0,
        regenerated_mcqs: int = 0,
        improved_after_regen: int = 0,
        initial_failures: int = 0,
        corrected_failures: int = 0,
        regen_with_feedback: int = 0,
        regen_improved_with_feedback: int = 0,
        low_quality_episodes_identified: int = 0,
        low_quality_episodes_removed: int = 0,
        human_judge_decisions: Optional[List[bool]] = None,
        auto_judge_decisions: Optional[List[bool]] = None,
        iterations_to_target: int = 0,
        difficulty_target_dist: Optional[Dict[str, float]] = None,
        accepted_mcq_ids: Optional[List[str]] = None,
    ):
        self.total_pipeline_runs = total_pipeline_runs
        self.successful_pipeline_runs = successful_pipeline_runs
        self.total_cost_usd = total_cost_usd
        self.total_processing_seconds = total_processing_seconds
        self.total_agent_interactions = total_agent_interactions
        self.episodes_assisted = episodes_assisted
        self.total_generations = total_generations
        self.regenerated_mcqs = regenerated_mcqs
        self.improved_after_regen = improved_after_regen
        self.initial_failures = initial_failures
        self.corrected_failures = corrected_failures
        self.regen_with_feedback = regen_with_feedback
        self.regen_improved_with_feedback = regen_improved_with_feedback
        self.low_quality_episodes_identified = low_quality_episodes_identified
        self.low_quality_episodes_removed = low_quality_episodes_removed
        self.human_judge_decisions = human_judge_decisions or []
        self.auto_judge_decisions = auto_judge_decisions or []
        self.iterations_to_target = iterations_to_target
        self.difficulty_target_dist = difficulty_target_dist or {
            "easy": 0.33, "medium": 0.34, "hard": 0.33
        }
        self.accepted_mcq_ids = accepted_mcq_ids or []


# ---------------------------------------------------------------------------
# BCSMetricsEvaluator
# ---------------------------------------------------------------------------

class BCSMetricsEvaluator:
    """
    Compute all evaluation metrics defined in BCS_Metrics.pdf.

    Parameters
    ----------
    kg : KnowledgeGraphBuilder
        Live (or loaded) knowledge graph instance.
    memory : EpisodicMemory, optional
        Episodic memory instance for memory-related metrics.
    min_facts_per_topic : int
        Minimum facts a topic needs to count as "covered" (used in TCS).
    """

    def __init__(self, kg, memory=None, min_facts_per_topic: int = 3):
        self.kg = kg
        self.memory = memory
        self.k = min_facts_per_topic

    # ==================================================================
    # CATEGORY A — Knowledge Graph & Fact Quality Metrics
    # ==================================================================

    def fact_quality_score(self) -> Optional[float]:
        """
        FQS — average composite quality score across all Fact nodes.

        Pulls `composite_score` stored by FactQualityGate.score_fact().
        Returns None if no scored facts exist.
        """
        scores = []
        for _, data in self.kg.graph.nodes(data=True):
            if data.get("type") == "FACT":
                cs = data.get("composite_score")
                if cs is not None:
                    scores.append(float(cs))
        if not scores:
            return None
        return round(sum(scores) / len(scores), 4)

    def graph_consistency_score(self) -> float:
        """
        GCS — fraction of Fact nodes that are "well-linked":
        connected to at least one ENTITY (SUBJECT_OF / OBJECT_IS),
        one TOPIC (ABOUT), and one SOURCE (SUPPORTED_BY).

        GCS = well_linked_facts / total_facts
        """
        total = 0
        well_linked = 0

        for node_id, data in self.kg.graph.nodes(data=True):
            if data.get("type") != "FACT":
                continue
            total += 1

            has_entity  = False
            has_topic   = False
            has_source  = False

            # Outgoing edges from this fact
            for _, tgt, edata in self.kg.graph.out_edges(node_id, data=True):
                rel = edata.get("relation", "")
                tgt_type = self.kg.graph.nodes[tgt].get("type", "")
                if rel == "ABOUT" and tgt_type == "TOPIC":
                    has_topic = True
                elif rel == "SUPPORTED_BY" and tgt_type == "SOURCE":
                    has_source = True
                elif rel == "OBJECT_IS" and tgt_type == "ENTITY":
                    has_entity = True

            # Incoming edges (SUBJECT_OF comes from ENTITY → FACT)
            for src, _, edata in self.kg.graph.in_edges(node_id, data=True):
                rel = edata.get("relation", "")
                src_type = self.kg.graph.nodes[src].get("type", "")
                if rel == "SUBJECT_OF" and src_type == "ENTITY":
                    has_entity = True

            if has_entity and has_topic and has_source:
                well_linked += 1

        if total == 0:
            return 0.0
        return round(well_linked / total, 4)

    def redundancy_reduction_rate(self, dedup_report: Dict) -> Optional[float]:
        """
        RRR — how effectively the deduplication step removed duplicates.

        RRR = 1 − (duplicates_after / duplicates_before)
            = pairs_merged / duplicates_found

        Parameters
        ----------
        dedup_report : dict
            The dict returned by FactQualityGate.deduplicate_graph().
        """
        found  = dedup_report.get("duplicates_found", 0)
        merged = dedup_report.get("pairs_merged", 0)
        if found == 0:
            return 1.0  # no duplicates existed → perfect
        return round(merged / found, 4)

    def topic_coverage_score(
        self, target_topics: List[str]
    ) -> float:
        """
        TCS — fraction of target topics that have at least k usable facts.

        TCS = topics_with_k_facts / total_target_topics

        Parameters
        ----------
        target_topics : list of str
            The complete list of BCS GK topics the system should cover.
        """
        if not target_topics:
            return 0.0

        covered = 0
        for topic in target_topics:
            fact_ids = self.kg.get_facts_by_topic(topic)
            usable = 0
            for fid in fact_ids:
                d = self.kg.get_fact_data(fid)
                if d and d.get("quality_verdict", "") not in ("REJECT",):
                    usable += 1
            if usable >= self.k:
                covered += 1

        return round(covered / len(target_topics), 4)

    def fact_utilization_score(self, accepted_mcq_fact_ids: List[str]) -> float:
        """
        FUS — fraction of usable KG facts that were actually used
              in accepted MCQs.

        FUS = facts_used_in_accepted_MCQs / total_usable_facts

        Parameters
        ----------
        accepted_mcq_fact_ids : list of str
            Flat list of fact IDs referenced by accepted MCQs.
        """
        used_set = set(accepted_mcq_fact_ids)

        usable_facts = []
        for node_id, data in self.kg.graph.nodes(data=True):
            if data.get("type") == "FACT":
                verdict = data.get("quality_verdict", "UNSCORED")
                if verdict not in ("REJECT", "REJECT_AFTER_REFINE"):
                    usable_facts.append(node_id)

        if not usable_facts:
            return 0.0

        used_count = sum(1 for fid in usable_facts if fid in used_set)
        return round(used_count / len(usable_facts), 4)

    # ==================================================================
    # CATEGORY B — Episodic Memory Metrics
    # ==================================================================

    def memory_utility_score(self, rd: RuntimeData) -> Optional[float]:
        """
        MUS — fraction of generations where a retrieved episode
              contributed to a successful outcome.

        MUS = episodes_assisted / total_generations
        """
        if rd.total_generations == 0:
            return None
        return round(rd.episodes_assisted / rd.total_generations, 4)

    def learning_efficiency_score(self, rd: RuntimeData) -> Optional[float]:
        """
        LES — fraction of generated MCQs that were accepted.

        LES = accepted_MCQs / total_generated_MCQs
        """
        if rd.total_generations == 0:
            return None
        accepted = len(rd.accepted_mcq_ids)
        return round(accepted / rd.total_generations, 4)

    def regeneration_improvement_rate(self, rd: RuntimeData) -> Optional[float]:
        """
        RIR — fraction of regenerated MCQs that improved to accepted.

        RIR = improved_after_regen / total_regenerated
        """
        if rd.regenerated_mcqs == 0:
            return None
        return round(rd.improved_after_regen / rd.regenerated_mcqs, 4)

    def forgetting_effectiveness(self, rd: RuntimeData) -> Optional[float]:
        """
        FE — how well the memory system removes low-quality episodes.

        FE = low_quality_removed / low_quality_identified
        """
        if rd.low_quality_episodes_identified == 0:
            return None
        return round(
            rd.low_quality_episodes_removed / rd.low_quality_episodes_identified, 4
        )

    # ==================================================================
    # CATEGORY C — MCQ Quality Metrics
    # ==================================================================

    def mcq_structural_validity(self, mcq_evaluations: List) -> float:
        """
        MSV — fraction of generated MCQs that pass basic format checks.

        Reads the `passed` flag from MCQEvaluation objects OR from dicts
        with a 'structurally_valid' / 'passed' key.

        MSV = structurally_valid / total_generated
        """
        if not mcq_evaluations:
            return 0.0
        valid = 0
        for ev in mcq_evaluations:
            if hasattr(ev, "scores"):
                # MCQEvaluation dataclass: format_score >= 0.5 as proxy
                if ev.scores.format_score >= 0.5:
                    valid += 1
            elif isinstance(ev, dict):
                if ev.get("structurally_valid", ev.get("passed", False)):
                    valid += 1
        return round(valid / len(mcq_evaluations), 4)

    def grounding_accuracy(self, mcq_evaluations: List) -> float:
        """
        GA — fraction of MCQs correctly traceable to a KG fact.

        Uses grounding_score >= GROUNDING_MIN (0.80) as threshold,
        matching the hard floor in mcq_quality.py.

        GA = grounded_MCQs / total_MCQs
        """
        if not mcq_evaluations:
            return 0.0
        GROUNDING_MIN = 0.80
        grounded = 0
        for ev in mcq_evaluations:
            gs = self._get_dim(ev, "grounding_score")
            if gs is not None and gs >= GROUNDING_MIN:
                grounded += 1
        return round(grounded / len(mcq_evaluations), 4)

    def distractor_quality_score(self, mcq_evaluations: List) -> Optional[float]:
        """
        DQS — average distractor quality score across all MCQs.

        DQS = (1/N) * Σ distractor_score_i
        """
        scores = []
        for ev in mcq_evaluations:
            ds = self._get_dim(ev, "distractor_score")
            if ds is not None:
                scores.append(ds)
        if not scores:
            return None
        return round(sum(scores) / len(scores), 4)

    def difficulty_calibration_error(
        self, mcq_evaluations: List, rd: RuntimeData
    ) -> Optional[float]:
        """
        DCE — mean absolute difference between target and observed
              difficulty distribution.

        DCE = Σ |P_target(d) − P_observed(d)| / num_difficulty_levels
        """
        if not mcq_evaluations:
            return None

        target = rd.difficulty_target_dist
        difficulty_counts: Counter = Counter()
        for ev in mcq_evaluations:
            diff = self._get_difficulty(ev)
            if diff:
                difficulty_counts[diff.lower()] += 1

        total = sum(difficulty_counts.values()) or 1
        observed = {k: v / total for k, v in difficulty_counts.items()}

        all_levels = set(target) | set(observed)
        error = sum(
            abs(target.get(d, 0.0) - observed.get(d, 0.0))
            for d in all_levels
        )
        return round(error / max(len(all_levels), 1), 4)

    def clarity_score(self, mcq_evaluations: List) -> Optional[float]:
        """
        CS — average clarity score across all MCQs.
        """
        scores = []
        for ev in mcq_evaluations:
            cs = self._get_dim(ev, "clarity_score")
            if cs is not None:
                scores.append(cs)
        if not scores:
            return None
        return round(sum(scores) / len(scores), 4)

    # ==================================================================
    # CATEGORY D — Agentic Loop Metrics
    # ==================================================================

    def judge_agreement_score(self, rd: RuntimeData) -> Optional[float]:
        """
        JAS — fraction of cases where the auto judge agrees with a
              human expert.

        JAS = matching_decisions / total_evaluated_cases
        """
        h = rd.human_judge_decisions
        a = rd.auto_judge_decisions
        if not h or not a or len(h) != len(a):
            return None
        matches = sum(1 for hi, ai in zip(h, a) if hi == ai)
        return round(matches / len(h), 4)

    def self_correction_efficiency(self, rd: RuntimeData) -> Optional[float]:
        """
        SCE — fraction of initial failures that were eventually corrected
              by the challenger–reasoner–judge loop.

        SCE = corrected_failures / total_initial_failures
        """
        if rd.initial_failures == 0:
            return None
        return round(rd.corrected_failures / rd.initial_failures, 4)

    def agent_efficiency_score(self, rd: RuntimeData) -> Optional[float]:
        """
        AES — accepted MCQs produced per agent interaction.

        AES = accepted_MCQs / agent_interactions
        """
        if rd.total_agent_interactions == 0:
            return None
        accepted = len(rd.accepted_mcq_ids)
        return round(accepted / rd.total_agent_interactions, 4)

    def failure_explanation_utility(self, rd: RuntimeData) -> Optional[float]:
        """
        FEU — fraction of judge-feedback-guided regenerations that led
              to an improved (accepted) MCQ.

        FEU = improved_with_feedback / total_regen_with_feedback
        """
        if rd.regen_with_feedback == 0:
            return None
        return round(rd.regen_improved_with_feedback / rd.regen_with_feedback, 4)

    # ==================================================================
    # CATEGORY E — BCS Alignment Metrics
    # ==================================================================

    def topic_alignment_score(
        self, bcs_benchmark_topics: List[str]
    ) -> float:
        """
        TAS — fraction of BCS benchmark topics that appear in the KG.

        TAS = aligned_topics / total_BCS_benchmark_topics
        """
        if not bcs_benchmark_topics:
            return 0.0

        kg_topics = {
            data.get("name", "").lower()
            for _, data in self.kg.graph.nodes(data=True)
            if data.get("type") == "TOPIC"
        }

        aligned = sum(
            1 for t in bcs_benchmark_topics if t.lower() in kg_topics
        )
        return round(aligned / len(bcs_benchmark_topics), 4)

    def question_similarity_score(
        self,
        mcq_evaluations: List,
        bcs_benchmark_questions: Optional[List[str]] = None,
    ) -> Optional[float]:
        """
        QSS — lexical overlap between generated questions and
              historical BCS questions (Jaccard similarity, averaged).

        If no benchmark questions are provided, returns None.

        Parameters
        ----------
        mcq_evaluations : list
            Generated MCQ evaluation objects or dicts with a 'question' key.
        bcs_benchmark_questions : list of str, optional
            Tokenized historical BCS question texts.
        """
        if not bcs_benchmark_questions or not mcq_evaluations:
            return None

        benchmark_token_sets = [_qss_tokenize(q) for q in bcs_benchmark_questions]

        similarities = []
        for ev in mcq_evaluations:
            q_text = self._get_question_text(ev)
            if not q_text:
                continue
            q_tokens = _qss_tokenize(q_text)
            best = max(
                (
                    len(q_tokens & bt) / len(q_tokens | bt)
                    for bt in benchmark_token_sets
                    if q_tokens | bt
                ),
                default=0.0,
            )
            similarities.append(best)

        if not similarities:
            return None
        return round(sum(similarities) / len(similarities), 4)

    def difficulty_distribution_matching_score(
        self,
        mcq_evaluations: List,
        bcs_difficulty_dist: Optional[Dict[str, float]] = None,
    ) -> Optional[float]:
        """
        DDMS — how closely the generated difficulty distribution matches
               the historical BCS difficulty distribution.

        Uses Mean Absolute Error across difficulty levels.
        Lower DDMS = better match.

        Parameters
        ----------
        bcs_difficulty_dist : dict, optional
            Historical distribution e.g. {"easy": 0.25, "medium": 0.55, "hard": 0.20}
        """
        if not mcq_evaluations:
            return None

        target = bcs_difficulty_dist or {"easy": 0.25, "medium": 0.50, "hard": 0.25}

        counts: Counter = Counter()
        for ev in mcq_evaluations:
            d = self._get_difficulty(ev)
            if d:
                counts[d.lower()] += 1

        total = sum(counts.values()) or 1
        observed = {k: v / total for k, v in counts.items()}

        all_levels = set(target) | set(observed)
        mae = sum(
            abs(target.get(d, 0.0) - observed.get(d, 0.0))
            for d in all_levels
        ) / max(len(all_levels), 1)

        return round(mae, 4)

    def coverage_of_historical_patterns(
        self,
        mcq_evaluations: List,
    ) -> float:
        """
        CHP — fraction of common BCS question structure patterns
              that appear in the generated MCQ set.

        Patterns checked (from the PDF):
            dates, treaties, appointments, rankings, organizations,
            locations, one-fact recall (who/what/when/where).
        """
        import re

        PATTERNS = {
            "dates":         [r"\b\d{4}\b", r"সালে", r"সনে"],
            "treaties":      [r"চুক্তি", r"treaty", r"accord", r"agreement"],
            "appointments":  [r"নিযুক্ত", r"appoint", r"elected", r"chairman", r"director"],
            "rankings":      [r"rank", r"largest", r"smallest", r"first", r"প্রথম", r"বৃহত্তম"],
            "organizations": [r"UN|UNESCO|WHO|IMF|SAARC|OIC|WTO|NGO", r"সংস্থা", r"organization"],
            "locations":     [r"কোথায়", r"located|capital|district|division|city|নদী|পর্বত"],
            "one_fact_recall": [r"\bকে\b", r"\bকখন\b", r"\bকী\b", r"\bwho\b|\bwhat\b|\bwhen\b"],
        }

        covered_patterns = set()
        for ev in mcq_evaluations:
            q_text = self._get_question_text(ev) or ""
            for pattern_name, regexes in PATTERNS.items():
                if pattern_name in covered_patterns:
                    continue
                for rx in regexes:
                    if re.search(rx, q_text, re.IGNORECASE):
                        covered_patterns.add(pattern_name)
                        break

        return round(len(covered_patterns) / len(PATTERNS), 4)

    # ==================================================================
    # CATEGORY F — System-Level Metrics
    # ==================================================================

    def end_to_end_success_rate(self, rd: RuntimeData) -> Optional[float]:
        """
        EESR — fraction of pipeline runs that produced at least one
               accepted MCQ.

        EESR = successful_runs / total_runs
        """
        if rd.total_pipeline_runs == 0:
            return None
        return round(rd.successful_pipeline_runs / rd.total_pipeline_runs, 4)

    def cost_per_accepted_mcq(self, rd: RuntimeData) -> Optional[float]:
        """
        CAM — average USD cost to produce one accepted MCQ.

        CAM = total_cost / accepted_MCQs
        """
        accepted = len(rd.accepted_mcq_ids)
        if accepted == 0:
            return None
        return round(rd.total_cost_usd / accepted, 6)

    def latency_per_successful_output(self, rd: RuntimeData) -> Optional[float]:
        """
        LSO — average seconds to produce one accepted MCQ.

        LSO = total_seconds / accepted_MCQs
        """
        accepted = len(rd.accepted_mcq_ids)
        if accepted == 0:
            return None
        return round(rd.total_processing_seconds / accepted, 2)

    def adaptation_speed(self, rd: RuntimeData) -> Optional[int]:
        """
        AS — number of pipeline iterations needed to reach the target
             quality threshold.

        Returns the raw iteration count (lower = faster adaptation).
        Returns None if not tracked.
        """
        if rd.iterations_to_target == 0:
            return None
        return rd.iterations_to_target

    # ==================================================================
    # CATEGORY G — Composite Metric
    # ==================================================================

    def graph_grounded_learning_efficiency(
        self,
        mcq_evaluations: List,
        rd: RuntimeData,
    ) -> Optional[float]:
        """
        GGLE — composite metric balancing quality, grounding, and efficiency.

        GGLE = (accepted_MCQs × GA) / (cost × iterations)

        Higher GGLE = more accepted MCQs, better grounding, lower cost,
        fewer iterations needed.
        """
        accepted = len(rd.accepted_mcq_ids)
        ga       = self.grounding_accuracy(mcq_evaluations)
        cost     = rd.total_cost_usd
        iters    = max(rd.iterations_to_target, 1)

        if cost <= 0:
            # Cost unknown — report as None rather than divide by zero
            return None

        return round((accepted * ga) / (cost * iters), 6)

    # ==================================================================
    # Full Report
    # ==================================================================

    def full_report(
        self,
        dedup_report: Optional[Dict] = None,
        mcq_evaluations: Optional[List] = None,
        runtime_data: Optional[RuntimeData] = None,
        bcs_benchmark_topics: Optional[List[str]] = None,
        bcs_benchmark_questions: Optional[List[str]] = None,
        bcs_difficulty_dist: Optional[Dict[str, float]] = None,
        target_topics: Optional[List[str]] = None,
        accepted_mcq_fact_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Compute and return all metrics as a structured dict.

        Parameters
        ----------
        dedup_report : dict
            Output of FactQualityGate.deduplicate_graph().
        mcq_evaluations : list
            List of MCQEvaluation objects (or equivalent dicts).
        runtime_data : RuntimeData
            Live telemetry collected during the pipeline run.
        bcs_benchmark_topics : list of str
            Official BCS GK topic list for alignment checks.
        bcs_benchmark_questions : list of str, optional
            Historical BCS question texts for QSS computation.
        bcs_difficulty_dist : dict, optional
            Historical difficulty distribution for DDMS.
        target_topics : list of str
            Topics the system is supposed to cover (for TCS).
        accepted_mcq_fact_ids : list of str
            Fact IDs referenced by accepted MCQs (for FUS).

        Returns
        -------
        dict with keys:
            'metrics'  → nested dict of metric values
            'summary'  → human-readable text block
            'timestamp'→ ISO timestamp
        """
        dedup_report      = dedup_report      or {}
        mcq_evaluations   = mcq_evaluations   or []
        runtime_data      = runtime_data      or RuntimeData()
        bcs_benchmark_topics   = bcs_benchmark_topics   or []
        bcs_benchmark_questions = bcs_benchmark_questions or []
        target_topics     = target_topics     or []
        accepted_mcq_fact_ids  = accepted_mcq_fact_ids  or []

        rd = runtime_data

        metrics: Dict[str, Any] = {
            # A — KG & Fact Quality
            "FQS":  self.fact_quality_score(),
            "GCS":  self.graph_consistency_score(),
            "RRR":  self.redundancy_reduction_rate(dedup_report),
            "TCS":  self.topic_coverage_score(target_topics) if target_topics else None,
            "FUS":  self.fact_utilization_score(accepted_mcq_fact_ids),

            # B — Episodic Memory
            "MUS":  self.memory_utility_score(rd),
            "LES":  self.learning_efficiency_score(rd),
            "RIR":  self.regeneration_improvement_rate(rd),
            "FE":   self.forgetting_effectiveness(rd),

            # C — MCQ Quality
            "MSV":  self.mcq_structural_validity(mcq_evaluations),
            "GA":   self.grounding_accuracy(mcq_evaluations),
            "DQS":  self.distractor_quality_score(mcq_evaluations),
            "DCE":  self.difficulty_calibration_error(mcq_evaluations, rd),
            "CS":   self.clarity_score(mcq_evaluations),

            # D — Agentic Loop
            "JAS":  self.judge_agreement_score(rd),
            "SCE":  self.self_correction_efficiency(rd),
            "AES":  self.agent_efficiency_score(rd),
            "FEU":  self.failure_explanation_utility(rd),

            # E — BCS Alignment
            "TAS":  self.topic_alignment_score(bcs_benchmark_topics) if bcs_benchmark_topics else None,
            "QSS":  self.question_similarity_score(mcq_evaluations, bcs_benchmark_questions),
            "DDMS": self.difficulty_distribution_matching_score(mcq_evaluations, bcs_difficulty_dist),
            "CHP":  self.coverage_of_historical_patterns(mcq_evaluations),

            # F — System-Level
            "EESR": self.end_to_end_success_rate(rd),
            "CAM":  self.cost_per_accepted_mcq(rd),
            "LSO":  self.latency_per_successful_output(rd),
            "AS":   self.adaptation_speed(rd),

            # G — Composite
            "GGLE": self.graph_grounded_learning_efficiency(mcq_evaluations, rd),
        }

        summary = self._build_summary(metrics)

        return {
            "metrics":   metrics,
            "summary":   summary,
            "timestamp": datetime.datetime.now().isoformat(),
        }

    # ==================================================================
    # Helper — Build Summary Text
    # ==================================================================

    def _build_summary(self, metrics: Dict[str, Any]) -> str:
        sections = {
            "A — Knowledge Graph & Fact Quality": ["FQS", "GCS", "RRR", "TCS", "FUS"],
            "B — Episodic Memory":                ["MUS", "LES", "RIR", "FE"],
            "C — MCQ Quality":                    ["MSV", "GA", "DQS", "DCE", "CS"],
            "D — Agentic Loop":                   ["JAS", "SCE", "AES", "FEU"],
            "E — BCS Alignment":                  ["TAS", "QSS", "DDMS", "CHP"],
            "F — System-Level":                   ["EESR", "CAM", "LSO", "AS"],
            "G — Composite":                      ["GGLE"],
        }

        descriptions = {
            "FQS":  "Fact Quality Score (avg composite)",
            "GCS":  "Graph Consistency Score",
            "RRR":  "Redundancy Reduction Rate",
            "TCS":  "Topic Coverage Score",
            "FUS":  "Fact Utilization Score",
            "MUS":  "Memory Utility Score",
            "LES":  "Learning Efficiency Score",
            "RIR":  "Regeneration Improvement Rate",
            "FE":   "Forgetting Effectiveness",
            "MSV":  "MCQ Structural Validity",
            "GA":   "Grounding Accuracy",
            "DQS":  "Distractor Quality Score",
            "DCE":  "Difficulty Calibration Error (lower=better)",
            "CS":   "Clarity Score",
            "JAS":  "Judge Agreement Score",
            "SCE":  "Self-Correction Efficiency",
            "AES":  "Agent Efficiency Score",
            "FEU":  "Failure Explanation Utility",
            "TAS":  "Topic Alignment Score",
            "QSS":  "Question Similarity Score",
            "DDMS": "Difficulty Distribution Matching (lower=better)",
            "CHP":  "Coverage of Historical Patterns",
            "EESR": "End-to-End Success Rate",
            "CAM":  "Cost per Accepted MCQ (USD)",
            "LSO":  "Latency per Successful Output (s)",
            "AS":   "Adaptation Speed (iterations)",
            "GGLE": "Graph-Grounded Learning Efficiency",
        }

        lines = [
            "",
            "=" * 68,
            "  BCSBatighor GK — Evaluation Metrics Report",
            f"  Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 68,
        ]

        for section, keys in sections.items():
            lines.append(f"\n  [{section}]")
            for k in keys:
                val = metrics.get(k)
                desc = descriptions.get(k, k)
                val_str = f"{val:.4f}" if isinstance(val, float) else str(val) if val is not None else "N/A (data not provided)"
                lines.append(f"    {k:<6}  {val_str:<12}  {desc}")

        lines.append("\n" + "=" * 68 + "\n")
        return "\n".join(lines)

    # ==================================================================
    # Private Helpers — uniform access to MCQEvaluation or dict
    # ==================================================================

    def _get_dim(self, ev, dim: str) -> Optional[float]:
        """Extract a dimension score from MCQEvaluation or dict.

        Uses explicit ``is not None`` checks rather than ``or`` so that a
        genuine score of 0.0 (falsy) is preserved instead of silently falling
        through to a different value.
        """
        if hasattr(ev, "scores"):
            return getattr(ev.scores, dim, None)
        if isinstance(ev, dict):
            nested = ev.get("scores", {})
            if isinstance(nested, dict) and nested.get(dim) is not None:
                return nested.get(dim)
            return ev.get(dim)
        return None

    def _get_difficulty(self, ev) -> Optional[str]:
        if hasattr(ev, "difficulty"):
            return ev.difficulty
        if isinstance(ev, dict):
            return ev.get("difficulty")
        return None

    def _get_question_text(self, ev) -> Optional[str]:
        if hasattr(ev, "question"):
            return ev.question
        if isinstance(ev, dict):
            return ev.get("question") or ev.get("question_text")
        return None


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\nRunning bcs_metrics.py self-test...\n")

    # Build a minimal KG for testing
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))

    try:
        from bcs.pipeline.kg_builder import KnowledgeGraphBuilder
        from bcs.pipeline.fact_quality import FactQualityGate

        kg = KnowledgeGraphBuilder()

        # Insert sample facts
        f1 = kg.insert_fact_pipeline(
            "Sheikh Mujibur Rahman is the Father of the Nation of Bangladesh.",
            [["Sheikh Mujibur Rahman", "PERSON"]],
            [["Bangladesh", "COUNTRY"]],
            topic="History",
            source_url="https://banglapedia.org/history1",
            publisher="Banglapedia",
        )
        f2 = kg.insert_fact_pipeline(
            "The capital of Bangladesh is Dhaka.",
            [["Bangladesh", "COUNTRY"]],
            [["Dhaka", "CITY"]],
            topic="Geography",
            source_url="https://example.com/geo",
            publisher="The Daily Star",
        )
        f3 = kg.insert_fact_pipeline(
            "Bangladesh was established in 1971.",
            [["Bangladesh", "COUNTRY"]],
            [["1971", "EVENT"]],
            topic="History",
            source_url="https://example.com/hist",
            publisher="Prothom Alo",
        )

        gate = FactQualityGate(kg)
        gate.run_quality_pipeline(extraction_date="2025-06-01")
        dedup = gate.deduplicate_graph()

        # Fake MCQ evaluation dicts (normally MCQEvaluation objects)
        fake_evals = [
            {
                "question": "বাংলাদেশের রাজধানী কোথায়?",
                "difficulty": "easy",
                "scores": {"grounding_score": 0.90, "clarity_score": 0.85,
                           "distractor_score": 0.75, "format_score": 0.80},
                "passed": True,
            },
            {
                "question": "বাংলাদেশ কত সালে স্বাধীন হয়?",
                "difficulty": "medium",
                "scores": {"grounding_score": 0.82, "clarity_score": 0.78,
                           "distractor_score": 0.60, "format_score": 0.70},
                "passed": True,
            },
        ]

        rd = RuntimeData(
            total_pipeline_runs=3,
            successful_pipeline_runs=3,
            total_cost_usd=0.04,
            total_processing_seconds=45.0,
            total_agent_interactions=12,
            episodes_assisted=2,
            total_generations=5,
            regenerated_mcqs=2,
            improved_after_regen=1,
            initial_failures=3,
            corrected_failures=2,
            regen_with_feedback=2,
            regen_improved_with_feedback=1,
            low_quality_episodes_identified=4,
            low_quality_episodes_removed=3,
            human_judge_decisions=[True, True, False, True],
            auto_judge_decisions=[True, False, False, True],
            iterations_to_target=3,
            accepted_mcq_ids=["mcq_001", "mcq_002"],
        )

        evaluator = BCSMetricsEvaluator(kg=kg)

        report = evaluator.full_report(
            dedup_report=dedup,
            mcq_evaluations=fake_evals,
            runtime_data=rd,
            bcs_benchmark_topics=["History", "Geography", "Science", "Economy"],
            target_topics=["History", "Geography"],
            accepted_mcq_fact_ids=[f1, f2],
        )

        print(report["summary"])

    except ImportError as e:
        print(f"[self-test] Import skipped (run from project root): {e}")
        print("bcs_metrics.py loaded successfully. Import BCSMetricsEvaluator to use.")
