"""
pilot_batch_sourcing.py
=======================
Evidence sourcing for the remaining Milestone-3A pilot facts (task 8).

Batch 01 and Batch 02 were worked by hand, five facts at a time, with a claim
card written for each. That does not scale to the rest of the pilot, and it
does not need to: the pieces already exist.

  * ``resource_corpus`` already resolved an article per fact and cached the
    pre-cutoff revision, so this pass is mostly offline.
  * ``resource_corpus.entity_probes`` already strips the curriculum-label
    trap (``subject_entities[0]`` is a copy of ``_subtopic``).
  * ``batch02_ra_sourcing.find_excerpt_candidates`` now returns prose windows
    ahead of markup, which is what kept BCSGK-0359-B out of Batch 02.

What this script does NOT do
----------------------------
It does not decide anything. Every record it writes is ``pending_reviewer``;
nothing is marked accepted, verified or merged, and the frozen Model B seed is
never touched. An excerpt here is a LEAD for a human to read -- the Batch-02
round produced six leads and a reviewer rejected four of them on reading, so
the automated step earns no more trust here than it did there.

    python pilot_batch_sourcing.py plan      # what would be attempted
    python pilot_batch_sourcing.py source    # write pending_reviewer records
    python pilot_batch_sourcing.py source --offline   # cached revisions only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import resource_corpus as R
import batch02_ra_sourcing as B

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
CORPUS_PATH = ROOT / "bcs_gk_facts.json"
PILOT_PATH = WORKFLOW / "pilot_batch_final.jsonl"
PROPOSALS_PATH = ROOT / "resourcing_proposals.json"
EVIDENCE_PATH = WORKFLOW / "evidence_ledger.jsonl"
LINKS_PATH = WORKFLOW / "fact_evidence_links.jsonl"
SOURCING_LOG = WORKFLOW / "pilot_batch_sourcing_log.jsonl"
READINGS_PATH = WORKFLOW / "pilot_batch_readings.jsonl"
SUMMARY_PATH = WORKFLOW / "pilot_batch_sourcing_summary.json"

CUTOFF = "2023-04-19"
TODAY = date.today().isoformat()

# Facts already carried through Batch 01/02 by hand. Re-sourcing them here
# would append a second, competing evidence trail for the same claim.
ALREADY_WORKED = {"BCSGK-0315", "BCSGK-0336", "BCSGK-0359"}


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def load_corpus() -> Dict[str, Dict[str, Any]]:
    facts = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return {f["fact_uid"]: f for f in facts}


def load_proposals() -> Dict[str, Dict[str, Any]]:
    if not PROPOSALS_PATH.exists():
        return {}
    data = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))
    rows = data["proposals"] if isinstance(data, dict) else data
    return {p["fact_uid"]: p for p in rows if p.get("fact_uid")}


_BN_TO_ASCII_DIGITS = str.maketrans("০১২৩৪৫"
                                    "৬৭৮৯", "0123456789")


def _is_date_like(value: str) -> bool:
    """True when the value is only digits and date separators, in either script."""
    core = re.sub(r"[\s\-–/.,]", "",
                  str(value).translate(_BN_TO_ASCII_DIGITS))
    return bool(core) and core.isdigit()


def probes_for(fact: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """
    ``(must_match, any_of)`` for the excerpt search.

    `must` is the fact's own subject terms and `any_of` its object terms, both
    filtered the way resource_corpus filters them: curriculum labels dropped
    (``subject_entities[0]`` is a copy of ``_subtopic``, and no article ever
    contains a syllabus heading), and generic words dropped so a window does
    not qualify on the strength of বাংলাদেশ alone.
    """
    def clean(values: Any) -> List[str]:
        # Entity fields hold [name, TYPE] pairs, e.g. ['Shakib Al Hasan',
        # 'PERSON'] -- not bare strings. str() on the pair yields the literal
        # "['Shakib Al Hasan', 'PERSON']", which matches nothing in any
        # article, and the whole batch then reports 'no support found' while
        # looking like a real result. Unwrap exactly as
        # resource_corpus.entity_probes does.
        out = []
        for v in (values or []):
            if isinstance(v, (list, tuple)):
                if not v:
                    continue
                v = v[0]
            v = str(v).strip().strip(".,;:")
            if not v or len(v) < 3:
                continue
            if R.is_curriculum_label(fact, v) or R._is_generic(v):
                continue
            out.append(v)
        return list(dict.fromkeys(out))

    must = clean(fact.get("subject_entities"))
    any_of = clean(fact.get("object_entities"))

    # A bare year is not something to anchor a window on. `১৯৯৭` occurs in
    # hundreds of articles, and a window centred on it says only that the
    # article mentions the year -- which is the mistake rule 2 exists to stop.
    anchors = [m for m in must if not _is_date_like(m)]
    if anchors:
        must = anchors

    # Without a subject term there is nothing to anchor on; use the object
    # terms as the anchor rather than searching for nothing.
    if not must and any_of:
        must, any_of = any_of, []

    # Without corroboration a window is one word in a long article. Fall back
    # to the fact's own content words -- but drop any that are already part of
    # a `must` term, or the corroboration would be satisfied by the anchor
    # itself and the two-signal test would collapse into one.
    if must and not any_of:
        anchor_words = {R._norm(w) for m in must
                        for w in re.findall(r"[\wঀ-৿]+", m)}
        any_of = [t for t in R.content_tokens(fact.get("fact_text"))
                  if len(t) >= 3 and not R._is_generic(t)
                  and R._norm(t) not in anchor_words
                  and not _is_date_like(t)][:12]
    return must, any_of


def titles_for(fact: Dict[str, Any],
               proposal: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """
    Articles to try, best first. A proposal's resolved_title already passed
    the aboutness and title-specificity checks, so it leads.
    """
    out: List[Tuple[str, str]] = []
    if proposal and proposal.get("resolved_title") and proposal.get("wiki_lang"):
        out.append((proposal["wiki_lang"], proposal["resolved_title"]))
    lang = R.fact_language(fact)
    for title in R.candidate_titles(fact):
        pair = (lang, title)
        if pair not in out:
            out.append(pair)
    return out[:4]


def select_targets() -> List[Dict[str, Any]]:
    pilot = read_jsonl(PILOT_PATH)
    return [row for row in pilot if row.get("fact_id") not in ALREADY_WORKED]


def _next_evidence_seq() -> int:
    return B._next_evidence_seq()


def source(offline: bool, limit: Optional[int]) -> int:
    corpus = load_corpus()
    proposals = load_proposals()
    targets = select_targets()
    if limit:
        targets = targets[:limit]

    session = None if offline else R._session()
    seq = _next_evidence_seq()
    ev_lines, link_lines, log_lines, readings = [], [], [], []
    outcomes: Dict[str, int] = {}

    for row in targets:
        uid = row["fact_id"]
        fact = corpus.get(uid)
        if not fact:
            outcomes["fact_missing_from_corpus"] = \
                outcomes.get("fact_missing_from_corpus", 0) + 1
            continue

        must, any_of = probes_for(fact)
        tried: List[str] = []
        found = None
        rejected: List[Dict[str, Any]] = []
        no_probe = not must or not any_of

        if not no_probe:
            for lang, title in titles_for(fact, proposals.get(uid)):
                tried.append("%s:%s" % (lang, title))
                rev = R._cache_get(lang, title)
                if (not rev or not rev.get("content")) and session is not None:
                    rev = R.revision_as_of(session, lang, title)
                if not rev or not rev.get("content"):
                    continue
                ts = rev.get("timestamp") or ""
                if not ts or ts[:10] > CUTOFF:
                    # A post-cutoff revision is not admissible evidence here,
                    # whatever it says.
                    continue
                cands = B.find_excerpt_candidates(rev["content"], must, any_of)
                rejected.extend({"title": title, **c} for c in cands
                                if c["region"] != "prose")
                prose = [c for c in cands if c["region"] == "prose"]
                if prose:
                    found = (lang, title, rev, prose[0])
                    break

        if no_probe or not found:
            reason = ("no usable subject/object probes after dropping "
                      "curriculum labels and generic terms" if no_probe else
                      "no pre-cutoff revision contained a prose window linking "
                      "the subject to the object")
            outcome = "no_probes" if no_probe else "no_prose_support_found"
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            log_lines.append({
                "logged_at": TODAY, "record_type": "pilot_batch_sourcing",
                "fact_id": uid, "pilot_id": row.get("pilot_id"),
                "outcome": outcome, "work_status": "deferred",
                "titles_tried": tried, "must_match": must, "any_of": any_of[:12],
                "reason": reason,
                "rejected_leads": [
                    {"title": r["title"], "region": r["region"],
                     "excerpt": r["excerpt"][:300]} for r in rejected[:3]],
            })
            continue

        lang, title, rev, cand = found
        ev_id = "EV_PILOT_%06d" % seq
        seq += 1
        rev_date = (rev.get("timestamp") or "")[:10]
        url = "https://%s.wikipedia.org/w/index.php?oldid=%s" % (lang, rev["revid"])
        outcomes["candidate_evidence_found"] = \
            outcomes.get("candidate_evidence_found", 0) + 1

        ev_lines.append({
            "evidence_id": ev_id, "evidence_version": 1,
            "source_type": "mediawiki_revision",
            "source_authority": "tertiary_reference",
            "publisher": "%s.wikipedia.org" % lang,
            "document_title": rev.get("resolved_title") or title,
            "canonical_url": url, "archive_url": None,
            "source_published_at": rev_date,
            "temporal_evidence_date": rev_date,
            "archive_capture_timestamp": None, "retrieved_at": TODAY,
            "content_hash_sha256": hashlib.sha256(
                rev["content"].encode("utf-8")).hexdigest(),
            "document_locator": "MediaWiki oldid=%s" % rev["revid"],
            "access_status": "retrieved",
            "admissibility_status": "pending_reviewer",
            "notes": ("Milestone-3A pilot batch. Pre-cutoff revision "
                      "(%s <= %s). The revision date is when the evidence "
                      "existed, not when the claim was published."
                      % (rev_date, CUTOFF)),
        })
        link_lines.append({
            "link_id": "LNK_%s_%s" % (uid, ev_id),
            "fact_id": uid, "claim_id": uid, "evidence_id": ev_id,
            "support_role": "primary_support",
            "supporting_excerpt": cand["excerpt"][:900],
            "excerpt_region": cand["region"],
            "subject_match": "; ".join(must[:3]),
            "predicate_match": fact.get("relation"),
            "object_match": "; ".join(any_of[:3]),
            "semantic_verdict": "pending_reviewer",
            "temporal_field_name": "mediawiki_revision_timestamp",
            "temporal_raw_value": rev.get("timestamp"),
            "valid_from": None, "valid_to": None,
            "temporal_verdict": "pending_reviewer",
            "leakage_verdict": "pending_reviewer",
            "review_status": "pending_reviewer", "reviewer_id": None,
            "claim_scope_match": "pending_reviewer",
            "unsupported_qualifiers": [],
        })
        log_lines.append({
            "logged_at": TODAY, "record_type": "pilot_batch_sourcing",
            "fact_id": uid, "pilot_id": row.get("pilot_id"),
            "outcome": "candidate_evidence_found",
            "work_status": "candidate_source_found",
            "titles_tried": tried, "must_match": must, "any_of": any_of[:12],
            "evidence_id": ev_id, "canonical_url": url,
            "revision_date": rev_date,
            "rejected_leads": [
                {"title": r["title"], "region": r["region"],
                 "excerpt": r["excerpt"][:300]} for r in rejected[:3]],
        })
        readings.append({
            "record_type": "excerpt_reading_proposal", "read_at": TODAY,
            "reviewer_id": "auto_reading_v2",
            "counts_toward_independent_review": False,
            "why_not": ("Produced by the sourcing pass itself. In Batch 02 a "
                        "reviewer read six such windows and rejected four; "
                        "these need the same reading before they mean "
                        "anything."),
            "fact_id": uid, "claim_id": uid, "evidence_id": ev_id,
            "fact_text": str(fact.get("fact_text") or "")[:400],
            "excerpt": cand["excerpt"][:900],
            "excerpt_region": cand["region"],
            "proposed_verdict": "pending_human_read",
            "accepted_for_build": False,
        })

    for path, rows in ((EVIDENCE_PATH, ev_lines), (LINKS_PATH, link_lines),
                       (SOURCING_LOG, log_lines), (READINGS_PATH, readings)):
        if not rows:
            continue
        with path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        print("appended %d row(s) -> %s" % (len(rows), path.name))

    summary = {
        "generated_at": TODAY, "batch": "milestone3a_pilot_remaining",
        "cutoff": CUTOFF, "offline": offline,
        "pilot_facts_targeted": len(targets),
        "excluded_already_worked": sorted(ALREADY_WORKED),
        "outcomes": outcomes,
        "evidence_records_written": len(ev_lines),
        "accepted_for_build": 0,
        "seed_touched": False,
        "note": ("Every record is pending_reviewer. A found excerpt is a lead, "
                 "not support: it has to be read against the fact's own "
                 "wording before it counts, and the claim may be narrower or "
                 "wider than the sentence that matched."),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2)
                            + "\n", encoding="utf-8")

    print("\npilot sourcing over %d fact(s):" % len(targets))
    for k, v in sorted(outcomes.items()):
        print("  %-28s %d" % (k, v))
    print("wrote %s" % SUMMARY_PATH.name)
    print("Nothing accepted for build. Frozen seed untouched.")
    return 0


def plan() -> int:
    corpus = load_corpus()
    proposals = load_proposals()
    targets = select_targets()
    print("pilot facts remaining: %d (excluding %s)"
          % (len(targets), ", ".join(sorted(ALREADY_WORKED))))
    cached = with_proposal = no_probe = 0
    for row in targets:
        fact = corpus.get(row["fact_id"])
        if not fact:
            continue
        must, any_of = probes_for(fact)
        if not must or not any_of:
            no_probe += 1
        p = proposals.get(row["fact_id"])
        if p and p.get("resolved_title"):
            with_proposal += 1
            if R._cache_get(p.get("wiki_lang"), p["resolved_title"]):
                cached += 1
    print("  with a resolved article from resourcing : %d" % with_proposal)
    print("  whose revision is already cached        : %d" % cached)
    print("  with no usable probes                   : %d" % no_probe)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["plan", "source"])
    ap.add_argument("--offline", action="store_true",
                    help="use only cached revisions; never hit the network")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    if args.command == "plan":
        return plan()
    return source(args.offline, args.limit)


if __name__ == "__main__":
    sys.exit(main())
