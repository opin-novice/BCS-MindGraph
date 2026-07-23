import argparse
import json
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

from bcs.pipeline.input_normalizer import InputNormalizer
from bcs.pipeline.intent_builder import IntentBuilder
from bcs.pipeline.kg_builder import KnowledgeGraphBuilder
from bcs.pipeline.episodic_store import EpisodicMemory
from bcs.generators.mcq_generator import MCQGenerator, facts_from_kg
from bcs.logging_config import get_logger

SNAPSHOT_PATH = "runtime/kg_snapshot.gpickle"

log = get_logger(__name__)


def load_data_to_kg(kg: KnowledgeGraphBuilder, data_dir: str = "data"):
    data_path = Path(data_dir)
    if not data_path.exists():
        log.warning("Data directory '%s' not found — KG will be empty.", data_dir)
        return

    for json_file in data_path.glob("*.json"):
        log.info("Loading data from %s ...", json_file)
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            log.warning("Failed to load %s: %s", json_file, e)
            continue

        if isinstance(raw, dict):
            records = raw.get("questions", raw.get("facts", [raw]))
        elif isinstance(raw, list):
            records = raw
        else:
            records = []

        for record in records:
            text = (
                record.get("fact_text")
                or record.get("text")
                or record.get("fact")
                or record.get("question_bn")
                or record.get("question", "")
            )
            topic = record.get("topic", "General")
            if not text:
                continue

            subject_entities = record.get("subject_entities", [])
            object_entities = record.get("object_entities", [])

            kg.insert_fact_pipeline(
                fact_text=text,
                subject_entities=subject_entities,
                object_entities=object_entities,
                topic=topic,
                source_url=record.get("source_url", ""),
                publisher=record.get("publisher", ""),
            )

    log.info("KG loaded: %d nodes, %d facts",
             kg.graph.number_of_nodes(),
             len([n for n, d in kg.graph.nodes(data=True) if d.get("type") == "FACT"]))


