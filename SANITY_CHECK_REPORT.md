# BCS Temporal MCQ -- Sanity Check & Verification Audit

**Repository:** d:\BCS_final
**Branch:** exp/2026-09-17-task2b-quality-gate
**Date:** 2026-09-21
**Scope:** 44 modules, 15 test suites, 5 narrative docs, 10 data artifacts

## Bottom line

**The manuscripts two headline claims are not currently defensible from the artifacts on disk.** The reported human-evaluation agreement (Krippendorffs alpha = 0.9784) comes from an RNG, not raters, and the reported baseline-comparison table that anchors the papers hypothesis test is the ablation studys numbers relabeled as three independent systems. Separately, the code-level distractor-validity screen that the papers quality claims lean on has been silently disabled since it was written, due to an unhandled NameError.

None of this appears to be found-and-hidden -- the manuscript itself discloses the human-eval simulation in a limitations note. But two other summary documents present it as a completed expert study, and the baseline-table error appears nowhere as a disclosed limitation. See "Cross-cutting synthesis" below for how the findings connect.

---

## Phase 1 -- Codebase Mapping & Architecture

**Status: structurally clean.** 44 root modules, 15 test_*.py suites, and six subdirectories (experiments/, baselines/, BCS-MindGraph/, Corpus-Sadia/, model_b_workflow/, paper_artifacts/) were mapped and every file was run through py_compile.

### [HIGH] scipy is imported but undeclared -- requirements.txt
experiments/hypothesis_testing.py imports scipy.stats to run the papers significance tests, but requirements.txt lists only python-dotenv, networkx, requests, beautifulsoup4, huggingface_hub. A clean checkout cannot reproduce the hypothesis-testing results without guessing the dependency.

### [MEDIUM] No documented environment-variable contract -- .env
Eight-plus modules (hf_client.py, main-pipeline.py, the four baselines/ scripts, and others) read from .env, but no .env.example exists to say what keys a fresh contributor needs to supply.

**Holds up well:** no broken local imports, no orphan modules, no syntax errors across all 44 root files plus experiments/ and baselines/. The module dependency graph (hf_client -> mcq_generator/mcq_quality; rejection_taxonomy -> fact_quality/mcq_generator/mcq_quality) is coherent and matches what each file actually does.

---

## Phase 2 -- Code Quality & Methodology Review

**Status: 3 critical, 2 high, 4 medium, 2 low.**

### [CRITICAL] Distractor-validity screen is silently disabled in every real run -- mcq_quality.py:373, mcq_generator.py:1295
Found via live test execution. check_distractor_plausibility() defines `def _norm(s: Any) -> str:` but Any is never imported (only Dict, List, Optional, Tuple) -- every call raises NameError. The real caller in mcq_generator.pys _screen_candidate_distractors() wraps this in `except Exception as exc: log.debug(...); return []`, so the crash is swallowed and the screener always reports no blockers. This is the exact check meant to catch a distractor that is simultaneously true at the cutoff date -- the papers core contrastive distractor validity guarantee. It has never actually run outside of a test that calls the function directly.

### [CRITICAL] Human evaluation responses are RNG output, not ratings -- experiments/human_eval_sample_generator.py:78-104
generate_human_eval_artifacts() hardcodes per-system Likert base scores (proposed system 4.6-5.0, generic-LLM 1.8-3.2) and draws Rater_1/2/3 responses from a seeded random.seed(42). The output file is titled BCSBatighor GK Human Evaluation Expert Responses. Every downstream statistic -- Krippendorffs alpha, system means -- measures this generator, not real annotators. See Phase 3 for how this is represented across documents.

### [CRITICAL] Baseline-comparison metrics are hardcoded constants, not computed values -- bcs_metrics.py:1026-1055
In evaluate_benchmark_comparison_file(), temporal_correctness, distractor_quality, and rejection_rate are literal constants keyed on sys_id (e.g. 1.0000 for the proposed system, 0.8333 / 0.6944 / 0.6111 for the others) -- never derived from the actual mcqs read from the input file. Only factual_validity and exam_relevance are genuinely computed. This is the root cause behind the mislabeled table in Phase 3, finding 2.

---

### [HIGH] Krippendorffs alpha is computed with the wrong normalization for D_o -- experiments/human_eval_krippendorff.py:56-58,76
D_o is a flat mean over all within-unit rating pairs instead of Krippendorffs (m_u - 1)-weighted per-unit average divided by total value count n. With a constant 3 raters per item (as used here), this makes the computed D_o exactly double the true value -- provable algebraically. D_e is computed correctly. Net effect: alpha_reported is approximately 2*alpha_true - 1, which systematically deflates the statistic. This is independent of, and compounds on top of, the fact the underlying ratings are synthetic.

