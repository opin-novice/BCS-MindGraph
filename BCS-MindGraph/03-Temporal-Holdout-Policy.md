# 03. Temporal Holdout Policy

## Purpose
Define the policy that prevents leakage between source knowledge and target evaluation.

## Required Discipline
The temporal-holdout policy is not optional; it is the core mechanism that validates the research claim.

## Core Evaluation Protocol
The model must be evaluated on a 2023 holdout scenario: reconstruct or forecast the 45th BCS GK content without seeing the target paper.

## Rules
- Do not include the target paper in the retrieval or training corpus
- Maintain time-aware knowledge state by date
- Separate historical knowledge from target evaluation data
- Treat the evaluation period as future relative to the maintained knowledge base
- Maintain explicit snapshot boundaries across time

## Why This Is Essential
Without temporal holdout, the system is not testing genuine forecasting or grounding; it is just memorizing or seeing the answer set indirectly.

## Implementation Guidance
Define a clean timeline with:
- source corpus time range
- knowledge update dates
- holdout evaluation date
- exclusion rules for the target paper

## Evaluation Requirement
Every experiment should state what the system was allowed to know and what it was prevented from knowing.