class Pipeline:
    def __init__(
        self,
        hf_api_key: Optional[str] = None,
        db_path: str = "runtime/memory.db",
        data_dir: str = "data",
    ):
        if hf_api_key is None:
            hf_api_key = os.getenv("GROQ_API_KEY") or os.getenv("HF_API_KEY")

        self.normalizer = InputNormalizer()
        self.intent_builder = IntentBuilder(hf_api_key=hf_api_key)
        self.kg = KnowledgeGraphBuilder()
        self.memory = EpisodicMemory(db_path=db_path)

        if os.path.exists(SNAPSHOT_PATH):
            self.kg.load_snapshot(SNAPSHOT_PATH)
        else:
            load_data_to_kg(self.kg, data_dir)

        self.mcq_gen = MCQGenerator(
            hf_api_key=hf_api_key or "",
            seen_questions_path="runtime/seen_questions.json",
        )

    def run(
        self,
        topic: str,
        difficulty: str = "medium",
        count: int = 1,
        max_facts: int = 5,
    ) -> dict:
        log.info("Pipeline run: topic=%s difficulty=%s count=%d", topic, difficulty, count)

        normalized = self.normalizer.normalize(topic)
        blueprint = self.intent_builder.build_blueprint(normalized)

        resolved_topic = blueprint.topic
        facts = facts_from_kg(self.kg, resolved_topic)
        if not facts:
            resolved_topic = topic
            facts = facts_from_kg(self.kg, resolved_topic)
        if not facts:
            log.warning("No facts found for topic '%s' — falling back to all facts.", topic)
            all_facts = []
            for nid, data in self.kg.graph.nodes(data=True):
                if data.get("type") == "FACT":
                    topic_name = "General"
                    for _, tgt, edata in self.kg.graph.edges(nid, data=True):
                        if edata.get("relation") == "ABOUT":
                            topic_name = tgt.replace("TOPIC_", "").title()
                            break
                    all_facts.append({
                        "fact_id": nid,
                        "text": data.get("text", ""),
                        "topic": topic_name,
                        "mcq_suitable_for": data.get("mcq_suitable_for", ["factual"]),
                        "mcq_readiness": data.get("mcq_readiness", 0.5),
                        "composite_score": data.get("composite_score", 0.0),
                        "source_reliability": data.get("source_reliability", 0.5),
                    })
            facts = all_facts

        if not facts:
            return {
                "mcqs": [],
                "topic": blueprint.topic,
                "difficulty": difficulty,
                "count": 0,
                "error": "No facts found in KG for the given topic.",
                "kg_size": self.kg.graph.number_of_nodes(),
                "memory_size": 0,
            }

        results = []
        for _ in range(count):
            try:
                result = self.mcq_gen.generate_from_facts(facts, difficulty=difficulty, topic=resolved_topic, max_facts=max_facts)
            except Exception as e:
                log.error("MCQ generation failed: %s", str(e)[:200])
                return {
                    "mcqs": [],
                    "topic": blueprint.topic,
                    "difficulty": difficulty,
                    "count": 0,
                    "error": f"MCQ generation failed: {str(e)[:200]}",
                    "kg_size": self.kg.graph.number_of_nodes(),
                    "memory_size": 0,
                }
            if result.mcqs:
                episode_id = self.memory.write_episode(
                    input_question=topic,
                    intent=blueprint.intent,
                    blueprint="single_correct_answer",
                    topic=blueprint.topic,
                    fact_ids=result.fact_ids,
                    mcqs=[m.to_episode_dict() for m in result.mcqs],
                    overall_score=result.overall_score,
                    accepted=int(result.accepted),
                    generation_config=result.generation_config,
                )
                log.info("Episode written: %s", episode_id)
            results.append(result)

        mcqs_output = []
        for r in results:
            for m in r.mcqs:
                mcqs_output.append({
                    "question": m.question,
                    "options": [f"{o.key}) {o.text}" for o in m.options],
                    "correct_answer": m.correct_answer,
                    "difficulty": m.difficulty,
                    "quality_score": m.quality_score,
                    "explanation": m.explanation,
                })

        return {
            "mcqs": mcqs_output,
            "topic": resolved_topic,
            "difficulty": difficulty,
            "count": len(mcqs_output),
            "kg_size": self.kg.graph.number_of_nodes(),
            "memory_size": len([e for e in self.memory.get_high_performing_topics()]),
        }

    def close(self):
        self.mcq_gen.save_seen_questions()
        self.memory.close()
        try:
            os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
            snapshot = {"graph": self.kg.graph, "topic_stats": dict(getattr(self.kg, "topic_stats", {}))}
            import pickle
            with open(SNAPSHOT_PATH, "wb") as f:
                pickle.dump(snapshot, f)
            log.info("KG snapshot saved to %s", SNAPSHOT_PATH)
        except Exception as exc:
            log.warning("KG snapshot save failed: %s", str(exc)[:100])


def main():
    parser = argparse.ArgumentParser(description="BCS Batighor — MCQ Generation Pipeline")
    parser.add_argument("--topic", type=str, default="Bangladesh Geography",
                        help="Topic for MCQ generation")
    parser.add_argument("--difficulty", type=str, default="medium",
                        choices=["easy", "medium", "hard"],
                        help="Difficulty level")
    parser.add_argument("--count", type=int, default=1,
                        help="Number of MCQs to generate")
    parser.add_argument("--data-dir", type=str, default="data",
                        help="Directory containing JSON data files")
    parser.add_argument("--db-path", type=str, default="runtime/memory.db",
                        help="Path to episodic memory database")
    args = parser.parse_args()

    hf_api_key = os.getenv("GROQ_API_KEY") or os.getenv("HF_API_KEY")
    if not hf_api_key:
        log.warning("No GROQ_API_KEY found. Set it in .env file.")

    pipeline = Pipeline(
        hf_api_key=hf_api_key,
        db_path=args.db_path,
        data_dir=args.data_dir,
    )

    try:
        result = pipeline.run(
            topic=args.topic,
            difficulty=args.difficulty,
            count=args.count,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
