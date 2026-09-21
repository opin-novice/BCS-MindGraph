# RESEARCH_LOG

One entry per architectural or hyperparameter decision, written **before** the
run it justifies. Hypothesis and results are written by hand; the dry-run check
appends commit, branch, diff stat and environment.

---

## 2026-09-17 — Task 2B: quality gate upgrade

**Branch:** `exp/2026-09-17-task2b-quality-gate`
**Seed:** `bcs_gk_facts_model_b.json`, unmodified (sha256 `474aae6a…`, release
`model-b-seed-2026-09-16`)
**Cutoff:** 2023-04-19, strict guard PASS (69/69) before and after this change

### Hypothesis

The Task 2A defects are not judgement calls, so they should not be routed to a
judge model. Spelling, synonym distractors, source-term translation and
repeated questions are all decidable from a controlled table. If that is true,
a rule-based layer should reject all four defects in the frozen Task 2A output
at zero inference cost, and reject nothing else in it.

The negative half of the hypothesis is the part worth testing: a checker that
also fails the two clean Culture items would be measuring its own aggression,
not the batch.

### What changed

1. **`ChallengerAgent.SYSTEM_PROMPT`** — added three constraint blocks:
   standard Bengali orthography and Bengali numerals; a ban on distractors
   that are synonyms, alternative names or translations of the correct answer;
   and a requirement that multiple MCQs from one fact have different answers.
   Prompt fingerprint moved `37bd81f7fa799ff7` → `f465c4f51422dc8a`
   (reasoner and judge unchanged, as expected — only the challenger prompt
   was touched).

2. **`mcq_quality.RuleBasedScreener`** — four new codes, all LLM-free:
   `MISSPELLING` (controlled 3-entry table), `SYNONYM_DISTRACTOR`
   (orthography-normalised equivalence classes), `MISTRANSLATION` and
   `DROPPED_QUALIFIER` (source-term guard against the English seed fact).
   `screen()` now takes an optional `supporting_facts` argument; without it
   the translation guard is skipped rather than guessed at.

3. **`mcq_quality.find_near_duplicates`** — batch-level check for two MCQs
   from one fact whose correct answers name the same entity. Stem similarity
   does not see the Task 2A pair (the two stems share one token); the shared
   answer entity does.

4. **`HARD_FAILURE_CODES`** — the new codes fail an MCQ outright instead of
   deducting from the composite. Task 2A's worst item scored 0.878 overall
   *with* a mistranslation and a second correct option in it; a defect that
   only moves a weighted average cannot be relied on to stop anything.

