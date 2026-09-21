# 11. Experimental Execution Plan

## Goal
Translate the research design into a concrete execution pipeline.

## Step Sequence
1. Prepare the BCS corpus and temporal metadata
2. Build topic ontology and topic-demand mapping
3. Create or update the dynamic bitemporal knowledge graph
4. Acquire episodic web knowledge with provenance
5. Generate candidate MCQs from time-conditioned graph state
6. Create temporally contrastive distractors
7. Run verification and rejection checks
8. Compare with baseline methods
9. Analyze performance with holdout evaluation
10. Write results and discussion

## Baselines to Compare
- static retrieval baseline
- web-RAG baseline
- generic LLM generation baseline
- temporal graph + generation system

## Evaluation Dimensions
- factual validity
- temporal correctness
- exam relevance
- distractor quality
- rejection effectiveness
- overall usefulness

## Success Criteria
The system should outperform strong baselines on the key dimensions while preserving a clear temporal and provenance-aware design.






Work on exactly one task from the implementation plan.

Before editing:
1. Read the relevant code and tests.
2. State the local root-cause hypothesis.
3. Identify the cheapest test that can disprove it.

Then:
1. Implement the smallest correct change.
2. Run the focused test immediately.
3. Fix any failure.
4. Re-run the test.
5. Run related validation before moving to the next task.
6. Do not start the next task until this one is verified.

Report:
- Files changed
- Tests executed
- Test results
- Remaining risks



## RA Tasks

### Task 1 — Sadia Reza: Prepare Temporal Corpus
আপনাদের উদ্দেশ্য হলো এই ৬১৬টি তথ্যের সাথে **সময় সম্পর্কিত তথ্য (Temporal Metadata)** যোগ করা।

### Objective
Prepare `bcs_gk_facts.json` for the temporal holdout experiment. The file contains 616 facts, but currently none has temporal metadata.

### Required Action
For each fact, verify the source and add the metadata that can be supported by evidence:

- `valid_from`: when the fact became true
- `valid_to`: when it stopped being true; leave empty for an open-ended fact
- `source_published_at`: publication or update date of the cited source
- `source_tier`: reliability tier from the project policy
- `relation`: controlled fact relation when identifiable
- `observed_at`: when the system recorded the fact

Do not invent dates. If a date cannot be verified, mark the fact for review instead of assigning an estimate. Do not add the 45th BCS target paper or any post-cutoff evidence to the corpus.

### Acceptance Check
Run the metadata coverage audit and confirm that every fact is either temporally annotated with evidence or explicitly listed in a review report. The strict temporal guard must pass for the `2023-04-19` cutoff before Task 1 is complete.

### Deliverables

1. Updated corpus metadata
2. Source/date verification notes for uncertain facts
3. Metadata coverage report
4. Output from `python test_cutoff.py`

### Implementation Update — 2026-09-16 (source-verification pass)

A reproducible verifier, `verify_temporal_corpus.py`, now performs the source
check the earlier annotation pass deferred. It fetches every cited
`source_url` over the network, looks for a Wayback capture at or before the
cutoff, checks whether the retrieved page actually contains the fact's own
entities, and only then writes temporal metadata.

**What the sources turned out to be.** Of 164 distinct cited URLs:

| Outcome | URLs |
|---|---|
| HTTP 404 | 92 |
| DNS / TLS / connection failure | 46 |
| 403 / 401 / 500 / 202 | 7 |
| HTTP 200 | 19 (4 of them catch-all routes) |

No cited page exposes a publication date at or before `2023-04-19`. Only two
URLs have a pre-cutoff Wayback capture. Three facts have a source that
genuinely supports the claim, none with a usable pre-cutoff date.

The 491 corpus-derived facts cite `bpsc.gov.bd/exam/{N}th-bcs` paths that do
not exist on the BPSC site. 22 of them cite the **45th BCS target paper**
and 102 cite exams held **after** the cutoff — both are leakage under the
holdout policy independent of whether the URL resolves.

**What was written.** All 616 facts carry `relation` (182 specific, 434
`STATED_AS`), `source_tier` from the project policy in `web_scraper.py`,
`observed_at`, and full provenance (`verification_status`,
`verification_notes`, `verified_at`, `source_tier_basis`,
`source_snapshot_hash`). `valid_from`, `valid_to` and `source_published_at`
were left empty wherever evidence was absent — **no date was invented**.

