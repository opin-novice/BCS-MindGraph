import threading
import time
from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from bcs.api.schemas import (
    GenerateRequest, GenerateResponse, MCQOut, OptionOut, TopicOut, HealthResponse,
)
from bcs.pipeline.main_pipeline import Pipeline
from bcs.generators.mcq_generator import facts_from_kg

router = APIRouter()

mcq_generated = Counter("mcq_generated_total", "Total MCQs generated")
mcq_accepted = Counter("mcq_accepted_total", "Total MCQs accepted by judge")
pipeline_duration = Histogram("pipeline_duration_seconds", "Pipeline run duration", buckets=(1, 5, 10, 30, 60, 120, 300))

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
def health():
    p = get_pipeline()
    kg_nodes = p.kg.graph.number_of_nodes()
    kg_facts = len([n for n, d in p.kg.graph.nodes(data=True) if d.get("type") == "FACT"])
    memory_size = len([e for e in p.memory.get_high_performing_topics()])
    return HealthResponse(
        status="ok",
        kg_nodes=kg_nodes,
        kg_facts=kg_facts,
        memory_size=memory_size,
        model="llama-3.1-8b-instant",
    )


@router.get("/topics", response_model=List[TopicOut])
def topics():
    p = get_pipeline()
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


@router.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    p = get_pipeline()
    t0 = time.time()
    try:
        result = p.run(topic=req.topic, difficulty=req.difficulty, count=req.count, max_facts=req.max_facts)
        mcqs = []
        for m in result["mcqs"]:
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
            ))
        elapsed = time.time() - t0
        pipeline_duration.observe(elapsed)
        mcq_generated.inc(len(mcqs))
        for m in mcqs:
            if m.quality_score >= 0.7:
                mcq_accepted.inc()

        grounding_facts = result.get("mcqs", [])[:3] if result.get("mcqs") else []
        return GenerateResponse(
            mcqs=mcqs,
            topic=result["topic"],
            difficulty=result["difficulty"],
            count=result["count"],
            error=result.get("error"),
            kg_size=result.get("kg_size"),
            memory_size=result.get("memory_size"),
            generation_duration_ms=int(elapsed * 1000),
            grounding_facts=[m.get("explanation", "") for m in result.get("mcqs", [])[:3]] if result.get("mcqs") else None,
        )
    except Exception as e:
        elapsed = time.time() - t0
        pipeline_duration.observe(elapsed)
        raise HTTPException(status_code=500, detail=str(e)[:500])
