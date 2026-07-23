"""
web_scraper.py
==============
Steps 4 & 5: Web Query Generation + Web Search & Scraping
BCSBatighor GK Knowledge Graph System


Responsibilities
----------------
Step 4 — Web Query Generation:
  1. Accept a Blueprint (from intent_builder.py Step 2).
  2. Generate Bangla + English search queries from blueprint fields.
  3. Expand queries with BCS-specific context boosters.

Step 5 — Web Search & Scraping:
  1. Execute DuckDuckGo or Google Custom Search queries.
  2. Scrape HTML from returned URLs.
  3. Extract candidate factual sentences from scraped content.
  4. Return structured ScrapedResult objects ready for Step 6
     (Fact Quality Gate).

Design decisions
----------------
- Uses `requests` + `BeautifulSoup` (no Selenium needed for most BCS sources).
- DuckDuckGo search via `duckduckgo_search` package (no API key needed).
- Falls back to Google Custom Search if GOOGLE_API_KEY + GOOGLE_CSE_ID
  env variables are set.
- Rate-limiting with exponential back-off to avoid banning.
- Sentence extraction uses simple heuristics (≥8 words, ends with punctuation).
- All results are deduplicated at URL and sentence level.

Dependencies
------------
    pip install requests beautifulsoup4 duckduckgo-search lxml
"""

import os
import re
import time
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from bcs.logging_config import get_logger
from bcs.cache import web_cache, make_cache_key, TTLCache

# Optional: duckduckgo_search
try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False

log = get_logger("web_scraper")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT   = 12       # seconds per HTTP request
RATE_LIMIT_DELAY  = 1.5      # seconds between requests
MAX_RESULTS_PER_QUERY = 5    # top-N URLs to scrape per query
MAX_SENTENCES_PER_URL = 15   # cap sentences extracted per page
MIN_SENTENCE_WORDS    = 6    # minimum word count for a valid sentence
MAX_SENTENCE_WORDS    = 80   # maximum word count (avoid paragraph blobs)

# Domains we know are high-quality BCS GK sources
PREFERRED_DOMAINS = {
    "en.wikipedia.org", "bn.wikipedia.org",
    "banglapedia.org",
    "bbs.gov.bd",           # Bangladesh Bureau of Statistics
    "moedu.gov.bd",         # Ministry of Education
    "dailystar.net", "thedailystar.net",
    "prothomalo.com",
    "bdnews24.com",
    "bbc.com/bengali",
    "bn.bdnews24.com",
}

# Domains to skip entirely (ads, SEO spam, social media)
BLOCKED_DOMAINS = {
    "facebook.com", "twitter.com", "instagram.com", "youtube.com",
    "reddit.com", "tiktok.com", "pinterest.com",
}

# Sentence extraction patterns for Bangla + English
SENTENCE_END_RE = re.compile(r'[.।!?]\s*')
BANGLA_RE       = re.compile(r'[\u0980-\u09FF]')

# HTTP headers to avoid bot detection
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BCSBatighor-Bot/1.0; "
        "+https://github.com/bcsbatighor)"
    ),
    "Accept-Language": "bn,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WebSearchResult:
    """A single URL result from a search engine."""
    url:       str
    title:     str
    snippet:   str
    rank:      int
    source:    str  # "duckduckgo" | "google" | "mock"


@dataclass
class ScrapedSentence:
    """A candidate factual sentence extracted from a scraped page."""
    text:       str
    url:        str
    language:   str   # "bn" | "en" | "mixed"
    word_count: int
    sentence_hash: str   # for deduplication

    @staticmethod
    def from_text(text: str, url: str) -> "ScrapedSentence":
        lang = "bn" if len(BANGLA_RE.findall(text)) / max(len(text), 1) > 0.3 else "en"
        words = text.split()
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
        return ScrapedSentence(
            text=text.strip(),
            url=url,
            language=lang,
            word_count=len(words),
            sentence_hash=h,
        )


