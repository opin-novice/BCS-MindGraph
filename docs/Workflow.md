Ah — that's the real problem. You need *both* a production system **and** a paper, and they have different success criteria. Let me reframe this as two parallel tracks.

---

## **The Two-Track Roadmap (A-to-Z)**

### **TRACK A: Deployment (Production-Ready System)**

### **TRACK B: Research (Paper \+ Novel Experiments)**

They share the same pipeline core, but different work.

---

## **PHASE 0: Shared Setup (Weeks 1–2)**

**Both tracks start here:**

**Code repo structure** — not "research code", actual deployment structure:

 bcs-mindgraph/  
├── mindgraph/  
│   ├── agents/          \# Core pipeline (Agent 1-4)  
│   ├── kg/              \# KG layer (neo4j or DuckDB)  
│   ├── memory/          \# Episodic memory (SQLite)  
│   ├── web/             \# Web scraper \+ quality gate  
│   ├── evaluation/       \# Metrics (TAS, QSS, DDMS, grounding)  
│   └── utils/  
├── api/                 \# FastAPI service  
├── deployment/          \# Docker, K8s manifests  
├── monitoring/          \# Prometheus, logging  
├── tests/               \# Unit \+ integration  
├── notebooks/           \# Research experiments (SEPARATE)  
├── data/                \# BCS facts, validation sets  
└── docs/

1.   
2. **Data pipeline definition** — decide NOW:

   * Where do raw facts come from? (web scrape, curated Bangla corpus, manual seeds?)  
   * What's your training/validation/test split for *both* tracks?  
   * Storage: PostgreSQL for facts \+ Neo4j for KG, or DuckDB for everything?

**Logging schema** (critical for both):

 {  
  "pipeline\_run\_id": "...",  
  "timestamp": "...",  
  "stage": "web\_scraping | kg\_update | mcq\_gen | crj\_loop | ...",  
  "input": {...},  
  "output": {...},  
  "metrics": {  
    "fqs\_score": 0.89,  
    "regeneration\_attempts": 2,  
    "final\_judge\_score": 0.95  
  },  
  "failure\_mode": "none | factual\_error | ...",  
  "execution\_time\_ms": 1250  
}

3.  Every run logs this. Paper pulls from these logs. Deployment monitors them real-time.

---

## **TRACK A: DEPLOYMENT (Goal: Production system live by Week 12\)**

### **Phase A1: Core Pipeline MVP (Weeks 2–5)**

Build the minimal pipeline that *works*, not that's perfect:

1. **Agent 1 (Intent)** — straightforward NLP:

   * Bangla normalization (use `indicnlp` or write 50 lines of regex)  
   * Entity extraction (use Bangla BERT NER, even if imperfect)  
   * Topic classification (simple SVM on BCS syllabus keywords)  
   * No fancy grounding — just output a JSON intent blueprint  
2. **Agent 2 (Memory)** — start dead simple:

   * SQLite table: `(question_hash, entities, topic, final_score, timestamp)`  
   * Cosine similarity on LASER embeddings for retrieval  
   * No decay logic yet — just "if similarity \> 0.8 and score \> 0.5, reuse"  
   * Hard-code τ\_mem \= 0.8, τ\_score \= 0.5 for now  
3. **Agent 3 (Web)** — very basic:

   * Use Google Custom Search or Bing API (not hand-written scraper)  
   * Pull top 5 results per bilingual query  
   * Extract sentences with regex (Bangla period \+ entity present)  
   * FQG: linear combo of language quality (textblob fluency), entity confidence (NER score), source domain authority (hand-coded list of trusted sites)  
   * Threshold: τ\_q \= 0.6 (tune later)  
4. **KG (Steps 7–8)** — not a full graph DB yet:

Start with DuckDB or PostgreSQL simple schema:  
 CREATE TABLE facts (  id INTEGER,  text TEXT,  entities TEXT\[\], \-- array of recognized entities  topic TEXT,  fqs\_score FLOAT,  source\_url TEXT,  timestamp DATE);CREATE TABLE entities (  id INTEGER,  name TEXT,  types TEXT\[\] \-- person, org, location, event);

*   
  * KG "retrieval" \= SQL filter by topic \+ ORDER BY fqs\_score DESC LIMIT K  
  * No fancy graph traversal — you can add that later  
