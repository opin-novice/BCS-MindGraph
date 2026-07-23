import os
import threading
import time
from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path
from typing import List, Any, Dict

from bcs.logging_config import get_logger

log = get_logger(__name__)

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from bcs.api.schemas import (
    GenerateRequest, GenerateResponse, MCQOut, OptionOut, TopicOut, HealthResponse,
    FeedbackRequest, FeedbackResponse, FeedbackStatsResponse,
)
from bcs.pipeline.main_pipeline import Pipeline
from bcs.generators.mcq_generator import facts_from_kg
from bcs.generators.mcq_generator import DEFAULT_MODEL as MCQ_MODEL
from bcs.pipeline_logger import get_pipeline_logger

router = APIRouter()

mcq_generated = Counter("mcq_generated_total", "Total MCQs generated")
mcq_accepted = Counter("mcq_accepted_total", "Total MCQs accepted by judge")
pipeline_duration = Histogram("pipeline_duration_seconds", "Pipeline run duration", buckets=(1, 5, 10, 30, 60, 120, 300))
feedback_received = Counter("feedback_received_total", "Total user feedback entries", ["rating"])

_pipeline: Pipeline = None
_pipeline_lock = threading.Lock()


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                _pipeline = Pipeline(
                    data_dir=str(Path(__file__).resolve().parent.parent.parent.parent / "data"),
                    db_path=str(Path(__file__).resolve().parent.parent.parent.parent / "runtime" / "memory.db"),
                )
    return _pipeline


@router.get("/health", response_model=HealthResponse)
def health(p: Pipeline = Depends(get_pipeline)):
    kg_nodes = p.kg.graph.number_of_nodes()
    kg_facts = len([n for n, d in p.kg.graph.nodes(data=True) if d.get("type") == "FACT"])
    memory_size = len([e for e in p.memory.get_high_performing_topics()])
    return HealthResponse(
        status="ok",
        kg_nodes=kg_nodes,
        kg_facts=kg_facts,
        memory_size=memory_size,
        model=os.getenv("GROQ_MODEL", MCQ_MODEL),
    )


@router.get("/topics", response_model=List[TopicOut])
def topics(p: Pipeline = Depends(get_pipeline)):
    topic_map = {}
    for nid, data in p.kg.graph.nodes(data=True):
        if data.get("type") == "FACT":
            for _, tgt, edata in p.kg.graph.edges(nid, data=True):
                if edata.get("relation") == "ABOUT":
                    tname = tgt.replace("TOPIC_", "").title()
                    topic_map[tname] = topic_map.get(tname, 0) + 1
                    break
            else:
                topic_map["General"] = topic_map.get("General", 0) + 1
    return [TopicOut(topic=t, fact_count=c) for t, c in sorted(topic_map.items(), key=lambda x: -x[1])]


@router.get("/logs")
def get_logs(limit: int = 50):
    return get_pipeline_logger().get_recent_logs(limit=limit)


@router.get("/logs/{run_id}")
def get_run_logs(run_id: str):
    return get_pipeline_logger().get_run_logs(run_id)


@router.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest, p: Pipeline = Depends(get_pipeline)):
    if req.rating < 1 or req.rating > 5:
        raise HTTPException(status_code=422, detail="Rating must be between 1 and 5")
    feedback_id = p.memory.write_feedback(
        episode_id=req.episode_id,
        mcq_id=req.mcq_id,
        fact_ids=req.fact_ids,
        rating=req.rating,
        category=req.category,
        comment=req.comment,
    )
    feedback_received.labels(rating=str(req.rating)).inc()

    if req.rating <= 2 and req.fact_ids:
        for fid in req.fact_ids:
            node = p.kg.get_fact(fid)
            if node:
                current = node.get("composite_score", 0.5)
                penalty = 0.1 * (3 - req.rating)
                new_score = max(0.0, current - penalty)
                p.kg.update_fact_attribute(fid, "composite_score", new_score)
                p.kg.update_fact_attribute(fid, "user_feedback_low", req.rating)
                log.info("Fact %s composite_score adjusted: %.3f -> %.3f (rating=%d)", fid, current, new_score, req.rating)

    return FeedbackResponse(id=feedback_id, message="Feedback recorded")


@router.get("/feedback/stats", response_model=FeedbackStatsResponse)
def feedback_stats(p: Pipeline = Depends(get_pipeline)):
    return p.memory.get_feedback_stats()


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, p: Pipeline = Depends(get_pipeline)):
    t0 = time.time()
    try:
        result = p.run(topic=req.topic, difficulty=req.difficulty, count=req.count, max_facts=req.max_facts)
        mcqs = []
        raw_mcqs = result.get("mcqs", [])
        for i, m in enumerate(raw_mcqs):
            opts = []
            for o in m["options"]:
                key_text = o.split(") ", 1)
                opts.append(OptionOut(key=key_text[0].strip(), text=key_text[1].strip() if len(key_text) > 1 else key_text[0]))
            mcqs.append(MCQOut(
                question=m["question"],
                options=opts,
                correct_answer=m["correct_answer"],
                difficulty=m["difficulty"],
                quality_score=m.get("quality_score", 0.0),
                explanation=m.get("explanation", ""),
                mcq_id=m.get("mcq_id"),
                episode_id=m.get("episode_id"),
                fact_ids=m.get("fact_ids"),
            ))
        elapsed = time.time() - t0
        pipeline_duration.observe(elapsed)
        mcq_generated.inc(len(mcqs))
        for m in mcqs:
            if m.quality_score >= 0.7:
                mcq_accepted.inc()

        return GenerateResponse(
            mcqs=mcqs,
            topic=result["topic"],
            difficulty=result["difficulty"],
            count=result["count"],
            error=result.get("error"),
            kg_size=result.get("kg_size"),
            memory_size=result.get("memory_size"),
            generation_duration_ms=int(elapsed * 1000),
            grounding_facts=[m.get("explanation", "") for m in raw_mcqs[:3]] if raw_mcqs else None,
            pipeline_run_id=result.get("pipeline_run_id"),
        )
    except Exception as e:
        elapsed = time.time() - t0
        pipeline_duration.observe(elapsed)
        raise HTTPException(status_code=500, detail=str(e)[:500])
