# Project Audit: PI Review

#project-management #PI-review

## Executive Summary
This audit compares the current codebase against the original research plan in the project guideline document. The project is materially advanced and contains strong implementations in temporal graph construction, fact quality validation, MCQ generation, and episodic memory. However, the implementation is not yet fully aligned with the project’s strict research discipline.

The main risk is not lack of code — it is lack of enforcement of the project’s non-negotiable rules:
- temporal holdout discipline,
- provenance-based fact validity,
- verification before release,
- and strict evaluation against the planned benchmark.

The codebase is therefore in a strong prototype-to-research transition state, but it still needs intervention before it can be treated as a fully validated research system.

---

## Overall Status

### Current Assessment
- Strong foundation: Done
- Research-grade enforcement: Need to Fix
- Publication-level rigor: Need to Improve
- Missing or incomplete by plan: Need to Implement

### Bottom Line
The system already contains a substantial technical foundation. The remaining work is primarily about making the project follow the original plan consistently and defensibly.

---

## Audit Matrix

| Plan Section / Feature | Status | File Path(s) in Codebase | RA Notes / Technical Debt |
|---|---|---|---|
| Research framing / project thesis | Mostly Done | [[00-Project-Overview]], [[01-Research-Framing]], [main-pipeline.py](main-pipeline.py) | The research objective is present in comments and the vault. It is not yet formalized as a single authoritative publication-ready thesis statement across all artifacts. |
| Input normalization | Done | [input_normalizer.py](input_normalizer.py) | Clean and functional preprocessing for mixed Bangla/English inputs. |
| Intent and blueprint extraction | Done | [intent_builder.py](intent_builder.py) | Topic extraction and query generation are implemented and usable. |
| Temporal holdout policy / pre-2023 firewall | Need to Fix | [main-pipeline.py](main-pipeline.py), [web_scraper.py](web_scraper.py), [bcs_metrics.py](bcs_metrics.py) | The comments explicitly warn that the temporal firewall was previously off and could mix post-cutoff evidence. This is a direct mismatch with the plan's non-negotiable holdout requirement. |
| BCS-GK corpus construction | Need to Improve | [main-pipeline.py](main-pipeline.py), [bcs_questions_corpus.json](bcs_questions_corpus.json) | Corpus support exists, but it is not yet enforced with strict temporal and benchmark discipline. |
| Topic ontology / topic-grounded demand | Need to Improve | [intent_builder.py](intent_builder.py), [main-pipeline.py](main-pipeline.py) | Topic mapping exists, but the ontology is not yet fully rigorous or persistent as a validated demand model. |
| Dynamic bitemporal knowledge graph | Done | [kg_builder.py](kg_builder.py) | This is one of the strongest pieces of the project. It clearly supports temporal validity windows, observation times, store/update semantics, and provenance-aware fact management. |
| Episodic web acquisition and provenance | Mostly Done | [web_scraper.py](web_scraper.py), [episodic_store.py](episodic_store.py) | The acquisition layer exists and tracks source tier/date metadata, but it is not consistently enforced as a required evidence layer. |
| Fact quality gate | Done | [fact_quality.py](fact_quality.py) | The fact gate is already sophisticated and useful for filtering weak evidence. |
| Agentic MCQ generation architecture | Done | [mcq_generator.py](mcq_generator.py), [main-pipeline.py](main-pipeline.py) | The generation loop, regeneration logic, and duplicate filtering are implemented. |
| Temporally contrastive distractor generation | Need to Improve | [mcq_generator.py](mcq_generator.py), [rejection_taxonomy.py](rejection_taxonomy.py) | Distractor scoring exists, but the project still needs a more explicit temporal contrastive strategy for high-quality exam distractors. |
| Verification and rejection pipeline | Mostly Done | [mcq_quality.py](mcq_quality.py), [rejection_taxonomy.py](rejection_taxonomy.py), [main-pipeline.py](main-pipeline.py) | The pipeline is implemented, but not uniformly enforced. Some mappings and checks remain incomplete or not anchored to the strict paper taxonomy. |
| Evaluation metrics / BCS metrics framework | Mostly Done | [bcs_metrics.py](bcs_metrics.py), [main-pipeline.py](main-pipeline.py) | The metrics structure is rich and promising, but some values depend on fields and runtime conditions that still need stronger consistency checks. |
| Episodic memory / learning from experience | Done | [episodic_store.py](episodic_store.py) | The memory layer is implemented and well-structured for long-term operational learning. |
| Experimental execution / benchmarking trail | Need to Improve | [main-pipeline.py](main-pipeline.py), [bcs_metrics_report.json](bcs_metrics_report.json), [mcq_quality_log.json](mcq_quality_log.json) | The experiments and logs exist, but a clean, reproducible holdout benchmark workflow is still not consistently enforced. |
| Paper-writing / final publication plan | Need to Implement | [[12-Paper-Writing-Plan]] | The writing plan exists in the vault, but it needs to be tied directly to the actual evaluation outputs and result tables. |

