"""
intent_builder.py
=================
Step 2: Intent / Blueprint Builder
BCSBatighor GK Knowledge Graph System

Role: Pipeline Step 2 (no single owner — shared utility)

Responsibilities
----------------
1. Accept a NormalizedInput (from Step 1).
2. Extract intent: topic, sub-topic, entities, time hints.
3. Produce a Blueprint dict that drives Steps 3–5 and the KG.
4. Provide a rule-based fallback when no LLM API key is available.
5. Cache blueprints to avoid redundant LLM calls.

Integrates with: input_normalizer.py (Step 1), web_scraper.py (Steps 3-5).
"""
import os
import requests
import json
import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
load_dotenv()

from bcs.pipeline.input_normalizer import BANGLA_DIGIT_MAP
from bcs.rate_limiter import wait_for_rate_limit

log = logging.getLogger("intent_builder")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Known BCS topics (used by rule-based fallback)
# ---------------------------------------------------------------------------

TOPIC_KEYWORD_MAP: Dict[str, List[str]] = {
    "History": [
        "মুক্তিযুদ্ধ", "স্বাধীনতা", "independence", "liberation", "war",
        "মুজিব", "mujib", "বঙ্গবন্ধু", "bangabandhu", "1971",
        "ভাষা আন্দোলন", "language movement", "partition", "দেশভাগ",
        "রাজনীতি", "politics", "সংবিধান", "constitution",
    ],
    "Geography": [
        "রাজধানী", "capital", "নদী", "river", "পাহাড়", "mountain",
        "বন", "forest", "সমুদ্র", "ocean", "sea", "জেলা", "district",
        "বিভাগ", "division", "সীমানা", "border", "অবস্থান", "located",
        "সুন্দরবন", "sundarbans", "পদ্মা", "padma", "মেঘনা", "যমুনা",
    ],
    "Science": [
        "বিজ্ঞান", "science", "আবিষ্কার", "invention", "প্রযুক্তি",
        "technology", "পদার্থ", "physics", "রসায়ন", "chemistry",
        "জীববিজ্ঞান", "biology", "গণিত", "mathematics",
    ],
    "Economy": [
        "অর্থনীতি", "economy", "জিডিপি", "gdp", "বাজেট", "budget",
        "রপ্তানি", "export", "আমদানি", "import", "ব্যাংক", "bank",
        "মুদ্রা", "currency", "টাকা", "taka", "বিনিয়োগ", "investment",
    ],
    "Culture": [
        "সংস্কৃতি", "culture", "সাহিত্য", "literature", "কবি", "poet",
        "লেখক", "writer", "শিল্প", "art", "সংগীত", "music",
        "রবীন্দ্রনাথ", "tagore", "নজরুল", "nazrul", "উৎসব", "festival",
    ],
    "International": [
        "জাতিসংঘ", "united nations", "un", "বিশ্ব", "world",
        "আন্তর্জাতিক", "international", "চুক্তি", "treaty", "সম্মেলন",
        "summit", "পুরস্কার", "award", "nobel", "নোবেল",
    ],
    "Government": [
        "সরকার", "government", "প্রধানমন্ত্রী", "prime minister",
        "রাষ্ট্রপতি", "president", "মন্ত্রণালয়", "ministry",
        "সংসদ", "parliament", "নির্বাচন", "election", "আইন", "law",
    ],
    "Infrastructure": [
        "সেতু", "bridge", "সড়ক", "road", "রেলপথ", "railway",
        "বিমানবন্দর", "airport", "বন্দর", "port", "বিদ্যুৎ", "power",
        "পদ্মা সেতু", "padma bridge", "মেট্রো", "metro",
    ],
}

# Question type patterns
QTYPE_PATTERNS = {
    "who_question":      [r'\b(কে|কার|কাকে|who|কোন ব্যক্তি)\b'],
    "when_question":     [r'\b(কখন|কবে|কত সালে|when|কোন সালে|কোন তারিখে)\b', r'\b\d{4}\b'],
    "where_question":    [r'\b(কোথায়|কোন জায়গায়|where|কোন দেশে|কোন শহরে)\b'],
    "numeric_ranking":   [r'\b(কত|কতটি|কতজন|কতটুকু|কততম|how many|how much|কোনটি বৃহত্তম|largest|smallest|first|second)\b'],
    "what_question":     [r'\b(কি|কী|what|কীসের|কোনটি)\b'],
    "why_how_question":  [r'\b(কেন|কীভাবে|why|how)\b'],
}

