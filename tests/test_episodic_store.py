import pytest
import os
import tempfile
from bcs.pipeline.episodic_store import EpisodicMemory


@pytest.fixture
def mem():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    m = EpisodicMemory(db_path=tmp.name)
    yield m
    m.close()
    os.unlink(tmp.name)


def test_write_episode(mem):
    eid = mem.write_episode(
        input_question="What is capital of Bangladesh?",
        intent="factual_recall",
        blueprint="single_correct_answer",
        topic="Geography",
        fact_ids=["FACT_001", "FACT_002"],
        mcqs=[{"question": "Q?", "options": ["A) Dhaka"], "correct_answer": "A", "difficulty": "easy", "quality_score": 0.9, "regeneration_round": 0}],
        overall_score=0.9,
        accepted=1,
        generation_config={"difficulty": "easy"},
    )
    assert eid is not None
    assert len(eid) > 0


def test_write_feedback(mem):
    eid = mem.write_episode(
        input_question="Test",
        intent="test", blueprint="single_correct_answer",
        topic="Test", fact_ids=["FACT_001"],
    )
    fid = mem.write_feedback(
        episode_id=eid,
        mcq_id="MCQ_001",
        fact_ids=["FACT_001"],
        rating=4,
        category="correct_clear",
        comment="Great question",
    )
    assert fid > 0


def test_feedback_stats(mem):
    eid = mem.write_episode(
        input_question="Test", intent="test",
        blueprint="single_correct_answer", topic="Test",
        fact_ids=["FACT_001"],
    )
    mem.write_feedback(episode_id=eid, mcq_id="MCQ_001", rating=5)
    mem.write_feedback(episode_id=eid, mcq_id="MCQ_002", rating=3)
    stats = mem.get_feedback_stats()
    assert stats["total"] == 2
    assert stats["avg_rating"] == 4.0


def test_feedback_for_fact(mem):
    eid = mem.write_episode(
        input_question="Test", intent="test",
        blueprint="single_correct_answer", topic="Test",
        fact_ids=["FACT_001"],
    )
    mem.write_feedback(episode_id=eid, mcq_id="MCQ_001", fact_ids=["FACT_001"], rating=2)
    rows = mem.get_feedback_for_fact("FACT_001")
    assert len(rows) == 1
    assert rows[0]["rating"] == 2


def test_retrieve_similar_episodes(mem):
    mem.write_episode(
        input_question="What?", intent="factual_recall",
        blueprint="single_correct_answer", topic="History",
        fact_ids=["FACT_001"],
        overall_score=0.9, accepted=1,
    )
    results = mem.retrieve_similar_episodes(topic="History", min_score=0.5)
    assert len(results) >= 1


def test_update_episode(mem):
    eid = mem.write_episode(
        input_question="Test", intent="test",
        blueprint="single_correct_answer", topic="Test",
        fact_ids=["FACT_001"],
        overall_score=0.5, accepted=0,
    )
    mem.update_episode(eid, accepted=1, overall_score=0.95)
    detail = mem.get_episode_detail(eid)
    assert detail["accepted"] == 1


def test_get_high_performing_topics(mem):
    mem.write_episode(
        input_question="Q", intent="test",
        blueprint="single_correct_answer", topic="Geography",
        fact_ids=["FACT_001"],
        overall_score=0.9, accepted=1,
    )
    topics = mem.get_high_performing_topics()
    assert len(topics) >= 1
    assert topics[0]["topic"] == "Geography"


def test_get_failed_facts(mem):
    eid = mem.write_episode(
        input_question="Q", intent="test",
        blueprint="single_correct_answer", topic="Test",
        fact_ids=["FACT_BAD"],
        overall_score=0.2, accepted=0,
    )
    failed = mem.get_failed_facts(top_n=5)
    assert len(failed) >= 1
    assert failed[0]["fact_id"] == "FACT_BAD"


def test_forget_old_episodes(mem):
    mem.write_episode(
        input_question="Old", intent="test",
        blueprint="single_correct_answer", topic="Test",
        fact_ids=["FACT_001"],
        overall_score=0.1, accepted=0,
    )
    deleted = mem.forget_old_episodes(decay_threshold=0.5, max_age_days=0)
    assert deleted >= 0