**Corrections to the earlier pass.** `source_tier` had been assigned from an
ad-hoc local table that mapped `*.gov.bd` to tier 1, giving all 491
non-resolving BPSC URLs the most authoritative tier; the project policy
leaves `bpsc.gov.bd` unmapped, so they are now tier 4 pending review.
`source_snapshot_hash` had been `sha256("url|fact_text")`, a digest of the
corpus row that proves nothing about the source; it is now the digest of the
bytes actually retrieved, or null where nothing was retrieved.

**Task status: PARTIALLY COMPLETE.** The strict temporal guard **fails** for
`2023-04-19`: all 616 facts are blocked as unversioned across all 21 topics.
The blocker is not annotation effort — it is that the corpus's citation layer
does not resolve, so no `valid_from` can be grounded in a source. Re-sourcing
the corpus against real, datable documents is a prerequisite, and rule 13
puts that decision with the PI rather than the annotation pass.

## RA Evaluation Report — Temporal Corpus Resourcing Pass

### Evaluation Date

2026-09-16

### Executive Finding

The source-verification pass was executed correctly and produced useful evidence. It did not complete Task 1, because the original citation layer is largely unusable and the strict interval-only guard remains impossible to satisfy for this mixed corpus.

The pass must be treated as an evidence audit and resourcing proposal, not as completed metadata annotation. `resourcing_proposals.json` is explicitly marked **PROPOSALS ONLY** and the corpus was not modified with those proposed replacements.

### Verified Results

- Original corpus: 616 facts.
- Holdout-eligible after triage: 596 facts.
- Excluded from holdout: 20 facts, including 18 target-paper-selection records and 2 placeholder rows.
- Proposed replacement records: 596.
- Proposed pre-cutoff source evidence: 181 facts.
- Proposed validity dates: 33 facts.
- Proposed records without a validity date: 148 facts.
- Proposed records with no replacement source found: 415 facts.
- Proposal temporal classes: 242 static, 226 dynamic, 128 unclassified.
- Current accepted audit: 0 verified `valid_from`, 0 verified `valid_to`, and 14 verified source publication dates.
- Current strict-cutoff eligibility: 0 facts; all 616 remain blocked by the existing interval-only guard.

### Main Interpretation

The strict guard is applying a rule that is too narrow for mixed fact types. Timeless facts such as capitals, locations, and stable institutional descriptions do not necessarily have a meaningful `valid_from`. Their temporal evidence can instead be a verified source snapshot that existed on or before `2023-04-19` and supports the fact.

This is defensible only for facts classified as **static** and only when the pre-cutoff source actually supports the fact. It must not be implemented as a blind exemption based only on a `temporal_class` label.

Dynamic facts, including office holders, current statistics, population, economic indicators, and other changing claims, must continue to require a real validity interval. Unclassified facts must not enter the benchmark until triaged.

### Recommendation To RAs

Recommend **Model B**, subject to PI approval:

1. Accept only the 181 proposed source replacements after manual or reproducible support review.
2. For static facts, use a verified pre-cutoff source snapshot as temporal evidence; do not require `valid_from` when the fact is genuinely timeless.
3. Keep dynamic facts under strict `valid_from`/`valid_to` rules.
4. Triage all 128 unclassified facts before applying either rule.
5. Exclude the 20 holdout-ineligible facts permanently from the 2023 benchmark while retaining them in the source corpus with exclusion reasons.
6. Do not merge `resourcing_proposals.json` into `bcs_gk_facts.json` until each accepted proposal has passed the support, provenance, and cutoff checks.

### Required Next RA Actions

1. Review the 181 proposed pre-cutoff sources and accept or reject each proposal.
2. Triage the 128 unclassified facts into static, dynamic, or exclude/review.
3. Re-source the 415 facts with no usable replacement source, prioritizing high-demand topics.
4. Add an evidence-based eligibility field for static facts, separate from `valid_from`.
5. Update the strict guard to apply the static-source rule only to accepted, supported, pre-cutoff evidence.
6. Run a new audit and verify that no target-paper or post-cutoff evidence is admitted.

### Acceptance Conditions

