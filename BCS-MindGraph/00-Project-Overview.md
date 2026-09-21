# 00. Project Overview

## Research Thesis
Historical BCS questions tell us what the examination tends to care about; a dynamically maintained, provenance-rich temporal knowledge graph tells us what was true at a specified time; an agentic generator converts the temporally bounded knowledge state into BCS-style MCQs and verifies them before release.

## Primary Task
Time-conditioned, fact-grounded MCQ generation for BCS General Knowledge.

## Primary Test
2023 holdout: reconstruct / forecast the 45th BCS GK content without seeing the target paper.

## Knowledge Backbone
Topic-guided, provenance-preserving bitemporal dynamic knowledge graph.

## Scientific Question
Can structured temporal knowledge maintenance improve factual validity, temporal alignment, and exam relevance over static RAG, web-RAG, and generic LLM generation?

## Central Research Contribution
This project argues that temporal grounding and provenance-aware graph maintenance materially improve the quality and reliability of exam-style MCQ generation beyond generic retrieval or static prompting.

## Implementation Goal
Build a research system that is not just a pipeline prototype but a reproducible, evaluation-driven research artifact that can support a publication-quality empirical study.

## Research Constraints
- No leakage from the target evaluation paper into the training or retrieval timeline
- Temporal grounding must be explicit and auditable
- Provenance must be preserved from source to fact to MCQ
- Quality must be verified before any generated item is accepted
- Evaluation must compare against strong baselines

## Research Deliverables
1. Temporal knowledge graph and maintenance pipeline
2. Fact-grounded, topic-guided MCQ generation system
3. Verification and rejection workflow
4. Experimental evaluation with holdout discipline
5. Paper-ready results, tables, and analysis

## Working Principle
The implementation plan in this vault follows the exact order of the research guideline. Each section is a required component of the overall contribution and should not be treated as an isolated suggestion.
