"""
resource_corpus.py
==================
Step 2 of the Task-1 remediation: find a REAL, datable source for each fact,
as that source existed at or before the holdout cutoff.

Why the MediaWiki revision API
------------------------------
The corpus's cited URLs are synthetic and 145 of 164 return no document, so
there is nothing to verify against. Verifying against the *live* 2026 web
does not help either: a 2026 page cannot evidence a 2023 knowledge state.

The MediaWiki API solves both problems in one call. Asking for the last
revision of an article at or before ``2023-04-19`` returns:

  * the article text exactly as it stood at the cutoff  -> supports or refutes
    the fact, with no post-cutoff contamination;
  * a revision timestamp provably <= the cutoff          -> source_published_at;
  * a stable ``oldid`` permalink to that exact revision  -> source_url that
    cannot drift, plus a content digest for source_snapshot_hash.

``bn.wikipedia.org`` and ``en.wikipedia.org`` are both tier 3 under the
project policy in ``web_scraper.SOURCE_TIER_MAP``.

This script DOES NOT MODIFY THE CORPUS
--------------------------------------
Rule 13 forbids silently replacing an unsupported source. Every result is
written to ``resourcing_proposals.json`` as a *proposal* carrying its own
evidence, for the PI to accept or reject. Applying accepted proposals is a
separate, explicit step (``apply`` subcommand), and even that refuses to run
without ``--i-have-reviewed-the-proposals``.

Methodological note
-------------------
Article *discovery* uses the present-day search index when an exact title
lookup fails. The search index is post-cutoff, so discovery is post-cutoff.
The *evidence* used for verification and dating is strictly the pre-cutoff
revision. Discovery-only leakage is bounded (it can surface an article, never
a fact), but it is recorded per proposal as ``discovery_method`` so the PI can
restrict the corpus to exact-title hits if a stricter reading is preferred.

Usage
-----
    python resource_corpus.py pilot --limit 30      # History/Liberation War sample
    python resource_corpus.py run --limit 200       # broader pass
    python resource_corpus.py report                # summarize proposals
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
CORPUS_PATH = ROOT / "bcs_gk_facts.json"
PROPOSALS_PATH = ROOT / "resourcing_proposals.json"
# Revision text is cached so that re-tuning the date/support logic is an
# offline, seconds-long operation instead of another full crawl.
REVISION_CACHE_DIR = ROOT / "snapshots" / "wiki_revisions"

from verify_temporal_corpus import (  # noqa: E402
    CUTOFF_DATE,
    BENGALI_DIGITS,
    GENERIC_ENTITIES,
    _norm,
    _is_generic,
    date_key,
)

USER_AGENT = (
    "BCS-MindGraph-Resourcer/1.0 (academic temporal-holdout corpus construction; "
    "contact: therblig3@gmail.com)"
)
API_TIMEOUT = 30
RATE_LIMIT_SLEEP = 0.3

# Cutoff instant: the last moment at or before which a revision may exist.
CUTOFF_INSTANT = CUTOFF_DATE + "T23:59:59Z"

BENGALI_RE = re.compile(r"[ঀ-৿]")

WIKIS = {
    "bn": "https://bn.wikipedia.org/w/api.php",
    "en": "https://en.wikipedia.org/w/api.php",
}
WIKI_TIER = 3  # per web_scraper.SOURCE_TIER_MAP


# ---------------------------------------------------------------------------
# MediaWiki access
# ---------------------------------------------------------------------------
def _session():
    import requests

    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _cache_path(lang: str, title: str) -> Path:
    key = hashlib.sha256(("%s|%s" % (lang, _norm(title))).encode("utf-8")).hexdigest()[:20]
    return REVISION_CACHE_DIR / lang / ("%s.json" % key)


def _cache_get(lang: str, title: str) -> Optional[Dict[str, Any]]:
    p = _cache_path(lang, title)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_put(lang: str, title: str, rec: Dict[str, Any]) -> None:
    p = _cache_path(lang, title)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def revision_as_of(session, lang: str, title: str,
                   instant: str = CUTOFF_INSTANT,
                   use_cache: bool = True) -> Dict[str, Any]:
    """
    Return the last revision of ``title`` at or before ``instant``.

    An article that exists today but has no revision before the cutoff did not
    exist then, and is reported as such rather than silently falling back to a
    later revision.
    """
    out: Dict[str, Any] = {
        "lang": lang, "title": title, "exists_now": False,
        "existed_at_cutoff": False, "revid": None, "timestamp": None,
        "content": None, "resolved_title": None, "error": None,
        "from_cache": False,
    }
    if use_cache:
        cached = _cache_get(lang, title)
        # A cached transport error is not a result -- retry those.
        if cached is not None and not cached.get("error"):
            cached["from_cache"] = True
            return cached
    try:
        resp = session.get(
            WIKIS[lang],
            params={
                "action": "query", "format": "json", "formatversion": "2",
                "prop": "revisions", "titles": title, "redirects": "1",
                "rvlimit": "1", "rvdir": "older", "rvstart": instant,
                "rvprop": "ids|timestamp|content", "rvslots": "main",
            },
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        pages = (data.get("query") or {}).get("pages") or []
        page = pages[0] if pages else None
        if page and not page.get("missing"):
            out["exists_now"] = True
            out["resolved_title"] = page.get("title")
            revs = page.get("revisions") or []
            if revs:  # otherwise: exists now, but not at the cutoff
                rev = revs[0]
                out["existed_at_cutoff"] = True
                out["revid"] = rev.get("revid")
                out["timestamp"] = rev.get("timestamp")
                out["content"] = (
                    ((rev.get("slots") or {}).get("main") or {}).get("content"))
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])

    # Cache every definite answer, including "no such article" and "did not
    # exist at the cutoff" -- those are results worth not re-asking for.
    # Transport errors are not cached.
    if use_cache and not out["error"]:
        _cache_put(lang, title, out)
    return out


def search_titles(session, lang: str, query: str, limit: int = 5) -> List[str]:
    """Present-day search, used only to DISCOVER a candidate article title."""
    try:
        resp = session.get(
            WIKIS[lang],
            params={"action": "query", "format": "json", "formatversion": "2",
                    "list": "search", "srsearch": query, "srlimit": str(limit),
                    "srnamespace": "0"},
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        return [r["title"] for r in
                ((resp.json().get("query") or {}).get("search") or [])]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Candidate generation and verification
# ---------------------------------------------------------------------------
def fact_language(fact: Dict[str, Any]) -> str:
    return "bn" if BENGALI_RE.search(str(fact.get("fact_text") or "")) else "en"


def candidate_titles(fact: Dict[str, Any]) -> List[str]:
    """Distinctive entity strings, most specific first."""
    cands: List[str] = []
    for key in ("subject_entities", "object_entities"):
        for pair in (fact.get(key) or []):
            if isinstance(pair, (list, tuple)) and pair:
                val = str(pair[0])
                # A syllabus heading is never an article title; trying it just
                # burns an API call and can redirect into a broad survey page.
                if not is_curriculum_label(fact, val):
                    cands.append(val)
    if fact.get("_correct_answer"):
        cands.append(str(fact["_correct_answer"]))
    for key in ("_subtopic", "_topic_bn"):
        if fact.get(key):
            cands.append(str(fact[key]))

    seen, out = set(), []
    for c in cands:
        c = c.strip().strip(".,;:")
        # Drop slashed alternates like "প্রবাসী/মুজিবনগর সরকার" -> both halves
        parts = [p.strip() for p in c.split("/")] if "/" in c else [c]
        for p in parts:
            if len(p) >= 3 and not _is_generic(p) and _norm(p) not in seen:
                seen.add(_norm(p))
                out.append(p)
    return out


def is_curriculum_label(fact: Dict[str, Any], value: str) -> bool:
    """
    True when `value` is a syllabus heading rather than a real entity.

    In the corpus-derived facts, subject_entities[0] is a copy of `_subtopic`
    ("প্রাচীন বাংলার জনপদসমূহ", "বাঙালি জাতির উদ্ভব ও বিকাশ"). No encyclopedia
    article contains that string, so counting it as a probe caps the match
    ratio below 1.0 and makes every such fact unverifiable no matter how good
    the article is.
    """
    v = _norm(value)
    return any(v == _norm(fact.get(k) or "") for k in ("_subtopic", "_topic_bn") if fact.get(k))


# Short function words that carry no identifying signal.
_STOPWORDS = {
    "এবং", "ছিল", "করে", "তাদের", "প্রধান", "একটি", "মধ্যে", "থেকে",
    "নামে", "পরিচিত", "অর্থ", "সকল", "নিয়ে", "গঠিত", "বলে", "মনে", "করা",
    "হয়েছে", "হিসেবে", "অন্যতম", "বিভিন্ন", "পরে", "প্রথম", "সময়",
    "that", "this", "with", "from", "were", "which", "their", "after", "been",
    "have", "when", "into", "also", "than", "then", "they", "known", "called",
}


def content_tokens(text: str) -> List[str]:
    """Distinctive words from the fact, used to measure article coverage."""
    toks = re.findall(r"[\wঀ-৿]{4,}", str(text or ""))
    out, seen = [], set()
    for t in toks:
        n = _norm(t)
        if n in _STOPWORDS or n.isdigit() or n in seen:
            continue
        seen.add(n)
        out.append(t)
    return out


def entity_probes(fact: Dict[str, Any]) -> List[str]:
    probes = []
    for key in ("subject_entities", "object_entities"):
        for pair in (fact.get(key) or []):
            if isinstance(pair, (list, tuple)) and pair:
                val = str(pair[0])
                if not is_curriculum_label(fact, val):
                    probes.append(val)
    if fact.get("_correct_answer"):
        probes.append(str(fact["_correct_answer"]))
    return [p for p in dict.fromkeys(probes) if len(_norm(p)) >= 3]


def title_is_about_entity(title: str, probes: List[str]) -> bool:
    """
    Require the article to be ABOUT one of the fact's entities, not merely to
    mention it.

    Token coverage alone over-credits common words: a fact about the first
    independent Nawab of Bengal scored 0.83 coverage against "নাটোর জেলা"
    (Natore District) purely because that district article contains ordinary
    history vocabulary plus one name. Containment between the article title
    and an entity is the cheap, reliable aboutness test -- "পুণ্ড্র" inside
    "পুণ্ড্রবর্ধন" passes, "নাটোর জেলা" against the Nawab entities does not.
    """
    t = _norm(title)
    if not t:
        return False
    for p in probes:
        n = _norm(p)
        if len(n) >= 3 and (n in t or t in n):
            return True
    return False


def check_support(fact: Dict[str, Any], content: str,
                  title: str = "") -> Dict[str, Any]:
    """
    Two independent signals, because entity fields alone are unreliable here:

      1. distinctive ENTITY hits (subject/object/answer, curriculum labels
         stripped);
      2. TOKEN COVERAGE -- what fraction of the fact's own content words the
         article contains.

    A single distinctive entity plus high token coverage is real support; a
    single entity on its own is not.
    """
    text = _norm(content)
    probes = entity_probes(fact)
    hits = [p for p in probes if _norm(p) in text]
    specific = [h for h in hits if not _is_generic(h)]
    ratio = round(len(hits) / len(probes), 3) if probes else 0.0

    tokens = content_tokens(fact.get("fact_text"))
    tok_hits = [t for t in tokens if _norm(t) in text]
    coverage = round(len(tok_hits) / len(tokens), 3) if tokens else 0.0

    about = title_is_about_entity(title, probes) if title else True
    evidence = (
        len(specific) >= 2
        or (len(specific) >= 1 and coverage >= 0.5)
        or (len(specific) >= 1 and ratio == 1.0 and len(probes) >= 2)
    )
    return {
        "probes": probes, "hits": hits, "specific_hits": specific, "ratio": ratio,
        "token_count": len(tokens), "token_hits": len(tok_hits),
        "token_coverage": coverage, "title_is_about_entity": about,
        "supported": bool(evidence and about),
    }


# Infobox parameters that carry a validity START for a given relation. These
# are structured fields about the article's own subject, which is what makes
# them evidence rather than a year scraped out of nearby prose.
RELATION_INFOBOX_FIELDS: Dict[str, Tuple[str, ...]] = {
    "founded": ("established", "established_date", "formation", "founded",
                "foundation", "formed", "date_founded", "প্রতিষ্ঠা", "প্রতিষ্ঠাকাল"),
    "enacted": ("date_effective", "date_enacted", "enacted", "commenced",
                "date_passed", "কার্যকর"),
    "held_on": ("date", "start_date", "event_date", "তারিখ"),
    "occurred_on": ("date", "start_date", "তারিখ"),
    "declared": ("date", "proclaimed", "declaration_date"),
    "adopted_as": ("adopted", "adoption", "date_adopted"),
    "holds_position": ("term_start", "দায়িত্ব গ্রহণ"),
    "term_start": ("term_start",),
    "appointed_on": ("term_start", "appointed"),
    "won": ("date", "year"),
    # Relations reachable once curated metadata is used for assignment.
    "ruled": ("reign", "reign_start", "reign-start", "era", "রাজত্বকাল", "শাসনকাল"),
    "led_by": ("term_start", "office_start", "দায়িত্ব গ্রহণ"),
    "member_of": ("date_admitted", "admission", "accession", "membership_date",
                  "date_of_admission"),
    "amended": ("date_effective", "date_enacted", "date", "কার্যকর"),
    "awarded_to": ("year", "date", "award_date", "প্রদান"),
    "created_by": ("published", "publication_date", "date", "প্রকাশ"),
    "recognized_by": ("date", "recognition_date"),
    "part_of": (),          # containment has no validity start in the infobox
    "located_in": (),       # ditto
    "known_as": (),
    "cultivated_in": (),
    "value_of": (),         # dynamic; refused earlier anyway
}

_INFOBOX_PARAM_RE_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _infobox_value(content: str, param: str) -> Optional[str]:
    """Raw value of a ``| param = value`` line in a wikitext infobox."""
    rx = _INFOBOX_PARAM_RE_CACHE.get(param)
    if rx is None:
        rx = re.compile(r"^\s*\|\s*%s\s*=\s*(.+?)\s*$" % re.escape(param),
                        re.IGNORECASE | re.MULTILINE)
        _INFOBOX_PARAM_RE_CACHE[param] = rx
    m = rx.search(content or "")
    return m.group(1) if m else None


def _date_from_wikitext(value: str) -> Optional[str]:
    """
    Pull a date out of a wikitext field value, preserving granularity
    (YYYY, YYYY-MM or YYYY-MM-DD) rather than padding to a false precision.
    """
    if not value:
        return None
    v = value.translate(BENGALI_DIGITS)
    # {{start date|1949|6|23}} / {{birth date|1920|3|17}}
    m = re.search(r"\{\{\s*(?:start[ _]date|birth[ _]date|date)[^}|]*\|\s*"
                  r"(\d{3,4})(?:\s*\|\s*(\d{1,2}))?(?:\s*\|\s*(\d{1,2}))?", v, re.I)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        if y and mo and d:
            return "%04d-%02d-%02d" % (int(y), int(mo), int(d))
        if y and mo:
            return "%04d-%02d" % (int(y), int(mo))
        return "%04d" % int(y)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return m.group(0)
    # "23 June 1949" / "June 23, 1949"
    months = ("january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december")
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})", v)
    if m and m.group(2).lower() in months:
        return "%s-%02d-%02d" % (m.group(3), months.index(m.group(2).lower()) + 1,
                                 int(m.group(1)))
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", v)
    if m and m.group(1).lower() in months:
        return "%s-%02d-%02d" % (m.group(3), months.index(m.group(1).lower()) + 1,
                                 int(m.group(2)))
    m = re.search(r"(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)", v)
    return m.group(1) if m else None


def valid_from_from_infobox(fact: Dict[str, Any], content: str,
                            support: Dict[str, Any]
                            ) -> Tuple[Optional[str], Optional[str]]:
    """
    Read valid_from from the structured infobox field that corresponds to the
    fact's controlled relation.

    This is the principled path: an infobox parameter is an assertion about the
    article's own subject, and the relation tells us WHICH parameter is the
    validity start. Only applied when the article is about the fact's entity,
    so the parameter describes the right thing.
    """
    relation = str(fact.get("relation") or "")
    fields = RELATION_INFOBOX_FIELDS.get(relation)
    if not fields:
        return None, None
    if not support.get("title_is_about_entity"):
        return None, ("infobox not used: the article is not about the fact's "
                      "entity, so its parameters describe something else")
    for param in fields:
        raw = _infobox_value(content, param)
        if not raw:
            continue
        got = _date_from_wikitext(raw)
        if got and date_key(got) <= date_key(CUTOFF_DATE):
            return got, ("infobox parameter '%s' of the pre-cutoff revision "
                         "(relation '%s')" % (param, relation))
    return None, None


def candidate_valid_from(fact: Dict[str, Any], content: str,
                         support: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Propose valid_from ONLY when the pre-cutoff revision itself ties the fact's
    single unambiguous year to one of its distinctive entities.

    Dynamic facts may use a relation-specific infobox validity field, but a
    bare year in fact text is never sufficient for a validity interval.
    """
    if not support.get("supported"):
        return None, "revision does not support the fact"

    # Preferred: a structured infobox field selected by the fact's relation.
    infobox_vf, infobox_basis = valid_from_from_infobox(fact, content, support)
    if infobox_vf:
        return infobox_vf, infobox_basis

    if fact.get("temporal_class") == "dynamic":
        return None, "dynamic fact has no relation-specific infobox validity date"

    years = sorted({y for y in re.findall(
        r"(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)",
        str(fact.get("fact_text") or "").translate(BENGALI_DIGITS))})
    if len(years) != 1:
        return None, ("no single unambiguous year in fact_text (%d found)" % len(years))
    year = years[0]

    page = _norm(content)
    anchors = [_norm(h) for h in support.get("specific_hits", [])]
    if not anchors:
        return None, "no distinctive entity to anchor the year against"
    for m in re.finditer(re.escape(year), page):
        window = page[max(0, m.start() - 400): m.end() + 400]
        if any(a in window for a in anchors):
            return year, ("year %s appears in the pre-cutoff revision within 400 "
                          "chars of a distinctive fact entity" % year)
    return None, "year not found near a distinctive entity in the revision"


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def resource_one(session, fact: Dict[str, Any]) -> Dict[str, Any]:
    lang = fact_language(fact)
    proposal: Dict[str, Any] = {
        "fact_uid": fact.get("fact_uid"),
        "corpus_id": fact.get("_corpus_id"),
        "topic": fact.get("topic"),
        "temporal_class": fact.get("temporal_class"),
        "fact_text_excerpt": str(fact.get("fact_text") or "")[:200],
        "current_source_url": fact.get("source_url"),
        "current_source_status": fact.get("verification_status"),
        "wiki_lang": lang,
        "discovery_method": None,
        "tried_titles": [],
        "outcome": "no_source_found",
        "proposed_source_url": None,
        "proposed_source_published_at": None,
        "proposed_source_tier": None,
        "proposed_valid_from": None,
        "proposed_valid_from_basis": None,
        "proposed_snapshot_hash": None,
        "revision_id": None,
        "revision_timestamp": None,
        "support": None,
        "notes": [],
    }

    titles = candidate_titles(fact)
    # Position in the candidate list = how central the entity is to the fact.
    candidate_rank = {_norm(t): i for i, t in enumerate(titles)}
    tried: List[str] = []

    scored: List[Tuple[Tuple[int, float, int], Dict[str, Any]]] = []

    def consider(title: str, method: str) -> None:
        """
        Evaluate a candidate and keep it as a scored option.

        Taking the FIRST passing candidate is wrong: the entity "Liberation War"
        redirects to the generic "War of independence", which can pass a loose
        support check while a far better article ("Bangladesh Liberation War")
        goes unexamined. Every candidate is scored and the strongest wins.
        """
        tried.append(title)
        rev = revision_as_of(session, lang, title)
        time.sleep(RATE_LIMIT_SLEEP)
        if rev["error"]:
            proposal["notes"].append("%s: %s" % (title, rev["error"]))
            return
        if not rev["exists_now"]:
            return
        if not rev["existed_at_cutoff"]:
            proposal["notes"].append(
                "'%s' exists today but had no revision on or before %s -- the "
                "article did not exist at the cutoff" % (title, CUTOFF_DATE))
            return
        support = check_support(fact, rev["content"] or "",
                                title=rev["resolved_title"] or title)
        if not support["supported"]:
            why = ("article is not about any of the fact's entities"
                   if not support["title_is_about_entity"]
                   else "%d/%d entities, %.0f%% token coverage"
                        % (len(support["hits"]), len(support["probes"]),
                           100 * support["token_coverage"]))
            proposal["notes"].append(
                "'%s' revision %s does not support the fact (%s)"
                % (rev["resolved_title"] or title, rev["revid"], why))
            return
        # Ranking, most significant first:
        #
        #  1. title exactness -- the article NAMES the entity rather than
        #     merely containing it;
        #  2. candidate rank -- candidates are ordered subject, object, answer,
        #     so an earlier one is what the fact is actually about;
        #  3. distinctive entity hits, then token coverage.
        #
        # Entity count cannot lead: a broad article ("Pakistan") mentions more
        # of a fact's entities than the precise one ("Lahore Resolution"), so
        # ranking by count alone systematically picks the vaguer source.
        resolved = _norm(rev["resolved_title"] or "")
        probes_n = [_norm(p) for p in support["probes"]]
        exactness = 2 if resolved in probes_n else (
            1 if any(p in resolved or resolved in p for p in probes_n if len(p) >= 3) else 0)
        rank_bonus = -candidate_rank.get(_norm(title), 99)
        scored.append((
            (exactness, rank_bonus, len(support["specific_hits"]),
             support["token_coverage"]),
            {"rev": rev, "support": support, "method": method, "queried": title},
        ))

    for title in titles[:4]:
        consider(title, "exact_title")

    if not scored and titles:
        for found in search_titles(session, lang, titles[0])[:3]:
            if _norm(found) in {_norm(t) for t in tried}:
                continue
            consider(found, "search_index")

    proposal["tried_titles"] = tried
    if not scored:
        return proposal

    scored.sort(key=lambda s: s[0], reverse=True)
    hit = scored[0][1]
    proposal["discovery_method"] = hit["method"]
    proposal["alternatives_considered"] = [
        {"title": s[1]["rev"]["resolved_title"],
         "specific_hits": len(s[1]["support"]["specific_hits"]),
         "token_coverage": s[1]["support"]["token_coverage"]}
        for s in scored[1:]
    ]

    rev, support = hit["rev"], hit["support"]
    ts_date = (rev["timestamp"] or "")[:10]
    if ts_date and date_key(ts_date) > date_key(CUTOFF_DATE):
        # Defensive: the API contract should make this impossible.
        proposal["outcome"] = "rejected_post_cutoff_revision"
        proposal["notes"].append("revision timestamp %s is after the cutoff" % ts_date)
        return proposal

    vf, vf_basis = candidate_valid_from(fact, rev["content"] or "", support)

    proposal.update({
        "outcome": "proposed" if vf else "proposed_without_valid_from",
        "proposed_source_url": "https://%s.wikipedia.org/w/index.php?oldid=%s"
                               % (lang, rev["revid"]),
        "proposed_source_published_at": ts_date,
        "proposed_source_tier": WIKI_TIER,
        "proposed_valid_from": vf,
        "proposed_valid_from_basis": vf_basis,
        "proposed_snapshot_hash": hashlib.sha256(
            (rev["content"] or "").encode("utf-8")).hexdigest(),
        "revision_id": rev["revid"],
        "revision_timestamp": rev["timestamp"],
        "resolved_title": rev["resolved_title"],
        "support": {k: support[k] for k in
                    ("probes", "hits", "specific_hits", "ratio",
                     "token_count", "token_hits", "token_coverage",
                     "title_is_about_entity")},
    })
    return proposal