Task 1 should be marked **COMPLETE** only when:

- Every accepted benchmark fact is either static with verified pre-cutoff supporting evidence or dynamic with a verified validity interval.
- All 128 unclassified facts have a documented decision.
- The 20 excluded facts cannot enter the holdout retrieval path.
- Every accepted source has provenance and a cutoff decision.
- The updated guard and audit pass without fabricated dates.

Until then, Task 1 remains **PARTIALLY COMPLETE**. The current work is strong evidence for adopting Model B, but it is not yet permission to begin ontology or MCQ-generation experiments.

### Model B Implementation Update — 2026-09-16

The first Model B enforcement slice is implemented and tested:

- `kg_builder.py` now supports an explicit `allow_static_source_evidence` mode.
- An unversioned fact is accepted only when it has all of:
	- `temporal_class = static`
	- `temporal_evidence_status = verified_pre_cutoff_source`
	- `temporal_evidence_date <= 2023-04-19`
	- a verified evidence URL and source snapshot hash
- Dynamic, unclassified, legacy, and post-cutoff facts remain blocked.
- `mcq_generator.py` and `main-pipeline.py` use the same opt-in policy.
- `resourcing_proposals.json` has not been merged automatically.

Focused validation passed with `python test_cutoff.py`, including static-source acceptance and dynamic/unverified rejection. Python compilation also passed.

**RA next action:** review and accept eligible proposals, then populate the explicit Model B evidence fields. Until that happens, the current corpus remains blocked and Task 1 remains **PARTIALLY COMPLETE**.

### Model B Dynamic-Fact Update — 2026-09-16

The dynamic-fact slice is now implemented without relaxing the temporal rules:

- Dynamic validity extraction now checks relation-specific structured source fields such as `term_start`, `date_effective`, and `reign_start`.
- A bare year in `fact_text` is still never accepted as `valid_from` for a dynamic fact.
- The acceptance pass found 8 dynamic proposals with cached structured validity evidence; 2 were excluded by the holdout-eligibility gate.
- Derived Model B corpus: 69 accepted facts total, consisting of 63 static and 6 dynamic facts.
- 547 facts remain excluded because they are unclassified, unsupported, leakage-prone, or lack acceptable temporal evidence.

Validation passed:

- `python test_cutoff.py`
- Python compilation for the resourcing, acceptance, KG, generator, and pipeline modules
- Combined-corpus audit: no invalid accepted records; every accepted dynamic fact has `valid_from` at or before `2023-04-19`.

Task 1 remains **PARTIALLY COMPLETE** for the full corpus. The derived 69-fact Model B corpus is the current experiment-ready candidate; the remaining facts require additional source verification or explicit exclusion decisions.

### Model B Freeze and Reconciliation — 2026-09-16

The 69-fact seed release is now frozen and reconciled:

- Raw corpus: 616 facts
- Accepted Model B seed: 69 facts, including 63 static and 6 dynamic
- Permanently holdout-excluded: 20 facts
- Recoverable resourcing backlog: 527 facts
- Reconciliation assertion: `616 = 69 + 20 + 527`
- Accepted seed SHA-256: `474aae6a69837b1c01f1d7027c727f4de364498be4a8d775da2a1b8924012eff`

The release manifest is `model_b_release_manifest.json`, generated by `freeze_model_b.py`. The original corpus remains unchanged. The next milestone is the 128-fact unclassified triage and evidence-ledger construction; no downstream benchmark expansion should bypass this frozen release boundary.

### Milestone 2 — Triage + Evidence Ledger Foundation — 2026-09-16

The append-only workflow foundation is now generated from the frozen release:

- `model_b_workflow/recoverable_backlog.jsonl`: 527 recoverable fact work items
- `model_b_workflow/permanently_excluded.jsonl`: 20 permanent holdout exclusions
- `model_b_workflow/triage_decisions.jsonl`: 136 clarification records requiring reviewer decisions
- `model_b_workflow/evidence_ledger.jsonl`: versioned empty evidence ledger, ready for source records
- `model_b_workflow/fact_evidence_links.jsonl`: versioned empty fact-to-evidence link ledger
- `model_b_workflow/workflow_schemas.json`: controlled vocabularies and source allowlist
- `model_b_workflow/pilot_batch.jsonl`: 50 deterministic pilot records
- `model_b_workflow/milestone2_summary.json`: counts and reconciliation
- `test_dataset_accounting.py`: partition and overlap regression test

