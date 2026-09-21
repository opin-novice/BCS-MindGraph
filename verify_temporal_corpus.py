"""
verify_temporal_corpus.py
=========================
Evidence-driven temporal enrichment for ``bcs_gk_facts.json`` (Task 1).

This script NEVER invents a date. Every value written into ``valid_from`` /
``valid_to`` / ``source_published_at`` must be traceable to one of:

  * machine-readable publication metadata on the live cited page
    (``article:published_time``, JSON-LD ``datePublished``/``dateModified``,
    ``<time datetime=...>``) or the HTTP ``Last-Modified`` header;
  * a Wayback Machine capture of the cited URL at or before the holdout
    cutoff (rule 8: "reliable archived evidence");
  * an explicit date that is present in the *fetched source text* and whose
    surrounding context matches the fact's own entities (rule 3).

Anything that cannot be grounded that way is left empty and routed to
``temporal_metadata_review_report.json`` with an explicit reason.

Source tiers come from the project policy (``web_scraper.SOURCE_TIER_MAP`` /
``DEFAULT_SOURCE_TIER``), not from an ad-hoc local table (rule 15).
Relations come from ``kg_builder.CONTROLLED_RELATIONS``; ``STATED_AS`` is
the fallback for facts that are not cleanly decomposable (rule 14).

Usage
-----
    python verify_temporal_corpus.py fetch      # network pass, fills the evidence cache
    python verify_temporal_corpus.py verify     # batched enrichment (cache-driven)
    python verify_temporal_corpus.py validate   # schema / date / id / provenance gate
    python verify_temporal_corpus.py audit      # write temporal_metadata_audit.json
    python verify_temporal_corpus.py triage-unlisted [--apply]
                                                # decide static/dynamic for facts
                                                # whose topic is on neither list
    python verify_temporal_corpus.py all        # fetch -> verify -> validate -> audit

Flags
-----
    --offline          never touch the network; use only cached evidence
    --batch-size N     facts per checkpointed batch (default 40)
    --limit N          only probe the first N distinct URLs (fetch pass)
    --no-wayback       skip the Wayback CDX lookups (faster live-only pass)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent

CORPUS_PATH = ROOT / "bcs_gk_facts.json"
REVIEW_REPORT_PATH = ROOT / "temporal_metadata_review_report.json"
NOTES_PATH = ROOT / "temporal_verification_notes.json"
AUDIT_PATH = ROOT / "temporal_metadata_audit.json"
EVIDENCE_CACHE_PATH = ROOT / "snapshots" / "temporal_source_evidence.json"
CHECKPOINT_PATH = ROOT / "snapshots" / "temporal_verify_checkpoint.json"

WORKFLOW_DIR = ROOT / "model_b_workflow"
UNLISTED_PROPOSALS_PATH = WORKFLOW_DIR / "unlisted_topic_triage_proposals.jsonl"
UNLISTED_REVIEW_QUEUE_PATH = WORKFLOW_DIR / "unlisted_topic_review_queue.csv"
CLASS_ADJUDICATIONS_PATH = WORKFLOW_DIR / "temporal_class_adjudications.jsonl"
# Append-only. The proposals file is a snapshot of what is still open and is
# rewritten each run, so it cannot double as the record of what was decided.
CLASS_DECISION_LEDGER_PATH = WORKFLOW_DIR / "temporal_class_decision_ledger.jsonl"
SEED_PATH = ROOT / "bcs_gk_facts_model_b.json"

# Holdout cutoff t* for the 45th BCS experiment (guideline t_exam - 30d).
CUTOFF_DATE = "2023-04-19"

# The 45th BCS is the *target paper*: it must never be used as evidence.
TARGET_EXAM_NUMBER = 45

OBSERVED_AT = dt.date.today().isoformat()

HTTP_TIMEOUT = 20
WAYBACK_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (compatible; BCS-MindGraph-TemporalVerifier/1.0; "
    "+research corpus provenance check)"
)

DATE_RE_FULL = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")

# ---------------------------------------------------------------------------
# Project policy imports. These are hard requirements, not optional niceties:
# silently falling back to a private tier table would violate rule 15, and a
# private relation set would violate rule 14. Fail loudly instead.
# ---------------------------------------------------------------------------
try:
    from kg_builder import CONTROLLED_RELATIONS, SOURCE_TIERS
except Exception as exc:  # pragma: no cover - import guard
    raise SystemExit(f"FATAL: cannot import controlled vocabulary from kg_builder.py: {exc}")

try:
    from web_scraper import (
        SOURCE_TIER_MAP,
        DEFAULT_SOURCE_TIER,
        infer_source_tier,
        extract_publication_dates,
        _parse_date_string,
    )
except Exception as exc:  # pragma: no cover - import guard
    raise SystemExit(f"FATAL: cannot import source-tier policy from web_scraper.py: {exc}")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯",
                               "0123456789")


def date_key(value: Optional[str]) -> str:
    """Comparable key for partial dates (YYYY / YYYY-MM / YYYY-MM-DD)."""
    if not value:
        return ""
    parts = value.split("-")
    parts += ["00"] * (3 - len(parts))
    return "-".join(p.zfill(2) if i else p.zfill(4) for i, p in enumerate(parts))


def is_valid_partial_date(value: Any) -> bool:
    if value in (None, ""):
        return True
    if not isinstance(value, str) or not DATE_RE_FULL.match(value):
        return False
    try:
        parts = [int(p) for p in value.split("-")]
    except ValueError:
        return False
    if len(parts) >= 2 and not 1 <= parts[1] <= 12:
        return False
    if len(parts) == 3:
        try:
            dt.date(parts[0], parts[1], parts[2])
        except ValueError:
            return False
    return True


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def project_source_tier(url: str) -> Tuple[int, str]:
    """
    Resolve a source tier using the PROJECT policy
    (``web_scraper.infer_source_tier`` over ``SOURCE_TIER_MAP``), and report
    the basis for the assignment.

    This delegates to the project function rather than reimplementing it, so
    there is exactly one tier policy in the codebase.
    """
    host = host_of(url)
    if not host:
        return DEFAULT_SOURCE_TIER, "no source_url; conservative project default (needs review)"
    tier = infer_source_tier(url)
    bare = host[4:] if host.startswith("www.") else host
    if tier != DEFAULT_SOURCE_TIER:
        return tier, "host '%s' matched the project SOURCE_TIER_MAP" % bare
    # Tier 4 is also the map's own value for reputable news, so distinguish an
    # explicit match from a fallback.
    mapped = any(bare == d or bare.endswith("." + d) for d in SOURCE_TIER_MAP)
    if mapped:
        return tier, "host '%s' matched the project SOURCE_TIER_MAP" % bare
    return (
        DEFAULT_SOURCE_TIER,
        "host '%s' is not in the project SOURCE_TIER_MAP; DEFAULT_SOURCE_TIER=%d "
        "applied conservatively (needs review)" % (bare, DEFAULT_SOURCE_TIER),
    )


def exam_numbers(raw: Optional[str]) -> List[int]:
    """Parse BCS exam ordinals (Bengali or ASCII digits) out of ``_bcs_exam``."""
    if not raw:
        return []
    return sorted({int(n) for n in re.findall(r"\d+", str(raw).translate(BENGALI_DIGITS))})


def cited_exam_number(url: str) -> Optional[int]:
    m = re.search(r"/(\d+)(?:th|st|nd|rd)-bcs", url or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Corpus triage (step 1): holdout eligibility and static/dynamic class.
#
# None of this deletes a fact (rule 12). Facts that cannot enter the holdout
# corpus are flagged with a reason and stay in the file.
# ---------------------------------------------------------------------------

# Placeholder / smoke-test rows that carry no citable claim. Matched on exact
# normalized text so a real fact can never be caught by accident.
JUNK_FACT_TEXTS = {
    "bangladesh is a country.",
    "they say that bangladesh has some stuff.",
}

# Topics whose facts are inherently time-varying: an office holder changes, a
# statistic is restated every year. These need a real validity interval and
# must NEVER get valid_from from a bare event year.
DYNAMIC_TOPICS = {
    "Appointments", "Current Affairs", "Economy", "Demography", "Climate",
}

# Surface cues that mark an individual fact as dynamic regardless of topic.
DYNAMIC_CUES = re.compile(
    "বর্তমান|প্রধানমন্ত্রী|রাষ্ট্রপতি|শতকরা|হার|জিডিপি|মার্কিন ডলার|"
    "current|present|as of|growth rate|per capita|gdp|percent|population of",
    re.IGNORECASE,
)

# Events that fix a fact's validity start for good.
STATIC_TOPICS = {
    "History", "Liberation War", "Constitution", "Constitution & Law",
    "Culture", "Language", "Geography", "Flora & Fauna",
}


# ---------------------------------------------------------------------------
# Step 1b: facts whose topic is on neither list.
#
# 135 facts sit in topics -- Bangladesh Affairs, Government, Education, Sports,
# Infrastructure, Defense, International Relations, Science & Technology --
# that are neither uniformly static nor uniformly dynamic. "Government" holds
# both `সংবিধানের ৬৫(১) অনুচ্ছেদ` (fixed until amended) and `সিটি কর্পোরেশন
# ১২টি` (restated every few years). For these the topic carries no signal, so
# each fact is decided from its OWN text by the cue table below -- the same
# justification as relation inference (rule 14): this reads the grammatical
# shape of the claim, it does not invent or infer a date.
#
# Two properties this table must keep:
#
#   * DYNAMIC IS THE SAFE DEFAULT. A dynamic fact mislabelled `static` can be
#     admitted on a pre-cutoff source that no longer supports the claim as the
#     corpus states it -- precisely the leak the holdout exists to catch. A
#     static fact mislabelled `dynamic` merely fails to find a valid_from and
#     drops out of the release corpus. So every dynamic rule is tested before
#     every static rule, and a fact matching both is dynamic.
#   * NO RULE GUESSES. A fact matching nothing stays `unclassified` and goes to
#     the reviewer queue. Coverage is not the goal; a recorded basis is.
#
# This table is consulted ONLY when the topic is on neither list, so it cannot
# reclassify any fact the earlier pass already decided -- including the 69 in
# the frozen Model B seed. test_triage_rules.py pins that.
# ---------------------------------------------------------------------------

# A year token, Gregorian or Bengali digits. Used as corroboration for the
# static rules only; on its own a year proves nothing (rule 2: a year inside
# fact_text is not a publication date).
YEAR_TOKEN = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b|[০-৯]{4}")

# Completed-event relations. Paired with a year token these mark a claim whose
# validity starts at a finished event and does not drift afterwards.
COMPLETED_EVENT_RELATIONS = {
    "founded", "enacted", "adopted_as", "created_by", "recognized_by",
    "occurred_on", "amended", "declared", "won", "member_of",
}

# Surface verbs for the same idea, for rows whose relation is still STATED_AS.
COMPLETED_EVENT_VERBS = re.compile(
    "প্রতিষ্ঠিত|প্রতিষ্ঠা|স্থাপিত|গঠিত|স্বাক্ষরিত|জারি|প্রণীত|কার্যকর|গৃহীত|"
    "চালু|উদ্বোধন|যাত্রা শুরু|আরোহণ|পাস হয়|সদস্যপদ|যোগদান|আইনে পরিণত|সাল থেকে|"
    "founded|established|signed|launched|commissioned|inaugurated|adopted|"
    "enacted|joined|became a member|became a full member|recognize|since ",
    re.IGNORECASE,
)

# (class, rule_id, pattern, rationale). Tested before any static rule.
UNLISTED_DYNAMIC_RULES: List[Tuple[str, str, "re.Pattern[str]", str]] = [
    ("dynamic", "D1_survey_edition", re.compile(
        "অর্থনৈতিক সমীক্ষা|জনশুমারি|আদমশুমারি|গৃহগণনা|শুমারি|"
        "(?:রিপোর্ট|প্রতিবেদন)\\s*[০-৯0-9]|census|economic survey",
        re.IGNORECASE),
     "the claim is pegged to a survey/census edition, which restates the "
     "number every round; its validity is the edition's, not the fact's"),

    ("dynamic", "D2_open_ended_count", re.compile(
        "(?:over|more than|about|approximately|nearly)\\s+[\\d,.]+|"
        "(?:প্রায়|অন্তত)\\s*[০-৯0-9]|"
        "(?:সংখ্যা|মোট)[^।]{0,25}[০-৯0-9][০-৯0-9,.]*\\s*(?:টি|জন)|"
        "[০-৯0-9][০-৯0-9,.]*\\s*(?:কিলোমিটার|বর্গকিলোমিটার|মেগাওয়াট|মিলিয়ন|কোটি|লাখ)|"
        "[\\d,.]+\\s*(?:MW|km|million)\\b",
        re.IGNORECASE),
     "an inventory count or measured total that grows or is revised; it needs "
     "a validity interval, not an event date"),

    ("dynamic", "D3_rate_or_ranking", re.compile(
        "[০-৯0-9]\\s*%|শতাংশ|শতকরা|হার\\b|মাথাপিছু|গড়\\s|আয়ুকাল|ঘনত্ব|"
        "per capita|completion rate|growth rate|"
        "অবস্থান[^।]{0,30}(?:ম|তম)|\\brank",
        re.IGNORECASE),
     "a rate, average or ranking -- recomputed each period by construction"),

    ("dynamic", "D4_mutable_superlative", re.compile(
        "busiest|largest|longest|biggest|highest\\b|flagship|পতাকাবাহী|"
        "বৃহত্তম|দীর্ঘতম|ক্ষুদ্রতম|ব্যস্ততম|সর্বোচ্চ|সর্বনিম্ন|সবচেয়ে",
        re.IGNORECASE),
     "a superlative or rotating designation (`flagship`) that another entity "
     "can take over; true only for as long as the ranking holds"),

    ("dynamic", "D5_projection", re.compile(
        "expected to|projected|প্রত্যাশিত|আশা করা|হওয়ার কথা", re.IGNORECASE),
     "a projection about the future, not a settled fact; it is revised as the "
     "project moves"),

    ("dynamic", "D6_running_tally", re.compile(
        "এ পর্যন্ত|সর্বশেষ|to date|so far|মোট\\s*[০-৯0-9]+\\s*বার",
        re.IGNORECASE),
     "an open running tally (`so far`, `most recently`), which the next "
     "occurrence changes"),
]

# Static pattern rules. These run only after S1_dated_completed_event has
# declined, because S1 is the two-signal rule and therefore the truer reason
# whenever both apply: `আইন ও সালিশ কেন্দ্র ... ১৯৮৬ সালে প্রতিষ্ঠিত` is static
# because of the 1986 founding, not because the sentence happens to contain
# the token `জনগোষ্ঠী`. Both orders label it static; only one records why.
UNLISTED_STATIC_RULES: List[Tuple[str, str, "re.Pattern[str]", str]] = [
    ("static", "S2_legal_provision", re.compile(
        "সংবিধানের|অনুচ্ছেদ|তফসিল|\\bবিধি\\b|\\bধারা\\b|অধ্যাদেশ|Act\\s+\\d{4}",
        re.IGNORECASE),
     "cites a numbered constitutional article, schedule, rule or statute; the "
     "provision's text is fixed until an amendment, which is itself a dated "
     "event"),

    ("static", "S3_ethnographic_cultural", re.compile(
        "নৃ-?গোষ্ঠী|উপজাতি|আদিবাসী|ভাষা পরিবার|উৎসব|নববর্ষ|মাতৃতান্ত্রিক|"
        "পিতৃতান্ত্রিক|জনগোষ্ঠী|পুঞ্জি|নাচ",
        re.IGNORECASE),
     "an ethnographic or cultural attribute (homeland, language family, "
     "festival, kinship system) -- not a quantity and not an office"),

    ("static", "S4_fixed_designation", re.compile(
        "জাতীয় (?:ক্রীড়া|খেলা|দিবস|সংগীত|পাখি|ফল|ফুল)|"
        "national (?:sport|day|anthem|bird|flower|fruit)|"
        "দিবস[^।]{0,25}পালন|observed on",
        re.IGNORECASE),
     "a designation fixed by decree (national symbol, observance date); it "
     "changes only by a further decree, which is a dated event"),

    ("static", "S6_definition", re.compile(
        "পূর্ণরূপ|full form|কে বলা হয়|stands for", re.IGNORECASE),
     "an expansion or definition of a term -- a naming fact, with no validity "
     "interval to check"),
]


def classify_unlisted_topic(fact: Dict[str, Any]) -> Tuple[str, str]:
    """
    Decide static/dynamic for a fact whose topic is on neither topic list.

    Returns ``(temporal_class, basis)``. ``temporal_class`` is
    ``"unclassified"`` when no rule fires -- that is a result, not a failure:
    it routes the fact to the reviewer queue instead of guessing.
    """
    text = str(fact.get("fact_text") or "")
    topic = str(fact.get("topic") or "")

    def hit(cls: str, rule_id: str, matched: str, rationale: str) -> Tuple[str, str]:
        return cls, (
            "topic '%s' is on neither list; rule %s matched %r in fact_text "
            "-- %s" % (topic, rule_id, matched.strip()[:40], rationale)
        )

    for cls, rule_id, pattern, rationale in UNLISTED_DYNAMIC_RULES:
        m = pattern.search(text)
        if m:
            return hit(cls, rule_id, m.group(0), rationale)

    # S1 needs two signals at once, so it is checked in code rather than as a
    # single pattern: a completed-event relation OR verb, corroborated by a
    # year token in the same text. Either alone is too weak -- `member_of`
    # with no year is an open-ended membership, and a bare year is just a
    # number inside a sentence (rule 2).
    year = YEAR_TOKEN.search(text)
    if year:
        rel = str(fact.get("relation") or "")
        verb = COMPLETED_EVENT_VERBS.search(text)
        if rel in COMPLETED_EVENT_RELATIONS or verb:
            signal = ("relation '%s'" % rel) if rel in COMPLETED_EVENT_RELATIONS \
                else ("verb %r" % verb.group(0).strip())
            return "static", (
                "topic '%s' is on neither list; rule S1_dated_completed_event "
                "matched %s with year token %r -- the claim's validity starts "
                "at a finished event and does not drift afterwards"
                % (topic, signal, year.group(0))
            )

    for cls, rule_id, pattern, rationale in UNLISTED_STATIC_RULES:
        m = pattern.search(text)
        if m:
            return hit(cls, rule_id, m.group(0), rationale)

    # S5 is last: a place's location is not a quantity and does not move. It
    # runs only after every dynamic rule has declined, so `সবচেয়ে ছোট ইউনিয়ন`
    # is already dynamic by D4 before it gets here.
    if str(fact.get("relation") or "") == "located_in":
        return "static", (
            "topic '%s' is on neither list; rule S5_geographic_location matched "
            "relation 'located_in' with no dynamic cue -- where something is "
            "located does not change without a dated event" % topic
        )

    return "unclassified", (
        "topic '%s' is on neither list and no cue pattern fired on this "
        "fact_text; referred to the reviewer queue rather than guessed" % topic
    )


# The rule id embedded in a basis string, for reporting. A basis with no rule
# id is the residual case, reported as `none` rather than as a stray token.
RULE_ID_RE = re.compile(r"rule ([DS]\d+_\w+)")


def rule_id_of(basis: str) -> str:
    m = RULE_ID_RE.search(basis or "")
    return m.group(1) if m else "none"


def triage_fact(fact: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify a fact for the holdout corpus. Returns the triage fields; the
    caller writes them onto the fact.
    """
    text = str(fact.get("fact_text") or "")
    url = str(fact.get("source_url") or "")
    cited = cited_exam_number(url)
    attributed = exam_numbers(fact.get("_bcs_exam"))

    reasons: List[str] = []
    eligible = True

    if _norm(text) in JUNK_FACT_TEXTS:
        eligible = False
        reasons.append("placeholder/smoke-test row, not a citable claim")

    if attributed and set(attributed) == {TARGET_EXAM_NUMBER}:
        # The fact was SELECTED by reading the target paper. Re-sourcing it
        # later does not undo that: the corpus builder saw the answer key.
        eligible = False
        reasons.append(
            "selection leakage: this fact's only exam attribution is the 45th BCS "
            "target paper, so including it would let the target paper shape the "
            "source corpus (rule 9)"
        )
    elif cited == TARGET_EXAM_NUMBER and attributed and min(attributed) < TARGET_EXAM_NUMBER:
        reasons.append(
            "cites the 45th BCS paper, but the same content was examined earlier "
            "(%dth BCS), so it is recoverable by re-sourcing to a pre-cutoff document"
            % min(attributed)
        )

    if attributed and min(attributed) > TARGET_EXAM_NUMBER:
        reasons.append(
            "content provenance is a post-cutoff exam (%dth BCS); usable only if "
            "re-sourced to a pre-cutoff document, and subject to the PI decision on "
            "post-cutoff selection bias" % min(attributed)
        )

    if not url:
        eligible = False
        reasons.append("no source_url to re-source from")

    topic = str(fact.get("topic") or "")
    if DYNAMIC_CUES.search(text) or topic in DYNAMIC_TOPICS:
        temporal_class = "dynamic"
        class_basis = "topic '%s' and fact-text cues" % topic
    elif topic in STATIC_TOPICS:
        temporal_class = "static"
        class_basis = "topic '%s' and fact-text cues" % topic
    else:
        # Topic carries no signal -- decide from the fact's own text, or send
        # it to the reviewer queue. See UNLISTED_TOPIC_RULES.
        temporal_class, class_basis = classify_unlisted_topic(fact)

    return {
        "holdout_eligible": eligible,
        "holdout_exclusion_reasons": [] if eligible else reasons,
        "holdout_notes": reasons if eligible else [],
        "temporal_class": temporal_class,
        "temporal_class_basis": class_basis,
        "earliest_exam_attribution": min(attributed) if attributed else None,
        "resourcing_required": True,
    }