INTENT_TYPES = [
    "factual_recall",    # Direct fact retrieval (who/what/when/where)
    "comparison",        # Compare two or more things
    "explanation",       # Why/how questions
    "enumeration",       # List things (how many, what are the...)
    "definition",        # What is X?
]


# ---------------------------------------------------------------------------
# Blueprint data class
# ---------------------------------------------------------------------------

@dataclass
class Blueprint:
    """
    Structured extraction result from Step 2.

    Consumed by:
    - web_scraper.py (Step 3) to generate search queries
    - pipeline.py to route to correct KG topics
    - episodic_store.py to record intent/blueprint

    Attributes
    ----------
    normalized_question : str
        The cleaned question text from Step 1.
    intent : str
        Detected intent class (e.g. 'factual_recall').
    question_type : str
        MCQ-aligned type (e.g. 'who_question').
    topic : str
        Primary KG topic (e.g. 'History').
    sub_topic : str
        More specific sub-topic if detected.
    entities : list of str
        Named entities mentioned (persons, orgs, places, etc.).
    time_hints : list of str
        Year or date mentions (e.g. ['1971', 'মার্চ']).
    search_keywords : list of str
        Key terms for generating web queries.
    bangla_query : str
        Suggested Bangla web search query.
    english_query : str
        Suggested English web search query.
    confidence : float
        0.0–1.0 confidence in the extraction (1.0 = rule-based certain).
    extraction_method : str
        'llm' | 'rule_based'
    """
    normalized_question: str
    intent:              str = "factual_recall"
    question_type:       str = "what_question"
    topic:               str = "General"
    sub_topic:           str = ""
    entities:            List[str] = field(default_factory=list)
    time_hints:          List[str] = field(default_factory=list)
    search_keywords:     List[str] = field(default_factory=list)
    bangla_query:        str = ""
    english_query:       str = ""
    confidence:          float = 0.8
    extraction_method:   str = "rule_based"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __str__(self):
        return (
            f"Blueprint(\n"
            f"  intent          = {self.intent}\n"
            f"  question_type   = {self.question_type}\n"
            f"  topic           = {self.topic}\n"
            f"  sub_topic       = {self.sub_topic}\n"
            f"  entities        = {self.entities}\n"
            f"  time_hints      = {self.time_hints}\n"
            f"  bangla_query    = \"{self.bangla_query}\"\n"
            f"  english_query   = \"{self.english_query}\"\n"
            f"  confidence      = {self.confidence}\n"
            f"  method          = {self.extraction_method}\n"
            f")"
        )


# ---------------------------------------------------------------------------
# Rule-based extractor (fast, no API key needed)
# ---------------------------------------------------------------------------