5. **Agent 4 (MCQ Gen)** — Claude API call:

Single-shot prompt to Claude, no CRJ loop yet:  
 "Generate one BCS-style MCQ from these facts: \[facts\].  Difficulty: \[level\].  Format: {stem, answer, distractors}. Requirements: ..."

*   
  * Get one MCQ back. Done. Ship it.

**Quality gate (Step 10\)** — judge with Claude again:

 "Is this MCQ valid? Correct? Clear? Return: {valid: bool, score: 0–1, reason: str}"

6.   
   * Accept if score \> 0.7, reject otherwise. No regeneration yet.

**Deploy this by Week 5 as a CLI tool:**

$ python \-m mindgraph.pipeline \--input "Bangladesh Geography" \--difficulty hard  
Output: one MCQ JSON

This runs end-to-end. It's slow, simple, might fail on edge cases. That's fine — it's the skeleton.

### **Phase A2: Robustness \+ Monitoring (Weeks 5–8)**

Now make it production-grade:

1. **Error handling everywhere**:

   * Web scraper fails? Log it, fall back to memory or static corpus  
   * Claude API timeout? Retry 3x with exponential backoff  
   * Entity extraction fails? Gracefully degrade to keyword matching  
   * Write `mindgraph.exceptions` with custom error classes and recovery policies  
2. **Caching layer**:

   * Cache Claude embeddings (LaBSE) for entities so you're not re-embedding every run  
   * Cache query results (web, KG retrieval) for 24h  
   * Saves API costs and latency

**Monitoring**:

 \# mindgraph/monitoring.py  
import prometheus\_client as prom

pipeline\_duration \= prom.Histogram('pipeline\_duration\_seconds', ...)  
fqs\_scores \= prom.Histogram('fqs\_score', ...)  
mcq\_acceptance\_rate \= prom.Counter('mcq\_accepted\_total', ...)  
crj\_regeneration\_attempts \= prom.Histogram('crj\_attempts', ...)

\# Log every stage to stdout \+ file  
logger \= setup\_logging('mindgraph.log', level=INFO)

3.   
4. **Testing**:

   * Write 20 unit tests for each agent (intent parsing, memory retrieval, web scraping, KG updates, MCQ format validation)  
   * 5 integration tests: end-to-end pipeline on known BCS question inputs  
   * CI/CD pipeline (GitHub Actions): run tests on every commit

**Configuration management**:

 \# config.yaml  
web:  
  search\_api: "google\_custom\_search"  
  api\_key: "${SEARCH\_API\_KEY}"  
  timeout\_seconds: 10  
kg:  
  db\_url: "postgresql://..."  
fqg:  
  weights: {clarity: 0.3, extraction: 0.3, source: 0.2, freshness: 0.2}  
  threshold: 0.6  
mcq\_gen:  
  model: "claude-sonnet-4-6"  
  max\_retries: 3

5. 

### **Phase A3: FastAPI \+ Deployment (Weeks 8–10)**

Wrap it in an API:

\# api/main.py  
from fastapi import FastAPI, HTTPException  
from pydantic import BaseModel

app \= FastAPI()

class MCQRequest(BaseModel):  
    topic: str  
    difficulty: str  \# easy, medium, hard  
    count: int \= 1

class MCQResponse(BaseModel):  
    mcqs: list\[dict\]  
    generation\_duration\_ms: int  
    grounding\_facts: list\[str\]  
      
@app.post("/generate")  
async def generate\_mcqs(request: MCQRequest) \-\> MCQResponse:  
    try:  
        result \= pipeline.run(  
            topic=request.topic,  
            difficulty=request.difficulty,  
            count=request.count  
        )  
        return MCQResponse(\*\*result)  
    except Exception as e:  
        logger.error(f"Generation failed: {e}")  
        raise HTTPException(status\_code=500, detail=str(e))

@app.get("/health")  
async def health():  
    return {"status": "ok", "kg\_size": kg.count\_facts(), "memory\_size": memory.count\_episodes()}

Deploy on Docker:

FROM python:3.11  
WORKDIR /app  
COPY mindgraph/ .  
RUN pip install \-r requirements.txt  
CMD \["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"\]