# ---------------------------------------------------------------------------
# Relation inference (rule 14)
#
# Assigning a relation is a *vocabulary* decision about the fact's own text,
# not a temporal claim, so it is safe to do even when the cited source cannot
# be verified. Every pattern below is a high-precision surface cue; anything
# that does not match falls back to STATED_AS rather than guessing.
# ---------------------------------------------------------------------------
RELATION_PATTERNS: List[Tuple[str, str]] = [
    ("amended",       "সংশোধন|amendment"),
    ("recognized_by", "স্বীকৃতি|recognit|recognis|recogniz"),
    ("declared",      "ঘোষণা|declar"),
    ("adopted_as",    "গৃহীত হয়|adopted as|adopted in"),
    ("created_by",    "রচয়িতা|রচনা করেন|নকশা|ডিজাইন|designed by|composed by|written by"),
    ("enacted",       "কার্যকর হয়|প্রণীত|enacted|came into force"),
    ("founded",       "প্রতিষ্ঠিত|প্রতিষ্ঠা|গঠিত হয়|founded|established|was set up"),
    ("ruled",         "শাসন করে|শাসনকাল|শাসনামল|ruled|reigned"),
    ("headquarters",  "সদর দপ্তর|headquarter"),
    ("member_of",     "সদস্যপদ|সদস্য হয়|সদস্য রাষ্ট্র|member of|joined the"),
    ("awarded_to",    "পুরস্কার|পদক|সম্মাননা|awarded|prize"),
    ("cultivated_in", "চাষ হয়|উৎপাদিত হয়|cultivat|grown in"),
    ("held_on",       "অনুষ্ঠিত হয়|held on|was held"),
    ("holds_position", "প্রধানমন্ত্রী|রাষ্ট্রপতি|প্রধান বিচারপতি|prime minister|president of|chief justice"),
    ("led_by",        "নেতৃত্বে|নেতৃত্ব দেন|led by|under the leadership"),
    ("located_in",    "অবস্থিত|রাজধানী|located in|situated in|capital of|capital is"),
    ("known_as",      "পরিচিত|উপাধি|known as|called the|nicknamed"),
    ("won",           "জয়লাভ করে|চ্যাম্পিয়ন|শিরোপা|won the|winner of"),
    ("part_of",       "অংশ ছিল|অন্তর্গত|part of"),
    ("migrated_to",   "আগমন ঘটে|উপমহাদেশে আসে|migrat"),
]