### [HIGH] RejectionTally over-counts and can report a rate above 100% -- rejection_taxonomy.py:272-284
report() sums per-code occurrences, not distinct rejected items -- an MCQ that fails on two canonical codes (e.g. E-DIST + E-AMB) is counted twice, so rejection_rate can exceed 1.0 and does not equal the fraction of items rejected the guideline defines. test_rejection_taxonomy_suite.py does not catch this because no test exercises a multi-code item -- one existing test actually bakes in the same wrong expectation.

### [MEDIUM] Operator-precedence bug in as_of() point-in-time queries -- kg_builder.py:549
```
if vf_key <= target_key < vt_key or vf is None:
```
The trailing `or vf is None` short-circuits the whole containment check, so any fact with an unknown valid_from is treated as valid for any query date, even one whose valid_to has already passed. get_graph_snapshot and get_facts_by_topic_as_of use the correct form and do not share this bug.

### [MEDIUM] classify_duplicate_option() is defined but never called -- rejection_taxonomy.py:187-194
Meant to route a duplicate-equals-correct-answer defect to E-MULTI; instead FAILURE_CODE_MAP statically sends every DUPLICATE_OPTIONS to E_DIST. Genuine multiple-correct-answer defects caught this way are mis-tallied in the papers taxonomy breakdown (Phase 3, finding 3, where E-MULTI reports as 0).

### [MEDIUM] Semantic confusers are not checked against the specific fact being asked -- mcq_generator.py:1693-1879
generate_semantic_confusers() selects by KG-neighborhood/subtype/taxonomy overlap and a synonym blacklist only -- unlike generate_temporal_distractors(), it never checks whether a candidate confuser is itself a true answer for the fact/relation in question. Correctness depends entirely on the downstream screener catching it -- which, per the finding above, has been silently off.

### [MEDIUM] remove_fact() leaves a dangling version-index entry -- kg_builder.py:1174-1184
The removed fact_id is never purged from self._version_index. A later as_of() or get_fact_history() call indexes self.graph.nodes[fid] on a now-deleted id and raises KeyError.

### [LOW] Unrestricted pickle.load() in load_snapshot() -- kg_builder.py:1210-1231
Fine while snapshots are self-produced; there is no guard if this is ever pointed at a .gpickle from outside the pipeline -- arbitrary code execution on load.

### [LOW] Two minor robustness gaps -- kg_builder.py, hf_client.py
_generate_id() uses an 8-hex-char UUID slice with no collision check, and graph.add_node silently merges attributes on a collision rather than raising -- at corpus scale this could silently fuse two unrelated facts. Separately, hf_client.call_llm passes no explicit timeout to chat_completion, so a stalled connection hangs instead of being caught by the retry loop.

**Holds up well:** hf_client.pys rate-limit retry/backoff correctly identifies and escalates without swallowing failures. JudgeAgent._verify_and_correct independently recomputes overall_score/passed rather than trusting the LLMs self-report. safe_parse_json has a sensible multi-stage repair path and returns None on total failure rather than silently corrupting data.

---

## Phase 3 -- Research Artifact & Data Consistency

**Status: 4 mismatches.** Cross-examined PRD.md, COMPLIANCE_AUDIT_REPORT.md, Task2_Final_Execution_Summary.md, FINAL_MANUSCRIPT_DRAFT.md, and SUPERVISOR_FINAL_EXECUTIVE_SUMMARY.md against the JSON artifacts they cite.

### Finding 1 -- MISMATCH (undisclosed in 2 of 5 documents)
Claim: 3 BCS-cadre expert raters, blinded evaluation of 100 MCQs, Krippendorffs alpha = 0.9784, Likert 4.81/5.0 -- presented as a completed study in COMPLIANCE_AUDIT_REPORT.md section 14 and SUPERVISOR_FINAL_EXECUTIVE_SUMMARY.md section 14/3.3.
Actual: No real raters exist -- see Phase 2, finding 2. The manuscripts own section 9.4 discloses the simulation; the other two documents do not.

### Finding 2 -- MISMATCH (hypothesis test built on relabeled data)
Claim: Manuscript section 5.1 and Supervisor Summary section 1.3 report Factual Validity 61.11% / 80.56% / 86.11% / 97.22% across Generic-LLM / Static-RAG / Web-RAG / Proposed, used to support H1 (p = 4.97e-4).
Actual: experiments/baseline_metrics_report.json -- the real 4-system run -- shows factual_validity: 1.0 for all four systems. The reported numbers are instead experiments/ablation_study_report.jsons per-variant scores for the proposed system alone, relabeled as three independent baselines.

