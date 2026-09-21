"""
batch02_ra_sourcing.py
======================
RA working tool for Milestone 3B, Batch 02.

Two jobs, both append-only, neither of which merges anything into the frozen
Model B seed:

  1. `attest`  -- record the review-independence caveat for the two facts that
     already carry an RA_1/RA_2 verdict. The two stored `reason` strings are
     byte-identical, which is direct evidence the passes were not personnel-
     independent, so the audit trail must not describe them as such.

  2. `source`  -- claim-card construction and evidence sourcing for the three
     remaining Batch-02 facts, against pre-cutoff MediaWiki revisions only.
     Nothing is taken in without a direct supporting excerpt; a claim with no
     admissible excerpt is logged `deferred`, never quietly dropped.

Every write lands in model_b_workflow/ as a new line. No existing record is
rewritten, no frozen seed is touched, and no status above
`candidate_source_found` / `pending_reviewer` is ever set by this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / "model_b_workflow"
EVIDENCE_PATH = WORKFLOW / "evidence_ledger.jsonl"
LINKS_PATH = WORKFLOW / "fact_evidence_links.jsonl"
REVIEW_BATCH02 = WORKFLOW / "pilot_evidence_review_decisions_batch02.jsonl"
ATTESTATION_PATH = WORKFLOW / "review_independence_attestation.json"
CLAIM_CARDS_PATH = WORKFLOW / "batch02_claim_cards.jsonl"
SOURCING_LOG = WORKFLOW / "batch02_evidence_sourcing_log.jsonl"
WORK_QUEUE = WORKFLOW / "batch02_work_status.jsonl"

CUTOFF = "2023-04-19"
TODAY = date.today().isoformat()

sys.path.insert(0, str(ROOT))
from resource_corpus import revision_as_of, _session  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Review-independence attestation
# ---------------------------------------------------------------------------
ATTESTED_FACTS = ("BCSGK-0083", "BCSGK-0109")

ATTESTATION = {
    "review_mode": "two_pass_single_operator",
    "reviewer_identities": "RA_1 and RA_2 labels operated by the same reviewer",
    "personnel_independent": False,
    "independence_level": "procedural separation only; not personnel-independent",
    "external_second_review_required": True,
    "merge_eligibility": "blocked pending external second-review confirmation",
    "evidence_for_this_finding": (
        "The RA_1 and RA_2 decision records for both evidence items carry "
        "byte-identical `reason` strings, which is inconsistent with two "
        "independently formed judgements."
    ),
}


def attest() -> int:
    lines = [json.loads(l) for l in
             REVIEW_BATCH02.read_text(encoding="utf-8").splitlines() if l.strip()]

    # Confirm the identical-reason finding rather than asserting it.
    by_fact: Dict[str, List[str]] = {}
    for rec in lines:
        if rec.get("record_type") == "review_provenance_annotation":
            continue
        by_fact.setdefault(rec["fact_id"], []).append(rec.get("reason", ""))
    identical = {f: (len(set(r)) == 1 and len(r) > 1) for f, r in by_fact.items()}

    appended = 0
    with REVIEW_BATCH02.open("a", encoding="utf-8") as fh:
        for fact_id in ATTESTED_FACTS:
            rec = dict(ATTESTATION)
            rec.update({
                "record_type": "review_provenance_annotation",
                "fact_id": fact_id,
                "annotated_at": TODAY,
                "annotates_reviews": [
                    r["review_id"] for r in lines
                    if r.get("fact_id") == fact_id and r.get("review_id")
                ],
                "reasons_byte_identical": identical.get(fact_id),
                "review_status_unchanged": True,
                "note": (
                    "This annotation does not alter any verdict. It records that "
                    "the two passes were not personnel-independent, so the audit "
                    "report must not claim independent human reviewers."
                ),
            })
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            appended += 1

    ATTESTATION_PATH.write_text(
        json.dumps({
            "generated_at": TODAY,
            "batch": "pilot_static_revised_02",
            "facts": list(ATTESTED_FACTS),
            "reasons_byte_identical_per_fact": identical,
            **ATTESTATION,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("Appended %d review-provenance annotation(s) to %s"
          % (appended, REVIEW_BATCH02.name))
    print("Wrote %s" % ATTESTATION_PATH.name)
    for f, same in identical.items():
        print("  %s: RA_1/RA_2 reasons byte-identical = %s" % (f, same))
    return 0


# ---------------------------------------------------------------------------
# 2. Claim cards for the three remaining Batch-02 facts
#
# Each raw fact is decomposed into atomic subject-predicate-object assertions.
# Two of the three are COMPOUND: one corpus row asserts several independent
# locations. A compound row cannot be evidenced by a single excerpt, so each
# atomic claim is tracked separately and the row is acceptable only when every
# atom is independently supported.
# ---------------------------------------------------------------------------
CLAIM_CARDS: List[Dict[str, Any]] = [
    {
        "fact_id": "BCSGK-0359",
        "reviewed_route": "static_geographic",
        "corpus_temporal_class": "unclassified",
        "ra_temporal_class": "static",
        "ra_class_reason": (
            "Ethnic homeland distribution and a village's administrative "
            "parentage are stable geographic assertions, not time-varying "
            "statistics."
        ),
        "compound": True,
        "atomic_claims": [
            {"claim_id": "BCSGK-0359-A",
             "subject": "গারো (Garo people)",
             "predicate": "located_in",
             "object": ("ময়মনসিংহ, শেরপুর, নেত্রকোণা, "
                        "টাঙ্গাইল, জামালপুর, সুনামগঞ্জ, সিলেট"),
             "qualifiers": ["মূলত / primarily"],
             "titles_bn": ["গারো", "গারো জনগোষ্ঠী"],
             "titles_en": ["Garo people"],
             "must_match": ["গারো"],
             "any_of": ["ময়মনসিংহ", "নেত্রকোণা", "শেরপুর"]},
            {"claim_id": "BCSGK-0359-B",
             "subject": "বিরিশিরি (Birishiri)",
             "predicate": "part_of",
             "object": "দুর্গাপুর উপজেলা, নেত্রকোণা জেলা",
             "qualifiers": ["ঐতিহ্যবাহী গ্রাম / traditional village"],
             "titles_bn": ["বিরিশিরি", "বিরিশিরি ইউনিয়ন"],
             "titles_en": ["Birishiri"],
             "must_match": ["বিরিশিরি"],
             "any_of": ["দুর্গাপুর", "নেত্রকোণা"]},
        ],
    },
    {
        "fact_id": "BCSGK-0315",
        "reviewed_route": "static_geographic",
        "corpus_temporal_class": "dynamic",
        "ra_temporal_class": "needs_clarification",
        "ra_class_reason": (
            "CLASSIFICATION CONFLICT: the batch route calls this "
            "static_geographic, but the automated triage marked it dynamic "
            "because topic=Economy. Where a practice is found can shift over "
            "time, so 'jhum is practised in X' is static only when read as a "
            "regional-association claim. PI adjudication is required before "
            "this atom can be accepted under the static rule."
        ),
        "compound": False,
        "atomic_claims": [
            {"claim_id": "BCSGK-0315-A",
             "subject": "জুম চাষ (jhum / shifting cultivation)",
             "predicate": "cultivated_in",
             "object": "চট্টগ্রাম ও পার্বত্য চট্টগ্রামের জেলাসমূহ",
             "qualifiers": [],
             "titles_bn": ["জুম চাষ", "জুম"],
             "titles_en": ["Jhum cultivation", "Shifting cultivation"],
             "must_match": ["জুম"],
             "any_of": ["পার্বত্য চট্টগ্রাম", "চট্টগ্রাম"]},
        ],
    },
    {
        "fact_id": "BCSGK-0336",
        "reviewed_route": "static_institutional_geographic",
        "corpus_temporal_class": "dynamic",
        "ra_temporal_class": "needs_clarification",
        "ra_class_reason": (
            "Five independent government-farm locations in one row. "
            "Institutional siting is stable in practice but administratively "
            "revisable, and each atom needs its own official source. This row "
            "is a decomposition candidate, not a single acceptable fact."
        ),
        "compound": True,
        "atomic_claims": [
            {"claim_id": "BCSGK-0336-A",
             "subject": "কেন্দ্রীয় গো-প্রজনন খামার",
             "predicate": "located_in", "object": "সাভার, ঢাকা",
             "qualifiers": [], "titles_bn": ["সাভার উপজেলা"],
             "titles_en": [], "must_match": ["প্রজনন"], "any_of": ["সাভার"]},
            {"claim_id": "BCSGK-0336-B",
             "subject": "কেন্দ্রীয় মুরগি প্রজনন খামার",
             "predicate": "located_in", "object": "মিরপুর, ঢাকা",
             "qualifiers": [], "titles_bn": ["মিরপুর"], "titles_en": [],
             "must_match": ["মুরগি"], "any_of": ["মিরপুর"]},
            {"claim_id": "BCSGK-0336-C",
             "subject": "হাঁস প্রজনন খামার",
             "predicate": "located_in", "object": "নারায়ণগঞ্জ",
             "qualifiers": [], "titles_bn": ["নারায়ণগঞ্জ জেলা"],
             "titles_en": [], "must_match": ["হাঁস"], "any_of": ["নারায়ণগঞ্জ"]},
            {"claim_id": "BCSGK-0336-D",
             "subject": "মহিষ প্রজনন ও উন্নয়ন খামার",
             "predicate": "located_in", "object": "বাগেরহাট",
             "qualifiers": [], "titles_bn": ["বাগেরহাট জেলা"],
             "titles_en": [], "must_match": ["মহিষ"], "any_of": ["বাগেরহাট"]},
            {"claim_id": "BCSGK-0336-E",
             "subject": "বেসরকারি কুমির প্রজনন কেন্দ্র",
             "predicate": "located_in", "object": "ভালুকা, ময়মনসিংহ",
             "qualifiers": [], "titles_bn": ["ভালুকা উপজেলা"],
             "titles_en": [], "must_match": ["কুমির"], "any_of": ["ভালুকা"]},
        ],
    },
]


# ---------------------------------------------------------------------------
# Excerpt search
#
# A wiki revision is not prose end to end. It opens with an infobox, carries
# file links, captions and <ref> blocks, and the corpus terms turn up inside
# all of them. A window drawn from that furniture is not a sentence asserting
# anything -- it is two nearby strings.
#
# That is not hypothetical. On the first Batch-02 pass, BCSGK-0359-B (বিরিশিরি
# is in দুর্গাপুর upazila, নেত্রকোণা district) was logged as `insufficient`
# because the only window returned was
#
#     image_skyline = বিরিশিরির উপছবি.jpg ... image_caption = সুসং দুর্গাপুরের ...
#
# -- a filename beside a caption. The article's own first sentence says
# exactly what the claim says, 1.7 kB further down. The search never reached
# it: the old loop returned the FIRST window that held both terms, and the
# infobox is always first.
#
# So: blank the non-prose regions before searching, and keep every candidate
# rather than the first. A markup-only hit is reported as a rejected lead, so
# the reviewer can see it was considered and why it does not count.
# ---------------------------------------------------------------------------
def mask_non_prose(content: str) -> str:
    """
    Blank out templates, file links, refs and comments, replacing each with
    spaces so every offset in the result still matches the original.
    """
    text = content or ""
    chars = list(text)

    def blank(lo: int, hi: int) -> None:
        for i in range(lo, min(hi, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "

    # Nested {{...}} templates: scan with a depth counter, since a regex
    # cannot match balanced pairs and infoboxes nest freely.
    depth, start = 0, None
    i = 0
    while i < len(text) - 1:
        pair = text[i:i + 2]
        if pair == "{{":
            if depth == 0:
                start = i
            depth += 1
            i += 2
            continue
        if pair == "}}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                blank(start, i + 2)
                start = None
            i += 2
            continue
        i += 1

    for pattern in (r"<ref[^>]*/>",
                    r"<ref[^>]*>.*?</ref>",
                    r"<!--.*?-->",
                    # File links and their captions.
                    r"\[\[\s*(?:চিত্র|File|Image|ফাইল)\s*:[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]",
                    # Category tags: filing metadata. An article filed under
                    # [[বিষয়শ্রেণী:নেত্রকোণা জেলা] does not thereby assert anything
                    # about the claim under review.
                    r"\[\[\s*(?:বিষয়শ্রেণী|Category)\s*:[^\]]*\]\]",
                    # Bare external links, [http://host/path Label]. The label
                    # is a citation title, not a sentence in the article: it
                    # put `Dhaka Metro Rail: At A Glance] at Travel Mate
                    # Bangladesh` forward as support for when the metro opened.
                    r"\[(?:https?:)?//[^\s\]]+[^\]]*\]",
                    # Bare URLs outside brackets, e.g. inside citation
                    # templates that have already been blanked, or loose in a
                    # reference list.
                    r"https?://[^\s\]|}]+"):
        for m in re.finditer(pattern, text, re.DOTALL | re.IGNORECASE):
            blank(m.start(), m.end())

    return "".join(chars)


# Bengali danda, western full stop, or a blank line: the places a sentence
# can honestly be cut. Trimming anywhere else would hand the reviewer half a
# clause and invite them to complete it themselves.
SENTENCE_BREAK = re.compile(r"[।.!?\n]")


def sentence_window(masked: str, start: int, end: int,
                    width: int = 320) -> Optional[str]:
    """
    A whole-sentence excerpt around ``masked[start:end]``, built from the
    masked text so no markup appears in it. None when the match does not
    survive trimming.
    """
    lo = max(0, start - width)
    hi = min(len(masked), end + width)

    breaks = [m.end() for m in SENTENCE_BREAK.finditer(masked, lo, start)]
    if breaks:
        lo = breaks[-1]
    tail = SENTENCE_BREAK.search(masked, end, hi)
    if tail:
        hi = tail.end()

    excerpt = re.sub(r"\s+", " ", masked[lo:hi]).strip()
    needle = re.sub(r"\s+", " ", masked[start:end]).strip()
    return excerpt if needle and needle in excerpt else None


def find_excerpt_candidates(content: str, must: List[str], any_of: List[str],
                            width: int = 320) -> List[Dict[str, Any]]:
    """
    Every window holding a required term together with a corroborating term,
    each tagged ``prose`` or ``markup_only``. Ordered prose first.
    """
    text = content or ""
    masked = mask_non_prose(text)
    # mask_non_prose replaces characters in place, so `masked` is the same
    # length as `text` and offsets are shared. Neither is flattened here --
    # collapsing whitespace first would shift the two by different amounts and
    # silently destroy that correspondence. Only the display window is
    # flattened, at the end.
    candidates: List[Dict[str, Any]] = []
    seen = set()

    for m_term in must:
        for m in re.finditer(re.escape(m_term.lower()), text.lower()):
            lo = max(0, m.start() - width)
            hi = min(len(text), m.end() + width)
            window = re.sub(r"\s+", " ", text[lo:hi]).strip()
            masked_window = masked[lo:hi]

            subject_in_prose = masked[m.start():m.end()].strip() != ""
            if subject_in_prose:
                # Show the reviewer the sentences, not the surrounding
                # template soup the window happens to overlap.
                window = sentence_window(masked, m.start(), m.end(), width) \
                    or window
            # The corroborating term has to survive masking too. A prose
            # subject beside a term that only appears in a caption is the same
            # false lead in a different order.
            object_in_prose = any(a.lower() in masked_window.lower()
                                  for a in any_of)
            if not any(a.lower() in window.lower() for a in any_of):
                continue

            region = ("prose" if (subject_in_prose and object_in_prose)
                      else "markup_only")
            key = (window[:120], region)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "excerpt": window,
                "offset": m.start(),
                "region": region,
                "matched_term": m_term,
                "subject_in_prose": subject_in_prose,
                "object_in_prose": object_in_prose,
            })

    candidates.sort(key=lambda c: (c["region"] != "prose", c["offset"]))
    return candidates


def find_excerpt(content: str, must: List[str], any_of: List[str],
                 width: int = 320) -> Optional[str]:
    """
    The best prose window, or None. A markup-only hit never stands in for one:
    a filename next to a caption asserts nothing about the claim.
    """
    for cand in find_excerpt_candidates(content, must, any_of, width):
        if cand["region"] == "prose":
            return cand["excerpt"]
    return None


def _next_evidence_seq() -> int:
    seq = 0
    if EVIDENCE_PATH.exists():
        for line in EVIDENCE_PATH.read_text(encoding="utf-8").splitlines():
            m = re.search(r"EV_PILOT_(\d+)", line)
            if m:
                seq = max(seq, int(m.group(1)))
    return seq + 1


def source() -> int:
    session = _session()
    seq = _next_evidence_seq()
    ev_lines: List[Dict[str, Any]] = []
    link_lines: List[Dict[str, Any]] = []
    log_lines: List[Dict[str, Any]] = []
    queue_lines: List[Dict[str, Any]] = []

    CLAIM_CARDS_PATH.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True)
                  for c in CLAIM_CARDS) + "\n", encoding="utf-8")
    print("Wrote %s (%d claim cards)\n" % (CLAIM_CARDS_PATH.name, len(CLAIM_CARDS)))

    for card in CLAIM_CARDS:
        fact_id = card["fact_id"]
        print("=== %s (%s, %d atomic claim(s)) ==="
              % (fact_id, card["ra_temporal_class"], len(card["atomic_claims"])))
        atoms_supported = 0

        for atom in card["atomic_claims"]:
            found = None
            tried: List[str] = []
            for lang, titles in (("bn", atom["titles_bn"]), ("en", atom["titles_en"])):
                for title in titles:
                    tried.append("%s:%s" % (lang, title))
                    rev = revision_as_of(session, lang, title)
                    if rev.get("error") or not rev.get("existed_at_cutoff"):
                        continue
                    excerpt = find_excerpt(rev.get("content") or "",
                                           atom["must_match"], atom["any_of"])
                    if excerpt:
                        found = (lang, title, rev, excerpt)
                        break
                if found:
                    break

            if not found:
                log_lines.append({
                    "logged_at": TODAY, "fact_id": fact_id,
                    "claim_id": atom["claim_id"],
                    "outcome": "not_enough_admissible_evidence",
                    "work_status": "deferred", "titles_tried": tried,
                    "reason": ("No pre-cutoff MediaWiki revision on the approved "
                               "route contained a direct excerpt linking the "
                               "subject to the object."),
                })
                print("  DEFER  %s  (tried %s)" % (atom["claim_id"], ", ".join(tried)))
                continue

            lang, title, rev, excerpt = found
            ev_id = "EV_PILOT_%06d" % seq
            seq += 1
            atoms_supported += 1
            rev_date = (rev.get("timestamp") or "")[:10]
            url = "https://%s.wikipedia.org/w/index.php?oldid=%s" % (lang, rev["revid"])

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
                    (rev.get("content") or "").encode("utf-8")).hexdigest(),
                "document_locator": "MediaWiki oldid=%s" % rev["revid"],
                "access_status": "retrieved",
                "admissibility_status": "pending_reviewer",
                "notes": ("Pre-cutoff revision (%s <= %s). The revision date is "
                          "the temporal evidence date, not a publication date "
                          "for the underlying claim." % (rev_date, CUTOFF)),
            })
            link_lines.append({
                "link_id": "LNK_%s_%s" % (atom["claim_id"], ev_id),
                "fact_id": fact_id, "claim_id": atom["claim_id"],
                "evidence_id": ev_id, "support_role": "primary_support",
                "supporting_excerpt": excerpt[:900],
                "subject_match": atom["subject"],
                "predicate_match": atom["predicate"],
                "object_match": atom["object"],
                "semantic_verdict": "pending_reviewer",
                "temporal_field_name": "mediawiki_revision_timestamp",
                "temporal_raw_value": rev.get("timestamp"),
                "valid_from": None, "valid_to": None,
                "temporal_verdict": "pending_reviewer",
                "leakage_verdict": "pending_reviewer",
                "review_status": "pending_reviewer", "reviewer_id": None,
                "claim_scope_match": "pending_reviewer",
                "unsupported_qualifiers": atom.get("qualifiers", []),
            })
            log_lines.append({
                "logged_at": TODAY, "fact_id": fact_id,
                "claim_id": atom["claim_id"], "outcome": "candidate_evidence_found",
                "work_status": "candidate_source_found", "titles_tried": tried,
                "evidence_id": ev_id, "canonical_url": url,
                "revision_date": rev_date,
            })
            print("  FOUND  %s  -> %s @ %s (%s)"
                  % (atom["claim_id"], rev.get("resolved_title"), rev_date, ev_id))

        total = len(card["atomic_claims"])
        row_status = ("candidate_source_found" if atoms_supported == total
                      else "source_searching" if atoms_supported else "deferred")
        queue_lines.append({
            "fact_id": fact_id, "batch": "pilot_static_revised_02",
            "updated_at": TODAY, "work_status": row_status,
            "evidence_status": ("complete" if atoms_supported == total
                                else "partial" if atoms_supported else "not_started"),
            "review_status": "pending",
            "atomic_claims_total": total,
            "atomic_claims_supported": atoms_supported,
            "compound": card["compound"],
            "ra_temporal_class": card["ra_temporal_class"],
            "ra_class_reason": card["ra_class_reason"],
            "model_b_merge_allowed": False,
            "accepted_for_build": False,
            "blocking_note": (
                "Compound row: acceptable only when every atomic claim is "
                "independently supported." if card["compound"]
                else "Single-atom row."),
        })
        print("  row status: %s (%d/%d atoms supported)\n"
              % (row_status, atoms_supported, total))

    for path, rows in ((EVIDENCE_PATH, ev_lines), (LINKS_PATH, link_lines),
                       (SOURCING_LOG, log_lines), (WORK_QUEUE, queue_lines)):
        if not rows:
            continue
        with path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        print("appended %d row(s) -> %s" % (len(rows), path.name))

    print("\nNo status above 'pending_reviewer' was set. Frozen seed untouched.")
    return 0


# ---------------------------------------------------------------------------
# 3. RA adjudication of the auto-retrieved excerpts
#
# An automated keyword window is a LEAD, not evidence. Each excerpt was read
# in full; the verdicts below are the result of that reading. Two of the six
# are outright false positives where the required term matched an unrelated
# context, which is precisely what the human-in-the-loop step exists to catch.
# ---------------------------------------------------------------------------
ADJUDICATION_PATH = WORKFLOW / "batch02_ra_excerpt_adjudication.jsonl"

ADJUDICATIONS: List[Dict[str, Any]] = [
    {
        "claim_id": "BCSGK-0359-A", "evidence_id": "EV_PILOT_000004",
        "ra_verdict": "partial_support",
        "claim_scope_match": "narrower_than_claim",
        "supported_scope": "গারো inhabit বৃহত্তর ময়মনসিংহ (greater Mymensingh)",
        "unsupported_qualifiers": ["সুনামগঞ্জ", "সিলেট"],
        "admissibility_recommendation": "hold_for_additional_evidence",
        "reviewer_note": (
            "The revision states the Garo live in India's Meghalaya and "
            "Bangladesh's greater Mymensingh. Greater Mymensingh covers "
            "Mymensingh, Netrokona, Sherpur, Jamalpur and Tangail, but NOT "
            "Sunamganj or Sylhet, which the corpus row also asserts. The "
            "excerpt therefore supports five of the seven districts only."
        ),
    },
    {
        "claim_id": "BCSGK-0359-B", "evidence_id": "EV_PILOT_000005",
        "ra_verdict": "insufficient_excerpt",
        "claim_scope_match": "not_established",
        "supported_scope": None,
        "unsupported_qualifiers": ["দুর্গাপুর উপজেলা", "নেত্রকোণা জেলা"],
        "admissibility_recommendation": "reject_excerpt_reextract_required",
        "reviewer_note": (
            "The captured window is infobox boilerplate; the term "
            "'দুর্গাপুর' matched only inside an image caption "
            "('সুসং দুর্গাপুরের চিনামাটির পাহাড়'). No prose sentence "
            "asserting Birishiri's upazila/district parentage was captured. "
            "Re-extraction against the infobox district fields is required "
            "before this can be treated as support."
        ),
    },
    {
        "claim_id": "BCSGK-0315-A", "evidence_id": "EV_PILOT_000006",
        "ra_verdict": "partial_support",
        "claim_scope_match": "narrower_than_claim",
        "supported_scope": "jhum is practised in পার্বত্য চট্টগ্রাম (CHT)",
        "unsupported_qualifiers": ["চট্টগ্রাম জেলা (plain Chattogram district)"],
        "admissibility_recommendation": "hold_pending_pi_classification",
        "reviewer_note": (
            "The revision supports jhum in the Chittagong Hill Tracts, but the "
            "corpus row asserts 'Chattogram AND the CHT districts'; plain "
            "Chattogram district is not supported by this excerpt. Separately, "
            "the static/dynamic classification conflict for this row is still "
            "unresolved, so it cannot be accepted under the static rule yet."
        ),
    },
    {
        "claim_id": "BCSGK-0336-A", "evidence_id": "EV_PILOT_000007",
        "ra_verdict": "false_positive",
        "claim_scope_match": "not_established",
        "supported_scope": None,
        "unsupported_qualifiers": ["কেন্দ্রীয় গো-প্রজনন খামার"],
        "admissibility_recommendation": "reject_excerpt",
        "reviewer_note": (
            "'প্রজনন' matched an occupation-share table for Savar "
            "upazila ('গবাদি পশু প্রজনন ... 1.90%'), which is a labour "
            "statistic, not a statement that the Central Cattle Breeding Farm "
            "is located in Savar. No support for the institution's location."
        ),
    },
    {
        "claim_id": "BCSGK-0336-D", "evidence_id": "EV_PILOT_000008",
        "ra_verdict": "false_positive",
        "claim_scope_match": "not_established",
        "supported_scope": None,
        "unsupported_qualifiers": ["মহিষ প্রজনন ও উন্নয়ন খামার"],
        "admissibility_recommendation": "reject_excerpt",
        "reviewer_note": (
            "'মহিষ' matched 'অষ্টাদশ ভুজা মহিষ মর্দিনী দেবীমূর্তি' -- a "
            "Mahishasuramardini deity statue in a passage on ancient religion "
            "in Bagerhat. Entirely unrelated to a buffalo breeding farm. Clear "
            "lexical false positive."
        ),
    },
    {
        "claim_id": "BCSGK-0336-E", "evidence_id": "EV_PILOT_000009",
        "ra_verdict": "weak_partial_support",
        "claim_scope_match": "narrower_than_claim",
        "supported_scope": "a 'কুমির খামার' is listed among Bhaluka's industries",
        "unsupported_qualifiers": ["বেসরকারি (private)", "প্রজনন কেন্দ্র (breeding centre)"],
        "admissibility_recommendation": "hold_for_official_source",
        "reviewer_note": (
            "The revision lists 'কুমির খামার' in a run-on list of Bhaluka "
            "industries. That is consistent with the claim but does not "
            "establish the named entity, its private status, or its function "
            "as a breeding centre. An official/institutional source is needed."
        ),
    },
]


def adjudicate() -> int:
    rows = []
    for a in ADJUDICATIONS:
        rec = dict(a)
        rec.update({
            "record_type": "ra_excerpt_adjudication",
            "batch": "pilot_static_revised_02",
            "adjudicated_at": TODAY,
            "reviewer_id": "RA_1",
            "review_mode": "two_pass_single_operator",
            "personnel_independent": False,
            "external_second_review_required": True,
            "accepted_for_build": False,
            "model_b_merge_allowed": False,
        })
        rows.append(rec)

    with ADJUDICATION_PATH.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["ra_verdict"]] = counts.get(r["ra_verdict"], 0) + 1
    print("Appended %d adjudication(s) -> %s" % (len(rows), ADJUDICATION_PATH.name))
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("  %-24s %d" % (k, v))
    print("\nAdmissible as candidate evidence right now: 0")
    print("Every excerpt is either partial, insufficient, or a false positive.")
    return 0


# ---------------------------------------------------------------------------
# 4. Follow-up sourcing round
#
# Three Batch-02 atoms came out of the first pass unsupported. Each gets a
# different treatment, because each failed for a different reason:
#
#   BCSGK-0359-B  The article always held the evidence; the search could not
#                 see past the infobox. Re-run on the same title with the
#                 fixed excerpt search -- no new source needed.
#   BCSGK-0359-A  বৃহত্তর ময়মনসিংহ was supported, সুনামগঞ্জ and সিলেট were not.
#                 Go to the district articles themselves, one per district,
#                 and record each answer separately -- including a negative.
#   BCSGK-0336    Needs an official pre-cutoff page from dls.gov.bd. The live
#                 site cannot serve as cutoff evidence, so this one depends on
#                 a Wayback capture and nothing else will do.
#
# A negative result is written down as deliberately as a positive one. "We
# looked in the district's own article and the group is not mentioned" is a
# finding about the corpus row; leaving it unlogged would let the next pass
# spend the same effort to learn the same thing.
# ---------------------------------------------------------------------------
FOLLOWUP_PATH = WORKFLOW / "batch02_followup_sourcing.jsonl"
FOLLOWUP_READINGS_PATH = WORKFLOW / "batch02_followup_readings.jsonl"

FOLLOWUP_TASKS: List[Dict[str, Any]] = [
    {
        "claim_id": "BCSGK-0359-B",
        "fact_id": "BCSGK-0359",
        "subject": "বিরিশিরি (Birishiri)",
        "predicate": "part_of",
        "object": "দুর্গাপুর উপজেলা, নেত্রকোণা জেলা",
        "titles": [("bn", "বিরিশিরি")],
        "must_match": ["বিরিশিরি"],
        "any_of": ["দুর্গাপুর", "নেত্রকোণা"],
        "followup_reason": (
            "First pass logged `insufficient -- matched only an image "
            "caption`. That was a search defect, not an absence of evidence: "
            "the infobox window was returned before the lead sentence was "
            "ever reached. Re-run with the corrected excerpt search."
        ),
        "expectation": "prose support expected in the article lead",
    },
    {
        "claim_id": "BCSGK-0359-A-sunamganj",
        "fact_id": "BCSGK-0359",
        "parent_claim_id": "BCSGK-0359-A",
        "subject": "গারো (Garo people)",
        "predicate": "located_in",
        "object": "সুনামগঞ্জ",
        "titles": [("bn", "সুনামগঞ্জ জেলা")],
        "must_match": ["গারো"],
        "any_of": ["জেলায়", "সুনামগঞ্জ", "আদিবাসী"],
        "followup_reason": (
            "RA_1 recorded বৃহত্তর ময়মনসিংহ as supported and সুনামগঞ্জ / সিলেট "
            "as unsupported (5 of 7 districts). Ask the district's own "
            "article rather than the ethnic-group article."
        ),
        "expectation": "unknown -- this is a genuine question, not a re-run",
    },
    {
        "claim_id": "BCSGK-0359-A-sylhet",
        "fact_id": "BCSGK-0359",
        "parent_claim_id": "BCSGK-0359-A",
        "subject": "গারো (Garo people)",
        "predicate": "located_in",
        "object": "সিলেট",
        "titles": [("bn", "সিলেট জেলা")],
        "must_match": ["গারো"],
        "any_of": ["জেলায়", "সিলেট", "আদিবাসী"],
        "followup_reason": "Same question, for the remaining unsupported district.",
        "expectation": "unknown -- this is a genuine question, not a re-run",
        "do_not_substitute": (
            "সিলেট বিভাগ (division) is NOT an acceptable stand-in for সিলেট জেলা "
            "(district). The division article's only গারো mention is পূর্ব কালে ... "
            "about historical cultural influence, not present residence."
        ),
    },
]


def followup() -> int:
    session = _session()
    seq = _next_evidence_seq()
    ev_lines, link_lines, log_lines, readings = [], [], [], []

    for task in FOLLOWUP_TASKS:
        cid = task["claim_id"]
        print("=== %s (%s)" % (cid, task["object"]))
        found = None
        rejected: List[Dict[str, Any]] = []
        tried: List[str] = []

        for lang, title in task["titles"]:
            tried.append("%s:%s" % (lang, title))
            rev = revision_as_of(session, lang, title)
            if rev.get("error") or not rev.get("existed_at_cutoff"):
                print("   %s: no pre-cutoff revision (%s)"
                      % (title, rev.get("error") or "did not exist"))
                continue
            cands = find_excerpt_candidates(rev.get("content") or "",
                                            task["must_match"], task["any_of"])
            prose = [c for c in cands if c["region"] == "prose"]
            rejected.extend({"title": title, **c} for c in cands
                            if c["region"] != "prose")
            if prose:
                found = (lang, title, rev, prose[0])
                break
            print("   %s: %d candidate(s), none in prose" % (title, len(cands)))

        if not found:
            log_lines.append({
                "logged_at": TODAY, "record_type": "followup_sourcing",
                "fact_id": task["fact_id"], "claim_id": cid,
                "parent_claim_id": task.get("parent_claim_id"),
                "outcome": "no_prose_support_found",
                "work_status": "deferred", "titles_tried": tried,
                "followup_reason": task["followup_reason"],
                "rejected_leads": [
                    {"title": r["title"], "region": r["region"],
                     "excerpt": r["excerpt"][:300]} for r in rejected[:5]],
                "finding": (
                    "Searched the approved pre-cutoff source for this object "
                    "and found no prose sentence supporting it. This is a "
                    "finding about the corpus row, not a gap in the search."
                ),
            })
            print("   RESULT: no prose support (%d rejected lead(s))\n"
                  % len(rejected))
            continue

        lang, title, rev, cand = found
        ev_id = "EV_PILOT_%06d" % seq
        seq += 1
        rev_date = (rev.get("timestamp") or "")[:10]
        url = "https://%s.wikipedia.org/w/index.php?oldid=%s" % (lang, rev["revid"])

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
                (rev.get("content") or "").encode("utf-8")).hexdigest(),
            "document_locator": "MediaWiki oldid=%s" % rev["revid"],
            "access_status": "retrieved",
            "admissibility_status": "pending_reviewer",
            "notes": ("Follow-up round. Pre-cutoff revision (%s <= %s)."
                      % (rev_date, CUTOFF)),
        })
        link_lines.append({
            "link_id": "LNK_%s_%s" % (cid, ev_id),
            "fact_id": task["fact_id"], "claim_id": cid,
            "parent_claim_id": task.get("parent_claim_id"),
            "evidence_id": ev_id, "support_role": "primary_support",
            "supporting_excerpt": cand["excerpt"][:900],
            "excerpt_region": cand["region"],
            "subject_match": task["subject"],
            "predicate_match": task["predicate"],
            "object_match": task["object"],
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
            "logged_at": TODAY, "record_type": "followup_sourcing",
            "fact_id": task["fact_id"], "claim_id": cid,
            "parent_claim_id": task.get("parent_claim_id"),
            "outcome": "candidate_evidence_found",
            "work_status": "candidate_source_found", "titles_tried": tried,
            "evidence_id": ev_id, "canonical_url": url,
            "revision_date": rev_date,
            "followup_reason": task["followup_reason"],
            "rejected_leads": [
                {"title": r["title"], "region": r["region"],
                 "excerpt": r["excerpt"][:300]} for r in rejected[:5]],
        })
        readings.append({
            "record_type": "excerpt_reading_proposal",
            "read_at": TODAY,
            "reviewer_id": "auto_reading_v2",
            "counts_toward_independent_review": False,
            "why_not": (
                "Produced by the sourcing pass itself, so it is a lead for a "
                "human reviewer to confirm or reject, not one of the two "
                "independent readings the merge rule requires."
            ),
            "fact_id": task["fact_id"], "claim_id": cid,
            "evidence_id": ev_id,
            "excerpt": cand["excerpt"][:900],
            "excerpt_region": cand["region"],
            "proposed_verdict": "pending_human_read",
            "accepted_for_build": False,
        })
        print("   FOUND  %s @ %s (%s)" % (rev.get("resolved_title"), rev_date, ev_id))
        print("   %s\n" % cand["excerpt"][:180])

    for path, rows in ((EVIDENCE_PATH, ev_lines), (LINKS_PATH, link_lines),
                       (FOLLOWUP_PATH, log_lines),
                       (FOLLOWUP_READINGS_PATH, readings)):
        if not rows:
            continue
        with path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        print("appended %d row(s) -> %s" % (len(rows), path.name))

    print("\nNothing was accepted for build. Frozen seed untouched.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["attest", "source", "adjudicate",
                                        "followup", "all"])
    args = ap.parse_args(argv)
    if args.command == "attest":
        return attest()
    if args.command == "source":
        return source()
    if args.command == "adjudicate":
        return adjudicate()
    if args.command == "followup":
        return followup()
    rc = attest()
    print()
    rc = rc or source()
    print()
    return rc or adjudicate()


if __name__ == "__main__":
    sys.exit(main())