class RuleBasedExtractor:
    """
    Heuristic intent and blueprint extraction.
    Used when no LLM API key is configured.
    """

    def extract(self, normalized_text: str) -> Blueprint:
        text  = normalized_text
        lower = text.lower()

        question_type = self._detect_question_type(lower)
        topic, sub_topic = self._detect_topic(lower)
        intent = self._detect_intent(question_type, lower)
        entities = self._extract_entities(text)
        time_hints = self._extract_time_hints(text)
        keywords = self._extract_keywords(text, entities, time_hints)

        # Build search queries
        # Keep question text but strip trailing '?'
        base = text.rstrip('?.।').strip()
        bangla_query  = f"{base} বাংলাদেশ"
        english_query = f"{' '.join(keywords[:5])} Bangladesh BCS"

        return Blueprint(
            normalized_question=normalized_text,
            intent=intent,
            question_type=question_type,
            topic=topic,
            sub_topic=sub_topic,
            entities=entities,
            time_hints=time_hints,
            search_keywords=keywords,
            bangla_query=bangla_query,
            english_query=english_query,
            confidence=0.75,
            extraction_method="rule_based",
        )

    # --- helpers ---

    def _detect_question_type(self, lower: str) -> str:
        for qtype, patterns in QTYPE_PATTERNS.items():
            for p in patterns:
                if re.search(p, lower):
                    return qtype
        return "what_question"

    def _detect_topic(self, lower: str) -> Tuple[str, str]:
        best_topic = "General"
        best_count = 0
        for topic, keywords in TOPIC_KEYWORD_MAP.items():
            count = sum(1 for kw in keywords if kw.lower() in lower)
            if count > best_count:
                best_count = count
                best_topic = topic
        sub_topic = ""
        # Simple sub-topic heuristic
        if best_topic == "History":
            if "১৯৭১" in lower or "1971" in lower or "মুক্তিযুদ্ধ" in lower:
                sub_topic = "Liberation War"
            elif "ভাষা" in lower or "language" in lower:
                sub_topic = "Language Movement"
        elif best_topic == "Geography":
            if "নদী" in lower or "river" in lower:
                sub_topic = "Rivers"
            elif "রাজধানী" in lower or "capital" in lower:
                sub_topic = "Capitals"
        return best_topic, sub_topic

    def _detect_intent(self, question_type: str, lower: str) -> str:
        if question_type in ("why_how_question",):
            return "explanation"
        if question_type in ("numeric_ranking",):
            return "enumeration"
        if "তুলনা" in lower or "পার্থক্য" in lower or "compare" in lower:
            return "comparison"
        if "কি" in lower or "কী" in lower or "what is" in lower:
            return "definition"
        return "factual_recall"

    def _extract_entities(self, text: str) -> List[str]:
        """Extract capitalized English words and common Bangla proper nouns."""
        entities = []
        # Capitalized English sequences (multi-word)
        for m in re.finditer(r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b', text):
            e = m.group().strip()
            if len(e) > 2 and e.lower() not in (
                'who', 'what', 'when', 'where', 'why', 'how', 'which',
                'the', 'and', 'for', 'bcs', 'bangladesh',
            ):
                entities.append(e)
        # Known Bangla entity keywords
        bangla_entities = [
            "বাংলাদেশ", "ঢাকা", "চট্টগ্রাম", "রাজশাহী", "খুলনা",
            "বরিশাল", "সিলেট", "রংপুর", "ময়মনসিংহ",
            "শেখ মুজিবুর রহমান", "বঙ্গবন্ধু", "জিয়াউর রহমান",
            "পদ্মা", "মেঘনা", "যমুনা", "সুন্দরবন", "কক্সবাজার",
        ]
        for ent in bangla_entities:
            if ent in text and ent not in entities:
                entities.append(ent)
        return entities[:8]  # cap at 8

    def _extract_time_hints(self, text: str) -> List[str]:
        hints = []
        # 4-digit years
        for m in re.finditer(r'\b(1[89]\d{2}|2[01]\d{2})\b', text):
            hints.append(m.group())
        # Bangla year patterns
        for m in re.finditer(r'(১[৮৯]\d\d|২[০১]\d\d)', text):
            # Convert Bangla digits
            y = ""
            for ch in m.group():
                y += BANGLA_DIGIT_MAP.get(ch, ch)
            hints.append(y)
        # Month names
        months_en = ['january','february','march','april','may','june',
                     'july','august','september','october','november','december']
        months_bn = ['জানুয়ারি','ফেব্রুয়ারি','মার্চ','এপ্রিল','মে',
                     'জুন','জুলাই','আগস্ট','সেপ্টেম্বর','অক্টোবর',
                     'নভেম্বর','ডিসেম্বর']
        lower = text.lower()
        for m in months_en:
            if m in lower:
                hints.append(m)
        for m in months_bn:
            if m in text:
                hints.append(m)
        return list(dict.fromkeys(hints))[:5]  # deduplicate, cap at 5

    def _extract_keywords(
        self, text: str, entities: List[str], time_hints: List[str]
    ) -> List[str]:
        """Extract the most informative tokens as search keywords."""
        # Start with entities and time hints
        keywords = list(entities) + list(time_hints)
        # Add long Bangla/English words that are not stop words
        stop_words = {
            'কি', 'কী', 'কে', 'কোন', 'কোথায়', 'কখন', 'কেন', 'কীভাবে',
            'এবং', 'বা', 'ও', 'তার', 'তাদের', 'এই', 'সেই', 'একটি',
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'in',
            'for', 'and', 'or', 'to', 'at', 'by', 'with',
        }
        for token in text.split():
            token_clean = token.strip('.,?!।')
            if len(token_clean) >= 3 and token_clean.lower() not in stop_words:
                if token_clean not in keywords:
                    keywords.append(token_clean)
        return keywords[:10]


# ---------------------------------------------------------------------------
# LLM-based extractor
# ---------------------------------------------------------------------------

class LLMExtractor:

    GROQ_API_URL = os.getenv("GROQ_API_URL",
                             "https://api.groq.com/openai/v1/chat/completions")
    GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    SYSTEM_PROMPT = (
        "তুমি একটি BCS পরীক্ষার প্রশ্ন বিশ্লেষক বিশেষজ্ঞ। "
        "প্রতিটি প্রশ্ন থেকে সঠিকভাবে intent, topic, entities এবং search queries বের করো। "
        "নিয়মাবলী: "
        "(১) topic অবশ্যই দেওয়া তালিকার মধ্যে থেকে বেছে নাও। "
        "(২) entities-এ শুধুমাত্র প্রশ্নে উল্লিখিত নাম/স্থান/সংস্থা রাখো। "
        "(৩) search_keywords সংক্ষিপ্ত ও নির্দিষ্ট হতে হবে। "
        "(৪) অনিশ্চিত হলে topic = General দাও। "
        "সর্বদা valid JSON ফরম্যাটে উত্তর দাও।"
    )

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._available = bool(api_key)

    @property
    def available(self) -> bool:
        return self._available

    def extract(self, normalized_text: str) -> Optional[Blueprint]:
        if not self._available:
            return None

        prompt = f"""
নিচের প্রশ্নটি বিশ্লেষণ করো এবং JSON ফরম্যাটে উত্তর দাও।

প্রশ্ন: "{normalized_text}"

JSON ফরম্যাট:
{{
  "intent": "factual_recall | comparison | explanation | enumeration | definition",
  "question_type": "who_question | when_question | where_question | numeric_ranking | what_question | why_how_question",
  "topic": "History | Geography | Science | Economy | Culture | International | Government | Infrastructure | General",
  "sub_topic": "যদি থাকে — যেমন Liberation War, Rivers ইত্যাদি",
  "entities": ["সনাক্ত করা সত্তা ১", "সত্তা ২"],
  "time_hints": ["১৯৭১", "মার্চ"],
  "search_keywords": ["মূল শব্দ ১", "শব্দ ২"],
  "bangla_query": "বাংলায় ওয়েব অনুসন্ধানের জন্য সেরা query",
  "english_query": "Best English web search query for this topic"
}}
"""
        wait_for_rate_limit()
        try:
            response = requests.post(
                self.GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 512,
                },
                timeout=60,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r'```(?:json)?\s*', '', raw).strip('` \n')
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())
            return Blueprint(
                normalized_question=normalized_text,
                intent=data.get("intent", "factual_recall"),
                question_type=data.get("question_type", "what_question"),
                topic=data.get("topic", "General"),
                sub_topic=data.get("sub_topic", ""),
                entities=data.get("entities", []),
                time_hints=data.get("time_hints", []),
                search_keywords=data.get("search_keywords", []),
                bangla_query=data.get("bangla_query", normalized_text),
                english_query=data.get("english_query", normalized_text),
                confidence=0.92,
                extraction_method="llm",
            )
        except Exception as exc:
            log.warning("CraftX extraction failed: %s — falling back to rule-based.", exc)
            return None