def select_facts(facts: List[Dict[str, Any]], pilot: bool,
                 limit: Optional[int]) -> List[Dict[str, Any]]:
    pool = [f for f in facts if f.get("holdout_eligible")]
    if pilot:
        pool = [f for f in pool
                if f.get("topic") in ("History", "Liberation War")
                and f.get("temporal_class") == "static"]
    return pool[:limit] if limit else pool


def run(pilot: bool, limit: Optional[int], resume: bool = False) -> int:
    facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    if not any("holdout_eligible" in f for f in facts):
        print("ERROR: corpus has no triage fields. Run:\n"
              "  python verify_temporal_corpus.py all --offline")
        return 1

    session = _session()
    existing = {}
    if PROPOSALS_PATH.exists():
        try:
            for p in json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))["proposals"]:
                existing[p["fact_uid"]] = p
        except Exception:
            existing = {}

    selected = select_facts(facts, pilot, None)
    if resume:
        # A full pass exceeds one command window, so skip facts already
        # decided and let the run be restarted until the pool is empty.
        before = len(selected)
        selected = [f for f in selected if f.get("fact_uid") not in existing]
        print("Resuming: %d already decided, %d remaining"
              % (before - len(selected), len(selected)))
    if limit:
        selected = selected[:limit]

    print("Re-sourcing %d fact(s) against pre-cutoff revisions (cutoff %s)\n"
          % (len(selected), CUTOFF_DATE))

    for i, fact in enumerate(selected, start=1):
        p = resource_one(session, fact)
        existing[p["fact_uid"]] = p
        flag = {"proposed": "OK  ", "proposed_without_valid_from": "SRC ",
                "no_source_found": "--  ",
                "rejected_post_cutoff_revision": "REJ "}.get(p["outcome"], "??  ")
        print("  [%3d/%3d] %s %s  %s" % (
            i, len(selected), flag, p["fact_uid"],
            (p.get("resolved_title") or (p["tried_titles"] or ["-"])[0])[:56]))
        if i % 10 == 0:
            _write_proposals(existing)

    _write_proposals(existing)
    print("\nWrote %s (%d proposals)" % (PROPOSALS_PATH.name, len(existing)))
    return report()