@dataclass
class ScrapedResult:
    """
    Complete output of the web scraping step for one Blueprint.

    Passed to Galib's FactQualityGate (Step 6) and then to
    Souvik's KnowledgeGraphBuilder (Step 7).
    """
    query_bangla:    str
    query_english:   str
    topic:           str
    urls_searched:   List[str]
    sentences:       List[ScrapedSentence]
    errors:          List[str] = field(default_factory=list)
    total_raw:       int = 0   # sentences before dedup

    def as_fact_dicts(self) -> List[Dict]:
        """
        Convert scraped sentences to the dict format expected by
        KnowledgeGraphBuilder.insert_fact_pipeline().
        """
        facts = []
        for s in self.sentences:
            facts.append({
                "fact_text":        s.text,
                "subject_entities": [],    # entity extraction done downstream
                "object_entities":  [],
                "topic":            self.topic,
                "source_url":       s.url,
                "publisher":        _publisher_from_url(s.url),
            })
        return facts


# ---------------------------------------------------------------------------
# Step 4 — Web Query Generator
# ---------------------------------------------------------------------------

class WebQueryGenerator:
    """
    Step 4: Generates optimised Bangla + English web search queries
    from an intent_builder.Blueprint.

    The Blueprint already carries bangla_query and english_query fields,
    but this class enriches them with BCS-specific boosters and produces
    alternative query variants for broader coverage.
    """

    BCS_BOOSTERS_EN = ["Bangladesh BCS GK", "Bangladesh history facts", "Banglapedia"]
    BCS_BOOSTERS_BN = ["বাংলাদেশ তথ্য", "বিসিএস প্রস্তুতি", "বাংলাপিডিয়া"]

    def generate(self, blueprint) -> List[Tuple[str, str]]:
        """
        Generate a list of (query_string, language) tuples.

        Parameters
        ----------
        blueprint : Blueprint (from intent_builder.py)

        Returns
        -------
        list of (query, lang) where lang is 'en' or 'bn'
        """
        queries = []

        # Primary queries from blueprint
        if blueprint.bangla_query:
            queries.append((blueprint.bangla_query, "bn"))
        if blueprint.english_query:
            queries.append((blueprint.english_query, "en"))

        # Keyword-based fallbacks if primary queries are very short
        if len(blueprint.search_keywords) >= 2:
            kw_str = " ".join(blueprint.search_keywords[:5])
            queries.append((f"{kw_str} Bangladesh facts", "en"))

        # Entity + topic boosters
        for entity in blueprint.entities[:2]:
            queries.append((f"{entity} {blueprint.topic} Bangladesh", "en"))

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for q, lang in queries:
            key = q.strip().lower()
            if key not in seen and len(key) > 3:
                seen.add(key)
                unique.append((q.strip(), lang))

        log.info("[QueryGen] Generated %d queries for topic='%s'", len(unique), blueprint.topic)
        return unique[:6]  # cap at 6 queries to avoid rate limiting


# ---------------------------------------------------------------------------
# Step 5 — Web Search & Scraper
# ---------------------------------------------------------------------------

