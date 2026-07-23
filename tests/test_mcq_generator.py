import pytest
from bcs.generators.mcq_generator import (
    _question_fingerprint,
    DuplicateDetector,
    MCQ,
    MCQOption,
    GenerationResult,
    safe_parse_json,
    facts_from_kg,
    DEMO_FACTS,
)
from bcs.pipeline.kg_builder import KnowledgeGraphBuilder


class TestQuestionFingerprint:
    def test_stable_hash(self):
        fp1 = _question_fingerprint("What is the capital?")
        fp2 = _question_fingerprint("What is the capital?")
        assert fp1 == fp2

    def test_different_questions(self):
        fp1 = _question_fingerprint("What is X?")
        fp2 = _question_fingerprint("What is Y?")
        assert fp1 != fp2

    def test_case_insensitive(self):
        fp1 = _question_fingerprint("What is Capital?")
        fp2 = _question_fingerprint("what is capital?")
        assert fp1 == fp2


class TestDuplicateDetector:
    def test_empty(self):
        d = DuplicateDetector(persist_path=None)
        assert not d.is_duplicate(MCQ(mcq_id="1", fact_id="F1", question="Q?", options=[], correct_answer="ক", difficulty="easy", question_type="factual", explanation=""))

    def test_register_and_detect(self):
        d = DuplicateDetector(persist_path=None)
        m = MCQ(mcq_id="1", fact_id="F1", question="Unique question?", options=[], correct_answer="ক", difficulty="easy", question_type="factual", explanation="")
        d.register(m)
        assert d.is_duplicate(m)

    def test_filter_duplicates(self):
        d = DuplicateDetector(persist_path=None)
        m1 = MCQ(mcq_id="1", fact_id="F1", question="Q1?", options=[], correct_answer="ক", difficulty="easy", question_type="factual", explanation="")
        m2 = MCQ(mcq_id="2", fact_id="F2", question="Q2?", options=[], correct_answer="ক", difficulty="easy", question_type="factual", explanation="")
        m3 = MCQ(mcq_id="3", fact_id="F3", question="Q1?", options=[], correct_answer="ক", difficulty="easy", question_type="factual", explanation="")
        unique, dupes = d.filter_duplicates([m1, m2, m3])
        assert len(unique) == 2
        assert len(dupes) == 1


def test_mcq_option():
    o = MCQOption(key="ক", text="Dhaka")
    assert o.key == "ক"
    assert o.text == "Dhaka"


def test_mcq_to_episode_dict():
    m = MCQ(mcq_id="MCQ_1", fact_id="FACT_1", question="Q?", options=[MCQOption(key="ক", text="A")], correct_answer="ক", difficulty="easy", question_type="factual", explanation="Exp")
    d = m.to_episode_dict()
    assert d["question"] == "Q?"
    assert d["correct_answer"] == "ক"


def test_generation_result():
    r = GenerationResult(
        episode_id="ep1",
        topic="History",
        fact_ids=["FACT_1"],
        mcqs=[],
        overall_score=0.9,
        accepted=True,
        crj_rounds=1,
        generation_config={"difficulty": "easy"},
    )
    assert r.overall_score == 0.9
    assert r.accepted is True
    payload = r.to_episode_payload()
    assert payload["topic"] == "History"


class TestSafeParseJson:
    def test_valid_json(self):
        assert safe_parse_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced(self):
        result = safe_parse_json('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_trailing_comma(self):
        result = safe_parse_json('{"a": 1, "b": 2,}')
        assert result == {"a": 1, "b": 2}

    def test_list_json(self):
        result = safe_parse_json('[{"a": 1}]')
        assert isinstance(result, list)
        assert result[0]["a"] == 1

    def test_none_input(self):
        assert safe_parse_json(None) is None

    def test_empty_string(self):
        assert safe_parse_json("") is None


class TestFactsFromKg:
    def test_returns_facts_for_topic(self):
        kg = KnowledgeGraphBuilder()
        kg.insert_fact_pipeline(
            fact_text="Bangladesh independence 1971.",
            subject_entities=[("Bangladesh", "COUNTRY")],
            object_entities=[],
            topic="History",
            source_url="https://example.com",
        )
        facts = facts_from_kg(kg, "History")
        assert len(facts) > 0

    def test_empty_for_unknown_topic(self):
        kg = KnowledgeGraphBuilder()
        facts = facts_from_kg(kg, "Nonexistent")
        assert facts == []

    def test_demo_facts_structure(self):
        assert len(DEMO_FACTS) >= 2
        for f in DEMO_FACTS:
            assert "fact_id" in f
            assert "text" in f
            assert "composite_score" in f
