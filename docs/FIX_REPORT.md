# Track A — Sanity Check & Fix Report

## Files Created

| File | Purpose |
|---|---|
| `src/bcs/logging_config.py` | Shared logger for all 6 modules (was missing, blocked all imports) |
| `src/bcs/__init__.py` | Package marker |
| `src/bcs/pipeline/__init__.py` | Package marker |
| `src/bcs/generators/__init__.py` | Package marker |
| `src/bcs/quality/__init__.py` | Package marker |
| `src/bcs/scraper/__init__.py` | Package marker |
| `requirements.txt` | All Python dependencies |
| `src/bcs/pipeline/main_pipeline.py` | Pipeline entry point — wires KG loading, intent building, fact retrieval, MCQ generation, episodic memory |
| `.env` | Copied from `.env.example` |

## Files Modified

| File | Change |
|---|---|
| `run_pipeline.py` | Added `sys.argv[0]` override for argparse |
| `src/bcs/generators/mcq_generator.py` | 1) `facts_from_kg()` no longer filters out unscored facts (defaults to accepting if no quality_verdict set). 2) Replaced unicode log chars (`→`, `──`, `✓`, `✗`) with ASCII-safe alternatives for Windows |
| `src/bcs/pipeline/intent_builder.py` | Fixed self-test import: `from input_normalizer` → `from bcs.pipeline.input_normalizer` |
| `src/bcs/pipeline/fact_quality.py` | Fixed self-test import: `from kg_builder` → `from bcs.pipeline.kg_builder` |
| `src/bcs/scraper/web_scraper.py` | Fixed self-test import: `from input_normalizer` → `from bcs.pipeline.input_normalizer` |
| `src/bcs/quality/bcs_metrics.py` | Fixed self-test imports: `from kg_builder/fact_quality` → `from bcs.pipeline.kg_builder/fact_quality` |

## Critical Blockers Resolved

1. **`logging_config.py` missing** — 6 modules import `from bcs.logging_config import get_logger`. Created with UTF-8 stream wrapper to prevent UnicodeEncodeError on Windows.
2. **No `__init__.py` files** — Python packages not recognized. Created all 5.
3. **`main_pipeline.py` missing** — `run_pipeline.py` referenced it but it didn't exist. Created full pipeline orchestrator.
4. **No `requirements.txt`** — Dependencies were scattered. Now centralized.
5. **Self-test imports used wrong paths** — 4 files used relative imports that fail when run from project root. Fixed to absolute.

## Functional Issues Fixed

1. **Unicode crash on Windows** — Log handler wrapped with UTF-8 `TextIOWrapper` to handle Bangla and special chars on cp1252 console.
2. **Facts filtered by quality gate** — `facts_from_kg()` in `mcq_generator.py` defaulted `quality_verdict` to `"REJECT"` and `mcq_readiness` to `0.0`, causing all freshly-loaded facts to be silently dropped. Changed defaults to accept unscored facts.
3. **Pipeline crash on missing API key** — Added try/except around `MCQGenerator.generate_from_facts()` with graceful JSON error response instead of stack trace.
4. **Topic display mismatch** — Pipeline now shows the resolved topic (user input fallback) instead of the raw blueprint topic.

## Verification Results

- All imports pass clean
- KG loads **1,953 nodes / 1,113 facts** from data files
- Topic resolution works: `History` → 98 facts, `Geography` → 16 facts, `Culture` → 22 facts
- CLI works: `python run_pipeline.py --topic "History" --difficulty medium --count 1`
- Pipeline returns clean JSON on both success and error paths

## What's Still Needed

- **Valid API key** — set `CRAFTX_API_KEY` or `HF_API_KEY` in `.env` (current key returns 401)
- Phase A2: Error handling layer, caching, Prometheus monitoring, config.yaml, unit tests
- Phase A3: FastAPI wrapper, Dockerfile
- Phase A4: Web UI, user feedback loop