# ---------------------------------------------------------------------------
# IntentBuilder — public API
# ---------------------------------------------------------------------------

class IntentBuilder:
    """
    Step 2 — Intent / Blueprint Builder.

    Accepts a NormalizedInput (from Step 1) and returns a Blueprint.
    Tries LLM extraction first (if API key available), falls back to rules.

    Usage
    -----
    from input_normalizer import InputNormalizer
    from intent_builder import IntentBuilder

    normalizer = InputNormalizer()
    builder    = IntentBuilder(hf_api_key="hf_...")   # or no key for rule-based

    ni  = normalizer.normalize("বাংলাদেশের প্রথম রাষ্ট্রপতি কে?")
    bp  = builder.build_blueprint(ni)
    print(bp.topic)        # History
    print(bp.bangla_query) # "বাংলাদেশের প্রথম রাষ্ট্রপতি বাংলাদেশ"
    """

    def __init__(self, hf_api_key: Optional[str] = None):
        self._rule_extractor = RuleBasedExtractor()
        self._llm_extractor  = (
            LLMExtractor(hf_api_key) if hf_api_key else None
        )

    def build_blueprint(self, normalized_input) -> Blueprint:
        """
        Build a Blueprint from a NormalizedInput object (or plain string).

        Parameters
        ----------
        normalized_input : NormalizedInput or str
            Output from InputNormalizer.normalize(), or raw string.

        Returns
        -------
        Blueprint
        """
        # Accept either NormalizedInput or plain str
        if hasattr(normalized_input, 'normalized_text'):
            text = normalized_input.normalized_text
        else:
            text = str(normalized_input)

        if not text.strip():
            return Blueprint(normalized_question=text, confidence=0.0)

        # Try LLM first
        if self._llm_extractor and self._llm_extractor.available:
            bp = self._llm_extractor.extract(text)
            if bp:
                log.info("[IntentBuilder] Blueprint via LLM | topic=%s | type=%s",
                         bp.topic, bp.question_type)
                return bp
            log.warning("[IntentBuilder] LLM extraction failed — using rule-based fallback.")

        # Rule-based fallback
        bp = self._rule_extractor.extract(text)
        log.info("[IntentBuilder] Blueprint via rules | topic=%s | type=%s",
                 bp.topic, bp.question_type)
        return bp

    def build_blueprints_batch(self, normalized_inputs: List) -> List[Blueprint]:
        """
        Build blueprints for a batch of NormalizedInputs.

        Adds a small delay between LLM calls to avoid rate-limiting.
        """
        blueprints = []
        for ni in normalized_inputs:
            bp = self.build_blueprint(ni)
            blueprints.append(bp)
            if bp.extraction_method == "llm":
                time.sleep(0.5)
        return blueprints


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

