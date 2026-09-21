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
- DuckDuckGo search via the `ddgs` package (no API key needed).
- Falls back to Google Custom Search if GOOGLE_API_KEY + GOOGLE_CSE_ID
  env variables are set.
- Rate-limiting with exponential back-off to avoid banning.
- Sentence extraction uses simple heuristics (≥8 words, ends with punctuation).
- All results are deduplicated at URL and sentence level.

Dependencies
------------
    pip install requests beautifulsoup4 ddgs lxml

    NOTE: the PyPI package is named `ddgs` (the old `duckduckgo-search`
    package was renamed and now installs a *different* module named
    `duckduckgo_search`). `pip install duckduckgo-search` will NOT make
    `import ddgs` work — DDGS_AVAILABLE silently ends up False and every
    search falls through to Google (or to nothing, if no Google keys are
    set). If the old package is already installed, either
    `pip uninstall duckduckgo-search && pip install ddgs`, or just keep
    it — the fallback import below also tries `duckduckgo_search`.
"""

import os
import re
import time
import json
import logging
import hashlib
import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from rejection_taxonomy import classify_source_decision, RejectionTally

# Optional: DuckDuckGo search client.
# Try the current PyPI package name first (`ddgs`), then fall back to the
# old, renamed one (`duckduckgo_search`) so this works whichever one the
# environment happens to have installed — see the dependency note above.
try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

log = logging.getLogger("web_scraper")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT   = 12       # seconds per HTTP request
RATE_LIMIT_DELAY  = 1.5      # seconds between requests
MAX_RESULTS_PER_QUERY = 5    # top-N URLs to scrape per query
MAX_SENTENCES_PER_URL = 15   # cap sentences extracted per page
MIN_SENTENCE_WORDS    = 6    # minimum word count for a valid sentence
MAX_SENTENCE_WORDS    = 80   # maximum word count (avoid paragraph blobs)

# FIX: archive.org (CDX + /wayback/available) lookups were observed
# failing outright — 503 Service Unavailable and read-timeouts on
# nearly every URL, back-to-back — which is an IA-side outage/rate
# limit, not "no snapshot exists." Each rejected URL was still paying
# the FULL cost of that failure (REQUEST_TIMEOUT-second attempt, a
# 2s-sleep retry, then a second REQUEST_TIMEOUT-second attempt on the
# availability API) before finally giving up, which is why a single
# topic could take minutes and burn through good sources like
# Wikipedia that almost certainly existed pre-cutoff. WAYBACK_TIMEOUT
# lets lookups fail faster; the breaker constants below stop retrying
# archive.org at all once it's clearly down, for a cooldown window.
WAYBACK_TIMEOUT = 6                     # seconds for CDX/availability lookups only
WAYBACK_BREAKER_THRESHOLD = 3           # consecutive network failures before tripping
WAYBACK_BREAKER_COOLDOWN_SECONDS = 90   # how long to skip Wayback lookups once tripped

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

# ---------------------------------------------------------------------------
# Source tiers (guideline §7.1). 1 = most authoritative. Unlisted domains
# default to tier 4 (reputable-but-unverified) rather than assumed tier 1 —
# never guess a source into a stronger tier than it's earned.
# ---------------------------------------------------------------------------
SOURCE_TIER_MAP = {
    # Tier 1 — official government / constitutional bodies / central banks
    "bbs.gov.bd": 1, "moedu.gov.bd": 1, "bb.org.bd": 1,
    "cabinet.gov.bd": 1, "mofa.gov.bd": 1, "pmo.gov.bd": 1,
    "bangladesh.gov.bd": 1, "parliament.gov.bd": 1,
    "supremecourt.gov.bd": 1, "army.mil.bd": 1,
    # Tier 2 — intergovernmental / official international orgs
    "un.org": 2, "worldbank.org": 2, "who.int": 2, "imf.org": 2,
    # Tier 3 — primary institutional sources (encyclopedic/reference bodies)
    "en.wikipedia.org": 3, "bn.wikipedia.org": 3, "banglapedia.org": 3,
    # Tier 4 — reputable news outlets
    "dailystar.net": 4, "thedailystar.net": 4, "prothomalo.com": 4,
    "bdnews24.com": 4, "bn.bdnews24.com": 4, "bbc.com": 4,
}

DEFAULT_SOURCE_TIER = 4  # unlisted domain: treat as reputable-news-tier, not authoritative


def _bare_host(url: str) -> str:
    """
    Host of `url`, lowercased, with a leading "www." removed.

    NOTE: this used to be `netloc.lower().lstrip("www.")`. str.lstrip takes a
    SET of characters, not a prefix, so it stripped any leading run of {'w','.'}
    — "worldbank.org" became "orldbank.org" and "who.int" became "ho.int", so
    both silently missed their SOURCE_TIER_MAP entries and fell through to the
    tier-4 default. Port is dropped so "example.com:8443" still matches.
    """
    try:
        host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def infer_source_tier(url: str) -> int:
    """Look up a URL's source tier from SOURCE_TIER_MAP, defaulting safely."""
    domain = _bare_host(url)
    if not domain:
        return DEFAULT_SOURCE_TIER
    for known_domain, tier in SOURCE_TIER_MAP.items():
        # Exact host or a subdomain of it — never a bare substring, which
        # would let "notbbs.gov.bd.evil.com" inherit a tier-1 rating.
        if domain == known_domain or domain.endswith("." + known_domain):
            return tier
    return DEFAULT_SOURCE_TIER