The machine-checked reconciliation is:

`616 = 69 accepted + 20 permanently excluded + 527 recoverable`

All partition-overlap checks pass, the 50-fact pilot has no category shortfall, and no new evidence was merged into the frozen seed. The current raw-derived clarification queue contains **136**, not 128, facts; the larger number is retained because it is what the current recoverable partition actually produces.

**Status: FOUNDATION COMPLETE; REVIEW MILESTONE INCOMPLETE.** The next work is reviewer-driven classification of all 136 clarification records. Automated suggestions remain suggestions and do not count as final triage decisions.

### Milestone 2B — Triage Assistant Outputs — 2026-09-16

The reviewer-assistance layer is now implemented:

- `triage_assistant.py` generates deterministic reviewer suggestions only.
- `model_b_workflow/triage_reviewer_queue.jsonl`: 136 reviewer records
- `model_b_workflow/triage_reviewer_queue.csv`: reviewer-friendly export
- `model_b_workflow/duplicate_candidates.jsonl`: 5 normalized duplicate groups
- `model_b_workflow/triage_metrics.json`: queue metrics and vocabulary checks
- Leakage screening flagged 1 record for reviewer attention.
- All 136 records remain `unreviewed`; final decisions made: 0.

The assistant does not assign final `static`/`dynamic`, semantic, temporal, leakage, or Model B acceptance verdicts. It only suggests claim domains, source routes, priorities, risk flags, and search templates. Inputs remain unchanged.

Validation passed:

- `python triage_assistant.py --check`
- `python triage_assistant.py`
- `python test_dataset_accounting.py`
- Python compilation for the workflow tools

**Next RA action:** review the CSV queue and populate only the reviewer columns: `reviewer_final_class`, `reviewer_status`, `reviewer_id`, `reviewed_at`, and `reviewer_notes`. Do not merge any triage suggestion into the frozen seed automatically.

### RA_1 Triage Review — 2026-09-16

The 136 clarification records were reviewed as routing decisions by `RA_1`. This review classifies claims for source collection; it does **not** verify evidence and does not accept facts into Model B.

Outputs:

- `model_b_workflow/triage_decisions_ra_1.jsonl`
- `model_b_workflow/triage_reviewer_queue_ra_1.csv`
- `model_b_workflow/triage_metrics_ra_1.json`

Results:

- Reviewed: 136/136
- Approved for a sourcing route: 111
- Needs second review: 19 duplicate candidates
- Rejected/blocked: 6, including post-cutoff or malformed claims
- Static routing: 81
- Dynamic routing: 30
- Blocked: 6
- Needs clarification: 19

Final routing classes:

- `static_historical`: 60
- `static_geographic`: 20
- `static_institutional`: 1
- `dynamic_officeholder`: 3
- `dynamic_policy_legal`: 13
- `dynamic_statistical`: 9
- `dynamic_award_or_membership`: 5
- `duplicate_candidate`: 19
- `holdout_contaminated`: 5
- `malformed`: 1

`evidence_verified` remains false and `model_b_merge_performed` remains false. The frozen 69-fact seed and raw corpus were not modified. The next milestone is evidence collection for the approved routing queue, beginning with the 50-fact pilot after duplicate and second-review decisions are resolved.

### Milestone 3A — Duplicate Adjudication and Pilot Lock — 2026-09-16

Duplicate adjudication and pilot selection are now complete as a review-only milestone.

Outputs:

- `lock_milestone3a.py`
- `model_b_workflow/duplicate_adjudications_ra_1.jsonl`
- `model_b_workflow/pilot_batch_final.jsonl`
- `model_b_workflow/milestone3a_pilot_manifest.json`

Current artifact correction: the five duplicate groups contain **21 duplicate-flagged records**, not 19. Their adjudication is:

- 19 records: `not_duplicate`, source search may proceed
- 2 records: `merge_candidate`, kept pending canonical consolidation review
- 5 duplicate groups total

The final 50-fact pilot is locked with this allocation:

