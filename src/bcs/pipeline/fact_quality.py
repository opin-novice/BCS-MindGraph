

import re
import datetime
import math
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

from tqdm import tqdm

from bcs.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_COMPOSITE_SCORE  = 0.40
SALVAGEABLE_THRESHOLD = 0.55
GOOD_THRESHOLD        = 0.75

SIMILARITY_THRESHOLD = 0.85

DEFAULT_SOURCE_RELIABILITY = 0.5
KNOWN_RELIABLE_PUBLISHERS = {
    "Prothom Alo", "The Daily Star", "Bangladesh Sangbad Sangstha",
    "Bangladesh Bureau of Statistics", "bdnews24.com", "Banglapedia",
    "Wikipedia", "UNESCO", "World Bank", "IMF", "WHO", "UN",
}

FRESHNESS_HALF_LIFE_YEARS = 3.0

# ---------------------------------------------------------------------------
# MCQ suitability patterns
# ---------------------------------------------------------------------------

WHO_PATTERNS = [
    r"\b(?:who|কে|কার|কাকে)\b",
    r"\b(?:president|prime minister|minister|leader|founder|father)\b",
    r"\b(?:রাষ্ট্রপতি|প্রধানমন্ত্রী|মন্ত্রী|নেতা|প্রতিষ্ঠাতা|জনক|সভাপতি|পরিচালক|চেয়ারম্যান)\b",
]

# NOTE: WHEN_PATTERNS intentionally kept broad so they detect ANY date signal.
# The real filtering happens inside _is_date_primary() — do NOT tighten
# these patterns, or facts with dates will not even reach the dominance check.
WHEN_PATTERNS = [
    r"\b(?:when|কখন|কবে|সালে|সনে)\b",
    r"\b(?:\d{4})\b",
    r"\b(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\b",
    r"\b(?:জানুয়ারি|ফেব্রুয়ারি|মার্চ|এপ্রিল|মে|জুন|জুলাই|"
    r"আগস্ট|সেপ্টেম্বর|অক্টোবর|নভেম্বর|ডিসেম্বর)\b",
]

NUMERIC_PATTERNS = [
    r"\b\d+(?:,\d{3})*(?:\.\d+)?\b",
    r"\b(?:largest|smallest|highest|lowest|most|least|first|second|third)\b",
    r"\b(?:সবচেয়ে|বৃহত্তম|ক্ষুদ্রতম|সর্বোচ্চ|সর্বনিম্ন|প্রথম|দ্বিতীয়|তৃতীয়)\b",
    r"\b(?:ranking|ranked|rank|position|number)\b",
]

WHERE_PATTERNS = [
    r"\b(?:where|কোথায়|কোন জায়গায়)\b",
    r"\b(?:located|situated|capital|city|district|division|country)\b",
    r"\b(?:অবস্থিত|রাজধানী|শহর|জেলা|বিভাগ|দেশ)\b",
]

# ---------------------------------------------------------------------------
# Dominance-check helpers
# (used by classify_mcq_suitability to suppress context-only when/where tags)
# ---------------------------------------------------------------------------

# Signals that indicate a PERSON is the primary testable element.
# No \b anchors — \b is unreliable with Bangla Unicode characters.
# These Bangla role-words are distinctive enough to match as substrings safely.
_PERSON_DOMINANCE = re.compile(
    r"(?:"
    r"রাষ্ট্রপতি|প্রধানমন্ত্রী|মন্ত্রী|নেতা|প্রতিষ্ঠাতা|জনক|সভাপতি|"
    r"সদস্য|চেয়ারম্যান|পরিচালক|সম্পাদক|সেনাপতি|গভর্নর|বিচারপতি|"
    r"president|minister|founder|chairman|director|"
    r"secretary|commander|governor|chief"
    r")",
    re.IGNORECASE,
)

