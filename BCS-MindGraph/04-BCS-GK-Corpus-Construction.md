# 04. BCS-GK Corpus Construction

## Goal
Construct a high-quality, exam-relevant corpus of BCS General Knowledge material keyed to time and topic.

## Requirements
The corpus must support:
- historical question retrieval
- temporal fact anchoring
- topic classification
- evaluation holdout integrity

## Corpus Components
1. Historical BCS questions
2. Topic metadata and taxonomy
3. Time annotations for question relevance
4. Provenance references to source material
5. Relationship between question content and underlying factual knowledge

## Construction Steps
- Gather exam questions and associated year/context
- Normalize entity and topic labels
- Map questions to ontology categories
- Build temporal anchors for each item
- Retain provenance where possible

## Quality Control
The corpus should be filtered and structured so that it is useful for both generation and evaluation, not just raw scraping.

## Research Importance
This corpus is the empirical foundation for answering whether temporal knowledge maintenance improves BCS GK MCQ generation in a meaningful way.
