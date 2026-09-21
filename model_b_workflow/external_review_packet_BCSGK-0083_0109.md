# External second review — two evidence items

**Prepared:** 2026-09-17 · **Expected effort:** 30–45 minutes · **Items:** 2

You do not need any prior knowledge of this project. Everything required is
in this document.

---

## 1. What you are being asked to do

We are building a set of general-knowledge facts that were **verifiably known
by 19 April 2023**. That date is a cutoff: the facts will later be used to
test a system on an exam held after it, so anything that leaked in from after
the cutoff would invalidate the test.

Each fact must be supported by a document that existed before the cutoff. Two
such supporting items were reviewed internally and approved. **We cannot count
those approvals**, for a reason explained in §2. We are asking you to read the
same two items independently and say whether the evidence supports the claim.

You are **not** being asked whether the claim is true. You may well know the
answer already. The question is narrower and it matters that you keep to it:

> Does *this specific document*, which existed before 19 April 2023, support
> *this specific wording*?

A claim can be perfectly true and still fail this test, if the cited document
does not actually say it.

### Two rules that cannot be bent

1. **Do not consult the 45th BCS exam paper or its answer key.** That is the
   exam being tested against. If you happen to know its contents, set that
   knowledge aside and judge only the document in front of you.
2. **Do not substitute a newer source.** If you look up one of these topics
   today and find a better page, that page is dated after the cutoff and is
   not usable. Tell us instead — "the cited document does not support this,
   though other sources might" is a useful answer.

---

## 2. Why an outside reader is needed

Both items were reviewed twice internally, recorded as `RA_1` and `RA_2`.
When those records were audited, the two reviewers' stated reasons were found
to be **byte-for-byte identical**. Two people forming a judgement separately
do not write the same sentence, so the two passes were treated as one
operator working twice, and the internal approvals were downgraded.

This is recorded in `review_independence_attestation.json` with
`personnel_independent: false`. Nothing has been merged on the strength of
those reviews.

So: please form your own view before reading ours. The internal reasoning is
quoted below each item, but read the excerpt first.

---

## 3. How to check an excerpt against its source

For each item we give you a permanent URL and a SHA-256 hash of the document
as retrieved. The check has three parts:

1. **Open the URL.** Wikipedia `oldid=` links are permanent revision
   snapshots — they show the page as it stood on that date, not as it is now.
   That is the point: it is pre-cutoff evidence.
2. **Find the quoted excerpt in the document.** Confirm it appears
   substantially as quoted, and that the surrounding context does not change
   its meaning. Quoting across an intervening "however" or a hypothetical
   changes what a sentence asserts.
3. **Compare the excerpt to the claim wording, word by word.** This is where
   these usually fail. In an earlier batch, one excerpt said a district holds
   the country's *largest single tea garden* while the claim said that
   district has *the most tea gardens* — same district, different assertion.

---

## 4. Item 1 — BCSGK-0083

### The claim, as worded in our corpus

> **Kabaddi is the national sport of Bangladesh.**

### The evidence

| | |
|---|---|
| Evidence id | `EV_PILOT_000002` |
| Document | "Bangladesh makes remarkable progress in sports in 14 years: Hasan" |
| Publisher | Bangladesh Sangbad Sangstha (BSS), the state news agency |
| URL | https://www.bssnews.net/news-flash/115350 |
| Published | 2023-03-13 — **before** the cutoff |
| SHA-256 | `0a4bf9acac81975f0bfe40d1c504ea08888bdc391c23f468ffa198644e091f65` |

**Excerpt on record:**

> He said, "Kabaddi is our national sport and the game has been
> internationalized."

### What we would like you to decide

1. Does the excerpt appear in that report, and does "He" refer to the
   Information and Broadcasting Minister?
2. **The central question:** the document reports a minister *saying* this in
   a speech. Does a reported ministerial remark establish that Kabaddi **is**
   the national sport, or does it only establish that the minister said so?
   These are different claims and our corpus asserts the first.