# ---------------------------------------------------------------------------
# Temporal cutoff enforcement (guideline §3.3, §7 — "Data firewall")
# ---------------------------------------------------------------------------

# Common places a publication/update date is declared in HTML.
_META_DATE_PROPS = [
    ("meta", {"property": "article:published_time"}),
    ("meta", {"name": "date"}),
    ("meta", {"name": "pubdate"}),
    ("meta", {"name": "publish-date"}),
    ("meta", {"itemprop": "datePublished"}),
    ("meta", {"property": "og:updated_time"}),
]
_META_MODIFIED_PROPS = [
    ("meta", {"property": "article:modified_time"}),
    ("meta", {"itemprop": "dateModified"}),
]

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_MONTH_NAME_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+(\d{1,2}),?\s+(\d{4})", re.I,
)
# Day-first form: "19 March 2023" — common in BD/UK-style reporting,
# and the exact format the faculty guideline itself uses for t_exam.
_DAY_MONTH_NAME_RE = re.compile(
    r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?,?\s+(\d{4})", re.I,
)


def _parse_date_string(raw: str) -> Optional[datetime.date]:
    """Best-effort parse of a date string into a date. Returns None, never guesses."""
    if not raw:
        return None
    raw = raw.strip()
    m = _ISO_DATE_RE.search(raw)
    if m:
        try:
            return datetime.date.fromisoformat(m.group(1))
        except ValueError:
            pass
    m = _MONTH_NAME_RE.search(raw)
    if m:
        try:
            return datetime.datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y"
            ).date()
        except ValueError:
            try:
                return datetime.datetime.strptime(
                    f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
                ).date()
            except ValueError:
                pass
    m = _DAY_MONTH_NAME_RE.search(raw)
    if m:
        try:
            return datetime.datetime.strptime(
                f"{m.group(2)} {m.group(1)} {m.group(3)}", "%b %d %Y"
            ).date()
        except ValueError:
            try:
                return datetime.datetime.strptime(
                    f"{m.group(2)} {m.group(1)} {m.group(3)}", "%B %d %Y"
                ).date()
            except ValueError:
                pass
    return None


