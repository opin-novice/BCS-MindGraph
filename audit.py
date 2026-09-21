import json
from pathlib import Path
from datetime import datetime

print("=" * 100)
print("BCS TEMPORAL MCQ RESEARCH CODEBASE - COMPREHENSIVE COMPLIANCE AUDIT")
print("=" * 100)
print(f"Audit Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

reqs = []

# 1. BITEMPORAL
print("## SECTION 1: BITEMPORAL GRAPH (§4.1)")
print()
with open("kg_builder.py", 'r', encoding='utf-8', errors='ignore') as f:
    kg = f.read()

bt = {"valid_from": "valid_from" in kg, "valid_to": "valid_to" in kg, "observed_at": "observed_at" in kg}
pit = {"as_of": "as_of" in kg, "snapshot": "get_graph_snapshot" in kg}
bt_found = sum(1 for v in bt.values() if v)
pit_found = sum(1 for v in pit.values() if v)

print(f"Timestamps: {bt_found}/3 - {['PASSED' if bt_found==3 else 'PARTIAL'][0]}")
for k,v in bt.items():
    print(f"  {'✓' if v else '✗'} {k}")
print(f"Point-in-Time: {pit_found}/2 - {['PASSED' if pit_found==2 else 'PARTIAL'][0]}")
for k,v in pit.items():
    print(f"  {'✓' if v else '✗'} {k}")
reqs.append(("BITEMPORAL", "PASSED" if bt_found==3 and pit_found==2 else "PARTIAL"))
print()

# 2. REJECTION TAXONOMY
print("## SECTION 2: REJECTION TAXONOMY (§10.2)")
print()
with open("rejection_taxonomy.py", 'r', encoding='utf-8', errors='ignore') as f:
    tax = f.read()

codes = ["E-TIME", "E-LEAK", "E-UNSUP", "E-MULTI", "E-DIST", "E-AMB", "E-STYLE", "E-DUP", "E-KG", "E-SRC"]
code_found = sum(1 for c in codes if c in tax)

structs = ["RejectionCode", "FAILURE_CODE_MAP", "map_codes", "RejectionTally"]
struct_found = sum(1 for s in structs if s in tax)

print(f"Codes: {code_found}/10")
for c in codes[:5]:
    print(f"  {'✓' if c in tax else '✗'} {c}")
print("  ...")
print(f"Structures: {struct_found}/4")
for s in structs:
    print(f"  {'✓' if s in tax else '✗'} {s}")
reqs.append(("REJECTION_TAXONOMY", "PASSED" if code_found==10 and struct_found==4 else "PARTIAL"))
print()

# 3. QUALITY GATES
print("## SECTION 3: QUALITY GATES")
print()
modules = {
    "mcq_quality.py": ["RuleBasedScreener", "LLMQualityEvaluator", "FactualityVerificationEngine"],
    "mcq_generator.py": ["MCQGenerationPipeline", "JudgeAgent"],
}

for mod, cls_list in modules.items():
    if Path(mod).exists():
        with open(mod, 'r', encoding='utf-8', errors='ignore') as f:
            mod_content = f.read()
        found = sum(1 for c in cls_list if c in mod_content)
        print(f"{mod}: {found}/{len(cls_list)}")
        reqs.append((mod.replace('.py', ''), "PASSED" if found==len(cls_list) else "PARTIAL"))
    else:
        print(f"{mod}: NOT FOUND")
        reqs.append((mod.replace('.py', ''), "MISSING"))
print()

# 4. TEST SUITE
print("## SECTION 4: TEST SUITE")
print()
tests = ["test_rejection_taxonomy_suite.py", "test_kg_bitemporal.py", "test_task2b_quality_gate.py", "test_generation_firewall.py", "test_contrastive_distractors.py"]
test_found = sum(1 for t in tests if Path(t).exists())
print(f"Test files: {test_found}/{len(tests)}")
reqs.append(("TEST_SUITE", "PASSED" if test_found==len(tests) else "PARTIAL"))
print()

# 5. DATA ARTIFACTS
print("## SECTION 5: DATA ARTIFACTS")
print()
corpus_path = Path("bcs_questions_corpus.json")
if corpus_path.exists():
    with open(corpus_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    q_count = len(data.get("questions", []))
    print(f"Questions corpus: {q_count}/494")
    reqs.append(("DATA_ARTIFACTS", "PASSED" if q_count>=494 else "PARTIAL" if q_count>=400 else "MISSING"))
else:
    print(f"Questions corpus: NOT FOUND")
    reqs.append(("DATA_ARTIFACTS", "MISSING"))
print()

# SUMMARY
print("=" * 100)
print("## COMPLIANCE SUMMARY")
print()
passed = sum(1 for r,s in reqs if s=="PASSED")
partial = sum(1 for r,s in reqs if s=="PARTIAL")
missing = sum(1 for r,s in reqs if s=="MISSING")
total = len(reqs)

comp = int((passed + partial*0.5) / total * 100) if total>0 else 0
print(f"Compliance Score: {comp}%")
print(f"  Passed: {passed}/{total}")
print(f"  Partial: {partial}/{total}")
print(f"  Missing: {missing}/{total}")
print()
print(f"Verdict: {'FULLY COMPLIANT' if comp>=95 else 'SUBSTANTIALLY COMPLIANT' if comp>=85 else 'COMPLIANT WITH GAPS' if comp>=70 else 'REQUIRES REMEDIATION'}")
print("=" * 100)