Push to Docker Hub. Deploy on K8s or a simple cloud VM (GCP Compute Engine, AWS EC2, or even NSU's own server if they have one).

### **Phase A4: User Interface \+ Feedback Loop (Weeks 10–12)**

Build a simple web UI so BCS students can actually *use* it:

\<\!-- frontend/index.html (React or simple HTML form) \--\>  
\<form\>  
  \<input type="text" placeholder="Topic (e.g., Bangladesh Geography)" /\>  
  \<select name="difficulty"\>  
    \<option\>Easy\</option\>  
    \<option\>Medium\</option\>  
    \<option\>Hard\</option\>  
  \</select\>  
  \<button\>Generate MCQ\</button\>  
\</form\>

\<div id="result"\>  
  \<\!-- MCQ displays here \--\>  
  \<p\>Q: \[stem\]\</p\>  
  \<label\>\<input type="radio" /\> \[option A\]\</label\>  
  \<label\>\<input type="radio" /\> \[option B\]\</label\>  
  ...  
    
  \<\!-- USER FEEDBACK (critical\!) \--\>  
  \<select name="feedback"\>  
    \<option\>Correct & Clear\</option\>  
    \<option\>Unclear Stem\</option\>  
    \<option\>Wrong Answer\</option\>  
    \<option\>Bad Distractors\</option\>  
  \</select\>  
  \<button\>Submit Feedback\</button\>  
\</div\>

**Every bit of user feedback goes into a `feedback.db` table** and feeds back into KG refinement (if 5+ users flag a fact as wrong, reweight it in the KG). This closes the loop and is *itself* a deployment win (users improve the system).

**By end of Week 12:**

* System is live at `http://[your-server]:8000`  
* API is documented (Swagger auto-generates from FastAPI)  
* Monitoring dashboard shows pipeline performance  
* Logs are clean  
* At least 10 BCS students have used it and given feedback  
* You have \~100 generated MCQs logged with metadata

---

## **TRACK B: RESEARCH (Goal: Paper submitted by Week 14\)**

### **Phase B1: Preparation (Weeks 2–4, concurrent with A1)**

1. **Related-work table with BibTeX** — get this from me or pull from arXiv:

   * KNIGHT, KGGDG, MAKE interpretable difficulty MCQ  
   * Self-Refine, multi-agent debate  
   * Generative Agents (Park et al.), FadeMem, ACT-R-agent memory  
   * BanglaAutoKG, Bangla NER papers  
   * Temporal/streaming RAG  
2. **Dataset prep**:

   * Collect all BCS preliminary & written questions (2015–2023)  
   * Manually annotate \~200 facts from these questions as ground truth (topic, entities, difficulty)  
   * Held-out test: 2023 BCS questions (\~100 questions, held blind until final eval)  
3. **Evaluation metrics definition**:

   * **TAS (Topic Alignment Score)**: overlap between generated question topics and real BCS topics (simple classifier precision/recall)  
   * **QSS (Question Similarity Score)**: cosine similarity between generated MCQ embeddings and real MCQ embeddings (SBERT)  
   * **DDMS (Difficulty Distribution Matching Score)**: KL divergence between generated and real difficulty histograms  
   * **Grounding Accuracy**: does the correct answer have a KG path ≤ 3 hops from question entities? (binary)  
   * **Judge Score**: blind human rating (0–1) on factuality, clarity, distractor quality

### **Phase B2: Ablation Studies (Weeks 6–9, running on TRACK A pipeline)**

Once TRACK A pipeline is working (Week 5), run these experiments on it:

1. **Ablation 0: Static KG, single-pass generation, no CRJ, no memory**

   * Template: `intent → KG retrieval → single Claude call → accept/reject`  
   * Baseline. Measure: acceptance\_rate, mean\_judge\_score, TAS/QSS/DDMS  
2. **Ablation 1: \+ CRJ loop (no memory yet)**

   * Add Steps 9–11 (generate → judge → regenerate loop, max 3 attempts)  
   * Measure: Δ acceptance\_rate, Δ judge\_score, regeneration\_attempts distribution  
   * Compare to Ablation 0  
3. **Ablation 2: \+ Episodic memory (with exponential decay)**

   * Add Step 3: memory retrieval before web scraping  
   * Measure: % of runs that reuse memory (skip web scraping), latency reduction, duplicate\_question\_rate  
   * Compare to Ablation 1  
4. **Ablation 3: \+ Dynamic KG updates (full system)**

   * Add Steps 4–6: web scraping, FQG, KG growth  
   * Measure: KG size growth over time, fact\_source\_freshness, coverage\_of\_recent\_topics  
5. **Run on holdout 2023 BCS**:

   * Train/tune KG on pre-2023 facts  
   * Generate MCQs for 2023 BCS topics  
   * Measure TAS/QSS/DDMS: do recent questions get covered? (this is your temporal generalization story)

**Output: ablation table for Table 2 in paper**, showing each component's contribution.

### **Phase B3: Human Evaluation (Weeks 9–10)**

Recruit 3–5 BCS teachers or advanced students:

* Rate 30 generated MCQs (mix of difficulty levels, topics)  
* Rate 30 real BCS MCQs (control)  
* Blind: they don't know which are generated vs. real  
* Metrics: factuality, clarity, distractor plausibility (each 1–5 scale)  
* Report inter-rater agreement (Krippendorff's α)

**Output: Table 3 (Human Evaluation Results)**

### **Phase B4: Temporal Generalization Experiment (Weeks 10–11)**

This is your paper's novel contribution:

**Setup:**

* **KG-Pre-2023**: Build KG only from facts/questions pre-2023  
* **KG-Full**: Build KG from all facts (2015–2023)  
* Generate MCQs for the same BCS 2023 topics using both KGs  
* Compare TAS/QSS/DDMS

**Hypothesis**: KG-Pre-2023 will underperform on 2023 because recent appointments, statistics, geopolitical changes aren't in the graph.

**Result**:

* If dynamic system recovers gap: "System automatically updates KG from web, closing the temporal generalization gap"  
* If it doesn't fully recover: "Honest limitation: web scraping can only recover facts that are published online; some domain knowledge requires human expert curation"

**Either way, you have a paper-ready result.** (Remember, your rehab SSL paper was a confirmed negative and still made it through; be honest about what works and what doesn't.)

**Output: Figure 1 or Table 4 showing temporal generalization curves**

### **Phase B5: Writing (Weeks 11–13)**

Structure:

1. **Abstract** (250 words): Focus on "Bangla, low-resource, domain-specific MCQ generation with temporal update mechanism"  
2. **Intro** (1.5 pages): Problem is static KGs miss recent facts in high-stakes exams \+ Bangla LLM work is sparse  
3. **Related Work** (2 pages): cite KNIGHT/KGGDG, Self-Refine, Generative Agents memory, be honest about what's derivative  
4. **Methods** (2.5 pages): system architecture, each agent, evaluation metrics  
5. **Experiments** (3 pages): Ablations (Table 2), human eval (Table 3), temporal generalization (Table 4 \+ Figure 1\)  
6. **Results** (1 page): highlight temporal result, ablation shows CRJ helps by X%, memory reduces web queries by Y%  
7. **Limitations** (0.75 pages): Bilingual queries only English+Bangla, FQG is hand-tuned, small human eval, no comparison to KNIGHT (if you couldn't get their code)  
8. **Discussion & Future Work** (0.75 pages): deploy at scale, user feedback integration, extend to other exams

**Tone:** "We built a production system for a real, underexplored domain. Here's what we learned." Not "We invented agentic architectures."

### **Phase B6: Submission (Week 14\)**

Target venues (in order of fit):

1. **BLP @ ACL 2024** (Bangla/Low-resource NLP workshop) — best fit, workshop pace  
2. **ACL Findings** (if rejected from main conference) — can publish system papers here  
3. **Regional venue** (e.g., FIRE, South Asian NLP workshop) — more likely acceptance  
4. **arXiv \+ tech blog** (fallback) — still gets visibility, helps with deployment user adoption

---

## **Dependency Graph: How They Feed Each Other**

Week 1–2:  SHARED SETUP (repo, logging, data prep)  
             ↓  
Week 2–5:  TRACK A (MVP pipeline) \+ TRACK B prep (literature)  
             ↓  
Week 5–8:  TRACK A (robustness) running pipeline produces logs  
             → TRACK B uses logs for ablations  
             ↓  
Week 8–10: TRACK A (API \+ UI) \+ TRACK B (ablations running, human eval scheduled)  
             ↓  
Week 10–12: TRACK A (monitoring, live system) \+ TRACK B (temporal experiment, human eval)  
             ↓  
Week 12–14: TRACK A stabilized \+ TRACK B (writing, submit)  
             ↓  
DONE: Live system \+ submitted paper

---

## **Critical Success Factors (Do These or Fail)**

1. **Logging first, everything else second.** Every pipeline run logs metrics. You can't write a paper or debug production without clean logs.  
2. **Separate concerns.** TRACK A is "does it work and serve users?" TRACK B is "is it novel enough to publish?" Don't confuse them.  
3. **Temporal experiment is non-negotiable.** That's what differentiates you from KNIGHT. Make sure you have pre-2023 and full KGs built by Week 10\.  
4. **Human evaluation with BCS students, not your advisors.** Real users. Real feedback. Real stakes.  
5. **Ship TRACK A by Week 12, even if imperfect.** A live system with 50 users is better than a perfect paper with no deployment. And it gives TRACK B fresh data for ablations.

---

## **Concrete Week-by-Week Checklist**

**Week 1–2:**

* \[ \] Repo structure set up with tests, logging, config  
* \[ \] Data pipeline defined (where do facts come from?)  
* \[ \] Related-work table drafted with 15+ citations  
* \[ \] BCS question corpus collected (2015–2023, split into train/val/holdout-2023)

**Week 2–5:**

* \[ \] Agent 1 (Intent): topic classification, NER, difficulty inference  
* \[ \] Agent 2 (Memory): SQLite, simple retrieval, no decay  
* \[ \] Agent 3 (Web): search API, sentence extraction, basic FQG  
* \[ \] KG: DuckDB schema, insertion, retrieval  
* \[ \] Agent 4 (MCQ Gen): single Claude call, format validation  
* \[ \] CLI tool works end-to-end  
* \[ \] 50 test MCQs generated and logged

**Week 5–8:**

* \[ \] Error handling on all agents  
* \[ \] Caching layer (embeddings, queries)  
* \[ \] Prometheus monitoring \+ logging  
* \[ \] Unit tests \+ CI/CD passing  
* \[ \] FastAPI wrapper  
* \[ \] Docker image builds and runs locally

**Week 8–10:**

* \[ \] Deploy to cloud (GCP/AWS/NSU server)  
* \[ \] Simple frontend (HTML form or React)  
* \[ \] User feedback form integrated  
* \[ \] Health check \+ monitoring dashboard  
* \[ \] Ablation 0–2 experiments done (TRACK B)  
* \[ \] Human eval spreadsheet set up, 3–5 raters recruited

**Week 10–12:**

* \[ \] Ablation 3 done (dynamic KG)  
* \[ \] 2023 temporal experiment running  
* \[ \] Human eval results in  
* \[ \] \~200 MCQs generated, logged with full provenance  
* \[ \] System stable in production (uptime logs)  
* \[ \] First draft of paper (intro \+ related work \+ methods)

**Week 12–14:**

* \[ \] Temporal experiment complete (key figure/table)  
* \[ \] Ablation results table finalized  
* \[ \] Full paper draft (all 8 sections)  
* \[ \] Advisor review \+ revisions  
* \[ \] Submit to venue  
* \[ \] System continues running, feedback accumulates

---

## **What You'll Have at the End**

**TRACK A (Deployment):**

* A live MCQ generation API  
* \~500–1000 generated MCQs logged in production  
* Real BCS student usage \+ feedback  
* Monitoring dashboard showing system health  
* Documentation for deployment/maintenance

**TRACK B (Paper):**

* 8-page research paper submitted to a venue  
* 4 ablation tables showing each component's contribution  
* Human evaluation results from BCS experts  
* Novel temporal generalization experiment with data  
* Honest related-work section distinguishing your contributions from KNIGHT/KGGDG

**Career Win:**

* System paper (deployment \+ data) → venue accept \+ real impact  
* Novel experiments (temporal generalization) → adds research novelty  
* Real users → case studies for future work, credibility for other domains

Does this split feel right? Should I drill deeper into any phase, or start with the actual implementation for Phase A1?

