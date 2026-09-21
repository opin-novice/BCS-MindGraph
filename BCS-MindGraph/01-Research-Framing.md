# 01. Research Framing

## Objective
Frame the project as a scientific contribution to temporal fact-grounded exam question generation.

## Core Framing Statement
The problem is not simply “generate MCQs from a topic,” but “generate exam-relevant MCQs that are valid at a specified time and grounded in auditable evidence.”

## Why This Matters
Static corpora and generic retrieval fail to capture:
- temporal validity
- provenance of facts
- changed historical narratives
- the exam’s topic distribution and expected difficulty

## Contribution Claims
The project contributes an implementation and evaluation framework that:
- maintains a dynamic temporal knowledge graph
- grounds generation in topic-aware and time-aware evidence
- uses structured provenance from episodic web acquisition
- verifies generated items before releasing them

## Non-Negotiable Research Design Principles
- Temporal-holdout discipline is mandatory
- Graph provenance is mandatory
- Topic grounding is mandatory
- Verification pipeline is mandatory
- Evaluation must be systematic and reproducible

## Expected Contribution to Literature
This work contributes to the growing space of temporal knowledge grounding and fact-checked generation by showing that time-aware structured memory improves exam-style item quality and factual reliability.
