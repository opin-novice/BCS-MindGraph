# BCS-MindGraph: Temporal MCQ Research Vault

## Mission
This vault is the primary working plan for the research project: temporally grounded, fact-validated MCQ generation for BCS General Knowledge.

The central claim is:

> Historical BCS questions reveal what examiners tend to value; a temporally maintained, provenance-rich knowledge graph reveals what was true at a given point in time; an agentic generator turns that time-bounded state into BCS-style MCQs and verifies them before release.

## Primary Task
Time-conditioned, fact-grounded MCQ generation for BCS General Knowledge.

## Primary Test
2023 holdout: reconstruct / forecast the 45th BCS GK content without seeing the target paper.

## Core Research Question
Can structured temporal knowledge maintenance improve factual validity, temporal alignment, and exam relevance versus static RAG, web-RAG, or generic LLM generation?

## Research Pillars
1. Temporal gold-standard evaluation and holdout discipline
2. Dynamic bitemporal knowledge graph with provenance
3. Topic-grounded knowledge demand and ontology
4. Episodic web acquisition and verification
5. Agentic MCQ generation and distractor design
6. Verification, rejection, and paper-ready evaluation

## Vault Structure
- [[00-Project-Overview]]
- [[01-Research-Framing]]
- [[02-Research-Questions-and-Hypotheses]]
- [[03-Temporal-Holdout-Policy]]
- [[04-BCS-GK-Corpus-Construction]]
- [[05-Topic-Ontology]]
- [[06-Dynamic-Bitemporal-Knowledge-Graph]]
- [[07-Episodic-Web-Knowledge-Acquisition]]
- [[08-Agentic-MCQ-Generation-Architecture]]
- [[09-Temporally-Contrastive-Distractors]]
- [[10-Verification-and-Rejection-Pipeline]]
- [[11-Experimental-Execution-Plan]]
- [[12-Paper-Writing-Plan]]

## Non-Negotiable Principle
The temporal-holdout discipline, graph provenance, generation pipeline, and evaluation protocol are coupled. Weakening any one of them can invalidate the central novelty claim.

## Immediate Next Step
Start with [[00-Project-Overview]] and then follow the numbered implementation sequence in order.