class WebScraper:
    """
    Step 5: Web Search & Scraping.

    1. Runs DuckDuckGo (or Google) search for each query.
    2. Fetches and parses HTML from top URLs.
    3. Extracts clean factual sentences.
    4. Deduplicates at sentence level.
    5. Returns a ScrapedResult object.

    Usage
    -----
    scraper = WebScraper()
    result  = scraper.scrape_for_blueprint(blueprint)
    # result.sentences → list of ScrapedSentence
    # result.as_fact_dicts() → ready for kg_builder.insert_fact_pipeline()
    """

    def __init__(
        self,
        google_api_key:  Optional[str] = None,
        google_cse_id:   Optional[str] = None,
        rate_limit_delay: float = RATE_LIMIT_DELAY,
        max_results_per_query: int = MAX_RESULTS_PER_QUERY,
    ):
        self._google_api_key  = google_api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._google_cse_id   = google_cse_id  or os.environ.get("GOOGLE_CSE_ID", "")
        self._delay           = rate_limit_delay
        self._max_results     = max_results_per_query
        self._session         = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._seen_hashes: set = set()   # cross-URL sentence dedup

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _make_cache_key(self, blueprint) -> str:
        parts = [
            blueprint.topic,
            blueprint.bangla_query or "",
            blueprint.english_query or "",
            " ".join(blueprint.search_keywords or []),
        ]
        return make_cache_key(*parts)

    def scrape_for_blueprint(self, blueprint) -> ScrapedResult:
        """
        Full pipeline: queries → URLs → scrape → sentences.

        Results are cached per blueprint (TTL: 1 hour) to avoid
        redundant network calls for repeated topics.

        Parameters
        ----------
        blueprint : Blueprint from intent_builder.py

        Returns
        -------
        ScrapedResult
        """
        cache_key = self._make_cache_key(blueprint)
        cached = web_cache.get(cache_key)
        if cached is not None:
            log.info("[Scraper] Cache HIT for topic='%s' (%d sentences)", blueprint.topic, len(cached.sentences))
            self._seen_hashes = {s.sentence_hash for s in cached.sentences}
            return cached

        qgen    = WebQueryGenerator()
        queries = qgen.generate(blueprint)

        all_sentences: List[ScrapedSentence] = []
        urls_searched: List[str] = []
        errors: List[str] = []
        total_raw = 0

        # Reset per-call dedup so reusing the same WebScraper instance across
        # multiple blueprints does not suppress valid sentences from later calls.
        self._seen_hashes = set()

        for query, lang in queries:
            log.info("[Scraper] Searching: \"%s\" (%s)", query[:70], lang)
            search_results = self._search(query)

            for sr in search_results[:self._max_results]:
                url = sr.url
                if url in urls_searched:
                    continue
                if self._is_blocked(url):
                    log.debug("[Scraper] Blocked domain: %s", url)
                    continue

                urls_searched.append(url)
                time.sleep(self._delay)

                try:
                    sentences = self._fetch_and_extract(url)
                    total_raw += len(sentences)

                    # Deduplicate by hash
                    for s in sentences:
                        if s.sentence_hash not in self._seen_hashes:
                            self._seen_hashes.add(s.sentence_hash)
                            all_sentences.append(s)
                except Exception as exc:
                    msg = f"{url}: {str(exc)[:80]}"
                    errors.append(msg)
                    log.warning("[Scraper] Error fetching %s: %s", url, str(exc)[:80])

        log.info(
            "[Scraper] Done. URLs=%d, raw_sentences=%d, unique=%d, errors=%d",
            len(urls_searched), total_raw, len(all_sentences), len(errors),
        )

        result = ScrapedResult(
            query_bangla=blueprint.bangla_query,
            query_english=blueprint.english_query,
            topic=blueprint.topic,
            urls_searched=urls_searched,
            sentences=all_sentences,
            errors=errors,
            total_raw=total_raw,
        )
        web_cache.set(cache_key, result)
        log.info("[Scraper] Cache MISS for topic='%s' — stored %d sentences for 1h", blueprint.topic, len(all_sentences))
        return result

    def scrape_urls(self, urls: List[str], topic: str = "General") -> ScrapedResult:
        """
        Scrape a fixed list of URLs directly (no search step).
        Useful for testing or when URLs are already known.
        """
        all_sentences: List[ScrapedSentence] = []
        errors: List[str] = []
        total_raw = 0

        for url in tqdm(urls, desc="Scraping URLs", unit="url"):
            if self._is_blocked(url):
                continue
            time.sleep(self._delay)
            try:
                sentences = self._fetch_and_extract(url)
                total_raw += len(sentences)
                for s in sentences:
                    if s.sentence_hash not in self._seen_hashes:
                        self._seen_hashes.add(s.sentence_hash)
                        all_sentences.append(s)
            except Exception as exc:
                errors.append(f"{url}: {str(exc)[:80]}")

        return ScrapedResult(
            query_bangla="",
            query_english="",
            topic=topic,
            urls_searched=urls,
            sentences=all_sentences,
            errors=errors,
            total_raw=total_raw,
        )

    # ------------------------------------------------------------------
    # Search engine integration
    # ------------------------------------------------------------------

    def _search(self, query: str) -> List[WebSearchResult]:
        """Try DuckDuckGo first, then Google, then return empty."""
        if DDGS_AVAILABLE:
            try:
                return self._ddg_search(query)
            except Exception as exc:
                log.warning("[Search] DuckDuckGo failed: %s", exc)

        if self._google_api_key and self._google_cse_id:
            try:
                return self._google_search(query)
            except Exception as exc:
                log.warning("[Search] Google failed: %s", exc)

        log.warning("[Search] No search engine available for query: %s", query[:50])
        return []

    def _ddg_search(self, query: str) -> List[WebSearchResult]:
        """Search DuckDuckGo using duckduckgo_search package with retry."""
        max_attempts = 3
        last_exc = None
        for attempt in range(max_attempts):
            try:
                results = []
                with DDGS() as ddgs:
                    for i, r in enumerate(ddgs.text(query, max_results=self._max_results)):
                        results.append(WebSearchResult(
                            url=r.get("href", ""),
                            title=r.get("title", ""),
                            snippet=r.get("body", ""),
                            rank=i + 1,
                            source="duckduckgo",
                        ))
                return results
            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", 0) or getattr(getattr(exc, "response", None), "status_code", 0)
                if status == 429 or "ratelimit" in str(exc).lower() or "rate limit" in str(exc).lower():
                    backoff = 3 * (2 ** attempt)
                    log.warning("[Search] DDG rate limited (attempt %d/%d) — retrying in %ds", attempt + 1, max_attempts, backoff)
                    time.sleep(backoff)
                    continue
                log.warning("[Search] DDG attempt %d/%d failed: %s", attempt + 1, max_attempts, str(exc)[:100])
                if attempt < max_attempts - 1:
                    time.sleep(2 * (2 ** attempt))
                    continue
                break
        log.warning("[Search] DDG failed after %d attempts: %s", max_attempts, str(last_exc)[:100])
        return []

    def _google_search(self, query: str) -> List[WebSearchResult]:
        """Search Google Custom Search API."""
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self._google_api_key,
            "cx":  self._google_cse_id,
            "q":   query,
            "num": self._max_results,
        }
        resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for i, item in enumerate(data.get("items", [])):
            results.append(WebSearchResult(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                rank=i + 1,
                source="google",
            ))
        return results

    # ------------------------------------------------------------------
    # Fetch & HTML parsing
    # ------------------------------------------------------------------

    def _fetch_and_extract(self, url: str) -> List[ScrapedSentence]:
        """Fetch a URL and extract factual sentences from its HTML."""
        resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return []

        soup = BeautifulSoup(resp.content, "lxml")

        # Remove nav, footer, script, style, ads
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "aside", "form", "iframe", "noscript"]):
            tag.decompose()

        # Prefer article body / main content
        main = (
            soup.find("article") or
            soup.find("main") or
            soup.find(id=re.compile(r"content|body|article", re.I)) or
            soup.find(class_=re.compile(r"content|body|article|post", re.I)) or
            soup.body
        )

        raw_text = main.get_text(separator=" ") if main else soup.get_text(separator=" ")
        raw_text = re.sub(r"\s+", " ", raw_text).strip()

        sentences = self._extract_sentences(raw_text, url)
        log.debug("[Scraper] %s → %d sentence(s)", url[:60], len(sentences))
        return sentences

    # ------------------------------------------------------------------
    # Sentence extraction
    # ------------------------------------------------------------------

    def _extract_sentences(self, text: str, url: str) -> List[ScrapedSentence]:
        """
        Split text into sentences and filter by quality.

        Keeps sentences that:
        - Are between MIN_SENTENCE_WORDS and MAX_SENTENCE_WORDS long
        - End with a sentence-ending punctuation (. । ! ?)
        - Contain at least some alphabetic content
        - Are not headers / navigation fragments (no pipe/tab chars)
        """
        # Split on sentence endings while keeping the delimiter
        raw_parts = re.split(r'(?<=[.।!?])\s+', text)

        results = []
        for part in raw_parts:
            part = part.strip()
            if not part:
                continue

            # Skip fragments with navigation artifacts
            if "|" in part or "\t" in part:
                continue

            # Check word count
            words = part.split()
            if not (MIN_SENTENCE_WORDS <= len(words) <= MAX_SENTENCE_WORDS):
                continue

            # Must have alphabetic content
            if not re.search(r"[a-zA-Z\u0980-\u09FF]", part):
                continue

            # Ensure terminal punctuation (add '.' if missing for downstream use)
            if part[-1] not in ".।!?":
                part += "."

            results.append(ScrapedSentence.from_text(part, url))

            if len(results) >= MAX_SENTENCES_PER_URL:
                break

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_blocked(self, url: str) -> bool:
        """Return True if URL's domain is in the blocked list."""
        try:
            domain = urlparse(url).netloc.lower().lstrip("www.")
            return any(b in domain for b in BLOCKED_DOMAINS)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _publisher_from_url(url: str) -> str:
    """Infer publisher name from URL domain."""
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
        # Map known domains to publisher names
        mapping = {
            "en.wikipedia.org":  "Wikipedia (EN)",
            "bn.wikipedia.org":  "Wikipedia (BN)",
            "banglapedia.org":   "Banglapedia",
            "bbs.gov.bd":        "Bangladesh Bureau of Statistics",
            "thedailystar.net":  "The Daily Star",
            "prothomalo.com":    "Prothom Alo",
            "bdnews24.com":      "bdnews24.com",
            "bbc.com":           "BBC",
        }
        for key, val in mapping.items():
            if key in domain:
                return val
        return domain.split(".")[0].capitalize()
    except Exception:
        return "Unknown"


