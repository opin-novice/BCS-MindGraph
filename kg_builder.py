"""
kg_builder.py
=============
Bitemporal Knowledge Graph Builder for BCSBatighor GK.

This version implements the schema from the faculty guideline, section 6
("Dynamic bitemporal knowledge graph"):

    f = (s, r, o, [t_valid_start, t_valid_end),
                  [t_obs_start, t_obs_end),
                  provenance, confidence)

Key change from the previous version: facts are no longer just free-text
blobs. Each fact now optionally carries:

  - relation            controlled predicate, e.g. "held_position"
  - valid_from/valid_to  when the fact was true in the WORLD (may be open)
  - observed_at          when THIS SYSTEM learned the fact (ingestion time)
  - source_tier          1 (official) .. 5 (secondary/coaching) per §7.1
  - status               accepted / disputed / superseded / pending
  - confidence           calibrated 0-1

Facts sharing the same (subject, relation) are treated as a *version
chain*: inserting a new one with a later valid_from closes out the old
fact's valid_to and marks it "superseded" instead of silently coexisting
or being overwritten (guideline §6.1: "Never overwrite a changed fact").

Backward compatibility: every old call — add_fact(text, ...),
insert_fact_pipeline(fact_text=..., subject_entities=..., ...) — still
works with its old arguments. The new bitemporal fields are optional
keyword arguments with safe defaults, so existing pipeline code
(main-pipeline.py) does not need to change to keep running. To get real
temporal versioning you must start passing `relation`, `valid_from`,
`valid_to`, `source_published_at`, and `source_tier` as they become
available upstream (entity/relation extraction, web_scraper.py).
"""

import networkx as nx
import uuid
import datetime
import hashlib
import os
import pickle
import re
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Controlled vocabularies (guideline §6.2 / §6.4)
# ---------------------------------------------------------------------------

ALLOWED_ENTITY_SUBTYPES = {
    "PERSON", "ORGANIZATION", "COUNTRY", "PLACE",
    "CITY", "RIVER", "MOUNTAIN", "FOREST", "OCEAN",
    "INSTITUTION", "EVENT", "DOCUMENT", "TREATY",
    "AWARD", "LANGUAGE", "CURRENCY", "ANIMAL", "PLANT",
    # --- added deliberately (see comment below), not ad hoc ---
    "ETHNIC_GROUP",  # peoples/tribes/nations as a collective, not an
                      # individual PERSON and not a COUNTRY/PLACE.
                      # Grounded in corpus frequency: Bangladesh Affairs
                      # subtopic "জাতি, গোষ্ঠী ও উপজাতি" (16 facts) plus
                      # History facts about আর্য/বাঙালি জাতি/অস্ট্রিক
                      # জনগোষ্ঠী that had no valid subtype under the
                      # original vocabulary.
    "DYNASTY",        # ruling houses/empires/sultanates as a political
                      # entity distinct from a single PERSON (ruler) or
                      # modern ORGANIZATION. Grounded in History subtopics
                      # "প্রাচীন ভারতের সাম্রাজ্যসমূহ" (7), "বাংলার
                      # মধ্যযুগ" (24), and colonial-era "শাসন" subtopics
                      # (British/Muslim/East India Company rule, 18
                      # combined) which are governance-by-dynasty facts.
    "SYMBOL",         # national symbols (flag, anthem, emblem) as their
                      # own entity type, distinct from a DOCUMENT.
                      # Grounded in the Culture topic, which is almost
                      # entirely this: "জাতীয় প্রতীকসমূহ" (5), "জাতীয়
                      # প্রতীক ও সংগীত" (4), "জাতীয় পতাকা" (3) — 12 of
                      # Culture's 13 facts.
}

# Small, high-precision relation vocabulary (guideline: "a small,
# high-precision relation set is better than hundreds of inconsistent
# predicates"). Extend deliberately, not ad hoc.
CONTROLLED_RELATIONS = {
    "holds_position", "appointed_on", "term_start", "term_end",
    "member_of", "joined_on", "chairs", "headquarters",
    "elected_as", "succeeded", "won", "awarded_to",
    "hosted_by", "held_on", "occurred_on", "led_by", "located_in",
    "value_of", "measured_in", "reporting_period", "source_agency",
    # --- added deliberately, grounded in recurring subtopic patterns
    # the original (office-holding-centric) vocabulary didn't cover ---
    "founded",        # establishment of a civilization/dynasty/institution
    "ruled",          # a dynasty/empire/colonial regime governed a place
                      # or period (History: "শাসন" subtopics, 42+ facts)
    "migrated_to",    # population movement (early History facts)
    "part_of",        # hierarchical/geographic containment (e.g. a
                      # জনপদ as part of ancient Bengal)
    "known_as",       # alternate name / epithet for the same entity
    "declared",       # formal declarations (e.g. declaration of
                      # independence -- "স্বাধীনতার ঘোষণা")
    "recognized_by",  # diplomatic recognition (Liberation War subtopic
                      # "বিভিন্ন দেশ কর্তৃক বাংলাদেশের স্বীকৃতি", 7 facts)
    "adopted_as",     # official adoption of a symbol/policy (Culture's
                      # national-symbol facts; Constitution amendments)
    "created_by",     # authorship/design/composition credit (flag
                      # designer, anthem composer, document author)
    "enacted",        # a law/constitution/act coming into force
    "amended",        # a constitutional/legal amendment ("সংবিধান
                      # সংশোধন", 6 facts)
    "cultivated_in",  # crop-to-region/season link (Economy's
                      # agriculture subtopics, ~19 facts)
    # Fallback for facts that are not yet decomposed into a clean relation
    "STATED_AS",
}

# Source tiers per guideline §7.1 (1 = most authoritative)
SOURCE_TIERS = {1, 2, 3, 4, 5}

FACT_STATUSES = {"accepted", "disputed", "superseded", "pending"}