COMPILED_RELATION_PATTERNS = [
    (rel, re.compile(pat, re.IGNORECASE)) for rel, pat in RELATION_PATTERNS
]


# --- Tier 2: curated corpus metadata -------------------------------------
#
# `_subtopic` and `_question_type` are hand-curated syllabus metadata already
# present on the corpus rows. They are a far better relation signal than a
# regex over free text, and using them is classification of existing data,
# not inference about the world.

SUBTOPIC_RELATION_CUES: List[Tuple[str, str]] = [
    # (controlled relation, Bengali/English keyword alternation over _subtopic)
    ("awarded_to",    "খেতাব|পুরস্কার|পদক|সম্মাননা"),
    ("cultivated_in", "ফসল|কৃষি|শস্য|চাষ"),
    ("member_of",     "জাতিসংঘ|সদস্য|ওআইসি|সার্ক"),
    ("amended",       "সংশোধন"),
    ("enacted",       "সংবিধান রচনা|মৌলিক অধিকার|রাষ্ট্র পরিচালনার মূলনীতি|আইন বিভাগ"),
    ("ruled",         "শাসন|আমল|বংশ|সাম্রাজ্য"),
    ("part_of",       "জনপদ"),
    ("value_of",      "আয়-ব্যয়|রাজস্ব|উৎপাদন|লেনদেন|জনসংখ্যা|আদমশুমারি|বাজার"),
    ("occurred_on",   "রণকৌশল|আন্দোলন|অপারেশন|গণহত্যা"),
]

COMPILED_SUBTOPIC_CUES = [
    (rel, re.compile(pat, re.IGNORECASE)) for rel, pat in SUBTOPIC_RELATION_CUES
]

# `_question_type` maps to a relation only where the pairing is unambiguous.
# "factual" and "conceptual" are deliberately absent: they describe how the
# question was asked, not what kind of claim the fact makes.
QUESTION_TYPE_RELATIONS: Dict[str, str] = {
    "date_event": "occurred_on",
    "location": "located_in",
}

# person_identification means "who was X?" -- which predicate that is depends
# on the topic the person is being identified within.
PERSON_TOPIC_RELATIONS: Dict[str, str] = {
    "Government": "holds_position",
    "Appointments": "holds_position",
    "Constitution": "holds_position",
    "Constitution & Law": "holds_position",
    "Liberation War": "led_by",
    "History": "led_by",
}


def infer_relation(fact: Dict[str, Any]) -> Tuple[str, str]:
    """
    Return (relation, basis). Falls back to STATED_AS whenever no
    high-precision cue fires -- the controlled vocabulary's documented
    fallback for facts that are not cleanly decomposed (rule 14).
    """
    haystack = " ".join(
        str(fact.get(k) or "") for k in ("fact_text", "_subtopic", "_topic_bn")
    )
    # Tier 1: high-precision surface cue in the fact's own text.
    for relation, pattern in COMPILED_RELATION_PATTERNS:
        m = pattern.search(haystack)
        if m:
            if relation not in CONTROLLED_RELATIONS:  # defensive
                continue
            return relation, (
                "surface cue matched controlled relation '%s'" % relation
            )

    # Tier 2: curated syllabus metadata. `_subtopic` is a hand-assigned
    # category, so a keyword match on it is more reliable than one on prose.
    subtopic = str(fact.get("_subtopic") or "")
    if subtopic:
        for relation, pattern in COMPILED_SUBTOPIC_CUES:
            if pattern.search(subtopic) and relation in CONTROLLED_RELATIONS:
                return relation, (
                    "curated _subtopic '%s' matched controlled relation '%s'"
                    % (subtopic[:60], relation)
                )

    # Tier 3: curated question type, where the pairing is unambiguous.
    qtype = str(fact.get("_question_type") or "")
    topic = str(fact.get("topic") or "")
    if qtype == "person_identification":
        relation = PERSON_TOPIC_RELATIONS.get(topic)
        if relation and relation in CONTROLLED_RELATIONS:
            return relation, (
                "curated _question_type 'person_identification' in topic '%s' "
                "maps to controlled relation '%s'" % (topic, relation)
            )
    elif qtype in QUESTION_TYPE_RELATIONS:
        relation = QUESTION_TYPE_RELATIONS[qtype]
        if relation in CONTROLLED_RELATIONS:
            return relation, (
                "curated _question_type '%s' maps to controlled relation '%s'"
                % (qtype, relation)
            )

    return "STATED_AS", (
        "no high-precision relation cue matched; controlled-vocabulary "
        "fallback STATED_AS applied (fact not cleanly decomposed)"
    )


# ---------------------------------------------------------------------------
# Source evidence acquisition
# ---------------------------------------------------------------------------
def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            # Never let a corrupt cache masquerade as "nothing cached yet" --
            # that would silently downgrade verified facts to unverified.
            print("WARNING: %s is unreadable (%s); treating as empty"
                  % (path.name, exc))
            return default
    return default


