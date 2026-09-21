# Human Evaluation Protocol & Expert Review Specification

**Project**: Bitemporal Knowledge Graph (BKG) MCQ Generation & Temporal Audit System for Bangladesh Civil Service (BCS) Exams  
**Evaluation Standard**: Section 14 (Human Evaluation & Expert Review Protocol)  
**Target Cutoff Date ($t^*$ control)**: `2023-04-19` (45th BCS Preliminary Exam Date)  

---

## 1. Overview & Objectives

The Human Evaluation Protocol provides a rigorous, blinded domain expert evaluation of generated Multiple Choice Questions (MCQs) for the Bangladesh Civil Service (BCS) General Knowledge examination.

The evaluation tests candidate questions across four core quality dimensions using a **5-point Likert scale**, evaluated by a panel of **3 domain experts** (BCS cadre officers and history/general knowledge subject matter experts).

---

## 2. Expert Panel Composition

The human evaluation panel consists of 3 independent expert raters:
* **Rater 1**: Senior BCS Administration Cadre Officer (Subject Matter Expert in Bangladesh Affairs & Public Governance).
* **Rater 2**: Assistant Professor of History / International Relations (Expert in Historical & Temporal Fact Verification).
* **Rater 3**: Experienced BCS Examination Coach & Question Setter (Expert in Distractor Quality & Exam Relevance).

---

## 3. Evaluation Dimensions & 5-Point Likert Rubric

Each question is evaluated independently across four dimensions on a 1–5 scale:

| Dimension | 1 (Poor) | 3 (Moderate) | 5 (Excellent) |
| :--- | :--- | :--- | :--- |
| **Clarity ($C$)** | Unclear, ambiguous stem, or confusing option wording. | Minor phrasing awkwardness, but understandable. | Crystal clear, grammatically flawless Bengali stem and options. |
| **Factual Validity ($F_v$)** | Factually incorrect or hallucinated claims. | Partially accurate, minor factual imprecision. | 100% factually accurate based on verifiable authoritative sources. |
| **Temporal Correctness ($T_c$)** | Severe post-cutoff leakage (facts valid post April 2023 used as pre-cutoff answers). | Borderline temporal ambiguity or missing date anchors. | Fully compliant with $t^* = \text{2023-04-19}$ cutoff constraint; no future leakage. |
| **Distractor Quality ($D_q$)** | Trivial, implausible, or obviously wrong distractors. | Moderately plausible distractors, some obvious eliminations. | High-quality, highly plausible contrastive distractors requiring deep domain knowledge. |

---

## 4. Sampling & Blinding Protocol

1. **Sample Size**: 100 MCQs (25 randomly sampled per system variant across `generic_llm`, `static_rag`, `web_rag`, and `proposed_temporal_kg`).
2. **Blinding**: System identifiers, model names, and pipeline tags are stripped. Questions are assigned randomized evaluation IDs (`HEVAL_001` to `HEVAL_100`).
3. **Gold Mapping**: An unblinded key (`human_eval_gold_mapping.json`) is maintained separately for post-hoc statistical analysis.

---

## 5. Inter-Annotator Agreement (Krippendorff's $\alpha$)

Inter-rater reliability is measured using **Krippendorff's Alpha ($\alpha$)** for interval scale data:

$$\alpha = 1 - \frac{D_o}{D_e}$$

where $D_o$ is the observed disagreement and $D_e$ is the expected disagreement by chance across all paired ratings:

$$D_o = \frac{1}{N} \sum_{k=1}^N \frac{1}{\binom{m_k}{2}} \sum_{i < j} (r_{k,i} - r_{k,j})^2$$

### Verification Threshold
An agreement threshold of **$\alpha \ge 0.80$** is required for publication-grade reliability.

---

## 6. Execution Script Artifacts

* Sample & Blinding Generator: `experiments/human_eval_sample_generator.py`
* Blinded Sample Data: `experiments/human_eval_blinded_sample.json`
* Unblinded Gold Key: `experiments/human_eval_gold_mapping.json`
* Expert Responses: `experiments/human_eval_responses.json`
* Krippendorff Analysis Engine: `experiments/human_eval_krippendorff.py`
* Output Audit Report: `experiments/human_eval_report.json`
