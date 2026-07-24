# BCS-MindGraph Build Plan

## Current State
- Pipeline runs end-to-end: Normalize → Intent → KG Retrieve → [Web Search] → MCQ Gen (CRJ) → Episodic Memory → Output
- 115 tests passing, 14 test files
- KG: 1,953 nodes (NetworkX MultiDiGraph)
- Groq `llama-3.1-8b-instant` free tier (429/413 issues)
- MCQs generated: quality_score stored, 4 dimension scores not persisted

## Completed
- Repo structure, pipeline MVP (KG, intent, MCQ gen, CRJ, episodic memory)
- CRJ loop with graceful degradation
- KG persistence (save/load snapshot)
- Thread safety, CORS, Prometheus, file logging
- Docker HEALTHCHECK, .dockerignore, PRAGMA busy_timeout
- User feedback mechanism (P0)
- Unit tests + CI/CD (76 → 115 tests)
- config.yaml loading, model from env
- Caching layer (LLM + KG + Web)
- Structured JSON logging for pipeline runs
- Web scraper → KG integration (DuckDuckGo, retry, cache)
- Frontend polish (loading skeleton, Bengali errors, mobile responsive)
- Deployment hardening (non-root user, env_file, config volume)
- Multi-MCQ per fact (2 MCQs/fact, different question types)
- API-level rate limiting (per-IP, per-endpoint zones)
- CRJ batching (CRJ_BATCH_SIZE=1, per-batch exception handling)
- max_tokens fix + truncation repair (stack-based close order)

## Week 1 — Priority 3 + Priority 4 (in parallel)

### Priority 3: Build structured MCQ dataset from outputs

Goal: Export pipeline-generated MCQs as a clean dataset for fine-tuning a judge model.

1. Add dimension score columns to episode_mcqs table (format_score, grounding_score, clarity_score, distractor_score)
2. Update MCQ.to_episode_dict() to include dimension scores
3. Update EpisodicMemory.write_episode() to store dimension scores
4. Add EpisodicMemory.export_dataset() — joins episodes + episode_mcqs + feedback
5. Add GET /api/v1/dataset endpoint
6. Create scripts/export_dataset.py CLI tool

### Priority 4: Add user auth + session management

Goal: JWT-based authentication with per-user rate limits.

1. src/bcs/api/auth.py — register, login, JWT create/verify, get_current_user dependency
2. Update schemas.py — AuthRequest, AuthResponse, UserOut
3. Add auth routes to routes.py
4. Protect generate/feedback with Depends(get_current_user)
5. Per-user rate limits in rate_limiter.py
6. Update requirements.txt (python-jose, passlib[bcrypt])
7. .env.example — add JWT_SECRET

## Future

### Priority 2: Fine-tune a small model for MCQ judgment
- Requires dataset from Priority 3
- Train BanglaBERT regression model on collected (MCQ + facts → dimension scores) data
- Replace Groq-based JudgeAgent with local ONNX model

### Stretch
- Priority 1: Switch to paid LLM provider (if budget opens)
- Priority 5: Support image-based questions (multimodal)