def _save_json(path: Path, payload) -> None:
    """
    Atomic write: serialize to a sibling temp file, then replace. A direct
    write_text of a large file can be interrupted mid-flush (a killed batch
    run, a timeout), leaving truncated JSON that silently reads back as an
    empty cache on the next pass.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def fetch_live(url: str) -> Dict[str, Any]:
    """
    Fetch the cited URL and record exactly what the response proves.

    ``meta_published_at`` is only ever filled from machine-readable page
    metadata (rule 8) -- never from a year that merely appears in prose.
    """
    import requests
    from bs4 import BeautifulSoup

    record: Dict[str, Any] = {
        "url": url,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "http_status": None,
        "final_url": None,
        "error": None,
        "content_sha256": None,
        "content_bytes": None,
        "page_title": None,
        "text_excerpt": None,
        "text_len": 0,
        "meta_published_at": None,
        "meta_modified_at": None,
        "http_last_modified": None,
        "reachable": False,
    }
    try:
        resp = requests.get(
            url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        record["http_status"] = resp.status_code
        record["final_url"] = resp.url
        record["content_bytes"] = len(resp.content)
        record["content_sha256"] = hashlib.sha256(resp.content).hexdigest()
        if resp.headers.get("Last-Modified"):
            lm = _parse_date_string(resp.headers["Last-Modified"])
            record["http_last_modified"] = lm.isoformat() if lm else None
        if resp.status_code == 200 and resp.content:
            record["reachable"] = True
            soup = BeautifulSoup(resp.text, "html.parser")
            pub, mod = extract_publication_dates(soup)
            record["meta_published_at"] = pub.isoformat() if pub else None
            record["meta_modified_at"] = mod.isoformat() if mod else None
            if soup.title and soup.title.string:
                record["page_title"] = soup.title.string.strip()[:300]
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            record["text_len"] = len(text)
            record["text_excerpt"] = text[:20000]
    except Exception as exc:
        record["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return record


def wayback_before_cutoff(url: str, cutoff: str) -> Dict[str, Any]:
    """
    Ask the Wayback CDX API for the last 200-capture of ``url`` at or before
    the cutoff. A capture proves the page existed and was reachable before
    the cutoff -- rule 8's "reliable archived evidence". Absence of a capture
    proves nothing on its own and is recorded as such.
    """
    import requests

    out: Dict[str, Any] = {"snapshot_date": None, "snapshot_url": None,
                           "first_capture": None, "error": None}
    cutoff_date = dt.date.fromisoformat(cutoff)
    cutoff_compact = cutoff.replace("-", "")

    # An unbounded `limit=-1` query makes CDX walk a URL's entire capture
    # history before slicing, which is exactly the pattern web_scraper.py
    # documents as timing out on heavily-crawled pages. Bound the window
    # instead, widening once before giving up.
    for window_days in (365 * 3, 365 * 20):
        from_str = (cutoff_date - dt.timedelta(days=window_days)).strftime("%Y%m%d")
        params = {"url": url, "from": from_str, "to": cutoff_compact, "limit": -1,
                  "filter": "statuscode:200", "output": "json",
                  "fl": "timestamp,original"}
        rows = None
        try:
            resp = requests.get(
                "https://web.archive.org/cdx/search/cdx", params=params,
                timeout=WAYBACK_TIMEOUT, headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            rows = resp.json() if resp.text.strip() else []
            out["error"] = None
        except Exception as exc:
            # A transport failure is not evidence of absence -- it is recorded
            # and the URL stays unverified rather than being called "no capture".
            out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
            break
        if rows and len(rows) >= 2:
            ts, original = rows[1][0], rows[1][1]
            out["snapshot_date"] = "%s-%s-%s" % (ts[:4], ts[4:6], ts[6:8])
            out["snapshot_url"] = "https://web.archive.org/web/%s/%s" % (ts, original)
            out["first_capture"] = out["snapshot_date"]
            break
    return out


def mark_catch_all_pages(cache: Dict[str, Any]) -> Dict[str, int]:
    """
    Detect soft-404 / catch-all routes: two or more DIFFERENT paths on the
    same host returning byte-identical content means the server answers every
    path with the same document (an SPA shell or a generic landing page).
    Such a 200 is not evidence that the cited page exists, so it must never
    be allowed to "support" a fact.
    """
    by_host_hash: Dict[Tuple[str, str], List[str]] = {}
    for url, entry in cache.items():
        live = entry.get("live") or {}
        digest = live.get("content_sha256")
        if live.get("http_status") == 200 and digest:
            by_host_hash.setdefault((host_of(url), digest), []).append(url)

    flagged = 0
    for (_, _), urls_sharing in by_host_hash.items():
        if len(urls_sharing) > 1:
            for u in urls_sharing:
                cache[u]["live"]["catch_all_page"] = True
                cache[u]["live"]["catch_all_siblings"] = [
                    x for x in urls_sharing if x != u][:5]
                flagged += 1
    return {"catch_all_urls": flagged}


def build_evidence(urls: List[str], offline: bool, use_wayback: bool,
                   limit: Optional[int] = None) -> Dict[str, Any]:
    """Probe each distinct URL once and cache the result."""
    cache: Dict[str, Any] = _load_json(EVIDENCE_CACHE_PATH, {})
    todo = [u for u in urls if u not in cache]
    if limit is not None:
        todo = todo[:limit]
    if offline:
        if todo:
            print("  [offline] %d URL(s) have no cached evidence; "
                  "they will be treated as unverified." % len(todo))
        mark_catch_all_pages(cache)
        return cache

    for i, url in enumerate(todo, start=1):
        print("  [%3d/%3d] %s" % (i, len(todo), url[:88]))
        entry = {"live": fetch_live(url)}
        if use_wayback:
            entry["wayback"] = wayback_before_cutoff(url, CUTOFF_DATE)
        else:
            entry["wayback"] = {"snapshot_date": None, "snapshot_url": None,
                                "first_capture": None, "error": "skipped (--no-wayback)"}
        cache[url] = entry
        if i % 10 == 0:
            _save_json(EVIDENCE_CACHE_PATH, cache)
        time.sleep(0.2)
    mark_catch_all_pages(cache)
    _save_json(EVIDENCE_CACHE_PATH, cache)
    return cache


# ---------------------------------------------------------------------------
# Does the fetched page actually support the fact? (rule 7)
# ---------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


# Entity strings so generic that finding them on a page proves nothing about
# a specific claim. "Bangladesh" appears on essentially every page in this
# corpus's domain set, so matching it alone is not verification.
GENERIC_ENTITIES = {
    "bangladesh", "bangladesh government", "government of bangladesh",
    "বাংলাদেশ",
    "বাংলাদেশের",
    "সরকার", "বাংলা",
    "country", "government", "state", "people",
}


def _is_generic(probe: str) -> bool:
    return _norm(probe) in GENERIC_ENTITIES


def support_check(fact: Dict[str, Any], live: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decide whether the fetched page text supports ``fact_text``.

    Deliberately strict: the page must contain the fact's own key entities
    (subject, object, and/or the recorded correct answer). A page that merely
    exists is NOT evidence for the claim.
    """
    result = {"supported": False, "probes": [], "hits": [], "misses": [],
              "specific_hits": [], "ratio": 0.0,
              "catch_all": bool(live.get("catch_all_page"))}
    text = _norm(live.get("text_excerpt"))
    if not text or result["catch_all"]:
        # A catch-all route serves the same bytes for every path, so its text
        # cannot evidence any particular claim.
        return result

    probes: List[str] = []
    for pair in (fact.get("subject_entities") or []):
        if isinstance(pair, (list, tuple)) and pair:
            probes.append(str(pair[0]))
    for pair in (fact.get("object_entities") or []):
        if isinstance(pair, (list, tuple)) and pair:
            probes.append(str(pair[0]))
    if fact.get("_correct_answer"):
        probes.append(str(fact["_correct_answer"]))

    probes = [p for p in dict.fromkeys(probes) if len(_norm(p)) >= 3]
    result["probes"] = probes
    if not probes:
        return result

    for p in probes:
        (result["hits"] if _norm(p) in text else result["misses"]).append(p)
    result["ratio"] = round(len(result["hits"]) / len(probes), 3)

    # Only distinctive entities count toward support. Two of them, or one
    # plus a complete match across at least two probes, is the bar.
    specific_hits = [h for h in result["hits"] if not _is_generic(h)]
    result["specific_hits"] = specific_hits
    result["supported"] = (
        len(specific_hits) >= 2
        or (len(specific_hits) >= 1 and result["ratio"] == 1.0 and len(probes) >= 2)
    )
    return result


