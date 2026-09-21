# 10. Verification and Rejection Pipeline

## Goal
Build a quality gate that rejects weak or unsupported generated MCQs before they are released.

## Required Behavior
Generated questions should be checked for:
- factual correctness
- temporal alignment
- answer validity
- distractor plausibility
- exam relevance
- missing provenance or unsupported claims

## Rejection Logic
Reject any item that fails any required threshold in factual or temporal verification.

## Why This Matters
Without verification, a generative system can produce fluent but wrong exam items. The rejection pipeline is essential to the project claim.

## Expected Outcome
The final accepted set should be significantly more reliable than raw generation output.
