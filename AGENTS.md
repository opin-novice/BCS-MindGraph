# BCS-MindGraph Build Plan

## Completed
- Repo structure, pipeline MVP (KG, intent, MCQ gen, CRJ, episodic memory)
- CRJ loop with graceful degradation
- KG persistence (save/load snapshot)
- Thread safety, CORS, Prometheus, file logging
- Docker HEALTHCHECK, .dockerignore, PRAGMA busy_timeout
- User feedback mechanism (P0)
- Unit tests + CI/CD (76 tests)
- config.yaml loading, model from env
- Caching layer (LLM + KG)

## Next — Production Building (in order)

### 1. Structured JSON logging
- Add structured JSON logging for pipeline runs
- Log format: `{pipeline_run_id, stage, metrics, execution_time_ms, failure_mode}`
- Write to `runtime/pipeline_log.jsonl`
- Enables monitoring dashboard and research data collection

### 2. Web scraper → KG integration
- Connect `pipeline/web_scraper.py` into main pipeline
- When KG has < min_facts for a topic, trigger web search → FQG → insert
- Core differentiator: dynamic KG expansion

### 3. Frontend polish
- Feedback stats dashboard
- MCQ history browser
- Mobile responsiveness, loading states

### 4. Deployment hardening
- Prometheus + Grafana compose stack
- Volume auto-creation
- Graceful shutdown handling
