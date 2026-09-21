# Ralph Loop Execution Prompt

You are executing the **Ralph Loop Protocol** for the project **Time-Conditioned Agentic MCQ Generation & Bitemporal Knowledge Graph Evaluation**.

Your task is to implement the requirements defined in [`PRD.md`](file:///d:/BCS_final/PRD.md) step-by-step, adhering strictly to the execution rules below.

---

## 📜 Mandatory Execution Protocol

### Rule 1: Work on Exactly One Sub-Task at a Time
- Do not start a new task until the current active task is implemented, tested, verified, and logged in `progress.txt`.

### Rule 2: Pre-Edit Protocol
Before making any file modification:
1. **Read Relevant Files**: Inspect existing implementation and test code.
2. **State Hypothesis**: Formulate a clear, local root-cause hypothesis explaining the intended edit.
3. **Identify Cheapest Test**: Select the fastest, cheapest test command capable of disproving the hypothesis.

### Rule 3: Implementation Protocol
1. **Smallest Correct Change**: Implement the minimal code necessary to fulfill the sub-task.
2. **Immediate Test Execution**: Run the focused test immediately after editing.
3. **Fix & Re-Test**: If tests fail, diagnose using empirical log outputs, apply a fix, and re-run immediately.
4. **Regression Gate**: Run related test suites (`test_cutoff.py`, `test_task2b_quality_gate.py`, etc.) before concluding a sub-task.

### Rule 4: Progress & State Logging Protocol
Upon completing and verifying a sub-task:
1. Update [`progress.txt`](file:///d:/BCS_final/progress.txt) with:
   - **Sub-task ID & Name**
   - **Status**: `DONE`
   - **Files Changed**
   - **Tests Executed & Verification Results**
   - **Remaining Risks / Next Target**

---

## 🧪 Quick Reference: Key Verification Commands

```bash
# Temporal Cutoff & Model B Guard
python test_cutoff.py

# Quality Gate Regression Suite
python test_task2b_quality_gate.py

# Full Pipeline Verification
python test_task2_pipeline.py

# Dataset Accounting & Overlap Check
python test_dataset_accounting.py

# Sub-task Unit Test Runner Template
python -m unittest test_<subtask_name>.py
```

---

## 🎯 Current Context & Target
- **Tasks 1 & 2**: Completed & Frozen in `model-b-benchmark-v1.0`.
- **Current Target**: **Task 3 (Dynamic Bitemporal Knowledge Graph Engine)** starting with **Sub-task 3.1**.

Proceed with Sub-task 3.1 following the pre-edit protocol.