---

## Key Blockers and Discrepancies

### 1. Temporal enforcement is not yet a hard gate
The code comments in [main-pipeline.py](main-pipeline.py) explicitly say the system previously ran with the temporal firewall off. This is a direct mismatch with the original requirement that temporal holdout be non-negotiable.

### 2. Rejection taxonomy exists, but it is not consistently enforced
[rejection_taxonomy.py](rejection_taxonomy.py) has strong taxonomy support, but several comments note that mappings and validation checks remain incomplete. This means accepted items may still pass through without strict compliance to the planned rejection rules.

### 3. The system still behaves like a monolithic script in places
The orchestration in [main-pipeline.py](main-pipeline.py) is dense and cross-coupled. For a research project of this scale, the execution flow should be cleaner and more explicit in order to maintain reproducibility and reviewer trust.

### 4. Web evidence integration is still optional rather than mandatory
[web_scraper.py](web_scraper.py) is implemented but not yet locked into the strict evidence pipeline expected by the plan. This matters because the entire claim depends on provenance-aware, temporally valid evidence.

### 5. Benchmark rigor still needs stronger enforcement
The code has metrics and evaluation but the holdout discipline and benchmark gating still need to be made explicit as a standard operating procedure for all runs.

---

## RA Action Plan

### Critical Fixes (Do Now)
- Enforce a mandatory temporal cutoff gate before facts are used in generation.
- Prevent any run path from accepting unversioned or post-cutoff sources without explicit review.
- Ensure all generated MCQs pass a rejection-taxonomy gate before acceptance.
- Add explicit checks for multiple-correct and leakage-risk cases during verification.

### Missing Implementations (Do Next)
- Convert the benchmark holdout into a strict experiment runner with documentation.
- Formalize the ontology as a maintained and reusable topic layer.
- Implement a clearer temporal distractor strategy tied to the target date state.
- Create a clean paper-ready output pipeline from experiments to results tables and narrative.

### Optimizations (Do Later)
- Break the pipeline into cleaner service boundaries for ingestion, graph maintenance, generation, validation, and evaluation.
- Add robust tests for temporal filtering, gating logic, and taxonomy conversion.
- Improve provenance caching and web scrape efficiency.
- Add a reporting dashboard for per-topic, per-difficulty, and per-time-slice evaluation.

---

## PI Verdict
The project is strong technically but not yet fully aligned with the original research plan. The implementation already contains most of the important building blocks, but the final phase of the project must focus on enforcing the plan’s non-negotiable discipline: temporal validity, evidence provenance, verification, and benchmark integrity.

This is not a failure of the codebase — it is a management and enforcement gap. The RA team now has a clear list of what is done, what is missing, and what must be fixed to make the project research-grade.

---

## Recommended Next Meeting Agenda for RAs
1. Review temporal holdout enforcement and identify the exact run path that violates it.
2. Check the rejection taxonomy on all accepted MCQs.
3. Verify topic coverage and ontology consistency.
4. Confirm provenance tracking for all web-acquired facts.
5. Define the benchmark runner for the 2023 holdout evaluation.
6. Create the final output checklist for the paper-writing phase.

---

## Quick Action Summary
- Done: Graph, fact quality, generation, memory, metrics
- Need to Fix: Temporal holdout discipline and proof of compliance
- Need to Improve: Distractor quality, ontology rigor, benchmark reproducibility
- Need to Implement: Final publication pipeline and research-grade enforcement

This note is intended to serve as the working implementation and review record for the RA team.