def supported_event_date(fact: Dict[str, Any], live: Dict[str, Any],
                         support: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Rule 3: a fact's historical event date may become ``valid_from`` ONLY when
    the cited source clearly supports it. That requires, jointly:

      1. the page was actually fetched,
      2. the page supports the fact's entities, and
      3. an explicit date from the fact text also appears in the page text,
         in a window that also contains one of the fact's matched entities.

    Returns (valid_from, basis) or (None, None). Nothing is guessed.
    """
    if not support.get("supported"):
        return None, None
    page = _norm(live.get("text_excerpt"))
    if not page:
        return None, None

    fact_text = str(fact.get("fact_text") or "").translate(BENGALI_DIGITS)
    years = sorted({
        y for y in re.findall(r"(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)", fact_text)
    })
    if len(years) != 1:
        # Zero years: nothing to ground. More than one: ambiguous which one
        # is the validity start -- refuse rather than pick.
        return None, None
    year = years[0]

    # Only a distinctive entity may anchor a date. Anchoring on "Bangladesh"
    # would turn any year anywhere on the page into a validity date.
    anchors = [_norm(h) for h in support.get("specific_hits", [])]
    if not anchors:
        return None, None
    for m in re.finditer(re.escape(year), page):
        window = page[max(0, m.start() - 400): m.end() + 400]
        if any(a and a in window for a in anchors):
            return year, (
                "year %s appears in the fetched source within 400 chars of a "
                "matched fact entity" % year
            )
    return None, None


# ---------------------------------------------------------------------------
# Per-fact enrichment
# ---------------------------------------------------------------------------
def fact_uid(index: int) -> str:
    return "BCSGK-%04d" % index


def enrich_fact(fact: Dict[str, Any], index: int,
                evidence: Dict[str, Any]) -> Dict[str, Any]:
    """
    Populate temporal + provenance metadata for one fact, in place.
    Returns the per-fact verification note record.
    """
    url = str(fact.get("source_url") or "")
    entry = evidence.get(url) or {}
    live = entry.get("live") or {}
    way = entry.get("wayback") or {}

    uid = fact.get("fact_uid") or fact_uid(index)
    fact["fact_uid"] = uid

    tier, tier_basis = project_source_tier(url)
    relation, relation_basis = infer_relation(fact)

    notes: List[str] = []
    blockers: List[str] = []

    # --- leakage screens that apply regardless of whether the URL resolves --
    cited_exam = cited_exam_number(url)
    attributed_exams = exam_numbers(fact.get("_bcs_exam"))
    is_target_paper = cited_exam == TARGET_EXAM_NUMBER
    is_post_cutoff_exam = cited_exam is not None and cited_exam > TARGET_EXAM_NUMBER

    if is_target_paper:
        blockers.append("cited source is the 45th BCS target paper (rule 9)")
    if is_post_cutoff_exam:
        blockers.append(
            "cited source is the %dth BCS, an exam held after the %s cutoff (rule 10)"
            % (cited_exam, CUTOFF_DATE)
        )

    # --- what the network probe actually proved -----------------------------
    if not url:
        verification_status = "unverifiable_no_source"
        notes.append("fact carries no source_url, so nothing can be verified")
        blockers.append("no source_url")
    elif not entry:
        verification_status = "unchecked_no_evidence"
        notes.append("no cached probe result for this URL (run the fetch pass)")
        blockers.append("source never probed")
    elif live.get("error"):
        verification_status = "source_unreachable"
        notes.append("live fetch failed: %s" % live["error"])
        blockers.append("source unreachable")
    elif live.get("http_status") and live["http_status"] != 200:
        verification_status = "source_not_found"
        notes.append("live fetch returned HTTP %s; the cited URL does not resolve "
                     "to a document" % live["http_status"])
        blockers.append("cited URL returns HTTP %s" % live["http_status"])
    else:
        support = support_check(fact, live)
        if support["supported"]:
            verification_status = "verified_supported"
            notes.append("fetched page contains %d/%d of the fact's key entities"
                         % (len(support["hits"]), len(support["probes"])))
        elif support.get("catch_all"):
            verification_status = "source_catch_all_page"
            notes.append("host returns byte-identical content for unrelated paths "
                         "(soft 404 / SPA shell); the cited page does not exist as "
                         "a distinct document")
            blockers.append("cited URL resolves to a catch-all page, not the cited document")
        else:
            verification_status = "fetched_unsupported"
            notes.append("fetched page (HTTP 200) does not contain the fact's key "
                         "entities (%d/%d matched); it does not support fact_text"
                         % (len(support["hits"]), len(support["probes"])))
            blockers.append("cited page does not support fact_text")

    support = support_check(fact, live) if live.get("text_excerpt") else {
        "supported": False, "probes": [], "hits": [], "misses": [],
        "specific_hits": [], "ratio": 0.0}

    # --- source_published_at: only from machine-readable source metadata ----
    source_published_at = None
    published_basis = None
    published_confidence = None
    if live.get("meta_published_at"):
        source_published_at = live["meta_published_at"]
        published_basis = "article/JSON-LD publication metadata on the fetched page"
        published_confidence = "page_metadata"
    elif live.get("meta_modified_at"):
        source_published_at = live["meta_modified_at"]
        published_basis = "JSON-LD dateModified on the fetched page (update date)"
        published_confidence = "page_metadata"
    elif live.get("http_last_modified"):
        source_published_at = live["http_last_modified"]
        published_basis = ("HTTP Last-Modified response header (transport-level "
                           "update date, weaker than page metadata)")
        published_confidence = "http_header"
    else:
        notes.append("no machine-readable publication or update date is visible "
                     "in the source")

    post_cutoff_evidence = bool(
        source_published_at and date_key(source_published_at) > date_key(CUTOFF_DATE)
    )
    if post_cutoff_evidence:
        blockers.append("source_published_at %s is after the %s cutoff (rule 10)"
                        % (source_published_at, CUTOFF_DATE))

    # An archived capture proves the page existed pre-cutoff, but a capture
    # date is NOT a publication date, so it is recorded separately and never
    # promoted into source_published_at.
    archived_before_cutoff = way.get("snapshot_date")
    if archived_before_cutoff:
        notes.append("Wayback capture %s proves the page existed before the cutoff"
                     % archived_before_cutoff)

    # --- valid_from / valid_to ---------------------------------------------
    valid_from, valid_from_basis = supported_event_date(fact, live, support)
    if valid_from and (is_target_paper or is_post_cutoff_exam or post_cutoff_evidence):
        # Never let a blocked source contribute a validity date to the
        # holdout corpus.
        notes.append("candidate valid_from %s withheld: its only evidence is "
                     "blocked for the holdout corpus" % valid_from)
        valid_from, valid_from_basis = None, None
    if not valid_from:
        notes.append("valid_from left empty: no cited source clearly supports an "
                     "event date for this fact (rule 3)")

    # Rule 4: valid_to stays empty unless a source documents supersession.
    # No supersession evidence is collected by this pass, so it stays empty.
    valid_to = None

    fact_text_digits = str(fact.get("fact_text") or "").translate(BENGALI_DIGITS)
    candidate_years = sorted({
        y for y in re.findall(r"(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)", fact_text_digits)
    })

    # --- write back ---------------------------------------------------------
    fact["relation"] = relation
    fact["relation_basis"] = relation_basis
    fact["source_tier"] = tier
    fact["source_tier_basis"] = tier_basis
    fact["observed_at"] = fact.get("observed_at") or OBSERVED_AT
    fact["valid_from"] = valid_from
    fact["valid_to"] = valid_to
    fact["source_published_at"] = source_published_at
    fact["source_published_at_basis"] = published_basis
    fact["source_published_at_confidence"] = published_confidence
    fact["temporal_candidate_years"] = candidate_years
    fact["source_archived_before_cutoff"] = archived_before_cutoff
    fact["source_archive_url"] = way.get("snapshot_url")
    fact["post_cutoff_evidence"] = post_cutoff_evidence
    fact["target_paper_evidence"] = is_target_paper

    # Real content digest of what was actually retrieved. The previous pass
    # stored sha256("url|fact_text"), which is a digest of the corpus row and
    # proves nothing about the source, so it is replaced here.
    fact["source_snapshot_hash"] = live.get("content_sha256")
    fact["source_snapshot_hash_basis"] = (
        "sha256 of the response body retrieved from source_url on %s"
        % (live.get("checked_at") or "n/a")
        if live.get("content_sha256")
        else "no content retrieved; no snapshot digest can be computed"
    )

    fact["verification_status"] = verification_status
    fact["verification_notes"] = "; ".join(notes)
    fact["verified_at"] = OBSERVED_AT

    # Step-1 triage: holdout eligibility + static/dynamic class.
    triage = triage_fact(fact)
    fact.update(triage)
    if not triage["holdout_eligible"]:
        blockers.extend(triage["holdout_exclusion_reasons"])

    resolved = bool(valid_from) and not blockers
    if resolved:
        fact["temporal_review_status"] = "resolved"
        fact["temporal_review_reason"] = ""
    else:
        fact["temporal_review_status"] = "needs_review"
        fact["temporal_review_reason"] = "; ".join(blockers or notes) or \
            "no verified temporal evidence"

    return {
        "fact_uid": uid,
        "corpus_id": fact.get("_corpus_id"),
        "topic": fact.get("topic"),
        "source_url": url,
        "publisher": fact.get("publisher"),
        "bcs_exam": fact.get("_bcs_exam"),
        "attributed_exam_numbers": attributed_exams,
        "cited_exam_number": cited_exam,
        "http_status": live.get("http_status"),
        "fetch_error": live.get("error"),
        "page_title": live.get("page_title"),
        "entity_probes": support.get("probes"),
        "entity_hits": support.get("hits"),
        "entity_specific_hits": support.get("specific_hits"),
        "entity_match_ratio": support.get("ratio"),
        "supports_fact_text": support.get("supported"),
        "source_published_at": source_published_at,
        "source_published_at_basis": published_basis,
        "wayback_snapshot_before_cutoff": archived_before_cutoff,
        "wayback_first_capture": way.get("first_capture"),
        "wayback_error": way.get("error"),
        "source_snapshot_hash": fact["source_snapshot_hash"],
        "source_tier": tier,
        "source_tier_basis": tier_basis,
        "relation": relation,
        "relation_basis": relation_basis,
        "valid_from": valid_from,
        "valid_from_basis": valid_from_basis,
        "valid_to": valid_to,
        "verification_status": verification_status,
        "verification_notes": fact["verification_notes"],
        "blockers": blockers,
        "verified_at": OBSERVED_AT,
    }


# ---------------------------------------------------------------------------
# Validation gate (run after every batch)
# ---------------------------------------------------------------------------
BASELINE_REQUIRED_FIELDS = (
    "fact_text", "subject_entities", "object_entities", "topic",
    "source_url", "publisher",
)
DATE_FIELDS = ("valid_from", "valid_to", "source_published_at", "observed_at",
               "verified_at", "source_archived_before_cutoff")


def validate(facts: List[Dict[str, Any]], scope: Optional[range] = None) -> Dict[str, Any]:
    """
    Structural + policy gate. ``errors`` are fatal (the batch loop stops);
    ``warnings`` are pre-existing input defects that this pass must surface
    but cannot fix without deleting facts or rewriting IDs.
    """
    errors: List[str] = []
    warnings: List[str] = []

    uids = [f.get("fact_uid") for f in facts if f.get("fact_uid")]
    dup_uids = sorted({u for u in uids if uids.count(u) > 1})
    if dup_uids:
        errors.append("duplicate fact_uid: %s" % dup_uids[:10])

    corpus_ids = [f.get("_corpus_id") for f in facts if f.get("_corpus_id")]
    dup_corpus_ids = sorted({c for c in corpus_ids if corpus_ids.count(c) > 1})
    if dup_corpus_ids:
        warnings.append(
            "duplicate _corpus_id in the INPUT corpus: %s -- pre-existing defect; "
            "not auto-resolved because rule 11/12 forbid rewriting IDs or "
            "deleting facts" % dup_corpus_ids
        )

    indices = scope if scope is not None else range(len(facts))
    for i in indices:
        f = facts[i]
        tag = f.get("fact_uid") or ("index %d" % i)
        for field in BASELINE_REQUIRED_FIELDS:
            if field not in f:
                errors.append("%s: lost baseline field '%s'" % (tag, field))
        for field in DATE_FIELDS:
            if not is_valid_partial_date(f.get(field)):
                errors.append("%s: '%s' is not YYYY / YYYY-MM / YYYY-MM-DD: %r"
                              % (tag, field, f.get(field)))
        vf, vt = f.get("valid_from"), f.get("valid_to")
        if vf and vt and date_key(vf) > date_key(vt):
            errors.append("%s: valid_from %s is after valid_to %s" % (tag, vf, vt))
        rel = f.get("relation")
        if rel not in CONTROLLED_RELATIONS:
            errors.append("%s: relation %r is outside CONTROLLED_RELATIONS" % (tag, rel))
        tier = f.get("source_tier")
        if tier not in SOURCE_TIERS:
            errors.append("%s: source_tier %r is outside %s" % (tag, tier, sorted(SOURCE_TIERS)))
        # Unsupported metadata: a validity date requires positive verification.
        if vf and f.get("verification_status") != "verified_supported":
            errors.append("%s: valid_from %s set without a verified supporting "
                          "source (status=%s)" % (tag, vf, f.get("verification_status")))
        if f.get("source_published_at") and not f.get("source_published_at_basis"):
            errors.append("%s: source_published_at set without a recorded basis" % tag)
        if f.get("temporal_review_status") == "needs_review" and \
                not f.get("temporal_review_reason"):
            errors.append("%s: needs_review without a reason" % tag)
        for prov in ("source_url", "source_tier", "source_tier_basis",
                     "source_snapshot_hash", "verification_status",
                     "verification_notes", "verified_at"):
            if prov not in f:
                errors.append("%s: missing provenance field '%s'" % (tag, prov))

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "duplicate_fact_uids": dup_uids,
            "duplicate_corpus_ids": dup_corpus_ids}


# ---------------------------------------------------------------------------
# Cutoff eligibility -- mirrors kg_builder.get_facts_by_topic_as_of /
# strict_temporal_guard, plus the project's own rules 9 and 10.
# ---------------------------------------------------------------------------
def cutoff_eligibility(fact: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if fact.get("holdout_eligible") is False:
        reasons.extend(fact.get("holdout_exclusion_reasons") or ["excluded by triage"])
    if fact.get("target_paper_evidence"):
        reasons.append("evidence is the 45th BCS target paper")
    if fact.get("post_cutoff_evidence"):
        reasons.append("source published after the cutoff")
    cited = cited_exam_number(str(fact.get("source_url") or ""))
    if cited is not None and cited > TARGET_EXAM_NUMBER:
        reasons.append("cited source is a post-cutoff BCS exam")

    vf, vt = fact.get("valid_from"), fact.get("valid_to")
    if not vf and not vt:
        reasons.append("unversioned: no valid_from/valid_to, so the fact cannot be "
                       "interval-checked against the cutoff")
    else:
        lo = date_key(vf) if vf else ""
        hi = date_key(vt) if vt else "9999-99-99"
        if not (lo <= date_key(CUTOFF_DATE) < hi):
            reasons.append("validity interval does not cover the cutoff")
    return (not reasons), reasons


# ---------------------------------------------------------------------------
# Batched driver
# ---------------------------------------------------------------------------
def run_verify(batch_size: int, offline: bool, use_wayback: bool,
               url_limit: Optional[int]) -> int:
    facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    print("Loaded %d facts from %s" % (len(facts), CORPUS_PATH.name))

    urls = list(dict.fromkeys(str(f.get("source_url") or "") for f in facts))
    urls = [u for u in urls if u]
    print("Distinct source URLs: %d" % len(urls))

    print("\n== Source evidence pass ==")
    evidence = build_evidence(urls, offline=offline, use_wayback=use_wayback,
                              limit=url_limit)
    catch_all = mark_catch_all_pages(evidence)
    print("Evidence records cached: %d (catch-all/soft-404 URLs flagged: %d)"
          % (len(evidence), catch_all["catch_all_urls"]))

    notes: List[Dict[str, Any]] = []
    total_batches = (len(facts) + batch_size - 1) // batch_size
    print("\n== Enrichment pass: %d batches of up to %d ==" % (total_batches, batch_size))

    for b in range(total_batches):
        start = b * batch_size
        stop = min(start + batch_size, len(facts))
        for i in range(start, stop):
            notes.append(enrich_fact(facts[i], i, evidence))

        result = validate(facts, scope=range(start, stop))
        if not result["ok"]:
            print("\nBATCH %d/%d FAILED validation -- stopping before the next batch:"
                  % (b + 1, total_batches))
            for e in result["errors"][:20]:
                print("   ERROR %s" % e)
            _save_json(CHECKPOINT_PATH, {"last_good_batch": b, "failed_batch": b + 1,
                                         "errors": result["errors"][:50]})
            return 1

        # checkpoint after every clean batch
        _save_json(CORPUS_PATH, facts)
        _save_json(CHECKPOINT_PATH, {
            "last_good_batch": b + 1, "of": total_batches,
            "facts_processed": stop, "validated": True,
            "warnings": result["warnings"],
            "checkpointed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        })
        print("  batch %2d/%2d  facts %3d-%3d  validated + checkpointed"
              % (b + 1, total_batches, start + 1, stop))

    _save_json(NOTES_PATH, {
        "corpus": CORPUS_PATH.name,
        "cutoff_date": CUTOFF_DATE,
        "generated_at": OBSERVED_AT,
        "method": ("live HTTP fetch of each cited source_url + Wayback CDX lookup "
                   "for a capture at or before the cutoff; entity-overlap support "
                   "check against the fetched page text"),
        "total_notes": len(notes),
        "notes": notes,
    })
    print("\nWrote %s (%d records)" % (NOTES_PATH.name, len(notes)))
    return 0


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def build_reports() -> int:
    facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    result = validate(facts)

    review_entries = []
    eligible, blocked = [], []
    blocked_reasons: Dict[str, int] = {}
    status_counts: Dict[str, int] = {}
    tier_counts: Dict[str, int] = {}
    relation_counts: Dict[str, int] = {}

    for f in facts:
        ok, reasons = cutoff_eligibility(f)
        (eligible if ok else blocked).append(f.get("fact_uid"))
        if not ok:
            for r in reasons:
                blocked_reasons[r] = blocked_reasons.get(r, 0) + 1
        status_counts[str(f.get("verification_status"))] = \
            status_counts.get(str(f.get("verification_status")), 0) + 1
        tier_counts[str(f.get("source_tier"))] = tier_counts.get(str(f.get("source_tier")), 0) + 1
        relation_counts[str(f.get("relation"))] = \
            relation_counts.get(str(f.get("relation")), 0) + 1

        if f.get("temporal_review_status") == "needs_review":
            review_entries.append({
                "fact_uid": f.get("fact_uid"),
                "corpus_id": f.get("_corpus_id"),
                "topic": f.get("topic"),
                "fact_text_excerpt": str(f.get("fact_text") or "")[:160],
                "source_url": f.get("source_url"),
                "publisher": f.get("publisher"),
                "bcs_exam": f.get("_bcs_exam"),
                "verification_status": f.get("verification_status"),
                "temporal_review_reason": f.get("temporal_review_reason"),
                "verification_notes": f.get("verification_notes"),
                "missing_evidence": [
                    k for k in ("valid_from", "source_published_at")
                    if not f.get(k)
                ],
                "source_tier": f.get("source_tier"),
                "source_tier_basis": f.get("source_tier_basis"),
                "temporal_candidate_years": f.get("temporal_candidate_years"),
                "cutoff_blocked_reasons": reasons,
            })

    _save_json(REVIEW_REPORT_PATH, {
        "corpus": CORPUS_PATH.name,
        "cutoff_date": CUTOFF_DATE,
        "generated_at": OBSERVED_AT,
        "total_facts": len(facts),
        "facts_requiring_review": len(review_entries),
        "why_unresolved": (
            "Each entry below could not be temporally annotated from evidence. "
            "No date was estimated or inferred: where the cited source does not "
            "resolve, does not support the claim, or post-dates the holdout "
            "cutoff, the temporal fields were left empty."
        ),
        "facts": review_entries,
    })

    invalid_dates = [e for e in result["errors"] if "is not YYYY" in e]
    unverifiable = [f.get("fact_uid") for f in facts if f.get("verification_status") in
                    ("source_not_found", "source_unreachable", "unverifiable_no_source",
                     "unchecked_no_evidence", "fetched_unsupported",
                     "source_catch_all_page")]

    published_by_confidence: Dict[str, int] = {}
    for f in facts:
        if f.get("source_published_at"):
            k = str(f.get("source_published_at_confidence"))
            published_by_confidence[k] = published_by_confidence.get(k, 0) + 1

    audit = {
        "corpus": CORPUS_PATH.name,
        "cutoff_date": CUTOFF_DATE,
        "generated_at": OBSERVED_AT,
        "total_facts": len(facts),
        "facts_with_verified_valid_from": sum(1 for f in facts if f.get("valid_from")),
        "facts_with_verified_valid_to": sum(1 for f in facts if f.get("valid_to")),
        "facts_with_verified_source_published_at": sum(
            1 for f in facts if f.get("source_published_at")),
        "source_published_at_by_confidence": published_by_confidence,
        "facts_with_source_tier": sum(1 for f in facts if f.get("source_tier") in SOURCE_TIERS),
        "facts_with_relation": sum(1 for f in facts if f.get("relation") in CONTROLLED_RELATIONS),
        "facts_with_specific_relation": sum(
            1 for f in facts if f.get("relation") not in (None, "STATED_AS")),
        "facts_requiring_review": len(review_entries),
        "facts_rejected_source_unverifiable": len(unverifiable),
        "post_cutoff_evidence_detected": sum(1 for f in facts if f.get("post_cutoff_evidence")),
        "target_paper_evidence_detected": sum(1 for f in facts if f.get("target_paper_evidence")),
        "post_cutoff_bcs_exam_sources": sum(
            1 for f in facts
            if (cited_exam_number(str(f.get("source_url") or "")) or 0) > TARGET_EXAM_NUMBER),
        "duplicate_ids": {
            "duplicate_fact_uids": result["duplicate_fact_uids"],
            "duplicate_corpus_ids": result["duplicate_corpus_ids"],
            "count": len(result["duplicate_fact_uids"]) + len(result["duplicate_corpus_ids"]),
        },
        "invalid_date_formats": len(invalid_dates),
        "invalid_date_details": invalid_dates[:20],
        "facts_eligible_for_cutoff": len(eligible),
        "facts_blocked_from_cutoff": len(blocked),
        "blocked_reason_counts": dict(sorted(blocked_reasons.items(),
                                             key=lambda kv: -kv[1])),
        "verification_status_counts": dict(sorted(status_counts.items(),
                                                  key=lambda kv: -kv[1])),
        "source_tier_counts": dict(sorted(tier_counts.items())),
        "relation_counts": dict(sorted(relation_counts.items(), key=lambda kv: -kv[1])),
        "triage": {
            "holdout_eligible": sum(1 for f in facts if f.get("holdout_eligible")),
            "holdout_excluded": sum(1 for f in facts if f.get("holdout_eligible") is False),
            "excluded_target_paper_selection": sum(
                1 for f in facts if f.get("holdout_eligible") is False
                and any("selection leakage" in r
                        for r in (f.get("holdout_exclusion_reasons") or []))),
            "excluded_placeholder_rows": sum(
                1 for f in facts if f.get("holdout_eligible") is False
                and any("placeholder" in r
                        for r in (f.get("holdout_exclusion_reasons") or []))),
            "temporal_class_counts": {
                k: sum(1 for f in facts if f.get("temporal_class") == k)
                for k in ("static", "dynamic", "unclassified")
            },
            "resourcing_candidates": sum(
                1 for f in facts
                if f.get("holdout_eligible") and f.get("resourcing_required")),
        },
        "strict_guard_ready": len(blocked) == 0,
        "strict_guard_blockage": {
            "blocked_fact_count": len(blocked),
            "root_cause": (
                "The corpus's citation layer does not resolve. Of the 164 distinct "
                "cited source_urls, only 19 returned HTTP 200 and 4 of those are "
                "catch-all routes serving identical bytes for unrelated paths. "
                "No cited page exposes a publication date at or before the cutoff, "
                "so no valid_from can be grounded in a source."
            ),
            "why_assigning_a_date_would_be_unsafe": (
                "The years present in fact_text are exam-answer content, not source "
                "publication dates, and rule 2 forbids treating them as such. "
                "Promoting them to valid_from would manufacture the very temporal "
                "metadata the holdout experiment is meant to test, making any "
                "resulting TVR/PCLR score an artifact of the annotation pass rather "
                "than a measurement of the system."
            ),
            "missing_evidence_summary": {
                "cited_url_returns_no_document": sum(
                    1 for f in facts if f.get("verification_status")
                    in ("source_not_found", "source_unreachable")),
                "cited_url_is_catch_all": sum(
                    1 for f in facts
                    if f.get("verification_status") == "source_catch_all_page"),
                "page_loads_but_does_not_support_fact": sum(
                    1 for f in facts
                    if f.get("verification_status") == "fetched_unsupported"),
                "no_source_url_at_all": sum(
                    1 for f in facts
                    if f.get("verification_status") == "unverifiable_no_source"),
                "supported_but_no_pre_cutoff_date": sum(
                    1 for f in facts
                    if f.get("verification_status") == "verified_supported"
                    and not f.get("valid_from")),
            },
        },
        "validation": {
            "ok": result["ok"],
            "error_count": len(result["errors"]),
            "errors": result["errors"][:50],
            "warnings": result["warnings"],
        },
        "blocked_fact_uids": blocked,
    }
    _save_json(AUDIT_PATH, audit)

    printable = {k: v for k, v in audit.items()
                 if k not in ("blocked_fact_uids", "invalid_date_details")}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


# ---------------------------------------------------------------------------
# Offline dry-run of the pipeline's own temporal gate
#
# main-pipeline.py loads the corpus into KnowledgeGraphBuilder and, when
# CUTOFF_DATE and STRICT_TEMPORAL_GUARD are set, raises RuntimeError if any
# topic still contains unversioned facts. This reproduces exactly that stage
# with no API key, no HF calls and no web scraping.
# ---------------------------------------------------------------------------
def run_guard(seed: bool = False) -> int:
    """
    Offline dry-run of the pipeline's temporal gate.

    ``--seed`` runs it over the frozen Model B release instead of the source
    corpus. That is the run that backs the Task 1 acceptance claim, so it has
    to be reproducible from a command rather than quoted from a transcript.

    Model B plumbing note: this function previously called
    ``insert_fact_pipeline`` WITHOUT ``temporal_class`` or the four
    ``temporal_evidence_*`` fields. Those are exactly what the static route
    reads, so every static fact arrived at the guard as `unversioned` and the
    dry-run could not reproduce what main-pipeline.py actually does -- it
    could only ever report FAIL. This is the same plumbing gap recorded in the
    handover; it was fixed in the pipeline loader and missed here.
    """
    from kg_builder import KnowledgeGraphBuilder

    path = SEED_PATH if seed else CORPUS_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    facts = data["facts"] if isinstance(data, dict) and "facts" in data else data
    kg = KnowledgeGraphBuilder()
    topics_seen: Dict[str, List[str]] = {}

    for raw in facts:
        fid = kg.insert_fact_pipeline(
            fact_text=raw["fact_text"],
            subject_entities=[tuple(p) for p in raw.get("subject_entities") or []],
            object_entities=[tuple(p) for p in raw.get("object_entities") or []],
            topic=raw["topic"],
            source_url=raw.get("source_url") or "",
            publisher=raw.get("publisher", ""),
            relation=raw.get("relation"),
            valid_from=raw.get("valid_from"),
            valid_to=raw.get("valid_to"),
            source_published_at=raw.get("source_published_at"),
            source_tier=raw.get("source_tier"),
            status=raw.get("status", "accepted"),
            # The five fields the Model B static route depends on.
            temporal_class=raw.get("temporal_class"),
            temporal_evidence_status=raw.get("temporal_evidence_status"),
            temporal_evidence_date=raw.get("temporal_evidence_date"),
            temporal_evidence_source_url=raw.get("temporal_evidence_source_url"),
            temporal_evidence_snapshot_hash=raw.get("temporal_evidence_snapshot_hash"),
        )
        for key in ("temporal_class", "temporal_evidence_status",
                    "temporal_evidence_date", "temporal_evidence_source_url",
                    "temporal_evidence_snapshot_hash"):
            if key in raw:
                kg.update_fact_attribute(fid, key, raw[key])
        topics_seen.setdefault(raw["topic"], []).append(fid)

    print("Loaded %d facts across %d topic(s) from %s at cutoff %s"
          % (len(facts), len(topics_seen), path.name, CUTOFF_DATE))
    print("Model B static-source evidence route: ENABLED")

    blocked: Dict[str, int] = {}
    static_ok = dynamic_ok = 0
    for topic in sorted(topics_seen):
        violating = kg.strict_temporal_guard(
            topic, CUTOFF_DATE, allow_static_source_evidence=True)
        survivors = kg.get_facts_by_topic_as_of(
            topic, as_of_date=CUTOFF_DATE, allow_static_source_evidence=True)
        usable = [f for f in survivors
                  if f.get("temporal_status") != "unversioned"]
        s = sum(1 for f in usable
                if f.get("temporal_status") == "static_valid_at_cutoff")
        d = len(usable) - s
        static_ok += s
        dynamic_ok += d
        status = "PASS" if not violating else "BLOCKED"
        print("  %-24s loaded=%-4d usable=%-4d (static=%-3d dyn=%-3d) "
              "unversioned=%-4d %s"
              % (topic, len(topics_seen[topic]), len(usable), s, d,
                 len(violating), status))
        if violating:
            blocked[topic] = len(violating)

    print("\n  static_valid_at_cutoff : %d" % static_ok)
    print("  valid_at_cutoff (dyn)  : %d" % dynamic_ok)
    print("  usable total           : %d / %d" % (static_ok + dynamic_ok, len(facts)))
    print("  guard violations       : %d" % sum(blocked.values()))

    if blocked:
        print("\nSTRICT TEMPORAL GUARD (%s): FAIL -- %d topic(s) still contain "
              "unversioned facts: %s" % (CUTOFF_DATE, len(blocked), blocked))
        return 1
    print("\nSTRICT TEMPORAL GUARD (%s): PASS" % CUTOFF_DATE)
    return 0


# ---------------------------------------------------------------------------
# Human class adjudications.
#
# The cue table decides what a sentence's grammar can settle. Two things it
# cannot settle, and that a person has to:
#
#   * the 30 facts no rule fires on;
#   * a fact where the topic prior and a reviewer's reading disagree, e.g.
#     BCSGK-0315 (`জুম চাষ ... পার্বত্য চট্টগ্রাম`), which topic=Economy makes
#     dynamic by blanket rule while RA_1 read it as a static regional claim.
#
# Those decisions land here, in an append-only ledger, and are applied with
# the reviewer's name attached -- never folded back into the pattern rules,
# where one person's reading of one sentence would silently move hundreds of
# other facts.
#
# Three conditions before any record is applied:
#
#   1. `reviewer_status == "approved"`, with a reviewer_id and a reason. A
#      record still reading `pending` is a question, not a decision.
#   2. `previous_temporal_class` matches what the corpus holds right now. If
#      the corpus moved since the reviewer looked, the record is stale and is
#      refused rather than replayed onto a fact they never saw.
#   3. The fact is not in the frozen Model B seed. The seed is frozen; a class
#      change there has to go through a re-freeze, not through this path.
# ---------------------------------------------------------------------------
ADJUDICATION_CLASSES = {"static", "dynamic"}


def load_class_adjudications() -> List[Dict[str, Any]]:
    if not CLASS_ADJUDICATIONS_PATH.exists():
        return []
    records = []
    for line in CLASS_ADJUDICATIONS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def seed_fact_uids() -> set:
    if not SEED_PATH.exists():
        return set()
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    facts = data["facts"] if isinstance(data, dict) and "facts" in data else data
    return {f.get("fact_uid") for f in facts if f.get("fact_uid")}


def applicable_adjudications(
        facts: List[Dict[str, Any]],
        records: Optional[List[Dict[str, Any]]] = None,
        frozen: Optional[set] = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Returns ``(applicable_by_uid, skipped_notes)``. Every rejection is reported
    -- a silently dropped adjudication looks exactly like one that was applied.

    ``records`` and ``frozen`` default to the on-disk ledger and the frozen
    seed; they are parameters so the gates can be tested without writing to
    either file.
    """
    by_uid = {f.get("fact_uid"): f for f in facts}
    if frozen is None:
        frozen = seed_fact_uids()
    applicable: Dict[str, Dict[str, Any]] = {}
    skipped: List[str] = []

    for rec in (load_class_adjudications() if records is None else records):
        uid = rec.get("fact_uid")
        tag = uid or "<no fact_uid>"
        status = rec.get("reviewer_status")
        if status != "approved":
            skipped.append("%s: reviewer_status=%r, not applied" % (tag, status))
            continue
        if not rec.get("reviewer_id") or not rec.get("reason"):
            skipped.append("%s: approved without a reviewer_id and reason" % tag)
            continue
        if rec.get("temporal_class") not in ADJUDICATION_CLASSES:
            skipped.append("%s: temporal_class=%r is outside %s"
                           % (tag, rec.get("temporal_class"),
                              sorted(ADJUDICATION_CLASSES)))
            continue
        if uid not in by_uid:
            skipped.append("%s: no such fact in the corpus" % tag)
            continue
        if uid in frozen:
            skipped.append("%s: in the frozen Model B seed; a class change there "
                           "needs a re-freeze, not an adjudication" % tag)
            continue
        current = by_uid[uid].get("temporal_class")
        if rec.get("previous_temporal_class") != current:
            skipped.append(
                "%s: stale -- reviewer saw %r, corpus now holds %r"
                % (tag, rec.get("previous_temporal_class"), current))
            continue
        applicable[uid] = rec

    return applicable, skipped


# ---------------------------------------------------------------------------
# Step 1b driver: propose, queue, and (on request) apply the class.
# ---------------------------------------------------------------------------
def run_triage_unlisted(apply_changes: bool) -> int:
    """
    Re-triage every fact still sitting at ``temporal_class == "unclassified"``.

    Always writes the full proposal set and a reviewer queue for the residual.
    Only writes the corpus when ``apply_changes`` is set, and then only the
    two class fields, and only on facts that are currently unclassified -- so
    a fact the earlier pass already decided can never be flipped here.
    """
    facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    targets = [f for f in facts if f.get("temporal_class") == "unclassified"]

    proposals, residual = [], []
    counts: Dict[str, int] = {"static": 0, "dynamic": 0, "unclassified": 0}
    rule_counts: Dict[str, int] = {}

    for f in targets:
        cls, basis = classify_unlisted_topic(f)
        rule = rule_id_of(basis)
        counts[cls] = counts.get(cls, 0) + 1
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
        row = {
            "fact_uid": f.get("fact_uid"),
            "corpus_id": f.get("_corpus_id"),
            "topic": f.get("topic"),
            "relation": f.get("relation"),
            "previous_temporal_class": "unclassified",
            "proposed_temporal_class": cls,
            "rule": rule,
            "basis": basis,
            "fact_text_excerpt": str(f.get("fact_text") or "")[:240],
        }
        proposals.append(row)
        if cls == "unclassified":
            residual.append(row)

    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    with UNLISTED_PROPOSALS_PATH.open("w", encoding="utf-8") as fh:
        for row in proposals:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    with UNLISTED_REVIEW_QUEUE_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["fact_uid", "corpus_id", "topic", "relation",
                         "reviewer_class", "reviewer_reason",
                         "fact_text_excerpt"])
        for row in residual:
            writer.writerow([row["fact_uid"], row["corpus_id"], row["topic"],
                             row["relation"], "", "", row["fact_text_excerpt"]])

    print("triage-unlisted: %d fact(s) carried temporal_class='unclassified'"
          % len(targets))
    print("  -> static       : %d" % counts["static"])
    print("  -> dynamic      : %d" % counts["dynamic"])
    print("  -> still unclear: %d (reviewer queue, not guessed)"
          % counts["unclassified"])
    for rule, n in sorted(rule_counts.items()):
        print("     %-28s %d" % (rule, n))
    print("  proposals: %s" % UNLISTED_PROPOSALS_PATH)
    print("  queue    : %s" % UNLISTED_REVIEW_QUEUE_PATH)

    if not apply_changes:
        print("  corpus NOT written (pass --apply to write the class fields)")
        return 0

    decided = {r["fact_uid"]: r for r in proposals
               if r["proposed_temporal_class"] != "unclassified"}
    # Timestamped, never overwritten: a second --apply run must not clobber the
    # backup taken before the first one, which is the only copy of the
    # pre-triage corpus.
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = CORPUS_PATH.with_suffix("%s.%s.bak" % (CORPUS_PATH.suffix, stamp))
    backup.write_text(CORPUS_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    ledger: List[Dict[str, Any]] = []
    applied_at = dt.datetime.now().isoformat(timespec="seconds")

    written = 0
    for f in facts:
        row = decided.get(f.get("fact_uid"))
        if row and f.get("temporal_class") == "unclassified":
            f["temporal_class"] = row["proposed_temporal_class"]
            f["temporal_class_basis"] = row["basis"]
            written += 1
            ledger.append({
                "record_type": "temporal_class_decision",
                "applied_at": applied_at,
                "fact_uid": f.get("fact_uid"),
                "corpus_id": f.get("_corpus_id"),
                "topic": f.get("topic"),
                "from_temporal_class": "unclassified",
                "to_temporal_class": row["proposed_temporal_class"],
                "decided_by": "rule",
                "rule": row["rule"],
                "basis": row["basis"],
            })

    adjudicated, skipped = applicable_adjudications(facts)
    for uid, rec in adjudicated.items():
        f = next(x for x in facts if x.get("fact_uid") == uid)
        previous = f.get("temporal_class")
        f["temporal_class"] = rec["temporal_class"]
        f["temporal_class_basis"] = (
            "adjudicated by %s on %s: %s"
            % (rec["reviewer_id"], rec.get("adjudicated_at") or "unknown date",
               rec["reason"])
        )
        ledger.append({
            "record_type": "temporal_class_decision",
            "applied_at": applied_at,
            "fact_uid": uid,
            "corpus_id": f.get("_corpus_id"),
            "topic": f.get("topic"),
            "from_temporal_class": previous,
            "to_temporal_class": rec["temporal_class"],
            "decided_by": rec["reviewer_id"],
            "rule": "human_adjudication",
            "basis": f["temporal_class_basis"],
        })
    if adjudicated:
        print("  adjudications applied: %d (%s)"
              % (len(adjudicated), ", ".join(sorted(adjudicated))))
    for note in skipped:
        print("  adjudication NOT applied -- %s" % note)

    result = validate(facts)
    if not result["ok"]:
        print("  ABORTED: validate() failed after the in-memory edit; corpus "
              "left untouched")
        for e in result["errors"][:10]:
            print("    ERROR %s" % e)
        return 1

    _save_json(CORPUS_PATH, facts)
    if ledger:
        with CLASS_DECISION_LEDGER_PATH.open("a", encoding="utf-8") as fh:
            for row in ledger:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print("  ledger appended: %d record(s) -> %s"
              % (len(ledger), CLASS_DECISION_LEDGER_PATH.name))
    print("  corpus written: %d fact(s) reclassified (backup at %s)"
          % (written + len(adjudicated), backup.name))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",
                        choices=["fetch", "verify", "validate", "audit",
                                 "guard", "triage-unlisted", "all"])
    parser.add_argument("--seed", action="store_true",
                        help="guard: run against the frozen Model B seed "
                             "instead of the full source corpus")
    parser.add_argument("--apply", action="store_true",
                        help="triage-unlisted: write the decided classes back "
                             "to the corpus (default: propose only)")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--no-wayback", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                        help="only probe the first N uncached URLs")
    args = parser.parse_args(argv)

    if args.command == "fetch":
        facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        urls = [u for u in dict.fromkeys(str(f.get("source_url") or "") for f in facts) if u]
        build_evidence(urls, offline=args.offline,
                       use_wayback=not args.no_wayback, limit=args.limit)
        return 0

    if args.command == "verify":
        return run_verify(args.batch_size, args.offline,
                          not args.no_wayback, args.limit)

    if args.command == "validate":
        facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        result = validate(facts)
        for w in result["warnings"]:
            print("WARNING %s" % w)
        for e in result["errors"][:50]:
            print("ERROR   %s" % e)
        print("validate: %s (%d error(s), %d warning(s), %d facts)"
              % ("PASS" if result["ok"] else "FAIL", len(result["errors"]),
                 len(result["warnings"]), len(facts)))
        return 0 if result["ok"] else 1

    if args.command == "audit":
        return build_reports()

    if args.command == "guard":
        return run_guard(args.seed)

    if args.command == "triage-unlisted":
        return run_triage_unlisted(args.apply)

    rc = run_verify(args.batch_size, args.offline, not args.no_wayback, args.limit)
    if rc != 0:
        return rc
    return build_reports()


if __name__ == "__main__":
    sys.exit(main())