# Signals that indicate a NUMERIC/MEASUREMENT fact is the primary element
# (article numbers, distances, percentages, counts — NOT bare years).
_NUMERIC_DOMINANCE = re.compile(
    r"(?:"
    r"কিলোমিটার|মিটার|হেক্টর|বর্গ|শতাংশ|কোটি|লক্ষ|হাজার|"
    r"অনুচ্ছেদ|ধারা|উপধারা|দফা|"
    r"km²|sq\.?\s*km|percent|%|MW|GW|"
    r"মেগাওয়াট|গিগাওয়াট"
    r")",
    re.IGNORECASE,
)

# Location-dominance: strong geographic markers (more specific than generic WHERE_PATTERNS)
_LOCATION_DOMINANCE = re.compile(
    r"(?:"
    r"রাজধানী|সদর দপ্তর|অবস্থিত|সীমানা|সীমান্ত|"
    r"capital|headquarters|located in|situated in|border"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# FactQualityGate Class
# ---------------------------------------------------------------------------

class FactQualityGate:
    """
    Fact Quality Gate for the BCSBatighor GK Knowledge Graph.

    Provides quality scoring, filtering, refinement, deduplication,
    and MCQ suitability analysis for facts in the Knowledge Graph.

    Usage
    -----
    from kg_builder import KnowledgeGraphBuilder
    from fact_quality import FactQualityGate

    kg = KnowledgeGraphBuilder()
    gate = FactQualityGate(kg)

    scores = gate.score_fact(
        text="Bangladesh achieved independence in 1971.",
        source_url="https://example.com",
        publisher="The Daily Star",
        extraction_date="2025-06-01"
    )
    """

    def __init__(self, kg_builder):
        self.kg = kg_builder

    # ------------------------------------------------------------------
    # 1. Fact Quality Scoring
    # ------------------------------------------------------------------

    def score_fact(
        self,
        text: str,
        source_url: str = "",
        publisher: str = "",
        extraction_date: Optional[str] = None,
        fact_id: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Compute quality scores for a single fact.

        Returns dict with keys:
            linguistic_clarity, factual_structure, source_reliability,
            temporal_freshness, composite_score, verdict
        """
        lc = self._score_linguistic_clarity(text)
        fs = self._score_factual_structure(text)
        sr = self._score_source_reliability(source_url, publisher)
        tf = self._score_temporal_freshness(extraction_date)

        composite = self._compute_composite(lc, fs, sr, tf)
        verdict   = self._get_verdict(composite)

        scores = {
            "linguistic_clarity": round(lc, 4),
            "factual_structure":  round(fs, 4),
            "source_reliability": round(sr, 4),
            "temporal_freshness": round(tf, 4),
            "composite_score":    round(composite, 4),
            "verdict":            verdict,
        }

        if fact_id and self.kg.graph.has_node(fact_id):
            for k, v in scores.items():
                if k != "verdict":
                    self.kg.update_fact_attribute(fact_id, k, v)
            self.kg.update_fact_attribute(fact_id, "quality_verdict", verdict)

        return scores

    def _score_linguistic_clarity(self, text: str) -> float:
        if not text or len(text.strip()) < 10:
            return 0.1

        text  = text.strip()
        score = 0.5

        word_count = len(text.split())
        if 5 <= word_count <= 60:
            score += 0.2
        elif word_count < 5:
            score -= 0.2
        elif word_count > 60:
            score -= 0.1

        if text[0].isupper() or ord(text[0]) >= 0x0980:
            score += 0.1
        if text[-1] in ".।!":
            score += 0.1

        special_ratio = (
            len(re.findall(r"[^a-zA-Z0-9\s\u0980-\u09FF.,;:!?'\"-]", text))
            / max(len(text), 1)
        )
        if special_ratio > 0.15:
            score -= 0.2

        if text.isupper():
            score -= 0.15

        return max(0.0, min(1.0, score))

    def _score_factual_structure(self, text: str) -> float:
        if not text or len(text.strip()) < 10:
            return 0.1

        text  = text.strip()
        score = 0.5

        assertion_verbs = (
            r"\b(?:is|are|was|were|has|have|had|became|established|located|"
            r"known|called|founded|divided|achieved|produced)\b"
        )
        bangla_assertions = r"(?:হয়|হয়েছে|ছিল|ছিলেন|হলেন|করেন|করেছেন|অবস্থিত|প্রতিষ্ঠিত|গঠিত)"

        if re.search(assertion_verbs, text, re.IGNORECASE) or \
            re.search(bangla_assertions, text):
            score += 0.2

        bangla_entities = re.findall(r'[\u0980-\u09FF]{3,}', text)
        if len(bangla_entities) >= 1:
          score += 0.1
        if len(bangla_entities) >= 3:
          score += 0.1

        named_entities = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
        if len(named_entities) >= 1:
            score += 0.1
        if len(named_entities) >= 2:
            score += 0.1

        if text.endswith("?") or text.startswith(
            ("Who", "What", "When", "Where", "How", "Why")
        ):
            score -= 0.3
        if text.startswith(("Do", "Please", "Tell", "Find", "List")):
            score -= 0.2

        if re.search(r"\d+", text):
            score += 0.05

        return max(0.0, min(1.0, score))

    def _score_source_reliability(self, source_url: str, publisher: str) -> float:
        score = DEFAULT_SOURCE_RELIABILITY

        if publisher:
            for known in KNOWN_RELIABLE_PUBLISHERS:
                if known.lower() in publisher.lower():
                    score = 0.9
                    break

        if source_url:
            score = max(score, 0.5)
            if re.search(r"\.gov\.bd|\.gov\b", source_url):
                score = max(score, 0.85)
            if re.search(r"\.edu|\.ac\.bd|\.ac\b", source_url):
                score = max(score, 0.80)
            if "wikipedia.org" in source_url:
                score = max(score, 0.70)
        else:
            score = min(score, 0.3)

        return max(0.0, min(1.0, score))

    def _score_temporal_freshness(self, extraction_date: Optional[str] = None) -> float:
        if not extraction_date:
            return 0.7
        try:
            ext_date = datetime.datetime.fromisoformat(extraction_date)
        except ValueError:
            return 0.7

        now       = datetime.datetime.now()
        age_years = (now - ext_date).days / 365.25
        if age_years < 0:
            return 1.0

        freshness = math.exp(-0.693 * age_years / FRESHNESS_HALF_LIFE_YEARS)
        return max(0.0, min(1.0, round(freshness, 4)))

    def _compute_composite(self, lc: float, fs: float, sr: float, tf: float) -> float:
        """Weighted composite: LC 25% | FS 30% | SR 25% | TF 20%."""
        return 0.25 * lc + 0.30 * fs + 0.25 * sr + 0.20 * tf

    def _get_verdict(self, composite: float) -> str:
        if composite >= GOOD_THRESHOLD:
            return "ACCEPT"
        elif composite >= SALVAGEABLE_THRESHOLD:
            return "REFINE"
        elif composite >= MIN_COMPOSITE_SCORE:
            return "SALVAGEABLE"
        else:
            return "REJECT"

    # ------------------------------------------------------------------
    # 2. Fact Refinement and Rejection
    # ------------------------------------------------------------------

    def refine_fact_text(self, text: str) -> Tuple[str, str]:
        """
        Attempt to salvage a fact by cleaning it.
        Returns (refined_text, status) where status is 'REFINED' or 'UNCHANGED'.
        """
        original = text
        refined  = text.strip()

        refined = re.sub(r"^[^\w\u0980-\u09FF]+", "", refined)
        refined = re.sub(r"[^\w\u0980-\u09FF.।!]+$", "", refined)
        refined = re.sub(r"\s{2,}", " ", refined)

        sentences     = re.split(r"[.।]", refined)
        seen          = set()
        unique_sents  = []
        for s in sentences:
            s_clean = s.strip().lower()
            if s_clean and s_clean not in seen:
                seen.add(s_clean)
                unique_sents.append(s.strip())
        refined = ". ".join(unique_sents)
        if refined and refined[-1] not in ".।":
            refined += "."

        if refined and refined[0].isalpha() and refined[0].islower():
            refined = refined[0].upper() + refined[1:]

        status = "REFINED" if refined != original else "UNCHANGED"
        return refined, status

    def evaluate_and_act(
        self,
        fact_id: str,
        source_url: str = "",
        publisher: str = "",
        extraction_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate a fact already in the KG and take action:
        ACCEPT → keep as-is  |  REFINE → rewrite  |  REJECT → remove
        """
        data = self.kg.get_fact_data(fact_id)
        if data is None:
            return {"fact_id": fact_id, "action": "NOT_FOUND",
                    "original_text": "", "final_text": "", "scores": {}}

        text   = data.get("text", "")
        scores = self.score_fact(text, source_url, publisher, extraction_date, fact_id)
        verdict = scores["verdict"]

        result = {
            "fact_id":       fact_id,
            "action":        verdict,
            "original_text": text,
            "final_text":    text,
            "scores":        scores,
        }

        if verdict == "ACCEPT":
            self.kg.update_fact_attribute(fact_id, "quality_verdict", "ACCEPT")

        elif verdict in ("REFINE", "SALVAGEABLE"):
            refined_text, refine_status = self.refine_fact_text(text)
            if refine_status == "REFINED":
                new_scores = self.score_fact(
                    refined_text, source_url, publisher, extraction_date, fact_id
                )
                if new_scores["composite_score"] >= MIN_COMPOSITE_SCORE:
                    self.kg.update_fact_attribute(fact_id, "text", refined_text)
                    self.kg.update_fact_attribute(fact_id, "quality_verdict", "REFINED_ACCEPT")
                    result["action"]     = "REFINED_ACCEPT"
                    result["final_text"] = refined_text
                    result["scores"]     = new_scores
                else:
                    self.kg.remove_fact(fact_id)
                    result["action"] = "REJECT_AFTER_REFINE"
            else:
                if scores["composite_score"] >= MIN_COMPOSITE_SCORE:
                    self.kg.update_fact_attribute(fact_id, "quality_verdict", "SALVAGEABLE_ACCEPT")
                    result["action"] = "SALVAGEABLE_ACCEPT"
                else:
                    self.kg.remove_fact(fact_id)
                    result["action"] = "REJECT"

        elif verdict == "REJECT":
            self.kg.remove_fact(fact_id)
            result["action"] = "REJECT"

        return result

    # ------------------------------------------------------------------
    # 3. Graph-Aware Filtering (Redundancy and Deduplication)
    # ------------------------------------------------------------------

    def find_duplicate_facts(self) -> List[Tuple[str, str, float]]:
        """
        Detect near-duplicate facts via token-level Jaccard similarity.
        Returns list of (fact_id_1, fact_id_2, similarity_score).
        """
        fact_nodes = [
            (nid, d.get("text", ""), set(self._tokenize(d.get("text", ""))))
            for nid, d in self.kg.graph.nodes(data=True)
            if d.get("type") == "FACT"
        ]

        duplicates = []
        n = len(fact_nodes)
        for i in range(n):
            for j in range(i + 1, n):
                id_a, _, tok_a = fact_nodes[i]
                id_b, _, tok_b = fact_nodes[j]
                if not tok_a or not tok_b:
                    continue
                intersection = tok_a & tok_b
                union        = tok_a | tok_b
                sim = len(intersection) / len(union) if union else 0.0
                if sim >= SIMILARITY_THRESHOLD:
                    duplicates.append((id_a, id_b, round(sim, 4)))

        return duplicates

    def merge_duplicate_facts(self, fact_id_keep: str, fact_id_remove: str) -> bool:
        """
        Keep one fact, transfer edges from the duplicate, remove duplicate.
        Returns True if merge succeeded.
        """
        if not self.kg.graph.has_node(fact_id_keep):
            return False
        if not self.kg.graph.has_node(fact_id_remove):
            return False

        for src, _, data in list(self.kg.graph.in_edges(fact_id_remove, data=True)):
            rel = data.get("relation", "RELATED")
            if not self.kg.graph.has_edge(src, fact_id_keep):
                self.kg.link(src, fact_id_keep, rel)

        for _, tgt, data in list(self.kg.graph.out_edges(fact_id_remove, data=True)):
            rel = data.get("relation", "RELATED")
            if not self.kg.graph.has_edge(fact_id_keep, tgt):
                self.kg.link(fact_id_keep, tgt, rel)

        removed_text    = self.kg.graph.nodes[fact_id_remove].get("text", "")
        existing_merged = self.kg.graph.nodes[fact_id_keep].get("merged_from", [])
        existing_merged.append({"id": fact_id_remove, "text": removed_text})
        self.kg.update_fact_attribute(fact_id_keep, "merged_from", existing_merged)

        self.kg.remove_fact(fact_id_remove)
        return True

    def deduplicate_graph(self) -> Dict[str, Any]:
        """Run full deduplication on the KG. Returns a report dict."""
        duplicates   = self.find_duplicate_facts()
        merged_count = 0
        details      = []
        removed      = set()

        for id_a, id_b, sim in duplicates:
            if id_a in removed or id_b in removed:
                continue

            data_a  = self.kg.get_fact_data(id_a) or {}
            data_b  = self.kg.get_fact_data(id_b) or {}
            score_a = data_a.get("composite_score", data_a.get("source_reliability", 0.5))
            score_b = data_b.get("composite_score", data_b.get("source_reliability", 0.5))

            keep, remove = (id_a, id_b) if score_a >= score_b else (id_b, id_a)

            if self.merge_duplicate_facts(keep, remove):
                merged_count += 1
                removed.add(remove)
                details.append({"kept": keep, "removed": remove, "similarity": sim})

        log.debug("Found %d duplicate pair(s), merged %d.", len(duplicates), merged_count)
        return {
            "duplicates_found": len(duplicates),
            "pairs_merged":     merged_count,
            "details":          details,
        }

    # ------------------------------------------------------------------
    # 4. MCQ Suitability Marking  (FIXED — dominance suppression)
    # ------------------------------------------------------------------

    def _is_date_primary(self, text: str) -> bool:
        """
        Returns True only when the date/year is the PRIMARY testable element,
        not just contextual background.

        Logic: if the fact also contains a strong person signal OR a
        non-date numeric signal (distance, percentage, count, article number),
        the date is contextual → suppress when_question tagging.

        Examples
        --------
        WHEN-primary:
          "বাংলাদেশের স্বাধীনতা দিবস ২৬ মার্চ ১৯৭১।"
          "আন্তর্জাতিক মাতৃভাষা দিবস ১৯৯৯ সালে স্বীকৃত হয়।"

        NOT when-primary (date is context):
          "মওলানা ভাসানী ১৯৪৯ সালে আওয়ামী লীগের প্রথম সভাপতি হন।"
          "পদ্মা সেতু ২০২২ সালে চালু হয় এবং এর দৈর্ঘ্য ৬.১৫ কিলোমিটার।"
        """
        has_person  = bool(_PERSON_DOMINANCE.search(text))
        has_numeric = bool(_NUMERIC_DOMINANCE.search(text))
        return not has_person and not has_numeric

    def _is_location_primary(self, text: str) -> bool:
        """
        Returns True only when the geographic location is the PRIMARY
        testable element, not merely mentioned alongside a person or number.

        Examples
        --------
        WHERE-primary:
          "বাংলাদেশের রাজধানী ঢাকায় অবস্থিত।"
          "সুন্দরবন বাগেরহাট জেলায় অবস্থিত।"

        NOT where-primary:
          "শেখ মুজিবুর রহমান ঢাকায় জন্মগ্রহণ করেন।"  ← person is primary
          "পদ্মা সেতু মুন্সিগঞ্জ জেলায় অবস্থিত এবং দৈর্ঘ্য ৬.১৫ কিলোমিটার।"
        """
        has_person  = bool(_PERSON_DOMINANCE.search(text))
        has_numeric = bool(_NUMERIC_DOMINANCE.search(text))
        # Location must be the dominant signal AND no competing strong signal
        has_location_signal = bool(_LOCATION_DOMINANCE.search(text))
        return has_location_signal and not has_person and not has_numeric

    def classify_mcq_suitability(self, fact_id: str) -> Dict[str, Any]:
        """
        Classify a fact's suitability for different MCQ types.

        FIX v2: Applies dominance suppression for when_question and
        where_question. A fact is only tagged when_suitable if the
        date/year is the PRIMARY testable element (not just present
        as background context). Same logic for where_question.

        This prevents the mcq_when_suitable count from inflating to
        40-50% of all facts, which was corrupting answer_dist.

        Tags: who_question, when_question, where_question,
              numeric_ranking, poor_candidate
        """
        data = self.kg.get_fact_data(fact_id)
        if data is None:
            return {"fact_id": fact_id, "text": "", "suitable_for": [],
                    "tags": {}, "mcq_readiness": 0.0}

        text = data.get("text", "")

        # ── Step 1: raw pattern match for each type ─────────────────
        raw_who     = self._matches_patterns(text, WHO_PATTERNS)
        raw_when    = self._matches_patterns(text, WHEN_PATTERNS)
        raw_where   = self._matches_patterns(text, WHERE_PATTERNS)
        raw_numeric = self._matches_patterns(text, NUMERIC_PATTERNS)

        # ── Step 2: dominance suppression ───────────────────────────
        # when_question: only tag if date is the PRIMARY element
        if raw_when:
            when_suitable = self._is_date_primary(text)
        else:
            when_suitable = False

        # where_question: only tag if location is the PRIMARY element
        if raw_where:
            where_suitable = self._is_location_primary(text)
        else:
            where_suitable = False

        # who_question and numeric_ranking: no suppression needed —
        # these are exactly what BCS over-indexes on, so we want them.
        who_suitable     = raw_who
        numeric_suitable = raw_numeric

        tags = {
            "who_question":   who_suitable,
            "when_question":  when_suitable,
            "where_question": where_suitable,
            "numeric_ranking": numeric_suitable,
        }

        suitable_for = [k for k, v in tags.items() if v]

        # ── Step 3: MCQ readiness score ─────────────────────────────
        composite = data.get("composite_score", data.get("source_reliability", 0.5))
        suitability_bonus = min(len(suitable_for) * 0.1, 0.3)
        mcq_readiness = min(1.0, composite * 0.7 + suitability_bonus + 0.1)

        if not suitable_for:
            tags["poor_candidate"] = True
            mcq_readiness *= 0.5

        # ── Step 4: Persist to KG node ──────────────────────────────
        self.kg.update_fact_attribute(fact_id, "mcq_tags",        tags)
        self.kg.update_fact_attribute(fact_id, "mcq_suitable_for", suitable_for)
        self.kg.update_fact_attribute(fact_id, "mcq_readiness",    round(mcq_readiness, 4))

        return {
            "fact_id":      fact_id,
            "text":         text,
            "suitable_for": suitable_for,
            "tags":         tags,
            "mcq_readiness": round(mcq_readiness, 4),
        }

    def classify_all_facts(self) -> List[Dict[str, Any]]:
        """
        Run MCQ suitability classification on all Fact nodes in the KG.
        Returns list of classification result dicts.
        """
        results = []
        for node_id, data in self.kg.graph.nodes(data=True):
            if data.get("type") == "FACT":
                results.append(self.classify_mcq_suitability(node_id))
        log.debug("Classified %d fact(s) for MCQ suitability.", len(results))
        return results

    # ------------------------------------------------------------------
    # 5. Full Quality Pipeline
    # ------------------------------------------------------------------

    def run_quality_pipeline(
        self,
        source_url: str = "",
        publisher: str = "",
        extraction_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete fact quality pipeline on all facts in the KG:

        1. Score all facts
        2. Refine or reject low-quality facts
        3. Deduplicate the graph
        4. Classify MCQ suitability for surviving facts
        """
        log.debug("Fact Quality Pipeline — Full Run")

        # Step 1: Score
        log.debug("[Step 1] Scoring all facts...")
        scoring_results = []
        fact_nodes = [
            (nid, d) for nid, d in self.kg.graph.nodes(data=True)
            if d.get("type") == "FACT"
        ]
        for fact_id, data in tqdm(fact_nodes, desc="Scoring facts", unit="fact"):
            fact_source    = ""
            fact_publisher = publisher
            for _, tgt, edata in self.kg.graph.edges(fact_id, data=True):
                if edata.get("relation") == "SUPPORTED_BY":
                    src_data = self.kg.get_fact_data(tgt)
                    if src_data:
                        fact_source    = src_data.get("url", source_url)
                        fact_publisher = src_data.get("publisher", publisher)
                    break

            scores = self.score_fact(
                text=data.get("text", ""),
                source_url=fact_source or source_url,
                publisher=fact_publisher,
                extraction_date=extraction_date,
                fact_id=fact_id,
            )
            scoring_results.append({"fact_id": fact_id, **scores})

        log.debug("Scored %d fact(s).", len(scoring_results))

        # Step 2: Act
        log.debug("[Step 2] Evaluating and acting on facts...")
        action_results = []
        current_facts  = [
            nid for nid, d in self.kg.graph.nodes(data=True)
            if d.get("type") == "FACT"
        ]
        for fact_id in tqdm(current_facts, desc="Evaluating facts", unit="fact"):
            result = self.evaluate_and_act(fact_id, source_url, publisher, extraction_date)
            action_results.append(result)

        accepted = sum(1 for r in action_results if "ACCEPT" in r["action"])
        rejected = sum(1 for r in action_results if "REJECT" in r["action"])
        refined  = sum(1 for r in action_results if "REFINE" in r["action"])
        log.debug("Accepted: %d | Refined: %d | Rejected: %d", accepted, refined, rejected)

        # Step 3: Deduplicate
        log.debug("[Step 3] Deduplicating graph...")
        dedup_report = self.deduplicate_graph()

        # Step 4: MCQ classification
        log.debug("[Step 4] Classifying MCQ suitability...")
        mcq_results = self.classify_all_facts()

        who_count     = sum(1 for r in mcq_results if "who_question"    in r["suitable_for"])
        when_count    = sum(1 for r in mcq_results if "when_question"   in r["suitable_for"])
        where_count   = sum(1 for r in mcq_results if "where_question"  in r["suitable_for"])
        numeric_count = sum(1 for r in mcq_results if "numeric_ranking" in r["suitable_for"])
        poor_count    = sum(1 for r in mcq_results if not r["suitable_for"])

        report = {
            "scoring":          scoring_results,
            "actions":          action_results,
            "deduplication":    dedup_report,
            "mcq_classification": mcq_results,
            "summary": {
                "total_facts_scored":   len(scoring_results),
                "accepted":             accepted,
                "refined":              refined,
                "rejected":             rejected,
                "duplicates_merged":    dedup_report["pairs_merged"],
                "facts_remaining":      sum(
                    1 for _, d in self.kg.graph.nodes(data=True)
                    if d.get("type") == "FACT"
                ),
                "mcq_who_suitable":     who_count,
                "mcq_when_suitable":    when_count,
                "mcq_where_suitable":   where_count,
                "mcq_numeric_suitable": numeric_count,
                "mcq_poor_candidates":  poor_count,
            },
        }

        for k, v in report["summary"].items():
            log.debug("%-25s: %s", k, v)

        return report

    # ------------------------------------------------------------------
    # 6. Quality Report Generation
    # ------------------------------------------------------------------

    def generate_quality_report(self) -> str:
        """Generate a human-readable quality report for all facts in the KG."""
        lines = [
            "=" * 65,
            "  FACT QUALITY REPORT — BCSBatighor GK Knowledge Graph",
            "  Generated: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "=" * 65,
        ]

        fact_nodes = [
            (nid, d) for nid, d in self.kg.graph.nodes(data=True)
            if d.get("type") == "FACT"
        ]

        if not fact_nodes:
            lines.append("\n  No facts found in the Knowledge Graph.\n")
            return "\n".join(lines)

        verdict_groups = defaultdict(list)
        for fid, data in fact_nodes:
            verdict = data.get("quality_verdict", "UNSCORED")
            verdict_groups[verdict].append((fid, data))

        for verdict in [
            "ACCEPT", "REFINED_ACCEPT", "SALVAGEABLE_ACCEPT",
            "REFINE", "SALVAGEABLE", "REJECT", "UNSCORED",
        ]:
            facts = verdict_groups.get(verdict, [])
            if not facts:
                continue
            lines.append(f"\n--- {verdict} ({len(facts)} fact(s)) ---")
            for fid, data in facts:
                text      = data.get("text", "")[:80]
                composite = data.get("composite_score", "N/A")
                mcq_tags  = data.get("mcq_suitable_for", [])
                lines.append(f"  [{fid[:16]}] score={composite} | mcq={mcq_tags}")
                full = data.get("text", "")
                lines.append(f'    "{text}..."' if len(full) > 80 else f'    "{text}"')

        lines.append("\n--- MCQ SUITABILITY SUMMARY ---")
        tag_counts = defaultdict(int)
        for _, data in fact_nodes:
            for tag in data.get("mcq_suitable_for", []):
                tag_counts[tag] += 1
            if not data.get("mcq_suitable_for"):
                tag_counts["poor_candidate"] += 1

        for tag, count in sorted(tag_counts.items()):
            lines.append(f"  {tag:<20}: {count} fact(s)")

        lines.append("\n" + "=" * 65)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into lowercase words for Jaccard comparison."""
        return re.findall(r"\b\w+\b", text.lower())

    def _matches_patterns(self, text: str, patterns: List[str]) -> bool:
        """Check if text matches any of the given regex patterns."""
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from bcs.pipeline.kg_builder import KnowledgeGraphBuilder

    print("\n" + "=" * 60)
    print("  fact_quality.py — Self Test (v2 with dominance suppression)")
    print("=" * 60)

    kg = KnowledgeGraphBuilder()

    # Facts designed to test dominance suppression:
    test_facts = [
        # Person-dominated → when_suitable should be FALSE (person is primary)
        ("মওলানা ভাসানী ১৯৪৯ সালে আওয়ামী লীগের প্রথম সভাপতি হন।",
         "History", "expect: who_question, NOT when_question"),
        # Date-dominated → when_suitable should be TRUE
        ("বাংলাদেশের স্বাধীনতা দিবস ২৬ মার্চ ১৯৭১।",
         "History", "expect: when_question"),
        # Numeric-dominated → when_suitable FALSE, numeric TRUE
        ("পদ্মা সেতু ২০২২ সালে চালু হয় এবং এর দৈর্ঘ্য ৬.১৫ কিলোমিটার।",
         "Infrastructure", "expect: numeric_ranking, NOT when_question"),
        # Location-primary → where_suitable TRUE
        ("বাংলাদেশের রাজধানী ঢাকায় অবস্থিত।",
         "Geography", "expect: where_question"),
        # Person + location → where_suitable FALSE
        ("শেখ মুজিবুর রহমান ঢাকায় জন্মগ্রহণ করেছিলেন।",
         "History", "expect: who_question, NOT where_question"),
        # Pure numeric
        ("বাংলাদেশের সংবিধানের ২৫ নম্বর অনুচ্ছেদে পররাষ্ট্র নীতি বর্ণিত।",
         "Constitution", "expect: numeric_ranking"),
    ]

    gate = FactQualityGate(kg)

    for text, topic, expectation in test_facts:
        fid = kg.insert_fact_pipeline(
            fact_text=text,
            subject_entities=[],
            object_entities=[],
            topic=topic,
            source_url="https://test.example.com",
            publisher="Test",
        )
        result = gate.classify_mcq_suitability(fid)
        print(f"\n  Text    : {text[:70]}")
        print(f"  Tagged  : {result['suitable_for']}")
        print(f"  {expectation}")

    print("\n  Self-test complete.\n")