3. If you judge it too weak, say what would be strong enough — a gazette
   notification, a National Sports Council page, a ministry document. You do
   not have to find one.

### A second, weaker item on the same fact

A later automated pass retrieved the English Wikipedia "Kabaddi" article
(revision of 2023-04-18, `oldid=1150553288`, evidence `EV_PILOT_000024`). Its
opening sentence is *"Kabaddi is a contact team sport"* — a definition of the
game that does not mention Bangladesh. We have already marked this
**insufficient**; it is listed here only so you know it exists and is not
being relied on.

### Internal reasoning (read after forming your own view)

> RA_1 / RA_2, identical text: "The dated BSS report directly quotes the
> Information and Broadcasting Minister: 'Kabaddi is our national sport'. The
> publication date is 2023-03-13, before cutoff, and the source is not
> exam/answer-key material."

Note that this reasoning addresses the date and the provenance but does not
address question 2 above.

---

## 5. Item 2 — BCSGK-0109

### The claim, as worded in our corpus

> **The Bangladesh Military Academy is located in Bhatiari, Chattogram.**

### The evidence

| | |
|---|---|
| Evidence id | `EV_PILOT_000003` |
| Document | "Bangladesh Military Academy", English Wikipedia |
| URL | https://en.wikipedia.org/w/index.php?oldid=1150163247 |
| Revision date | 2023-04-16 — **before** the cutoff, by three days |
| SHA-256 | `cbfafe21570ced7402a9001ff4055d2d9c27dd4d35f814d95267315b9a98bde3` |

**Excerpt on record:**

> It is located in Bhatiary, near Chittagong Hill Tracts, in the Chittagong
> District of south-east Bangladesh, about 13 kilometers north of Chittagong.
> The academy is situated on the slopes of the Sitakunda hill ranges and the
> shore of the Bay of Bengal.

### What we would like you to decide

1. Does the excerpt appear in that revision as quoted?
2. **Naming.** The claim says "Bhatiari, Chattogram"; the excerpt says
   "Bhatiary" and "Chittagong District". Chittagong was officially renamed
   Chattogram in 2018, and Bhatiari/Bhatiary is a transliteration variant. Is
   that an adequate match, or should the corpus wording be aligned to the
   source?
3. **Source type.** This is Wikipedia — a tertiary source. It is admissible
   under our rules because the revision is provably pre-cutoff, but if you
   think an institutional source should be required for a military
   installation, say so.

### Internal reasoning (read after forming your own view)

> RA_1 / RA_2, identical text: "The 2023-04-16 MediaWiki revision directly
> states that Bangladesh Military Academy is located in Bhatiary/Chittagong.
> Bhatiary and Chattogram are transliteration variants; the source is
> pre-cutoff and the location claim is static."

---

## 6. How to respond

Fill in `external_review_response_template.json` (next to this file), one
block per item. The fields, and what we mean by each:

| Field | What to put |
|---|---|
| `reviewer_name` / `affiliation` | So the record shows the review was external |
| `excerpt_found_in_source` | `true` / `false` / `partially` — did the quote check out |
| `supports_claim` | `full` / `partial` / `insufficient` / `contradicts` |
| `scope_mismatch` | Anything the claim asserts that the document does not |
| `reason` | **In your own words.** This is the point of the exercise — please do not paraphrase our reasoning back to us |
| `recommended_action` | `accept` / `narrow_the_claim` / `find_stronger_source` / `reject` |

"Insufficient" is a perfectly good answer and is not a failed review. Of the
last 21 automated matches we read, one supported its claim outright.

**Please do not edit any other file in this directory.** The ledgers are
append-only and your response is added as a new record.

### Questions

If anything here is ambiguous, say so in `reason` rather than guessing. An
ambiguity you flag is more useful to us than a verdict you were unsure of.