# --- Model B: combined static/dynamic temporal model -----------------------
#
# Most BCS GK facts are TIMELESS ("Dhaka is the capital"). Demanding a
# valid_from/valid_to interval for them is a category error, and refusing them
# would empty the holdout corpus. Model B therefore admits two distinct proofs
# that a fact was true and known at t*:
#
#   dynamic  -> a verified validity interval covering t* (valid_from/valid_to)
#   static   -> a verified source that DEMONSTRABLY existed at or before t*
#
# The static route is evidence-based, not label-based: a fact must carry all
# three of temporal_class, temporal_evidence_status and a dated
# temporal_evidence_date. Trusting temporal_class alone would turn every
# misclassification into a silent leak, which is the exact failure the holdout
# exists to detect.
#
# temporal_evidence_date is kept SEPARATE from source_published_at on purpose:
# a MediaWiki revision timestamp proves when the evidence existed, not when
# the underlying claim was published. Conflating them would corrupt the audit
# trail.
TEMPORAL_CLASSES = {"static", "dynamic", "unclassified",
                    "blocked", "needs_clarification"}
STATIC_EVIDENCE_VERIFIED = "verified_pre_cutoff_source"

_DATE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "day"),
    (re.compile(r"^\d{4}-\d{2}$"), "month"),
    (re.compile(r"^\d{4}$"), "year"),
]