5. **`rejection_taxonomy`** — new codes mapped onto §10.2. `SYNONYM_DISTRACTOR`
   maps to **E-MULTI**, not E-DIST: an option meaning the same thing as the
   correct answer *is* a second correct option. That closes the gap the module
   header records ("no stage currently detects multiple-correct-option cases
   directly").

### Result

`test_task2b_quality_gate.py` — **30/30 CHECK True**, no model calls. Run over
the 7 frozen Task 2A records:

| MCQ | new verdict |
|---|---|
| `MCQ_f78a10b0` | clean of new codes |
| `MCQ_89c2bdcc` | MISSPELLING |
| `MCQ_2051ff97` | MISSPELLING, NEAR_DUPLICATE (re-asks `MCQ_89c2bdcc`) |
| `MCQ_fd5da7cf` | MISSPELLING |
| `MCQ_795d94e5` | MISSPELLING, MISTRANSLATION, DROPPED_QUALIFIER, SYNONYM_DISTRACTOR |
| `MCQ_84efae05` | clean of new codes |
| `MCQ_21ac5280` | clean of new codes |

All four catalogued defects fail; the two clean Culture items survive.

Unchanged after the edit: `verify_temporal_corpus.py guard --seed` PASS,
`test_task2_pipeline.py` 21/21, `test_generation_firewall.py` 10/10,
`test_cutoff.py` and `test_dataset_accounting.py` exit 0.

### Open

- **The new bounded generation batch has not been run.** No HF token: the
  2026-09-17 token was pasted into a chat transcript and revoked by the PI,
  and `.env` holds an empty placeholder by design. Until a token is in
  `.env`, item 6 of the handover §9.5 checklist stays unticked and the
  prompt-side change (1) is untested against a live model.
- The orthography and synonym tables hold exactly the entries a real run
  produced. They are meant to grow from observed errors, not from guessing at
  Bengali spelling: a rejected good MCQ is invisible, an accepted bad one is
  at least auditable.
- `MCQ_f78a10b0` writes its options in ASCII digits (`7/8/9/10`) inside a
  Bengali stem. The prompt now forbids this; the gate does **not** fail it.
  Worth a decision before the full run — it is a real style defect, but it was
  not in the PI's list and failing it would change the pass rate on items
  nobody has reviewed.

---

## 2026-09-17 — Task 2B run 1: live bounded batch (Geography)

**Command:** `python task2a_smoke_run.py --tag task2b --topic Geography --facts 2`
**Model:** `Qwen/Qwen2.5-72B-Instruct`, HF provider `auto`, temperature 0.7
**Prompt fingerprint:** challenger `f465c4f51422dc8a` (was `37bd81f7fa799ff7`)
**Seed:** unchanged, sha256 verified against the release manifest at run start

### Hypothesis

The two facts Geography retrieves at this cutoff are the same two that
produced Task 2A's defective items — the Buriganga fact and the
"8 administrative divisions" fact. That makes this the sharpest available
test of the prompt change: the model is being handed the exact inputs on
which it previously wrote বুরিগঙ্গা, পাদমা, ASCII numerals, and two questions
with the same answer.

Predicted, in order of confidence:

1. No ASCII numerals in the divisions item. This is the most explicit
   instruction and the easiest to follow.
2. বুড়িগঙ্গা and পদ্মা spelled correctly. The prompt now names all three
   wrong forms.
3. No second question answered by বুড়িগঙ্গা.

What would falsify the approach: the gate catching these anyway. The prompt
is a request and the gate is a decision, so a clean gate report here means
the prompt worked; a failing one means prompt-level constraints are not
sufficient for this model and the repair belongs in regeneration, not in
wording.

A run of 2 facts cannot separate "the prompt works" from "this sample was
lucky". It is sized to show the pipeline end-to-end with the new fingerprint
on record, not to measure a rate.

### Result

(written after the run)

---

### Environment note

`venv/` in the repo was built on macOS (`/Users/simnim/...`) and cannot run on
this Windows machine. A local `.venv` was created (CPython 3.12.10) from
`requirements.txt`. Note `huggingface-hub` resolves to **1.32.0** here, a major
version ahead of what the Task 2A run used — worth confirming
`InferenceClient(token=..., provider=...)` still behaves the same way on the
first live call.

## Pre-run check (PASS) — 2026-09-21 20:05:21
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:06:20
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:06:40
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:08:01
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:08:22
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:08:35
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:08:37
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:09:21
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:09:36
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:09:48
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:17:15
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:18:00
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:18:24
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:32:39
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: mcq_quality.py
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:40:26
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:40:38
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:44:08
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 20:47:07
- Commit: no commit yet (branch: exp/2026-09-17-task2b-quality-gate)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 22:31:21
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: kg_builder.py
- Uncommitted changes: none
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 22:36:49
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: RESEARCH_LOG.md           | 6 ++++++
 requirements_snapshot.txt | 8 +++++++-
 2 files changed, 13 insertions(+), 1 deletion(-)
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 22:45:05
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: kg_builder.py
- Uncommitted changes: RESEARCH_LOG.md           | 14 ++++++++++++++
 requirements_snapshot.txt |  8 +++++++-
 2 files changed, 21 insertions(+), 1 deletion(-)
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 22:45:28
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: RESEARCH_LOG.md           | 22 ++++++++++++++++++++++
 requirements_snapshot.txt |  8 +++++++-
 2 files changed, 29 insertions(+), 1 deletion(-)
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 22:47:40
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: RESEARCH_LOG.md           | 30 ++++++++++++++++++++++++++++++
 requirements_snapshot.txt |  8 +++++++-
 2 files changed, 37 insertions(+), 1 deletion(-)
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 22:48:52
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: nothing (no script in command)
- Uncommitted changes: RESEARCH_LOG.md           | 38 ++++++++++++++++++++++++++++++++++++++
 requirements_snapshot.txt |  8 +++++++-
 2 files changed, 45 insertions(+), 1 deletion(-)
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 23:02:02
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: test_kg_bitemporal.py
- Uncommitted changes: RESEARCH_LOG.md           |  46 +++++++
 episodic_store.py         |   5 +-
 mcq_generator.py          |   8 ++
 requirements_snapshot.txt |   8 +-
 test_excerpt_search.py    | 321 +++++++++++++++++++++++-----------------------
 5 files changed, 226 insertions(+), 162 deletions(-)
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB

## Pre-run check (PASS) — 2026-09-21 23:03:29
- Commit: d4eb0d5 (branch: main)
- Syntax-checked: test_kg_bitemporal.py
- Uncommitted changes: RESEARCH_LOG.md           |  57 ++++++++
 episodic_store.py         |   5 +-
 mcq_generator.py          |   8 ++
 requirements_snapshot.txt |   8 +-
 test_excerpt_search.py    | 321 +++++++++++++++++++++++-----------------------
 5 files changed, 237 insertions(+), 162 deletions(-)
- GPU: NVIDIA GeForce RTX 5070, 12227 MiB