### Finding 3 -- MISMATCH (cited codes do not exist in the data)
Claim: Manuscript section 8 gives the canonical taxonomy distribution as E-TEMP 44%, E-MULTI 24%, E-STYLE 18%, E-FACT 14%.
Actual: rejection_taxonomy_report.json -- codes E-TEMP and E-FACT do not appear anywhere in the taxonomy. Real distribution (106 total): E-LEAK 71%, E-TIME 19%, E-DIST 9%, E-DUP 1%; E-MULTI and E-STYLE are both 0.

### Finding 4 -- MISMATCH (two documents carry untraceable figures)
Claim: Exam Relevance / Distractor Quality / Rejection Rate for the proposed system, cited identically in three documents.
Actual: Manuscript: 100% / 94.44% / 5.56% -- matches baseline_metrics_report.json. Compliance Audit section 12 and Supervisor Summary section 12: 94.44% / 91.67% / 2.78% -- trace to no artifact found.

### What checks out
Dataset sizes reconcile exactly: 616 facts to a 69-fact Model B seed (63 static + 6 dynamic) to 494 questions with a 427/47/20 temporal-class split matching manuscript section 4.1. Cohens kappa (0.8906 / 0.6238 / 0.1575) matches corpus_annotation_agreement.json exactly. Ablation-study deltas match manuscript section 6.

### Unverifiable / stale
mcq_quality_log.json references an orphaned dataset (524 facts, 20 MCQs) matching none of the current figures -- likely a leftover from an earlier pipeline stage. corpus_annotation_agreement.jsons dual-annotator pair is one SME versus an automated rule classifier, not two humans; the manuscript discloses this correctly, but PRD and Compliance-Audit phrasing (dual-annotator kappa) implies a stronger human agreement study than exists.

---

## Phase 4 -- Test Suite Execution

The project venv shipped without pip and without pytest or scipy installed -- both were bootstrapped for this run. Three of the six requested files are standalone scripts with a module-level sys.exit(), not real pytest modules; batching them with genuine unittest.TestCase suites crashes pytests collector (INTERNALERROR, "no tests ran"). Each was run in the mode it is actually written for.

| Suite | Run as | Checks | Result |
|---|---|---|---|
| test_rejection_taxonomy_suite.py | pytest | 25 / 25 | PASS |
| test_kg_bitemporal.py | pytest | 7 / 7 | PASS |
| test_contrastive_distractors.py | pytest | 9 / 12 | 3 FAILED |
| test_task2_pipeline.py | standalone script | 21 / 21 | PASS |
| test_triage_rules.py | standalone script | 29 / 29 | PASS |
| test_task2b_quality_gate.py | standalone script | 36 / 36 | PASS |

### [HIGH] Half the suite is not pytest-compatible -- test infrastructure
test_task2_pipeline.py, test_triage_rules.py, and test_task2b_quality_gate.py run their checks at import time and call sys.exit() at module scope. Running a plain pytest invocation over all six files does not produce the 6-suite result one would expect -- it crashes after the second script-style file is collected, silently discarding results for everything collected after it in that session.

The 3 failures in test_contrastive_distractors.py are the direct, reproducible consequence of the Phase 2 NameError: test_distractor_plausibility_allows_historical_past_state and test_distractor_plausibility_rejects_simultaneous_valid_at_cutoff crash with `NameError: name 'Any' is not defined`; test_mcq_generator_integrated_screener_filters_invalid_candidates fails because the expected code DISTRACTOR_VALID_AT_CUTOFF never appears -- the screener returns [] instead, exactly matching what the swallowed exception in mcq_generator.py:1295 predicts.

---

## Cross-cutting synthesis

The papers central claim is that a temporal-KG-grounded pipeline produces measurably better, more valid MCQs than three baselines, confirmed by both automated metrics and human judgment. All three legs of that argument have an independent, traceable defect: the automated baseline comparison is ablation data relabeled as baseline data (Phase 2 finding 3, Phase 3 finding 2); the human judgment is RNG output presented as expert ratings in two of five documents (Phase 2 finding 2, Phase 3 finding 1); and the code-level guarantee that distractors are not secretly also-correct -- the mechanism the contrastive claim rests on -- has been silently disabled by an unhandled NameError since it was written (Phase 2 finding 1, Phase 4). None of these were caught by the existing test suite because the tests that would catch them either do not exercise the failure path (Krippendorff, rejection-rate) or were never run in a mode where pytest could report their result (the three script-style suites).

## Suggested remediation order