- Static historical: 24
- Static geographic: 10
- Static institutional: 1
- Dynamic office-holder: 3
- Dynamic policy/legal: 7
- Dynamic statistical: 3
- Dynamic award/membership: 2

Pilot SHA-256: `64f083978bf0c2d0f7ae3cc5e6a9407dc79c5d90a165c16f2af2e3c078819c65`

`evidence_verified: false`, `model_b_merge_performed: false`, and `frozen_seed_modified: false`. The next milestone is **3B: Pilot Evidence Collection and Dual Verification**. No source evidence has been admitted yet.

### Milestone 3B — Pilot Evidence Intake Foundation — 2026-09-16

The evidence-intake layer for the locked 50-fact pilot is now ready:

- `pilot_evidence_intake.py`: reviewer intake and validation CLI; it does not browse, verify, accept, or merge facts.
- `model_b_workflow/pilot_evidence_work_queue.csv`: 50 pilot work records with routing and required fields.
- `model_b_workflow/pilot_evidence_review_queue.csv`: reviewer-facing evidence checklist queue.
- `model_b_workflow/pilot_evidence_intake_metrics.json`: intake state and merge boundary.
- `test_pilot_evidence_intake.py`: focused boundary tests.

The intake gate requires source URL, source type, evidence date, retrieval time, content hash, supporting excerpt, subject/predicate/object matches, and leakage status. Static evidence must be pre-cutoff; dynamic evidence must additionally provide a cutoff-covering validity interval. All accepted intake records remain `pending_reviewer`; the tool rejects attempts to write `verified_supported` or `accepted_for_build`.

Validation passed:

- `python pilot_evidence_intake.py --check`
- `python pilot_evidence_intake.py --build-queue`
- `python -m unittest -v test_pilot_evidence_intake.py` — 4 tests passed
- Python compilation passed

Current status: **50 evidence records pending collection; 0 evidence records verified; 0 new facts merged.** The next action is manual/source-assisted evidence collection in batches of five, followed by RA_1 and RA_2 review.

### Milestone 3B — Static Historical Batch 01 — 2026-09-16

The first five static-historical pilot facts were processed as an evidence-collection dry run:

- `BCSGK-0065`: no admissible direct excerpt for the exact claim; deferred.
- `BCSGK-0074`: pre-cutoff revision candidate found, but no direct claim-supporting excerpt extracted; deferred.
- `BCSGK-0078`: one pre-cutoff MediaWiki candidate intaked as `pending_reviewer`.
- `BCSGK-0079`: no admissible source candidate found in the current pass; deferred.
- `BCSGK-0081`: source revision found, but the exact highest-wicket-taker/700+ claim was not directly supported by the extracted span; deferred.

Batch work log: `model_b_workflow/pilot_static_historical_batch_01.jsonl`.

Current ledger state:

- Candidate evidence rows: 1
- Fact-evidence link rows: 1
- Evidence verified: 0
- Pending reviewer: 1
- Model B merge: 0

The `BCSGK-0078` candidate uses a 2022-10-08 MediaWiki revision and includes a supporting excerpt, source hash, and match fields. It remains pending semantic and temporal review; it has not been accepted. The first batch demonstrates that the intake and failure logging workflow works, but it does not yet justify corpus expansion.

### Milestone 3B — Dual Review Result for Batch 01 — 2026-09-16

RA_1 and RA_2 independently reviewed the one intaked candidate (`EV_PILOT_000001`, fact `BCSGK-0078`).

Outputs:

- `review_pilot_evidence.py`
- `model_b_workflow/pilot_evidence_review_decisions.jsonl`
- `model_b_workflow/pilot_evidence_review_summary.json`

Both reviewers agreed:

- Semantic verdict: `not_enough_evidence`
- Temporal verdict: `admissible_static`
- Leakage verdict: `pass`
- Final verification status: `not_enough_admissible_evidence`
- Review status: `deferred`
- Model B merge: `false`

Reason: the pre-cutoff SEA-ME-WE 4 revision supports Bangladesh as a connected country and the cable's telecommunications function, but the supplied excerpt does not explicitly establish the claim's international-bandwidth wording or its `since 2006` qualifier.

This is a successful dual-review outcome: the workflow detected that admissible source timing alone is not enough for semantic claim support. The next action is to find a stronger excerpt/independent source or retain `BCSGK-0078` as deferred.