def build_blueprint(
    normalized_text: str,
    hf_api_key: Optional[str] = None,
) -> Blueprint:
    """
    Module-level convenience function.

    Parameters
    ----------
    normalized_text : str
        Already-normalized question text.
    hf_api_key : str, optional
        HuggingFace API key for LLM extraction.

    Returns
    -------
    Blueprint
    """
    return IntentBuilder(hf_api_key=hf_api_key).build_blueprint(normalized_text)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from bcs.pipeline.input_normalizer import InputNormalizer

    normalizer = InputNormalizer()
    builder    = IntentBuilder()  # rule-based only (no HF key needed for test)

    test_cases = [
        "বাংলাদেশের প্রথম রাষ্ট্রপতি কে?",
        "পদ্মা সেতুর দৈর্ঘ্য কত কিলোমিটার?",
        "মুক্তিযুদ্ধ কবে শুরু হয়?",
        "সুন্দরবন কোথায় অবস্থিত?",
        "Bangladesh's GDP in 2023?",
        "বাংলাদেশের মোট জেলা কয়টি?",
        "শেখ মুজিবুর রহমান কবে জন্মগ্রহণ করেন?",
    ]

    print("\n" + "=" * 65)
    print("  intent_builder.py — Self Test (Rule-Based)")
    print("=" * 65 + "\n")

    for i, question in enumerate(test_cases, 1):
        ni = normalizer.normalize(question)
        bp = builder.build_blueprint(ni)
        print(f"[{i:02d}] Question : {question}")
        print(f"      Topic    : {bp.topic} / {bp.sub_topic}")
        print(f"      Intent   : {bp.intent}  |  Type: {bp.question_type}")
        print(f"      Entities : {bp.entities}")
        print(f"      Time     : {bp.time_hints}")
        print(f"      BN Query : {bp.bangla_query}")
        print(f"      EN Query : {bp.english_query}")
        print()

    print("  Self-test complete.\n")
