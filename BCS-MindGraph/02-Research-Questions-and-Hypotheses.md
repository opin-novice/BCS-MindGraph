# 02. Research Questions and Hypotheses

## Main Research Question
Can structured temporal knowledge maintenance improve factual validity, temporal alignment, and exam relevance over static RAG, web-RAG, and generic LLM generation?

## Secondary Research Questions
1. Does temporal graph maintenance improve factual consistency of generated MCQs?
2. Does time-aware knowledge state improve temporal alignment of questions and answers?
3. Does topic-guided knowledge demand improve exam relevance compared to generic generation?
4. Does explicit provenance reduce unsupported or hallucinated facts?
5. Does verification and rejection improve release quality compared to one-pass generation?

## Hypotheses
### H1: Temporal grounding improves factual validity
Generated MCQs from a temporal knowledge graph will have fewer factual errors than those generated from static retrieval or prompt-only baselines.

### H2: Temporal alignment improves answer validity
Questions conditioned on a time-specific knowledge state will better match the historical reality of that period than static or non-time-conditioned generation.

### H3: Topic-guided graph maintenance improves exam relevance
Topic-aware knowledge acquisition will lead to more relevant BCS-like content than generic retrieval-based generation.

### H4: Provenance-aware systems reduce unsupported claims
Retaining provenance and evidence chains will reduce unverified statements in final MCQs.

### H5: Verification and rejection improve release quality
A post-generation verification and rejection pipeline will reject low-quality items and improve final item reliability.

## Evaluation Logic
Each hypothesis should be operationalized through measurable indicators such as factual accuracy, temporal alignment, topic alignment, distractor quality, and rejection rate.