def _normalize_date(value) -> Optional[Tuple[str, str]]:
    """
    Normalize a date input into (iso_string, granularity).

    Accepts None, a datetime.date/datetime, or a string already in
    YYYY, YYYY-MM, or YYYY-MM-DD form. Does NOT invent missing
    day/month (guideline §6.5: "represent the granularity honestly").

    Returns None if value is None/empty. Raises ValueError for anything
    that isn't a recognizable date, so bad dates fail loudly at
    insertion time rather than corrupting the temporal index silently.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()[:10], "day"
    value = str(value).strip()
    for pattern, granularity in _DATE_PATTERNS:
        if pattern.match(value):
            return value, granularity
    raise ValueError(
        f"Unrecognized date format: {value!r}. "
        f"Use YYYY, YYYY-MM, or YYYY-MM-DD."
    )


def _date_key(date_str: Optional[str]) -> str:
    """
    Sortable/comparable key for partial dates. None (open-ended /
    unknown) sorts as +inf for valid_to and -inf for valid_from context
    is handled by the caller, not here — this just pads for comparison.
    """
    if date_str is None:
        return ""
    parts = date_str.split("-")
    parts += ["01"] * (3 - len(parts))
    return "-".join(parts)


class KnowledgeGraphBuilder:
    def __init__(self):
        self.graph = nx.MultiDiGraph()
        self.topic_stats = {}
        # Version index: (subject_node_id, relation) -> list of fact_ids,
        # in insertion order. Lets us find "the current open fact" for a
        # subject-relation pair without scanning the whole graph.
        self._version_index: Dict[Tuple[str, str], List[str]] = {}

    # -----------------------------
    # Utility
    # -----------------------------
    def _generate_id(self, prefix):
        # graph.add_node silently merges attributes into an existing node
        # on an id collision rather than raising, so an 8-hex-char
        # collision would silently fuse two unrelated facts/entities.
        # Regenerate rather than let that happen.
        for _ in range(10):
            candidate = f"{prefix}_{uuid.uuid4().hex[:8]}"
            if not self.graph.has_node(candidate):
                return candidate
        # Astronomically unlikely to ever be reached; fall back to a
        # collision-proof id rather than looping forever.
        return f"{prefix}_{uuid.uuid4().hex}"

    def _normalize_name(self, name):
        return name.strip().replace(" ", "_")

    def _snapshot_hash(self, url: str, text: str) -> str:
        """
        Cheap provenance fingerprint (guideline §6.3: source_url +
        snapshot_hash). This is NOT a real page snapshot — it's a hash of
        what we extracted, so if the same URL later yields different
        text we can detect drift. Replace with a real archived-page hash
        once web_scraper.py stores raw HTML/text snapshots.
        """
        return hashlib.sha256(f"{url}|{text}".encode("utf-8")).hexdigest()[:16]

    # -----------------------------
    # Node Creation
    # -----------------------------

    def add_entity(self, name, subtype):
        subtype_upper = subtype.upper()
        if subtype_upper not in ALLOWED_ENTITY_SUBTYPES:
            print(f"[add_entity] WARNING: subtype '{subtype}' not in allowed set. "
                  f"Mapping to closest or using as-is.")

        normalized = self._normalize_name(name)
        node_id = f"ENTITY_{normalized}"

        if not self.graph.has_node(node_id):
            self.graph.add_node(
                node_id,
                type="ENTITY",
                subtype=subtype_upper,
                name=name
            )
        return node_id

    def add_fact(self, text,
                 language_confidence=1.0,
                 extraction_confidence=1.0,
                 source_reliability=1.0,
                 temporal_freshness=1.0,
                 relation: Optional[str] = None,
                 valid_from=None,
                 valid_to=None,
                 observed_at=None,
                 source_tier: Optional[int] = None,
                 status: str = "accepted",
                 confidence: Optional[float] = None,
                 temporal_class: Optional[str] = None,
                 temporal_evidence_status: Optional[str] = None,
                 temporal_evidence_date=None,
                 temporal_evidence_source_url: Optional[str] = None,
                 temporal_evidence_snapshot_hash: Optional[str] = None):
        """
        Create a FACT node.

        New bitemporal/provenance parameters (all optional, all default
        to "unknown"/open rather than guessing):

        relation      controlled predicate label (see CONTROLLED_RELATIONS).
                      If omitted, defaults to "STATED_AS" — an explicit
                      marker meaning "this fact has not yet been
                      decomposed into a clean relation," rather than
                      silently pretending it has one.
        valid_from    when the fact became true in the world (str/date).
        valid_to      when it stopped being true; None = still open/valid.
        observed_at   when THIS SYSTEM ingested the fact. Defaults to now.
                      Deliberately separate from valid_from/valid_to.
        source_tier   1-5 per the source hierarchy (§7.1). None = unknown.
        status        accepted / disputed / superseded / pending.
        confidence    calibrated 0-1. If omitted, derived as the mean of
                      the four quality subscores (keeps old behaviour
                      meaningful without forcing callers to compute it).

        Model B (combined static/dynamic) parameters:

        temporal_class            "static" / "dynamic" / ... See
                                  TEMPORAL_CLASSES. A timeless fact is
                                  "static" and is proved by its evidence
                                  date rather than a validity interval.
        temporal_evidence_status  must be STATIC_EVIDENCE_VERIFIED for the
                                  static route to apply.
        temporal_evidence_date    when the supporting source demonstrably
                                  existed. Deliberately NOT the same field
                                  as source_published_at: a revision
                                  timestamp dates the evidence, not the
                                  claim.
        """
        fact_id = self._generate_id("FACT")

        relation = relation or "STATED_AS"
        if relation not in CONTROLLED_RELATIONS:
            print(f"[add_fact] WARNING: relation '{relation}' not in controlled "
                  f"vocabulary CONTROLLED_RELATIONS. Add it deliberately if this "
                  f"is a genuine new relation type, not a typo.")

        if status not in FACT_STATUSES:
            raise ValueError(f"status must be one of {FACT_STATUSES}, got {status!r}")
        if source_tier is not None and source_tier not in SOURCE_TIERS:
            raise ValueError(f"source_tier must be one of {SOURCE_TIERS}, got {source_tier!r}")

        if temporal_class is not None and temporal_class not in TEMPORAL_CLASSES:
            raise ValueError(
                f"temporal_class must be one of {TEMPORAL_CLASSES}, got {temporal_class!r}")

        valid_from_norm = _normalize_date(valid_from)
        valid_to_norm = _normalize_date(valid_to)
        # Fails loudly on a malformed evidence date, the same way validity
        # dates do -- a bad date must never reach the cutoff comparison.
        evidence_norm = _normalize_date(temporal_evidence_date)

        if confidence is None:
            confidence = (language_confidence + extraction_confidence
                          + source_reliability + temporal_freshness) / 4.0

        now_iso = str(datetime.datetime.now())

        self.graph.add_node(
            fact_id,
            type="FACT",
            text=text,
            relation=relation,
            language_confidence=language_confidence,
            extraction_confidence=extraction_confidence,
            source_reliability=source_reliability,
            temporal_freshness=temporal_freshness,
            confidence=confidence,
            valid_from=valid_from_norm[0] if valid_from_norm else None,
            valid_from_granularity=valid_from_norm[1] if valid_from_norm else None,
            valid_to=valid_to_norm[0] if valid_to_norm else None,
            valid_to_granularity=valid_to_norm[1] if valid_to_norm else None,
            observed_at=str(observed_at) if observed_at else now_iso,
            source_tier=source_tier,
            status=status,
            temporal_class=temporal_class,
            temporal_evidence_status=temporal_evidence_status,
            temporal_evidence_date=evidence_norm[0] if evidence_norm else None,
            temporal_evidence_granularity=evidence_norm[1] if evidence_norm else None,
            temporal_evidence_source_url=temporal_evidence_source_url,
            temporal_evidence_snapshot_hash=temporal_evidence_snapshot_hash,
            created_at=now_iso,  # kept for backward compatibility
        )

        return fact_id

    def add_source(self, url, publisher=None,
                    published_at=None, retrieved_at=None,
                    source_tier: Optional[int] = None):
        """
        Extended: now records source_published_at (when the page itself
        says it was published/updated) separately from retrieved_at
        (when the crawler fetched it), plus a source_tier per §7.1.

        `url` may be None — many facts (e.g. those sourced from a static
        BCS question-bank corpus rather than a live web page) have no
        source_url at all. In that case we key the source node on the
        publisher name instead of crashing uuid.uuid5(None), so every
        fact from the same no-URL publisher shares one synthetic source
        node rather than the pipeline dying on the first such fact.
        """
        if url:
            node_id = f"SOURCE_{uuid.uuid5(uuid.NAMESPACE_URL, url)}"
        else:
            placeholder = f"no-source-url::{publisher or 'unknown'}"
            node_id = f"SOURCE_{uuid.uuid5(uuid.NAMESPACE_URL, placeholder)}"

        published_norm = _normalize_date(published_at)
        if source_tier is not None and source_tier not in SOURCE_TIERS:
            raise ValueError(f"source_tier must be one of {SOURCE_TIERS}, got {source_tier!r}")

        if not self.graph.has_node(node_id):
            self.graph.add_node(
                node_id,
                type="SOURCE",
                url=url,
                publisher=publisher,
                source_published_at=published_norm[0] if published_norm else None,
                retrieved_at=str(retrieved_at) if retrieved_at else str(datetime.datetime.now()),
                source_tier=source_tier,
            )
        else:
            # Source already known — update tier/published date if we now
            # have better information, but never silently blank out data
            # we previously had.
            node = self.graph.nodes[node_id]
            if source_tier is not None and node.get("source_tier") is None:
                node["source_tier"] = source_tier
            if published_norm and node.get("source_published_at") is None:
                node["source_published_at"] = published_norm[0]
        return node_id

    def add_topic(self, topic_name):
        topic_id = f"TOPIC_{topic_name.upper()}"

        if not self.graph.has_node(topic_id):
            self.graph.add_node(
                topic_id,
                type="TOPIC",
                name=topic_name
            )

            self.topic_stats[topic_id] = {
                "fact_count": 0,
                "question_count": 0,
                "unmapped_facts": 0,
                "unmapped_questions": 0
            }

        return topic_id

    def add_question(self, text, question_type="original", topic=None,
                     source_fact_ids=None, parent_question_id=None):
        question_id = self._generate_id("QUESTION")

        self.graph.add_node(
            question_id,
            type="QUESTION",
            text=text,
            question_type=question_type,
            created_at=str(datetime.datetime.now())
        )

        if topic:
            topic_id = self.add_topic(topic)
            self.link(question_id, topic_id, "ABOUT")
            if topic_id in self.topic_stats:
                self.topic_stats[topic_id]["question_count"] += 1

        mapped = False
        if source_fact_ids:
            for fact_id in source_fact_ids:
                if self.graph.has_node(fact_id):
                    self.link(question_id, fact_id, "ASKS_FOR")
                    mapped = True

        if not mapped and topic:
            topic_id = f"TOPIC_{topic.upper()}"
            if topic_id in self.topic_stats:
                self.topic_stats[topic_id]["unmapped_questions"] += 1

        if parent_question_id and self.graph.has_node(parent_question_id):
            if question_type == "refined":
                self.link(question_id, parent_question_id, "REFINES")
            else:
                self.link(question_id, parent_question_id, "DERIVED_FROM")

        return question_id

    # -----------------------------
    # Edge Creation
    # -----------------------------

    def link(self, source, target, relation):
        self.graph.add_edge(source, target, relation=relation)

    # -----------------------------
    # Temporal Versioning (NEW)
    # -----------------------------

    def _version_key(self, subject_entity_id: str, relation: str) -> Tuple[str, str]:
        return (subject_entity_id, relation)

    def _close_prior_version(self, subject_entity_id: str, relation: str,
                             new_fact_id: str, new_valid_from: Optional[str]) -> Optional[str]:
        """
        If there is an existing OPEN fact (valid_to is None, status
        accepted) for this (subject, relation), and the new fact's
        valid_from is later than or equal to the old fact's valid_from,
        close the old fact's valid_to at the new fact's valid_from and
        mark it superseded. Link new_fact --PRECEDED_BY--> old_fact.

        Returns the closed fact's id, or None if nothing was closed.

        If the new fact has no valid_from, we cannot safely order it
        against the existing version, so we do NOT auto-close anything —
        both facts remain "accepted" and it's flagged as a conflict for
        manual/verifier review instead of guessing.
        """
        key = self._version_key(subject_entity_id, relation)
        chain = self._version_index.get(key, [])
        if not chain:
            return None

        # Find the current open (still-valid) version in the chain.
        open_fact_id = None
        for fid in reversed(chain):
            data = self.graph.nodes[fid]
            if data.get("status") == "accepted" and data.get("valid_to") is None:
                open_fact_id = fid
                break

        if open_fact_id is None:
            return None

        old_data = self.graph.nodes[open_fact_id]
        old_from = old_data.get("valid_from")

        if new_valid_from is None:
            # Can't order safely — flag conflict, don't guess.
            self.link(new_fact_id, open_fact_id, "CONFLICTS_WITH")
            old_data["status"] = "disputed"
            self.graph.nodes[new_fact_id]["status"] = "disputed"
            return None

        if old_from is not None and _date_key(new_valid_from) < _date_key(old_from):
            # New fact is actually OLDER than the current open version —
            # this is a backfill, not a supersession. Link but don't close.
            self.link(new_fact_id, open_fact_id, "PRECEDED_BY")
            return None

        # Normal case: new fact supersedes the old open fact.
        old_data["valid_to"] = new_valid_from
        old_data["valid_to_granularity"] = self.graph.nodes[new_fact_id].get("valid_from_granularity")
        old_data["status"] = "superseded"
        self.link(new_fact_id, open_fact_id, "PRECEDED_BY")
        self.link(open_fact_id, new_fact_id, "SUCCEEDED_BY")
        return open_fact_id

    def _register_version(self, subject_entity_id: str, relation: str, fact_id: str):
        key = self._version_key(subject_entity_id, relation)
        self._version_index.setdefault(key, []).append(fact_id)

    def as_of(self, subject_name: str, relation: str, date) -> Optional[Dict]:
        """
        Query: "what was true for (subject, relation) on this date?"
        (guideline §6.1 example query; §13.5 Snapshot Query Accuracy).

        Returns the fact data dict valid at `date`, or None if no
        version of this fact covers that date.
        """
        subject_id = f"ENTITY_{self._normalize_name(subject_name)}"
        key = self._version_key(subject_id, relation)
        chain = self._version_index.get(key, [])
        if not chain:
            return None

        target_key = _date_key(str(_normalize_date(date)[0])) if _normalize_date(date) else None
        if target_key is None:
            raise ValueError(f"Could not parse query date: {date!r}")

        for fid in chain:
            data = self.graph.nodes[fid]
            vf, vt = data.get("valid_from"), data.get("valid_to")
            vf_key = _date_key(vf) if vf else ""  # -inf if unknown start
            vt_key = _date_key(vt) if vt else "9999-99-99"  # +inf if open
            if vf_key <= target_key < vt_key:
                return dict(data, fact_id=fid)
        return None

    def get_fact_history(self, subject_name: str, relation: str) -> List[Dict]:
        """Return the full version chain for a (subject, relation) pair, in order."""
        subject_id = f"ENTITY_{self._normalize_name(subject_name)}"
        key = self._version_key(subject_id, relation)
        chain = self._version_index.get(key, [])
        return [dict(self.graph.nodes[fid], fact_id=fid) for fid in chain]

    def get_bitemporal_tuple(self, fact_id: str) -> Optional[Dict]:
        """
        Extract the full 9-element bitemporal tuple representation for a fact:
        (fact_id, subject, relation, object, valid_from, valid_to, observed_at, source_published_at, source_tier)
        """
        if not self.graph.has_node(fact_id):
            return None

        data = self.graph.nodes[fact_id]
        if data.get("type") != "FACT":
            return None

        # Extract subject(s): in-edges with relation="SUBJECT_OF"
        subjects = []
        for src, _, edata in self.graph.in_edges(fact_id, data=True):
            if edata.get("relation") == "SUBJECT_OF":
                node_data = self.graph.nodes.get(src, {})
                subjects.append(node_data.get("name", src))

        # Extract object(s): out-edges with relation="OBJECT_IS"
        objects = []
        for _, tgt, edata in self.graph.out_edges(fact_id, data=True):
            if edata.get("relation") == "OBJECT_IS":
                node_data = self.graph.nodes.get(tgt, {})
                objects.append(node_data.get("name", tgt))

        source_data = self.get_fact_source_data(fact_id) or {}
        source_published_at = source_data.get("source_published_at")
        source_tier = data.get("source_tier") or source_data.get("source_tier")

        subj_val = subjects[0] if len(subjects) == 1 else (subjects if subjects else None)
        obj_val = objects[0] if len(objects) == 1 else (objects if objects else None)

        return {
            "fact_id": fact_id,
            "subject": subj_val,
            "relation": data.get("relation", "STATED_AS"),
            "object": obj_val,
            "valid_from": data.get("valid_from"),
            "valid_to": data.get("valid_to"),
            "observed_at": data.get("observed_at"),
            "source_published_at": source_published_at,
            "source_tier": source_tier,
        }

    def get_all_bitemporal_tuples(self) -> List[Dict]:
        """Return bitemporal tuple representations for all FACT nodes in the graph."""
        tuples = []
        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "FACT":
                tup = self.get_bitemporal_tuple(node_id)
                if tup:
                    tuples.append(tup)
        return tuples

    def get_graph_snapshot(self, t_cutoff: str = "2023-04-19",
                           enforce_source_cutoff: bool = True,
                           enforce_observation_cutoff: bool = False,
                           allow_static_source_evidence: bool = True) -> 'KnowledgeGraphBuilder':
        """
        Produce a time-slice snapshot of the Knowledge Graph as of `t_cutoff`.

        Strictly filters out any node, relation, or attribute that was not valid
        or observable as of `t_cutoff`:
          1. valid_from <= t_cutoff < valid_to (or open valid_to)
          2. observed_at <= t_cutoff (system ingestion date, if enforce_observation_cutoff=True)
          3. source_published_at <= t_cutoff (if enforce_source_cutoff=True)
          4. Static facts proved by pre-cutoff evidence if unversioned.

        Returns a new KnowledgeGraphBuilder instance holding the isolated snapshot.
        """
        snapshot_kg = KnowledgeGraphBuilder()

        target_norm = _normalize_date(t_cutoff)
        if target_norm is None:
            raise ValueError(f"Invalid t_cutoff date: {t_cutoff!r}")
        target_key = _date_key(target_norm[0])

        valid_fact_ids = set()

        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "FACT":
                # 1. System observation / ingestion cutoff check (optional)
                if enforce_observation_cutoff:
                    obs_at = data.get("observed_at")
                    if obs_at:
                        obs_date = str(obs_at)[:10]
                        obs_norm = _normalize_date(obs_date)
                        if obs_norm and _date_key(obs_norm[0]) > target_key:
                            continue  # Ingested after t_cutoff

                # 2. Event time interval check
                vf, vt = data.get("valid_from"), data.get("valid_to")
                if vf is None and vt is None:
                    if not (allow_static_source_evidence and self._has_verified_static_source_evidence(data, target_key)):
                        continue  # Unversioned and unverified static fact
                else:
                    vf_key = _date_key(vf) if vf else ""
                    vt_key = _date_key(vt) if vt else "9999-99-99"
                    if not (vf_key <= target_key < vt_key):
                        continue  # Fact interval excludes t_cutoff

                # 3. Source publication cutoff check
                if enforce_source_cutoff:
                    source = self.get_fact_source_data(node_id)
                    pub = source.get("source_published_at") if source else None
                    if pub and _date_key(pub) > target_key:
                        continue  # Source published after t_cutoff

                valid_fact_ids.add(node_id)

        # Build set of nodes to include in snapshot
        nodes_to_include = set(valid_fact_ids)

        for fid in valid_fact_ids:
            for src, _, edata in self.graph.in_edges(fid, data=True):
                nodes_to_include.add(src)
            for _, tgt, edata in self.graph.out_edges(fid, data=True):
                nodes_to_include.add(tgt)

        # Also check QUESTION nodes: created_at <= t_cutoff
        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "QUESTION":
                created_at = data.get("created_at")
                if created_at:
                    c_date = str(created_at)[:10]
                    c_norm = _normalize_date(c_date)
                    if c_norm and _date_key(c_norm[0]) <= target_key:
                        nodes_to_include.add(node_id)

        # Copy nodes
        for nid in nodes_to_include:
            ndata = self.graph.nodes[nid]
            snapshot_kg.graph.add_node(nid, **dict(ndata))

        # Copy edges between included nodes
        for u, v, key, edata in self.graph.edges(keys=True, data=True):
            if u in nodes_to_include and v in nodes_to_include:
                snapshot_kg.graph.add_edge(u, v, key=key, **dict(edata))

        snapshot_kg._rebuild_topic_stats()
        snapshot_kg._rebuild_version_index()

        return snapshot_kg

    # -----------------------------
    # Fact Insertion Pipeline
    # -----------------------------

    def insert_fact_pipeline(self,
                             fact_text,
                             subject_entities,
                             object_entities,
                             topic,
                             source_url,
                             publisher=None,
                             quality_scores=None,
                             relation: Optional[str] = None,
                             valid_from=None,
                             valid_to=None,
                             observed_at=None,
                             source_published_at=None,
                             source_tier: Optional[int] = None,
                             status: str = "accepted",
                             temporal_class: Optional[str] = None,
                             temporal_evidence_status: Optional[str] = None,
                             temporal_evidence_date=None,
                             temporal_evidence_source_url: Optional[str] = None,
                             temporal_evidence_snapshot_hash: Optional[str] = None):
        """
        Same call contract as before, plus optional bitemporal/provenance
        args. When `relation` and `valid_from` are supplied AND there's
        exactly one subject entity, this fact is registered in the
        version chain and will automatically close/supersede any prior
        open fact for the same (subject, relation) pair.

        Facts inserted without a relation (the common case for your
        current bcg_gk_facts.json, which has no relation field yet)
        behave exactly as before: stored as-is, no versioning attempted.
        That's intentional — versioning requires knowing WHICH relation
        changed, and free text alone doesn't tell you that.
        """
        if quality_scores is None:
            quality_scores = {
                "language_confidence": 1.0,
                "extraction_confidence": 1.0,
                "source_reliability": 1.0,
                "temporal_freshness": 1.0
            }

        valid_from_norm = _normalize_date(valid_from)

        fact_id = self.add_fact(
            fact_text, **quality_scores,
            relation=relation, valid_from=valid_from, valid_to=valid_to,
            observed_at=observed_at,
            source_tier=source_tier, status=status,
            temporal_class=temporal_class,
            temporal_evidence_status=temporal_evidence_status,
            temporal_evidence_date=temporal_evidence_date,
            temporal_evidence_source_url=temporal_evidence_source_url,
            temporal_evidence_snapshot_hash=temporal_evidence_snapshot_hash,
        )
        topic_id = self.add_topic(topic)
        source_id = self.add_source(
            source_url, publisher,
            published_at=source_published_at, source_tier=source_tier,
        )

        self.link(fact_id, topic_id, "ABOUT")
        self.link(fact_id, source_id, "SUPPORTED_BY")

        subject_entity_ids = []
        for ent_name, subtype in subject_entities:
            ent_id = self.add_entity(ent_name, subtype)
            self.link(ent_id, fact_id, "SUBJECT_OF")
            subject_entity_ids.append(ent_id)

        for ent_name, subtype in object_entities:
            ent_id = self.add_entity(ent_name, subtype)
            self.link(fact_id, ent_id, "OBJECT_IS")

        # Attempt versioning only when we have enough information to do
        # it safely: a real relation (not the STATED_AS fallback), a
        # single unambiguous subject, and a valid_from to order against.
        effective_relation = relation or "STATED_AS"
        if (effective_relation != "STATED_AS"
                and len(subject_entity_ids) == 1
                and valid_from_norm is not None):
            subj_id = subject_entity_ids[0]
            self._close_prior_version(subj_id, effective_relation, fact_id,
                                      valid_from_norm[0])
            self._register_version(subj_id, effective_relation, fact_id)

        if topic_id in self.topic_stats:
            self.topic_stats[topic_id]["fact_count"] += 1

        return fact_id

    # -----------------------------
    # Unmapped Fact Tracking
    # -----------------------------

    def mark_fact_unmapped(self, fact_id, topic):
        topic_id = f"TOPIC_{topic.upper()}"
        if topic_id in self.topic_stats:
            self.topic_stats[topic_id]["unmapped_facts"] += 1
        if self.graph.has_node(fact_id):
            self.graph.nodes[fact_id]["unmapped"] = True

    def get_unmapped_facts(self):
        unmapped = []
        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "FACT" and data.get("unmapped", False):
                unmapped.append(node_id)
        return unmapped

    # -----------------------------
    # Adaptive Growth Logic
    # -----------------------------

    def analyze_topic_density(self, fact_threshold=50, question_threshold=30,
                               unmapped_threshold=10):
        expansion_recommendations = []

        for topic_id, stats in self.topic_stats.items():
            if stats["fact_count"] > fact_threshold:
                expansion_recommendations.append(
                    (topic_id, "EXPAND_DEPTH",
                     f"fact_count={stats['fact_count']} exceeds threshold={fact_threshold}")
                )
            if stats["question_count"] > question_threshold:
                expansion_recommendations.append(
                    (topic_id, "EXPAND_DEPTH",
                     f"question_count={stats['question_count']} exceeds threshold={question_threshold}")
                )
            total_unmapped = stats["unmapped_facts"] + stats.get("unmapped_questions", 0)
            if total_unmapped > unmapped_threshold:
                expansion_recommendations.append(
                    (topic_id, "EXPAND_WIDTH",
                     f"unmapped_items={total_unmapped} exceeds threshold={unmapped_threshold}")
                )

        return expansion_recommendations

    def get_topic_stats(self):
        return dict(self.topic_stats)

    # -----------------------------
    # Query Helpers
    # -----------------------------

    def get_facts_by_topic(self, topic_name):
        topic_id = f"TOPIC_{topic_name.upper()}"
        fact_ids = []
        for src, tgt, data in self.graph.edges(data=True):
            if tgt == topic_id and data.get("relation") == "ABOUT":
                if self.graph.nodes[src].get("type") == "FACT":
                    fact_ids.append(src)
        return fact_ids

    def get_fact_source_data(self, fact_id) -> Optional[Dict]:
        """
        Return the SOURCE node dict backing `fact_id` via its SUPPORTED_BY
        edge, or None if the fact has no linked source. Used by cutoff
        enforcement to check source_published_at independently of the
        fact's own valid_from/valid_to (guideline §3.3: a page published
        after the cutoff is disallowed even if it describes an earlier,
        otherwise-valid fact).
        """
        if not self.graph.has_node(fact_id):
            return None
        for _, tgt, edata in self.graph.edges(fact_id, data=True):
            if edata.get("relation") == "SUPPORTED_BY":
                node = self.graph.nodes.get(tgt)
                if node and node.get("type") == "SOURCE":
                    return dict(node)
        return None

    def get_facts_by_topic_as_of(self, topic_name: str, as_of_date=None,
                                 enforce_source_cutoff: bool = True,
                                 allow_static_source_evidence: bool = False,
                                 drop_unversioned: Optional[bool] = None) -> List[Dict]:
        """
        Cutoff-aware fact retrieval for a topic (guideline §8.2: the
        Temporal Retriever "queries KG(t*) and returns only facts valid
        under the cutoff plus provenance").

        Parameters
        ----------
        topic_name : str
        as_of_date : str / datetime.date / None
            Target cutoff time t*. If None, this is equivalent to
            get_facts_by_topic() with no temporal filtering at all — so
            existing callers that don't pass a cutoff keep working
            unchanged.
        enforce_source_cutoff : bool
            If True (default), also reject a fact whose supporting
            source was published after as_of_date, even when the fact
            itself has no valid_from/valid_to recorded. This closes a
            gap that pure fact-interval filtering misses: a fact with
            no temporal interval can still leak post-cutoff information
            if its only evidence is a post-cutoff page.
        allow_static_source_evidence : bool
            Model B opt-in for an explicitly accepted, pre-cutoff source
            snapshot supporting a static fact.

        Returns
        -------
        List[Dict] — one dict per surviving fact, each the fact's node
        data plus:
            fact_id          : str
            temporal_status  : "unchecked" (no as_of_date given),
                               "valid_at_cutoff" (interval covers t*),
                               or "unversioned" (fact has no
                               valid_from/valid_to, so it was NOT
                               actually checked against t* — only its
                               source was, if enforce_source_cutoff).

        Facts that fail the cutoff are excluded, not silently
        downgraded — callers should not assume every returned fact was
        interval-checked; check `temporal_status` if that distinction
        matters downstream (e.g. the Temporal Verifier).
        """
        # RETRIEVAL vs AUDIT are different questions and must not share one
        # switch. Retrieval asks "what may I generate from?", so under Model B
        # an unversioned fact is excluded. Auditing asks "what is still
        # unproved?", so the strict guard must SEE those same records — it
        # detects breaches by looking for them. An earlier revision dropped
        # them for both callers, which made strict_temporal_guard report PASS
        # by hiding its violations instead of satisfying them.
        if drop_unversioned is None:
            drop_unversioned = allow_static_source_evidence

        fact_ids = self.get_facts_by_topic(topic_name)

        if as_of_date is None:
            return [dict(self.graph.nodes[fid], fact_id=fid,
                        temporal_status="unchecked")
                   for fid in fact_ids]

        target_norm = _normalize_date(as_of_date)
        if target_norm is None:
            raise ValueError(f"as_of_date could not be parsed: {as_of_date!r}")
        target_key = _date_key(target_norm[0])

        results = []
        for fid in fact_ids:
            data = self.graph.nodes[fid]
            vf, vt = data.get("valid_from"), data.get("valid_to")

            if vf is None and vt is None:
                # No interval recorded. Under Model B this is not
                # automatically a failure: a TIMELESS (static) fact is
                # proved instead by evidence that demonstrably existed at
                # or before t*. Anything without that proof stays
                # "unversioned" — it is not automatically valid.
                if (allow_static_source_evidence
                        and self._has_verified_static_source_evidence(data, target_key)):
                    temporal_status = "static_valid_at_cutoff"
                else:
                    temporal_status = "unversioned"
                if drop_unversioned and temporal_status == "unversioned":
                    continue
            else:
                vf_key = _date_key(vf) if vf else ""              # -inf
                vt_key = _date_key(vt) if vt else "9999-99-99"    # +inf
                if not (vf_key <= target_key < vt_key):
                    continue  # fact's own interval excludes t* — drop
                temporal_status = "valid_at_cutoff"

            if enforce_source_cutoff:
                source = self.get_fact_source_data(fid)
                pub = source.get("source_published_at") if source else None
                if pub and _date_key(pub) > target_key:
                    continue  # source published after cutoff — leakage, drop

            results.append(dict(data, fact_id=fid, temporal_status=temporal_status))

        return results

    @staticmethod
    def _has_verified_static_source_evidence(data: Dict, target_key: str) -> bool:
        """Require explicit accepted evidence before Model B can bypass intervals."""
        if data.get("temporal_class") != "static":
            return False
        if data.get("temporal_evidence_status") != "verified_pre_cutoff_source":
            return False
        evidence_date = data.get("temporal_evidence_date")
        if not evidence_date or _date_key(evidence_date) > target_key:
            return False
        return bool(data.get("temporal_evidence_source_url")
                    and data.get("temporal_evidence_snapshot_hash"))

    def get_questions_by_topic(self, topic_name):
        topic_id = f"TOPIC_{topic_name.upper()}"
        question_ids = []
        for src, tgt, data in self.graph.edges(data=True):
            if tgt == topic_id and data.get("relation") == "ABOUT":
                if self.graph.nodes[src].get("type") == "QUESTION":
                    question_ids.append(src)
        return question_ids

    def get_fact_data(self, fact_id):
        if self.graph.has_node(fact_id):
            return dict(self.graph.nodes[fact_id])
        return None

    def strict_temporal_guard(self, topic_name: str, as_of_date,
                              enforce_source_cutoff: bool = True,
                              allow_static_source_evidence: bool = False) -> List[str]:
        """
        Strict benchmark gate for the project plan: under a real holdout run,
        any fact that is unversioned or otherwise not interval-checked against
        the target time is treated as a blocker for generation.

        Returns a list of fact IDs that violate the strict guard. An empty list
        means the topic is compliant with the temporal benchmark contract.
        """
        facts = self.get_facts_by_topic_as_of(
            topic_name,
            as_of_date=as_of_date,
            enforce_source_cutoff=enforce_source_cutoff,
            allow_static_source_evidence=allow_static_source_evidence,
            # The guard must see unproved facts to report them. Letting this
            # default to the retrieval behaviour would hide every violation
            # and turn this gate into a rubber stamp.
            drop_unversioned=False,
        )
        return [
            fact["fact_id"] for fact in facts
            if fact.get("temporal_status") == "unversioned"
        ]

    # -----------------------------
    # Multi-Hop Traversal & Path Query Engine
    # -----------------------------

    def find_paths_between_entities(self, start_entity_name: str, end_entity_name: str,
                                    max_hops: int = 4, as_of_date=None) -> List[Dict]:
        """
        Find all relational path chains connecting start_entity to end_entity.

        If `as_of_date` is specified, traversal is restricted strictly to the
        temporally isolated snapshot of the graph valid as of `as_of_date`.

        Returns a list of path dictionary objects, each containing:
            start_entity : str
            end_entity   : str
            path_length  : int (number of edges)
            nodes        : List[str] (node IDs in sequence)
            facts        : List[str] (FACT node IDs in path)
            relations    : List[str] (edge relations in path)
        """
        target_kg = self.get_graph_snapshot(t_cutoff=as_of_date) if as_of_date else self
        g = target_kg.graph

        start_id = f"ENTITY_{self._normalize_name(start_entity_name)}"
        end_id = f"ENTITY_{self._normalize_name(end_entity_name)}"

        if not g.has_node(start_id) or not g.has_node(end_id):
            return []

        undirected_g = g.to_undirected()

        paths = []
        try:
            raw_paths = list(nx.all_simple_paths(undirected_g, source=start_id, target=end_id, cutoff=max_hops * 2))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

        for node_path in raw_paths:
            path_facts = []
            path_relations = []
            for i in range(len(node_path) - 1):
                u, v = node_path[i], node_path[i + 1]
                if g.has_edge(u, v):
                    edge_data = g.get_edge_data(u, v)
                elif g.has_edge(v, u):
                    edge_data = g.get_edge_data(v, u)
                else:
                    edge_data = {}

                rel = None
                if edge_data:
                    first_key = list(edge_data.keys())[0]
                    rel = edge_data[first_key].get("relation", "UNKNOWN")
                path_relations.append(rel)

                if g.nodes[u].get("type") == "FACT":
                    path_facts.append(u)
                if g.nodes[v].get("type") == "FACT":
                    path_facts.append(v)

            path_facts = list(dict.fromkeys(path_facts))

            paths.append({
                "start_entity": start_entity_name,
                "end_entity": end_entity_name,
                "path_length": len(node_path) - 1,
                "nodes": node_path,
                "facts": path_facts,
                "relations": path_relations,
            })

        return paths

    def get_entity_neighborhood(self, entity_name: str, radius: int = 2,
                                 as_of_date=None) -> Optional[Dict]:
        """
        Extract the multi-hop neighborhood around an entity within `radius` steps.

        Restricted to the snapshot at `as_of_date` if provided.

        Returns a dictionary containing:
            entity_id          : str
            entity_name        : str
            connected_entities : List[Dict] (name, subtype, distance)
            connected_facts    : List[Dict] (fact_id, text, relation, valid_from, valid_to)
            topics             : List[str]
            total_nodes        : int
        """
        target_kg = self.get_graph_snapshot(t_cutoff=as_of_date) if as_of_date else self
        g = target_kg.graph

        entity_id = f"ENTITY_{self._normalize_name(entity_name)}"
        if not g.has_node(entity_id):
            return None

        undirected_g = g.to_undirected()
        distances = nx.single_source_shortest_path_length(undirected_g, entity_id, cutoff=radius * 2)

        connected_entities = []
        connected_facts = []
        topics = set()

        for nid, dist in distances.items():
            if nid == entity_id:
                continue
            ndata = g.nodes[nid]
            ntype = ndata.get("type")
            if ntype == "ENTITY":
                connected_entities.append({
                    "entity_id": nid,
                    "name": ndata.get("name", nid),
                    "subtype": ndata.get("subtype"),
                    "distance": dist,
                })
            elif ntype == "FACT":
                connected_facts.append({
                    "fact_id": nid,
                    "text": ndata.get("text"),
                    "relation": ndata.get("relation"),
                    "valid_from": ndata.get("valid_from"),
                    "valid_to": ndata.get("valid_to"),
                    "source_tier": ndata.get("source_tier"),
                    "distance": dist,
                })
            elif ntype == "TOPIC":
                topics.add(ndata.get("name", nid))

        return {
            "entity_id": entity_id,
            "entity_name": entity_name,
            "connected_entities": connected_entities,
            "connected_facts": connected_facts,
            "topics": list(topics),
            "total_nodes": len(distances),
        }

    def update_fact_attribute(self, fact_id, key, value):
        if self.graph.has_node(fact_id):
            self.graph.nodes[fact_id][key] = value

    def remove_fact(self, fact_id):
        if not self.graph.has_node(fact_id):
            return

        for _, tgt, data in list(self.graph.edges(fact_id, data=True)):
            if data.get("relation") == "ABOUT" and tgt in self.topic_stats:
                self.topic_stats[tgt]["fact_count"] = max(
                    0, self.topic_stats[tgt]["fact_count"] - 1
                )

        was_versioned = any(
            fact_id in fids for fids in self._version_index.values()
        )

        self.graph.remove_node(fact_id)

        # A removed fact must not linger in the version chain -- as_of()
        # and get_fact_history() index straight into self.graph.nodes[fid]
        # and would raise KeyError on a dangling entry.
        if was_versioned:
            self._rebuild_version_index()

    # -----------------------------
    # Snapshot Save / Load
    # -----------------------------

    def save_snapshot(self, folder="snapshots"):
        os.makedirs(folder, exist_ok=True)

        filename = f"kg_{datetime.datetime.now().strftime('%Y_%m')}.gpickle"
        path = os.path.join(folder, filename)

        snapshot = {
            "graph": self.graph,
            "topic_stats": dict(self.topic_stats),
            "version_index": dict(self._version_index),
        }
        with open(path, "wb") as f:
            pickle.dump(snapshot, f)

        print(f"Knowledge Graph saved to {path}  "
              f"({self.graph.number_of_nodes()} nodes, "
              f"{self.graph.number_of_edges()} edges, "
              f"{len(self.topic_stats)} topic stats)")
        return path

    def load_snapshot(self, filepath):
        try:
            with open(filepath, "rb") as f:
                data = pickle.load(f)

            if isinstance(data, dict) and "graph" in data:
                self.graph = data["graph"]
                self.topic_stats = data.get("topic_stats", {})
                self._version_index = data.get("version_index", {})
                if not self._version_index:
                    self._rebuild_version_index()
            else:
                self.graph = data
                self.topic_stats = {}
                self._rebuild_topic_stats()
                self._rebuild_version_index()

            print(f"Knowledge Graph loaded from {filepath}  "
                  f"({self.graph.number_of_nodes()} nodes, "
                  f"{len(self.topic_stats)} topic stats)")
        except (FileNotFoundError, pickle.UnpicklingError, EOFError) as e:
            print(f"[load_snapshot] ERROR: Could not load '{filepath}': {e}")

    def _rebuild_topic_stats(self):
        self.topic_stats = {}
        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "TOPIC":
                self.topic_stats[node_id] = {
                    "fact_count": 0,
                    "question_count": 0,
                    "unmapped_facts": 0,
                    "unmapped_questions": 0,
                }
        for src, tgt, edge_data in self.graph.edges(data=True):
            if edge_data.get("relation") == "ABOUT" and tgt in self.topic_stats:
                src_type = self.graph.nodes[src].get("type")
                if src_type == "FACT":
                    self.topic_stats[tgt]["fact_count"] += 1
                elif src_type == "QUESTION":
                    self.topic_stats[tgt]["question_count"] += 1

    def _rebuild_version_index(self):
        """
        Reconstruct the (subject, relation) -> [fact_ids] index from
        SUBJECT_OF edges and each fact's stored relation. Used after
        loading a legacy snapshot that predates the version index.
        Order is approximated by valid_from (unknowns last), since edge
        insertion order isn't preserved across a pickle reload reliably.
        """
        self._version_index = {}
        pending: Dict[Tuple[str, str], List[str]] = {}
        for src, tgt, edge_data in self.graph.edges(data=True):
            if edge_data.get("relation") != "SUBJECT_OF":
                continue
            subj_id, fact_id = src, tgt
            fact_data = self.graph.nodes.get(fact_id, {})
            if fact_data.get("type") != "FACT":
                continue
            relation = fact_data.get("relation")
            if not relation or relation == "STATED_AS":
                continue
            pending.setdefault((subj_id, relation), []).append(fact_id)

        for key, fids in pending.items():
            fids.sort(key=lambda fid: _date_key(self.graph.nodes[fid].get("valid_from")))
            self._version_index[key] = fids

    # -----------------------------
    # Debug Summary
    # -----------------------------

    def summary(self):
        print("----- Knowledge Graph Summary -----")
        print(f"Total Nodes: {self.graph.number_of_nodes()}")
        print(f"Total Edges: {self.graph.number_of_edges()}")

        type_count = {}
        for _, data in self.graph.nodes(data=True):
            t = data.get("type", "UNKNOWN")
            type_count[t] = type_count.get(t, 0) + 1

        print("Node Type Distribution:")
        for k, v in type_count.items():
            print(f"  {k}: {v}")

        versioned = sum(1 for fids in self._version_index.values() if len(fids) > 1)
        print(f"Versioned (subject, relation) pairs with >1 fact: {versioned}")