def _write_proposals(by_uid: Dict[str, Any]) -> None:
    payload = {
        "generated_at": dt.date.today().isoformat(),
        "cutoff_date": CUTOFF_DATE,
        "corpus": CORPUS_PATH.name,
        "status": "PROPOSALS ONLY -- the corpus has not been modified",
        "method": ("MediaWiki revision API: last revision at or before the cutoff; "
                   "entity-overlap support check against that revision's text"),
        "total": len(by_uid),
        "proposals": sorted(by_uid.values(), key=lambda p: p["fact_uid"] or ""),
    }
    tmp = PROPOSALS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(PROPOSALS_PATH)


def report() -> int:
    if not PROPOSALS_PATH.exists():
        print("No proposals yet.")
        return 1
    data = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    props = data["proposals"]
    counts: Dict[str, int] = {}
    for p in props:
        counts[p["outcome"]] = counts.get(p["outcome"], 0) + 1
    disc: Dict[str, int] = {}
    for p in props:
        if p.get("discovery_method"):
            disc[p["discovery_method"]] = disc.get(p["discovery_method"], 0) + 1

    n = len(props)
    usable = counts.get("proposed", 0)
    sourced = usable + counts.get("proposed_without_valid_from", 0)
    print("\n=== re-sourcing results (%d facts) ===" % n)
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("  %4d  %s" % (v, k))
    print("\n  pre-cutoff source found : %d/%d (%.0f%%)"
          % (sourced, n, 100.0 * sourced / n if n else 0))
    print("  + usable valid_from     : %d/%d (%.0f%%)"
          % (usable, n, 100.0 * usable / n if n else 0))
    print("  discovery: %s" % (disc or "n/a"))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["pilot", "run", "report"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--resume", action="store_true",
                    help="skip facts that already have a proposal")
    args = ap.parse_args(argv)

    if args.command == "report":
        return report()
    if args.command == "pilot":
        return run(pilot=True, limit=args.limit or 30, resume=args.resume)
    return run(pilot=False, limit=args.limit, resume=args.resume)


if __name__ == "__main__":
    sys.exit(main())
