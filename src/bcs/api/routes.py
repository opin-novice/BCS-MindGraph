from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List

from bcs.api.schemas import (
    GenerateRequest, GenerateResponse, MCQOut, OptionOut, TopicOut, HealthResponse,
)
from bcs.pipeline.main_pipeline import Pipeline
from bcs.generators.mcq_generator import facts_from_kg

router = APIRouter()

_pipeline: Pipeline = None


def get_pipeline() -> Pipeline:
    global _pipeline
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


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    p = get_pipeline()
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
        return GenerateResponse(
            mcqs=mcqs,
            topic=result["topic"],
            difficulty=result["difficulty"],
            count=result["count"],
            error=result.get("error"),
            kg_size=result.get("kg_size"),
            memory_size=result.get("memory_size"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])