def extract_publication_dates(soup: "BeautifulSoup") -> Tuple[Optional[datetime.date], Optional[datetime.date]]:
    """
    Best-effort extraction of (published_date, modified_date) from a
    page's <meta> tags and JSON-LD blocks. Either value may be None —
    an unresolvable date is NOT the same as "safe to use," callers must
    treat None according to the active cutoff policy, not assume it
    means "old enough."
    """
    published, modified = None, None

    for tag_name, attrs in _META_DATE_PROPS:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get("content"):
            published = _parse_date_string(tag["content"])
            if published:
                break

    for tag_name, attrs in _META_MODIFIED_PROPS:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get("content"):
            modified = _parse_date_string(tag["content"])
            if modified:
                break

    # JSON-LD structured data (common on news sites)
    if published is None:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
            except (ValueError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if published is None and item.get("datePublished"):
                    published = _parse_date_string(str(item["datePublished"]))
                if modified is None and item.get("dateModified"):
                    modified = _parse_date_string(str(item["dateModified"]))

    # <time datetime="..."> as a last resort
    if published is None:
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            published = _parse_date_string(time_tag["datetime"])

    return published, modified


@dataclass
class SourceDecision:
    """
    Audit record of what the cutoff policy decided about one URL.
    The guideline (§19.3) requires every query/source logged and every
    cutoff violation automatically blocked — this is that log entry.
    """
    url:               str
    decision:          str   # "accepted" | "rejected" | "archived_fallback"
    reason:            str
    published_at:      Optional[str]   # ISO date string or None
    modified_at:       Optional[str]
    retrieved_via:     str   # "live" | "wayback"
    source_tier:       int
    cutoff_date:       Optional[str]
    rejection_code:    Optional[str] = None
    # Canonical guideline §10.2 code for non-"accepted" decisions
    # (E-LEAK for temporal violations, E-SRC for below-floor tier).
    # None for "accepted" decisions — nothing to log there.


class CutoffPolicy:
    """
    Enforces the temporal data firewall (guideline §3, §3.3).

    cutoff_date=None means NO enforcement — every page is accepted as-is.
    This is the default so existing exploratory/non-experiment runs keep
    working unchanged. For the actual frozen 2023 experiment, you MUST
    construct the scraper with an explicit cutoff_date.

    allow_undated: when a page's publication date can't be extracted at
    all, should it be accepted anyway? Default False when a cutoff is
    active — per §3.3 ("do not use search-result snippets without
    recording the page's original publication date"), an unknown date
    is treated as unsafe, not as "probably fine."

    use_wayback_fallback: when a live page is rejected (post-cutoff, or
    updated after cutoff, or undated), try the Wayback Machine for a
    snapshot at-or-before the cutoff and use that instead of dropping
    the source entirely (§3.3: "use an archived snapshot ... for the
    relevant claim").
    """

    def __init__(self, cutoff_date: Optional[datetime.date] = None,
                 allow_undated: bool = False,
                 use_wayback_fallback: bool = True):
        self.cutoff_date = cutoff_date
        self.allow_undated = allow_undated
        self.use_wayback_fallback = use_wayback_fallback

    @property
    def enforcing(self) -> bool:
        return self.cutoff_date is not None

    def evaluate(self, url: str, published: Optional[datetime.date],
                 modified: Optional[datetime.date]) -> Tuple[str, str]:
        """
        Decide accepted/rejected/needs_archive for a live-fetched page.
        Returns (decision, reason). decision is one of:
          "accepted"       — safe to use as-is
          "needs_archive"  — live page is unsafe; try Wayback fallback
          "rejected"       — unsafe and no fallback should be attempted
                              (only when use_wayback_fallback is False)
        """
        if not self.enforcing:
            return "accepted", "no cutoff enforced"

        if published is None:
            if self.allow_undated:
                return "accepted", "no publication date found; allow_undated=True"
            return (("needs_archive" if self.use_wayback_fallback else "rejected"),
                    "no publication date found")

        if published > self.cutoff_date:
            return (("needs_archive" if self.use_wayback_fallback else "rejected"),
                    f"published {published.isoformat()} is after cutoff {self.cutoff_date.isoformat()}")

        if modified is not None and modified > self.cutoff_date:
            return (("needs_archive" if self.use_wayback_fallback else "rejected"),
                    f"modified {modified.isoformat()} is after cutoff {self.cutoff_date.isoformat()} "
                    f"(page existed before cutoff but was edited later)")

        return "accepted", f"published {published.isoformat()} <= cutoff {self.cutoff_date.isoformat()}"

# Sentence extraction patterns for Bangla + English
SENTENCE_END_RE = re.compile(r'[.।!?]\s*')
BANGLA_RE       = re.compile(r'[\u0980-\u09FF]')

# FIX: known encyclopedia/navigation boilerplate that reads as a
# grammatically valid "sentence" (right length, ends in punctuation,
# has alphabetic content) but isn't a fact — e.g. Wikipedia's
# "From Wikipedia, the free encyclopedia" masthead, disambiguation
# hatnotes, footnote/citation markers, and page-chrome links. Matched
# case-insensitively against the whole candidate sentence.
BOILERPLATE_RE = re.compile(
    r"from wikipedia,?\s*the free encyclopedia"
    r"|jump to (?:content|navigation|search)"
    r"|for other uses,?\s*see"
    r"|\(disambiguation\)"
    r"|^\s*\[\s*[a-z0-9]{1,3}\s*\]"       # leading footnote marker, e.g. "[ d ]", "[1]"
    r"|this article (?:is about|needs|possibly)"
    r"|citation needed"
    r"|retrieved from\b"
    r"|this page was last edited"
    r"|archived from the original"
    r"|see also\s*:?\s*$"
    r"|main article\s*:",
    re.IGNORECASE,
)

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
    # Provenance fields (guideline §6.3 / §7) — populated by WebScraper
    # so they can flow straight into kg_builder.insert_fact_pipeline().
    source_published_at: Optional[str] = None   # ISO date string, or None if unknown
    source_tier:          Optional[int] = None
    retrieved_via:         str = "live"          # "live" | "wayback"
    html_digest:          Optional[str] = None
    archive_permalink:    Optional[str] = None
    snapshot_date:        Optional[str] = None
    observed_at:          Optional[str] = None
    evidence_weight:      Optional[float] = None

    @staticmethod
    def from_text(text: str, url: str,
                  source_published_at: Optional[str] = None,
                  source_tier: Optional[int] = None,
                  retrieved_via: str = "live",
                  html_digest: Optional[str] = None,
                  archive_permalink: Optional[str] = None,
                  snapshot_date: Optional[str] = None,
                  observed_at: Optional[str] = None,
                  evidence_weight: Optional[float] = None) -> "ScrapedSentence":
        lang = "bn" if len(BANGLA_RE.findall(text)) / max(len(text), 1) > 0.3 else "en"
        words = text.split()
        h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
        if evidence_weight is None:
            tier_weights = {1: 1.00, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.30}
            tier = source_tier if source_tier is not None else 4
            evidence_weight = tier_weights.get(tier, 0.30)
        return ScrapedSentence(
            text=text.strip(),
            url=url,
            language=lang,
            word_count=len(words),
            sentence_hash=h,
            source_published_at=source_published_at,
            source_tier=source_tier,
            retrieved_via=retrieved_via,
            html_digest=html_digest,
            archive_permalink=archive_permalink,
            snapshot_date=snapshot_date,
            observed_at=observed_at or datetime.datetime.now().isoformat(),
            evidence_weight=round(evidence_weight, 4),
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
    source_decisions: List[SourceDecision] = field(default_factory=list)  # full audit log

    def as_fact_dicts(self) -> List[Dict]:
        """
        Convert scraped sentences to the dict format expected by
        KnowledgeGraphBuilder.insert_fact_pipeline(). Now carries
        source_published_at, source_tier, and full episodic provenance.
        """
        facts = []
        for s in self.sentences:
            facts.append({
                "fact_text":            s.text,
                "subject_entities":     [],    # entity extraction done downstream
                "object_entities":      [],
                "topic":                self.topic,
                "source_url":           s.archive_permalink or s.url,
                "publisher":            _publisher_from_url(s.url),
                "source_published_at":  s.source_published_at or s.snapshot_date,
                "source_tier":          s.source_tier,
                "source_reliability":   s.evidence_weight or 1.0,
                "observed_at":          s.observed_at,
                "temporal_evidence_status": "verified_pre_cutoff_source" if (s.retrieved_via == "wayback" or (s.source_published_at and s.source_published_at <= "2023-04-19")) else None,
                "temporal_evidence_source_url": s.archive_permalink or s.url,
                "temporal_evidence_snapshot_hash": s.html_digest,
            })
        return facts

    def leakage_report(self) -> Dict:
        """
        Summarize what the cutoff policy rejected/archived for this
        blueprint's queries — the audit trail §19.3 and §20.1 require.
        """
        rejected = [d for d in self.source_decisions if d.decision == "rejected"]
        archived = [d for d in self.source_decisions if d.decision == "archived_fallback"]
        accepted = [d for d in self.source_decisions if d.decision == "accepted"]
        return {
            "accepted": len(accepted),
            "rejected_post_cutoff_or_undated": len(rejected),
            "archived_fallback_used": len(archived),
            "rejected_urls": [d.url for d in rejected],
        }


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
        cutoff_date:      Optional[datetime.date] = None,
        allow_undated:    bool = False,
        use_wayback_fallback: bool = True,
        min_source_tier:  Optional[int] = None,
    ):
        """
        cutoff_date: if set, enforces the temporal data firewall (§3.3).
        No source published (or last modified) after this date will be
        used — live or archived. Leave None for exploratory/non-frozen
        runs; MUST be set for the actual 2023 experiment.

        min_source_tier: if set, sources whose inferred tier (§7.1;
        1=official ... 5=secondary) is numerically worse than this floor
        are logged with rejection code E-SRC (guideline §10.2), separate
        from temporal (E-LEAK) rejections. None = no tier floor enforced.
        """
        self._min_source_tier = min_source_tier
        self.rejection_tally  = RejectionTally()
        self._google_api_key  = google_api_key or os.environ.get("GOOGLE_API_KEY", "")
        self._google_cse_id   = google_cse_id  or os.environ.get("GOOGLE_CSE_ID", "")
        self._delay           = rate_limit_delay
        self._max_results     = max_results_per_query
        self._session         = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._seen_hashes: set = set()   # cross-URL sentence dedup
        self._policy = CutoffPolicy(
            cutoff_date=cutoff_date,
            allow_undated=allow_undated,
            use_wayback_fallback=use_wayback_fallback,
        )
        self._decisions: List[SourceDecision] = []   # audit log, reset per scrape call

        # FIX (v2): archive.org's CDX and /wayback/available endpoints
        # were observed failing INDEPENDENTLY — CDX 503ing on every
        # single call while /available kept responding successfully
        # (just usually without a usable pre-cutoff snapshot). A single
        # shared fail-streak meant every /available success reset the
        # counter to 0 even though CDX was still completely dead, so
        # the breaker never actually tripped and every URL kept paying
        # the full CDX cost (~14s of timeouts/retries) before falling
        # through to /available. Track each endpoint's health
        # separately so a dead CDX gets skipped on its own.
        self._cdx_fail_streak    = 0
        self._cdx_breaker_until: Optional[float] = None
        self._availability_fail_streak  = 0
        self._availability_breaker_until: Optional[float] = None

        if self._policy.enforcing:
            log.info("[Scraper] Cutoff enforcement ACTIVE: t* = %s "
                     "(allow_undated=%s, wayback_fallback=%s)",
                     cutoff_date.isoformat(), allow_undated, use_wayback_fallback)
        else:
            log.warning("[Scraper] No cutoff set — running WITHOUT temporal data-firewall "
                        "enforcement. Do not use this mode for the frozen 2023 experiment.")

    # ------------------------------------------------------------------
    # Relevance filter (FIX: off-topic scraped junk becoming "facts")
    # ------------------------------------------------------------------
    # Previously every sentence extracted from every scraped page became a
    # candidate fact tagged with the blueprint's topic, with no check that
    # the sentence had anything to do with the actual question. In
    # practice this let completely unrelated page content (e.g. a "Google
    # Drive for Desktop install location" paragraph picked up while
    # scraping for "Padma Bridge length") flow straight into the KG under
    # the "Infrastructure" topic and later get accepted as a real MCQ.
    # This filter drops any scraped sentence that doesn't mention at
    # least one keyword/entity actually tied to the blueprint's question,
    # before it's ever considered a fact.

    _STOPWORDS_EN = {
        "the", "is", "of", "in", "a", "an", "to", "what", "where", "when",
        "who", "which", "how", "many", "does", "did", "are", "was", "were",
        "and", "or", "for", "on", "at", "by", "with", "this", "that",
    }

    @classmethod
    def _blueprint_keywords(cls, blueprint) -> List[str]:
        kws: set = set()
        kws.update(k for k in (getattr(blueprint, "entities", None) or []) if k)
        kws.update(k for k in (getattr(blueprint, "search_keywords", None) or []) if k)
        for q in (getattr(blueprint, "english_query", ""), getattr(blueprint, "bangla_query", "")):
            if not q:
                continue
            for w in re.split(r"\s+", q):
                w = w.strip("?.,!\"'()।")
                if len(w) < 3:
                    continue
                if w.lower() in cls._STOPWORDS_EN:
                    continue
                kws.add(w)
        return [k for k in kws if k]

    @staticmethod
    def _sentence_is_relevant(text: str, keywords: List[str]) -> bool:
        if not keywords:
            # No keywords extracted from the blueprint at all — don't
            # over-filter in that edge case, just pass everything through
            # (matches the old behavior when we have nothing to check
            # against, rather than silently dropping every sentence).
            return True
        lower_text = text.lower()
        for kw in keywords:
            if kw.lower() in lower_text or kw in text:
                return True
        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scrape_for_blueprint(self, blueprint) -> ScrapedResult:
        """
        Full pipeline: queries → URLs → scrape → sentences.

        Parameters
        ----------
        blueprint : Blueprint from intent_builder.py

        Returns
        -------
        ScrapedResult
        """
        qgen    = WebQueryGenerator()
        queries = qgen.generate(blueprint)

        all_sentences: List[ScrapedSentence] = []
        urls_searched: List[str] = []
        errors: List[str] = []
        total_raw = 0

        # Reset per-call dedup so reusing the same WebScraper instance across
        # multiple blueprints does not suppress valid sentences from later calls.
        self._seen_hashes = set()
        self._decisions = []

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

        # ── Relevance filter — drop off-topic sentences before they can
        # ever become "facts" (see _blueprint_keywords/_sentence_is_relevant
        # above). Only applied here, not in scrape_urls(), since a direct
        # URL list has no blueprint to check relevance against.
        keywords = self._blueprint_keywords(blueprint)
        before   = len(all_sentences)
        all_sentences = [s for s in all_sentences if self._sentence_is_relevant(s.text, keywords)]
        dropped = before - len(all_sentences)
        if dropped:
            log.info("[Scraper] Dropped %d off-topic sentence(s) not matching "
                      "blueprint keywords %s.", dropped, keywords[:8])

        log.info(
            "[Scraper] Done. URLs=%d, raw_sentences=%d, unique=%d, errors=%d",
            len(urls_searched), total_raw, len(all_sentences), len(errors),
        )

        return ScrapedResult(
            query_bangla=blueprint.bangla_query,
            query_english=blueprint.english_query,
            topic=blueprint.topic,
            urls_searched=urls_searched,
            sentences=all_sentences,
            errors=errors,
            total_raw=total_raw,
            source_decisions=list(self._decisions),
        )

    def scrape_urls(self, urls: List[str], topic: str = "General") -> ScrapedResult:
        """
        Scrape a fixed list of URLs directly (no search step).
        Useful for testing or when URLs are already known.
        """
        all_sentences: List[ScrapedSentence] = []
        errors: List[str] = []
        total_raw = 0
        self._decisions = []

        for url in urls:
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
            source_decisions=list(self._decisions),
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

        log.error(
            "[Search] No search engine available for query: %s — "
            "DDGS_AVAILABLE=%s, google_configured=%s. Install a search "
            "backend with `pip install ddgs`, or set GOOGLE_API_KEY + "
            "GOOGLE_CSE_ID and pass them into WebScraper(...). Every topic "
            "will silently return 0 URLs / 0 scraped facts until one of "
            "these is fixed.",
            query[:50], DDGS_AVAILABLE,
            bool(self._google_api_key and self._google_cse_id),
        )
        return []

    def _ddg_search(self, query: str) -> List[WebSearchResult]:
        """Search DuckDuckGo using duckduckgo_search package."""
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
        """
        Fetch a URL, apply the temporal cutoff policy, and extract
        factual sentences from whichever HTML (live or archived) the
        policy allows. Every decision is appended to self._decisions.
        """
        tier = infer_source_tier(url)

        resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            self._log_decision(url, "rejected", "non-HTML content type",
                               None, None, "live", tier)
            return []

        soup = BeautifulSoup(resp.content, "lxml")
        published, modified = extract_publication_dates(soup)

        html_digest = hashlib.sha256(resp.content).hexdigest()[:16]
        archive_permalink = url if "web.archive.org/web/" in url else None
        snapshot_date = published.isoformat() if published else None
        observed_at = datetime.datetime.now().isoformat()

        decision, reason = self._policy.evaluate(url, published, modified)

        retrieved_via = "live"
        if decision == "needs_archive":
            archived = self._try_wayback_fallback(url)
            if archived is not None:
                if len(archived) == 4:
                    soup, snap_date, archive_permalink, snap_digest = archived
                    published = snap_date
                    snapshot_date = snap_date.isoformat() if snap_date else None
                    html_digest = snap_digest
                else:
                    soup, published = archived
                    snapshot_date = published.isoformat() if published else None
                retrieved_via = "wayback"
                self._log_decision(url, "archived_fallback",
                                   f"{reason}; used Wayback snapshot <= cutoff",
                                   published, None, retrieved_via, tier)
            else:
                self._log_decision(url, "rejected",
                                   f"{reason}; no Wayback snapshot before cutoff available",
                                   published, modified, "live", tier)
                return []
        elif decision == "rejected":
            self._log_decision(url, "rejected", reason, published, modified, "live", tier)
            return []
        else:
            self._log_decision(url, "accepted", reason, published, modified, "live", tier)

        # Remove nav, footer, script, style, ads
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "aside", "form", "iframe", "noscript"]):
            tag.decompose()

        for tag in soup(["table", "sup", "figcaption"]):
            tag.decompose()
        for tag in soup(class_=re.compile(
                r"hatnote|dablink|shortdescription|infobox|navbox|"
                r"mw-editsection|ambox|metadata|reflist|catlinks|"
                r"printfooter|thumbcaption|vector-page-toolbar|mw-jump-link",
                re.I)):
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

        published_str = published.isoformat() if published else None
        sentences = self._extract_sentences(
            raw_text, url, published_str, tier, retrieved_via,
            html_digest=html_digest,
            archive_permalink=archive_permalink,
            snapshot_date=snapshot_date,
            observed_at=observed_at,
        )
        log.debug("[Scraper] %s → %d sentence(s) [%s]", url[:60], len(sentences), retrieved_via)
        return sentences

    def _log_decision(self, url, decision, reason, published, modified, retrieved_via, tier):
        rejection_code = classify_source_decision(
            decision, reason,
            source_tier=tier,
            min_required_tier=self._min_source_tier,
        )
        if rejection_code:
            self.rejection_tally.add([rejection_code])
        self._decisions.append(SourceDecision(
            url=url,
            decision=decision,
            reason=reason,
            published_at=published.isoformat() if published else None,
            modified_at=modified.isoformat() if modified else None,
            retrieved_via=retrieved_via,
            source_tier=tier,
            cutoff_date=self._policy.cutoff_date.isoformat() if self._policy.cutoff_date else None,
            rejection_code=rejection_code,
        ))
        if decision == "rejected":
            log.warning("[Cutoff] REJECTED %s — %s", url[:70], reason)
        elif decision == "archived_fallback":
            log.info("[Cutoff] ARCHIVED FALLBACK %s — %s", url[:70], reason)

    # ------------------------------------------------------------------
    # FIX (v2): archive.org circuit breaker — per endpoint
    # ------------------------------------------------------------------
    # Two independent breakers: "cdx" for the CDX Server API, "availability"
    # for the /wayback/available endpoint. See WAYBACK_* constants for
    # tunables. Each opens (skips that endpoint) after
    # WAYBACK_BREAKER_THRESHOLD consecutive *network* failures on THAT
    # endpoint specifically — never "no snapshot in this window," which
    # is a normal, valid outcome and must NOT count as a failure. Keeping
    # the two separate matters because in practice one endpoint can be
    # completely dead while the other keeps responding (just not always
    # usefully) — a shared counter lets the healthy one mask the dead
    # one forever. Each half-opens after its own cooldown.
    # ------------------------------------------------------------------

    def _breaker_is_open(self, channel: str) -> bool:
        until_attr  = f"_{channel}_breaker_until"
        streak_attr = f"_{channel}_fail_streak"
        until = getattr(self, until_attr)
        if until is None:
            return False
        if time.time() >= until:
            setattr(self, until_attr, None)
            setattr(self, streak_attr, 0)
            log.info("[Wayback/%s] circuit breaker cooldown elapsed — probing again", channel)
            return False
        return True

    def _breaker_note_failure(self, channel: str) -> None:
        streak_attr = f"_{channel}_fail_streak"
        until_attr  = f"_{channel}_breaker_until"
        streak = getattr(self, streak_attr) + 1
        setattr(self, streak_attr, streak)
        if streak >= WAYBACK_BREAKER_THRESHOLD and getattr(self, until_attr) is None:
            setattr(self, until_attr, time.time() + WAYBACK_BREAKER_COOLDOWN_SECONDS)
            log.warning(
                "[Wayback/%s] %d consecutive failures on this endpoint — opening "
                "its circuit breaker for %ds (the other Wayback endpoint is "
                "tracked separately and keeps being tried normally).",
                channel, streak, WAYBACK_BREAKER_COOLDOWN_SECONDS,
            )

    def _breaker_note_success(self, channel: str) -> None:
        setattr(self, f"_{channel}_fail_streak", 0)
        setattr(self, f"_{channel}_breaker_until", None)

    def _try_wayback_fallback(self, url: str):
        """
        Query the Wayback Machine for the LAST archived snapshot at or
        before the cutoff date, and fetch it if one exists.

        Returns (soup, published_date) on success, or None if no
        eligible snapshot exists. Never raises — a Wayback failure just
        means the fallback isn't available, not a pipeline crash.

        FIX: the previous implementation only queried the
        `/wayback/available` "closest" endpoint, which returns the
        snapshot nearest in TIME to the requested timestamp — not
        necessarily before it. For a densely archived page (Wikipedia,
        government sites, news homepages — edited/re-crawled often),
        the nearest snapshot to the cutoff date is frequently a day or
        two AFTER it, so the old code returned None and the source got
        logged as "no Wayback snapshot before cutoff available" even
        when years of earlier snapshots plainly existed (visible in
        run logs for en.wikipedia.org/wiki/Bangladesh and similar
        pages). We now query the CDX Server API first, which supports
        a real "at-or-before" bound via `to=<cutoff>` + descending
        sort, so it cannot return a post-cutoff snapshot. The old
        availability-API call is kept as a secondary fallback only for
        the rare case CDX itself errors or is rate-limited.
        """
        if self._policy.cutoff_date is None:
            return None

        if self._breaker_is_open("cdx") and self._breaker_is_open("availability"):
            log.debug("[Wayback] both endpoint breakers open — skipping lookup entirely for %s", url[:60])
            return None

        cutoff_str = self._policy.cutoff_date.strftime("%Y%m%d")

        snap_date, snap_url = self._cdx_last_snapshot_before(url, cutoff_str)
        if snap_url is None:
            snap_date, snap_url = self._availability_snapshot_before(url, cutoff_str)
        if snap_url is None:
            return None

        try:
            snap_resp = self._session.get(snap_url, timeout=REQUEST_TIMEOUT)
            snap_resp.raise_for_status()
            soup = BeautifulSoup(snap_resp.content, "lxml")
            return soup, snap_date
        except Exception as exc:
            log.debug("[Wayback] fetching snapshot failed for %s: %s", url[:60], str(exc)[:80])
            return None

    def _cdx_last_snapshot_before(self, url: str, cutoff_str: str):
        """
        Ask the CDX Server API for the last successful capture of `url`
        at or before `cutoff_str` (YYYYMMDD).

        FIX (3rd pass): `limit=-1` with no `from` floor still asks CDX
        to walk a URL's ENTIRE capture history to find the last row
        before slicing. For a lightly-archived page that's cheap; for
        one IA crawls constantly (en.wikipedia.org/wiki/Bangladesh,
        .../Sundarbans, .../History_of_Bangladesh, ...) that history
        can run to thousands of rows, and IA's public CDX endpoint
        throttles/errors those unbounded queries under load — which is
        exactly the "503 Service Unavailable" / read-timeout pattern
        seen in practice, concentrated on the highest-traffic pages.

        Bounding the query with `from=` (a fixed window back from the
        cutoff) caps how much history CDX has to touch regardless of
        how often the page is crawled, so the same negative-limit
        trick becomes cheap again. If a page genuinely has no capture
        in that window (e.g. it didn't exist yet), we widen the window
        once before giving up — still far cheaper than an unbounded
        scan. Each attempt gets one short retry on a transient
        error (503 / timeout / other connection hiccup), since IA's
        CDX endpoint is known to be intermittently flaky independent
        of query cost.

        Returns (date, snapshot_url) or (None, None).
        """
        if self._breaker_is_open("cdx"):
            log.debug("[Wayback/CDX] breaker open — skipping CDX for %s", url[:60])
            return None, None

        cdx_url = "https://web.archive.org/cdx/search/cdx"
        cutoff_date = self._policy.cutoff_date

        # Progressively wider windows back from the cutoff: 3 years,
        # then 15 years. Almost everything this pipeline scrapes
        # (country/city/history articles) is far older than 3 years,
        # so the first, cheap, narrow query is expected to hit most of
        # the time; the wide window is a rarer fallback, not the norm.
        for window_days in (365 * 3, 365 * 15):
            from_str = (cutoff_date - datetime.timedelta(days=window_days)).strftime("%Y%m%d")
            params = {
                "url": url,
                "from": from_str,
                "to": cutoff_str,
                "limit": -1,      # last row of the (ascending, bounded) window
                "filter": "statuscode:200",
                "output": "json",
            }

            for attempt in range(2):  # one retry on a transient error
                try:
                    resp = self._session.get(cdx_url, params=params, timeout=WAYBACK_TIMEOUT)
                    resp.raise_for_status()
                    rows = resp.json()
                    # A response we could parse means archive.org is up,
                    # regardless of whether this particular URL has a
                    # capture in the window — that's a real signal the
                    # service is healthy, so reset the breaker here.
                    self._breaker_note_success("cdx")
                    break
                except Exception as exc:
                    if attempt == 0:
                        log.debug("[Wayback/CDX] transient error for %s, retrying once: %s",
                                  url[:60], str(exc)[:80])
                        time.sleep(2)
                        continue
                    log.warning("[Wayback/CDX] lookup failed for %s — falling back to "
                                "availability API: %s", url[:60], str(exc)[:80])
                    self._breaker_note_failure("cdx")
                    return None, None
            else:
                continue  # both attempts raised — shouldn't reach here, but be safe

            if rows and len(rows) >= 2:
                header   = rows[0]
                ts_idx   = header.index("timestamp")
                orig_idx = header.index("original")
                snap_ts  = rows[1][ts_idx]
                original = rows[1][orig_idx]

                if len(snap_ts) < 8:
                    continue
                snap_date = datetime.date(int(snap_ts[:4]), int(snap_ts[4:6]), int(snap_ts[6:8]))
                if snap_date > cutoff_date:
                    continue  # shouldn't happen with `to=`, but verify anyway

                snapshot_url = f"https://web.archive.org/web/{snap_ts}/{original}"
                return snap_date, snapshot_url
            # else: no capture in this window — widen and try again

        return None, None

    def _availability_snapshot_before(self, url: str, cutoff_str: str):
        """
        Secondary fallback: the old `/wayback/available` lookup, used
        only if the CDX API call above fails outright (network error,
        rate limit, etc). Its "closest" result can still land after
        the cutoff, so that case is explicitly rejected here rather
        than silently accepted.

        Returns (date, snapshot_url) or (None, None).
        """
        if self._breaker_is_open("availability"):
            log.debug("[Wayback/available] breaker open — skipping for %s", url[:60])
            return None, None

        try:
            avail_url = "https://archive.org/wayback/available"
            resp = self._session.get(
                avail_url, params={"url": url, "timestamp": cutoff_str},
                timeout=WAYBACK_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            # Reached and parsed a response — archive.org is up, even if
            # there's no usable snapshot for this particular URL below.
            self._breaker_note_success("availability")
            snapshot = data.get("archived_snapshots", {}).get("closest")
            if not snapshot or not snapshot.get("available"):
                return None, None

            snap_ts = snapshot.get("timestamp", "")  # YYYYMMDDhhmmss
            if len(snap_ts) < 8:
                return None, None
            snap_date = datetime.date(int(snap_ts[:4]), int(snap_ts[4:6]), int(snap_ts[6:8]))
            if snap_date > self._policy.cutoff_date:
                return None, None  # "closest" landed after cutoff — no usable fallback here

            return snap_date, snapshot["url"]
        except Exception as exc:
            log.debug("[Wayback/available] lookup failed for %s: %s", url[:60], str(exc)[:80])
            self._breaker_note_failure("availability")
            return None, None

    # ------------------------------------------------------------------
    # Sentence extraction
    # ------------------------------------------------------------------

    def _extract_sentences(self, text: str, url: str,
                           source_published_at: Optional[str] = None,
                           source_tier: Optional[int] = None,
                           retrieved_via: str = "live",
                           html_digest: Optional[str] = None,
                           archive_permalink: Optional[str] = None,
                           snapshot_date: Optional[str] = None,
                           observed_at: Optional[str] = None) -> List[ScrapedSentence]:
        """
        Split text into sentences and filter by quality.

        Keeps sentences that:
        - Are between MIN_SENTENCE_WORDS and MAX_SENTENCE_WORDS long
        - End with a sentence-ending punctuation (. । ! ?)
        - Contain at least some alphabetic content
        - Are not headers / navigation fragments (no pipe/tab chars)
        - Are not known encyclopedia/navigation boilerplate (BOILERPLATE_RE)
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

            # Skip known encyclopedia/nav boilerplate ("From Wikipedia,
            # the free encyclopedia", disambiguation hatnotes, footnote
            # markers, etc.) — see BOILERPLATE_RE for the full list.
            if BOILERPLATE_RE.search(part):
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

            results.append(ScrapedSentence.from_text(
                part, url,
                source_published_at=source_published_at,
                source_tier=source_tier,
                retrieved_via=retrieved_via,
                html_digest=html_digest,
                archive_permalink=archive_permalink,
                snapshot_date=snapshot_date,
                observed_at=observed_at,
            ))

            if len(results) >= MAX_SENTENCES_PER_URL:
                break

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_blocked(self, url: str) -> bool:
        """Return True if URL's domain is in the blocked list."""
        domain = _bare_host(url)
        if not domain:
            return False
        return any(domain == b or domain.endswith("." + b) for b in BLOCKED_DOMAINS)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _publisher_from_url(url: str) -> str:
    """Infer publisher name from URL domain."""
    try:
        domain = _bare_host(url)
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