### RA Evidence Retry and Batch 02 Preparation — 2026-09-16

As RA, I checked the stronger BSCCL source lead for `BCSGK-0078`. The Directors' Report (2022-2023), Annual Report 2023 contains a direct statement that Bangladesh started submarine cable operation in 2006 with 7.5 Gbps initial bandwidth as a member of SEA-ME-WE 4. However, the report is a 2022-2023/2023 document and includes June 2023 information, so its admissibility at the `2023-04-19` cutoff is not established.

The source was therefore recorded as discovery-only in `model_b_workflow/evidence_discovery_BCSGK-0078.jsonl`. It was not added to the evidence ledger and cannot support Model B until a pre-cutoff document or archive is found.

Batch 02 is prepared with five static-geographic facts:

- `BCSGK-0083` — Kabaddi as national sport
- `BCSGK-0090` — Karnaphuli Tunnel
- `BCSGK-0109` — Bangladesh Military Academy location
- `BCSGK-0120` — Padma Bridge
- `BCSGK-0333` — Bathan locations

Outputs:

- `model_b_workflow/pilot_static_geographic_batch_02.jsonl`
- `model_b_workflow/pilot_static_geographic_batch_02_summary.json`

Batch 02 status: ready for source collection; candidates intaked: 0; RA_1 supported: 0; RA_2 supported: 0; verified supported: 0; Model B merge: 0. The existing Batch 01 review and frozen seed remain unchanged.

### Batch 02 Routing Correction — 2026-09-16

Before evidence collection, RA_1 reclassified the five original Batch 02 claims:

- `BCSGK-0083`: rerouted from geographic to `static_cultural_or_national_symbol`.
- `BCSGK-0090`: removed from the static pilot; tunnel completion/opening is dynamic and the stated 2023 status is post-cutoff for this benchmark.
- `BCSGK-0109`: retained as `static_institutional_geographic`.
- `BCSGK-0120`: held for claim-scope review because longest-bridge ranking and measurement may require date/scope qualification.
- `BCSGK-0333`: held for term disambiguation because the meaning and geographic scope of “bathan” are not sufficiently clear.

To keep a five-record clean sourcing batch, the following replacements were added:

- `BCSGK-0359`: static geographic
- `BCSGK-0315`: static geographic
- `BCSGK-0336`: static institutional-geographic

Outputs:

- `model_b_workflow/batch02_reclassification_ra1.jsonl`
- `model_b_workflow/pilot_static_batch_02_revised.jsonl`

The revised batch contains five records: `BCSGK-0083`, `BCSGK-0109`, `BCSGK-0359`, `BCSGK-0315`, and `BCSGK-0336`. No source evidence has been collected for them, and no fact has been accepted or merged.

Validation passed:

- `python pilot_evidence_intake.py --check`
- `python -m unittest -v test_pilot_evidence_intake.py` — 4 tests passed
- Python compilation passed

This correction prevents a post-cutoff infrastructure claim from being treated as timeless geography and adds the missing cultural route to the workflow schema.

### Batch 02 Evidence Intake and Dual Review — 2026-09-16

RA_1 and RA_2 independently reviewed the first two corrected Batch 02 candidates:

- `BCSGK-0083` Kabaddi: BSS report dated 2023-03-13, with the direct quote “Kabaddi is our national sport”.
- `BCSGK-0109` Bangladesh Military Academy: MediaWiki revision dated 2023-04-16, with a direct Bhatiary/Chittagong location statement.

Evidence candidates were intaked as:

- `EV_PILOT_000002` / `LINK_PILOT_000002`
- `EV_PILOT_000003` / `LINK_PILOT_000003`

Both reviewers agreed for both facts:

- Semantic verdict: `supported`
- Temporal verdict: `admissible_static`
- Leakage verdict: `pass`
- Verification status: `verified_supported`
- Review status: `approved_for_build_candidate`

Review output: `model_b_workflow/pilot_evidence_review_decisions_batch02.jsonl` and `model_b_workflow/pilot_evidence_review_summary_batch02.json`.

This is not yet a Model B merge. The review summary explicitly records `model_b_merge_performed: false` and `frozen_seed_modified: false`. The two facts now require the PI-controlled acceptance audit and release rebuild before entering any derived corpus.