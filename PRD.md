# Product Requirements Document (PRD) — Experimental Execution Plan (Tasks 3–10)

**Project:** Time-Conditioned Agentic MCQ Generation & Bitemporal Knowledge Graph Evaluation  
**Target Examination:** 45th BCS Examination (`t* = 2023-04-19`)  
**Benchmark Release:** `model-b-benchmark-v1.0` (69 seed facts, 36 benchmark MCQs)  
**Execution Loop:** Ralph Loop Protocol  

---

## 📌 Executive Status

- **Task 1 — Temporal Corpus & Model B Freeze**: ✅ **COMPLETED** (69 verified facts, 0 cutoff violations)
- **Task 2 — MCQ Engine, Quality Screener & Pilot Release**: ✅ **COMPLETED** (36/36 Screener PASS, 97.3% Acceptance)
- **Current Active Target**: **Task 3 — Dynamic Bitemporal Knowledge Graph Engine**

---

## 🎯 Task Breakdown (Tasks 3 to 10)

---

### Task 3: Dynamic Bitemporal Knowledge Graph Engine

#### 3.1 Bitemporal Schema & Graph Representation
- **Objective**: Extend `kg_builder.py` to support full bitemporal tuples `(fact_id, subject, relation, object, valid_from, valid_to, observed_at, source_published_at, source_tier)`.
- **Target File**: [`kg_builder.py`](file:///d:/BCS_final/kg_builder.py)
- **Acceptance Criteria**:
  - Support for `valid_from` and `valid_to` interval checking.
  - Inclusion of `source_tier` (1–4) and `observed_at` metadata.
- **Verification Command**: `python -m unittest test_kg_bitemporal.py`

#### 3.2 Time-Slice Snapshot Query Engine
- **Objective**: Implement `get_graph_snapshot(t_cutoff="2023-04-19")` in `kg_builder.py` that strictly filters out any node, relation, or attribute valid after `t_cutoff`.
- **Target File**: [`kg_builder.py`](file:///d:/BCS_final/kg_builder.py)
- **Acceptance Criteria**:
  - Zero leakage of post-cutoff facts or updated entity states.
  - Return exact temporal state as of `2023-04-19`.
- **Verification Command**: `python test_cutoff.py`

#### 3.3 Bitemporal Unit & Integration Test Suite
- **Objective**: Write comprehensive tests in `test_kg_bitemporal.py` validating snapshot isolation and interval logic.
- **Target File**: `test_kg_bitemporal.py`
- **Acceptance Criteria**:
  - 100% test pass on static facts, dynamic validity intervals, and post-cutoff rejection.
- **Verification Command**: `python test_kg_bitemporal.py`

---

### Task 4: Episodic Web Knowledge Acquisition Engine

#### 4.1 Provenance-Tracked Episodic Web Retrieval
- **Objective**: Integrate `episodic_store.py` and `web_scraper.py` with strict Wayback Machine timestamp matching at or before `2023-04-19`.
- **Target Files**: [`episodic_store.py`](file:///d:/BCS_final/episodic_store.py), [`web_scraper.py`](file:///d:/BCS_final/web_scraper.py)
- **Acceptance Criteria**:
  - Web retrieval returns source HTML digest, archive permalink, and snapshot date.
  - Block non-archived or post-cutoff live web pages.
- **Verification Command**: `python -m unittest test_episodic_provenance.py`

#### 4.2 Source-Tier & Evidence Weighting Module
- **Objective**: Implement automated source-tier weighting (Tier 1: Official Gazette/BPSC to Tier 4: Unverified news) in `episodic_store.py`.
- **Target File**: [`episodic_store.py`](file:///d:/BCS_final/episodic_store.py)
- **Acceptance Criteria**:
  - Assign explicit tier weight to retrieved evidence facts.
  - Reject facts lacking admissible source tier or pre-cutoff snapshot.
- **Verification Command**: `python test_episodic_provenance.py`

---

### Task 5: Multi-Baseline Generation Pipeline Suite

#### 5.1 Generic LLM Generation Baseline
- **Objective**: Implement `baselines/generic_llm_baseline.py` using direct LLM prompting (no RAG, no KG, no Screener).
- **Target File**: `baselines/generic_llm_baseline.py`
- **Acceptance Criteria**:
  - Generate 36 MCQs corresponding to the benchmark topic demand without external context.
- **Verification Command**: `python baselines/generic_llm_baseline.py --check`

#### 5.2 Static RAG Baseline
- **Objective**: Implement `baselines/static_rag_baseline.py` using standard dense retrieval over untemporalized corpus.
- **Target File**: `baselines/static_rag_baseline.py`
- **Acceptance Criteria**:
  - Retrieve top-k context from unversioned static corpus and generate 36 MCQs.
- **Verification Command**: `python baselines/static_rag_baseline.py --check`

#### 5.3 Web-RAG Baseline
- **Objective**: Implement `baselines/web_rag_baseline.py` using live web retrieval without temporal cutoff guards.
- **Target File**: `baselines/web_rag_baseline.py`
- **Acceptance Criteria**:
  - Perform live web search RAG and generate 36 MCQs (simulating standard non-time-aware web-RAG).
- **Verification Command**: `python baselines/web_rag_baseline.py --check`

#### 5.4 Proposed System Interface Wrapper
- **Objective**: Wrap proposed Temporal KG + Agentic Generator (`mcq_generator.py` + `mcq_quality.py`) in `baselines/proposed_temporal_kg.py`.
- **Target File**: `baselines/proposed_temporal_kg.py`
- **Acceptance Criteria**:
  - Execute full proposed pipeline with Model B seed and RuleBasedScreener.
- **Verification Command**: `python baselines/proposed_temporal_kg.py --check`

---

### Task 6: Temporally Contrastive Distractor Engine

#### 6.1 Past-State & Outdated Entity Distractor Generator
- **Objective**: Add `generate_temporal_distractors()` in `mcq_generator.py` to extract true historical facts valid at $t < t^*$ as hard negative distractors.
- **Target File**: [`mcq_generator.py`](file:///d:/BCS_final/mcq_generator.py)
- **Acceptance Criteria**:
  - Produce plausibly deceptive distractors that were true in prior years but invalid at `2023-04-19`.
- **Verification Command**: `python -m unittest test_contrastive_distractors.py`

#### 6.2 Distractor Plausibility & Ambiguity Screener
- **Objective**: Upgrade `mcq_quality.py` to check that contrastive distractors are distinct from the valid answer at $t^*$.
- **Target File**: [`mcq_quality.py`](file:///d:/BCS_final/mcq_quality.py)
- **Acceptance Criteria**:
  - Reject distractors that are synonymous or simultaneously valid at $t^*$.
- **Verification Command**: `python test_task2b_quality_gate.py`

---

### Task 7: Automated Verification & Rejection Taxonomy Suite

#### 7.1 Rejection Code Integration (§10.2 Standard)
- **Objective**: Ensure all 6 failure categories (`E-FACT`, `E-TEMP`, `E-DIST`, `E-MULTI`, `E-AMBIG`, `E-STYLE`) are logged in `rejection_taxonomy.py`.
- **Target File**: [`rejection_taxonomy.py`](file:///d:/BCS_final/rejection_taxonomy.py)
- **Acceptance Criteria**:
  - Every rejected MCQ across all baselines receives an explicit taxonomy code and diagnostic log entry.
- **Verification Command**: `python -m unittest test_rejection_taxonomy_suite.py`

#### 7.2 Full Pipeline Verification Integration
- **Objective**: Wire rejection taxonomy tallies into `main-pipeline.py` Stage 14 summary reporting.
- **Target File**: [`main-pipeline.py`](file:///d:/BCS_final/main-pipeline.py)
- **Acceptance Criteria**:
  - Produce full JSON report containing total generations, screener pass rates, and rejection breakdowns.
- **Verification Command**: `python test_task2_pipeline.py`

---

### Task 8: Benchmark Comparative Execution

#### 8.1 Comparative Benchmark Run
- **Objective**: Execute all 4 generation systems (Generic LLM, Static RAG, Web-RAG, Proposed System) across the 36-question benchmark set.
- **Target Files**: `task3_comparative_run.py`, `experiments/baseline_comparison_results.json`
- **Acceptance Criteria**:
  - Generate 36 MCQs per system (144 MCQs total) with full provenance metadata.
- **Verification Command**: `python task3_comparative_run.py`

#### 8.2 Execution Audit & Integrity Verification
- **Objective**: Verify zero missing values, valid JSON structure, and prompt/model parameter logging in output.
- **Target File**: `test_comparative_execution.py`
- **Acceptance Criteria**:
  - 100% data integrity check pass for generated benchmark artifacts.
- **Verification Command**: `python test_comparative_execution.py`

---

### Task 9: Statistical Holdout Evaluation & Ablation Study

#### 9.1 Metric Calculation Suite
- **Objective**: Run `bcs_metrics.py` across all 4 system outputs to compute Factual Validity, Temporal Correctness, Exam Relevance, Distractor Quality, and Rejection Rate.
- **Target File**: [`bcs_metrics.py`](file:///d:/BCS_final/bcs_metrics.py)
- **Acceptance Criteria**:
  - Produce metric table comparing proposed method against all 3 baselines.
- **Verification Command**: `python bcs_metrics.py --input experiments/baseline_comparison_results.json`

#### 9.2 Hypothesis Testing (H1–H5 Validation)
- **Objective**: Perform statistical significance tests (p-values, chi-square, t-tests) testing H1 through H5.
- **Target File**: `experiments/hypothesis_testing.py`
- **Acceptance Criteria**:
  - Output p-values and confidence intervals confirming or refuting H1–H5.
- **Verification Command**: `python experiments/hypothesis_testing.py`

#### 9.3 Component Ablation Study
- **Objective**: Evaluate 3 ablated variants of proposed system: (1) w/o Temporal KG, (2) w/o Screener, (3) w/o Prompt Constraints.
- **Target File**: `experiments/ablation_study.py`
- **Acceptance Criteria**:
  - Record performance drop for each ablation component.
- **Verification Command**: `python experiments/ablation_study.py`

---

### Task 10: Publication Artifact Generation & Final Paper Execution

#### 10.1 LaTeX Tables & Figure Export
- **Objective**: Export publication-ready LaTeX tables (`tables.tex`) and high-resolution chart figures (`figures/`).
- **Target Directory**: `paper_artifacts/`
- **Acceptance Criteria**:
  - Formatted LaTeX tables for main baseline comparisons, rejection taxonomy, and ablation results.
- **Verification Command**: `python paper_artifacts/generate_tables.py`

#### 10.2 Final Paper Results Synthesis
- **Objective**: Draft `PAPER_RESULTS_SECTION.md` integrating empirical findings into the storyline defined in [12. Paper Writing Plan](file:///d:/BCS_final/BCS-MindGraph/12-Paper-Writing-Plan.md).
- **Target File**: `PAPER_RESULTS_SECTION.md`
- **Acceptance Criteria**:
  - Comprehensive draft covering empirical results, baseline comparison, ablation insights, and limitations.
- **Verification Command**: `python -m unittest test_paper_artifacts.py`


### Task: Comprehensive Supervisor Guideline & PRD Compliance Audit

**Objective:**
Perform a strict, line-by-line compliance audit of our completed research codebase and outputs against the supervisor's original guideline document located at:
`D:\BCS_final\BCS_Temporal_MCQ_RA_Implementation_Evaluation_Paper_Guideline.docx`

**Execution Instructions for Claude Code:**

1. **Document Inspection:**
   - Read and parse the complete contents of `D:\BCS_final\BCS_Temporal_MCQ_RA_Implementation_Evaluation_Paper_Guideline.docx`.
   - Extract every single requirement, guideline, methodology step, evaluation metric, baseline requirement, statistical test, and reporting instruction outlined by the supervisor. Do NOT skip any section or minor requirement.

2. **Cross-Verification against Codebase & Artifacts:**
   - Systematically cross-verify each extracted requirement against our completed project codebase and outputs:
     - Knowledge Graph & Temporal Logic: `src/` directory, `bitemporal_kg/`
     - Quality Gate & Screener: `rule_based_screener.py`, `quality_gate.py`
     - Benchmarks & Evaluation: `baseline_metrics_report.json`, `hypothesis_testing_report.json`
     - Ablation & Paper Artifacts: `ablation_study_report.json`, `paper_tables_and_figures.json`, `FINAL_RESEARCH_PAPER.md`
     - Test Suites: `test_comparative_execution.py`, `test_cutoff.py`, `test_task2b_quality_gate.py`

3. **Output Deliverable (`COMPLIANCE_AUDIT_REPORT.md`):**
   Generate a comprehensive markdown report titled `COMPLIANCE_AUDIT_REPORT.md` at the project root containing:
   
   - **Executive Compliance Summary:** Overall coverage percentage (e.g., 98% or 100%) and high-level status.
   - **Line-by-Line Requirement Mapping Table:**
     | Supervisor Guideline / Requirement | Implementation Status (`[COMPLETED]`, `[PARTIAL]`, `[MISSING]`) | Corresponding File / Code Artifact Location | Evidence & Metric Proof |
     | :--- | :---: | :--- | :--- |
   - **Gap Analysis (If Any):** Explicitly highlight any minor prompt detail, evaluation criteria, or guideline instruction that was overlooked or partially implemented.
   - **Action Plan for Identified Gaps:** Provide exact actionable code/paper edits to achieve 100% total alignment with the supervisor's document before final submission.

**Constraint:** Be realistic, rigid, and uncompromising. Do not make assumptions or give false positive compliance statuses without verifying actual source code or output evidence.