1. Decide how the manuscript should represent the human-eval and baseline-comparison numbers -- a judgment call for the author and supervisor, not a code fix. (FINAL_MANUSCRIPT_DRAFT.md section 9.4)
2. Import Any in mcq_quality.py and re-run the distractor screen against existing generated MCQs to find out how many already-accepted items it would actually have rejected. (mcq_quality.py:32)
3. Correct D_os normalization in the Krippendorff implementation before it is used on any real rating data. (experiments/human_eval_krippendorff.py:56)
4. Make RejectionTally.report() count distinct rejected items, not per-code occurrences, and add a test with a multi-code item. (rejection_taxonomy.py:272)
5. Fix the as_of() operator precedence so an unknown valid_from does not universally satisfy point-in-time queries. (kg_builder.py:549)
6. Add scipy to requirements.txt and write a .env.example documenting required keys.
7. Convert or clearly mark the three script-style test files so a plain pytest invocation over the suite does not silently drop results.

---
*Audit executed via live test runs (pytest 9.1.1 / Python 3.12.10, project venv), full-file code review of the components listed above, and direct JSON/artifact inspection -- not a static lint pass.*

---

## Addendum -- Remediation pass (2026-09-21, same day)

### Correction to this audit
Before fixing anything, the Krippendorff D_o finding above was independently re-derived and checked numerically against the general per-unit (m_u - 1)-weighted formula. For a constant number of raters per item (3, as used in this codebase), the code formula and the general formula are mathematically identical -- confirmed both algebraically and with a concrete numeric example. **The Krippendorff implementation is correct as written and was left unchanged.** The original "factor of 2" finding above is retracted.

### Fixed and verified (tests green after each change)
1. **mcq_quality.py:32** -- added the missing `Any` import. The distractor-validity screener no longer crashes; all 3 previously-failing tests in test_contrastive_distractors.py now pass (12/12).
2. **rejection_taxonomy.py** -- RejectionTally now tracks `_items_rejected` separately from `_total_rejections` (code occurrences), so `_rejection_rate` can no longer exceed 1.0. Added a regression test (`test_rejection_rate_counts_items_not_codes`). 26/26 pass.
3. **kg_builder.py:549** -- removed the `or vf is None` clause from `as_of()` that let an unknown valid_from bypass the valid_to upper bound. Added a white-box regression test since `as_of()` had zero prior coverage. 9/9 pass.
4. **requirements.txt** -- added `scipy`. **.env.example** -- created, documenting every env var actually read across the codebase (HF_* and GOOGLE_*).
5. **Three standalone-script test files** (test_task2_pipeline.py, test_triage_rules.py, test_task2b_quality_gate.py) -- guarded their module-level `sys.exit()` behind `if __name__ == "__main__":` so pytest can import them without crashing the collector, while standalone `python test_x.py` behavior is unchanged (verified: still exit 0 with the same pass counts). The exact originally-requested single pytest command now runs cleanly end to end (46 collected, 46 passed).
6. **rejection_taxonomy.py / mcq_quality.py** -- wired `classify_duplicate_option()` in: a duplicate option that specifically repeats the *correct* answer's text now also gets `DUPLICATE_MATCHES_CORRECT_ANSWER`, mapped to E-MULTI, distinct from a duplicate among two wrong distractors (still just E-DIST). Added 4 new checks to test_task2b_quality_gate.py (40/40 pass).
7. **kg_builder.py** -- `remove_fact()` now rebuilds the version index after removing a versioned fact, so `as_of()`/`get_fact_history()` can no longer hit a dangling id. Added a regression test. 9/9 pass.
8. **hf_client.py** -- `_build_client()` now passes a configurable request timeout (`HF_REQUEST_TIMEOUT_SECONDS`, default 60s) to `InferenceClient`, so a stalled connection fails and retries instead of hanging. Smoke-tested against the installed huggingface_hub version.
9. **kg_builder.py** -- `_generate_id()` now checks for a collision against existing graph nodes and regenerates rather than silently letting `graph.add_node` merge two unrelated facts/entities together.

### Deliberately not touched
- **The fabricated human-eval data and the mislabeled baseline-comparison table** (Phase 2/3 findings) are manuscript/data-representation decisions, not code bugs -- left for the author and supervisor to decide, per explicit instruction.
- **`generate_semantic_confusers()` not checking against the specific fact being asked** -- left as-is. It is a generation-time heuristic, not a bug with a single clear fix, and its safety net (the distractor-plausibility screener) is now actually running again after fix #1 above.
- **Unrestricted `pickle.load()` in `load_snapshot()`** -- left as-is. A restricted unpickler for a networkx-graph-containing snapshot is nontrivial to get right without breaking legitimate loads, and every current caller only loads snapshots this same codebase produced. Worth a real look if snapshots are ever sourced from outside the pipeline.

### Full regression status after this pass
test_rejection_taxonomy_suite.py 26/26, test_kg_bitemporal.py 9/9, test_contrastive_distractors.py 12/12, test_task2_pipeline.py 21/21, test_triage_rules.py 29/29, test_task2b_quality_gate.py 40/40. All green; `py_compile` clean on every touched file.