# ---------------------------------------------------------------------------
# Convenience wrapper (for pipeline.py)
# ---------------------------------------------------------------------------

def scrape_for_blueprint(blueprint, **kwargs) -> ScrapedResult:
    """
    Module-level convenience function.

    Parameters
    ----------
    blueprint : Blueprint from intent_builder.py
    **kwargs  : passed to WebScraper.__init__()

    Returns
    -------
    ScrapedResult
    """
    return WebScraper(**kwargs).scrape_for_blueprint(blueprint)


# ---------------------------------------------------------------------------
# Self-test (runs without a search API — uses mock URLs)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from input_normalizer import InputNormalizer
    from intent_builder import IntentBuilder

    print("\n" + "=" * 65)
    print("  web_scraper.py — Self Test")
    print("=" * 65 + "\n")

    normalizer = InputNormalizer()
    builder    = IntentBuilder()

    test_question = "বাংলাদেশের প্রথম রাষ্ট্রপতি কে?"
    ni = normalizer.normalize(test_question)
    bp = builder.build_blueprint(ni)

    print(f"Question   : {test_question}")
    print(f"Blueprint  : topic={bp.topic}, type={bp.question_type}")
    print(f"BN Query   : {bp.bangla_query}")
    print(f"EN Query   : {bp.english_query}")

    qgen = WebQueryGenerator()
    queries = qgen.generate(bp)
    print(f"\nGenerated {len(queries)} search queries:")
    for i, (q, lang) in enumerate(queries, 1):
        print(f"  [{i}] ({lang}) {q}")

    # Test URL scraping directly (Wikipedia — no search key needed)
    print("\n  Scraping Wikipedia article directly...")
    scraper = WebScraper()
    result  = scraper.scrape_urls(
        urls=["https://en.wikipedia.org/wiki/Sheikh_Mujibur_Rahman"],
        topic="History",
    )

    print(f"  URLs scraped  : {len(result.urls_searched)}")
    print(f"  Sentences found: {len(result.sentences)}")
    print(f"  Errors        : {len(result.errors)}")
    if result.sentences:
        print("\n  Sample sentences:")
        for s in result.sentences[:5]:
            print(f"    [{s.language}] \"{s.text[:90]}\"")

    print("\n  Self-test complete.")
