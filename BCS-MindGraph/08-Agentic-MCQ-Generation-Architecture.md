# 08. Agentic MCQ Generation Architecture

## Goal
Design a generator that uses the temporal graph and topic grounding to create BCS-style MCQs.

## Architecture
The generator should operate as an agentic pipeline that:
- selects topics
- retrieves time-bounded facts
- selects relevant evidence
- generates candidate questions
- proposes distractors
- verifies final item quality

## Inputs
- topic guidance
- time-conditioned graph state
- relevant historical facts
- provenance data
- BCS-style question structure

## Outputs
- multiple candidate MCQ items
- answer keys
- distractor sets
- provenance notes
- quality flags

## Research Significance
The generation architecture is where temporal grounding becomes exam-ready content. It should not just generate fluent questions; it must generate valid, time-aware, exam-relevant questions.
