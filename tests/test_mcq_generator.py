import json
from unittest.mock import patch, MagicMock
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
    ChallengerAgent,
    MCQGenerator,
    call_llm,
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


class TestMultiMcqPerFact:
    """Verify the multi-MCQ-per-fact generation (2 MCQs per fact)."""

    def test_challenger_prompt_requests_two_per_fact(self):
        agent = ChallengerAgent(client="fake-key")
        facts = [
            {"fact_id": "F1", "text": "Bangladesh became independent in 1971.",
             "mcq_suitable_for": ["when_question", "who_question"]}
        ]
        with patch("bcs.generators.mcq_generator.call_llm",
                   return_value='{"mcqs":[]}') as mock_call:
            agent.generate(facts)
            prompt = mock_call.call_args[0][3]
            assert "2 different MCQs per fact" in prompt
            assert "exactly 2 different MCQs per fact" in prompt

    def test_generates_two_mcqs_for_one_fact(self):
        agent = ChallengerAgent(client="fake-key")
        facts = [
            {"fact_id": "F1", "text": "Bangladesh became independent in 1971.",
             "mcq_suitable_for": ["when_question", "who_question"]}
        ]
        mock_response = json.dumps({
            "mcqs": [
                {
                    "fact_id": "F1",
                    "question": "বাংলাদেশ কবে স্বাধীন হয়?",
                    "options": {"ক": "১৯৪৭", "খ": "১৯৫২", "গ": "১৯৭১", "ঘ": "১৯৯০"},
                    "correct_answer": "গ",
                    "difficulty": "medium",
                    "question_type": "when_question",
                    "explanation": "বাংলাদেশ ১৯৭১ সালে স্বাধীন হয়।",
                },
                {
                    "fact_id": "F1",
                    "question": "বাংলাদেশের স্বাধীনতা সংগ্রামের নেতা কে ছিলেন?",
                    "options": {"ক": "জিয়াউর রহমান", "খ": "শেখ মুজিবুর রহমান",
                                "গ": "হুসেইন মুহাম্মদ এরশাদ", "ঘ": "খালেদা জিয়া"},
                    "correct_answer": "খ",
                    "difficulty": "medium",
                    "question_type": "who_question",
                    "explanation": "শেখ মুজিবুর রহমান বাংলাদেশের স্বাধীনতা সংগ্রামের নেতা।",
                },
            ]
        })
        with patch("bcs.generators.mcq_generator.call_llm",
                   return_value=mock_response):
            mcqs = agent.generate(facts)
        assert len(mcqs) == 2
        assert mcqs[0].fact_id == "F1"
        assert mcqs[1].fact_id == "F1"
        assert mcqs[0].question_type == "when_question"
        assert mcqs[1].question_type == "who_question"
        assert mcqs[0].question != mcqs[1].question

    def test_dedup_allows_different_questions_same_fact(self):
        d = DuplicateDetector(persist_path=None)
        m1 = MCQ(mcq_id="1", fact_id="F1", question="Q1 from F1?", options=[],
                 correct_answer="ক", difficulty="easy", question_type="when", explanation="")
        m2 = MCQ(mcq_id="2", fact_id="F1", question="Q2 from F1?", options=[],
                 correct_answer="ক", difficulty="easy", question_type="who", explanation="")
        unique, dupes = d.filter_duplicates([m1, m2])
        assert len(unique) == 2
        assert len(dupes) == 0

    def test_multi_mcq_per_fact_integration(self):
        with patch("bcs.generators.mcq_generator.call_llm") as mock_call:
            def side_effect(api_key, model, system, user, **kwargs):
                if "Generate 4 MCQs" in user:
                    return json.dumps({
                        "mcqs": [
                            {"fact_id": "F1", "question": "Q1 from F1?", "options": {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}, "correct_answer": "ক", "difficulty": "medium", "question_type": "when_question", "explanation": "Exp"},
                            {"fact_id": "F1", "question": "Q2 from F1?", "options": {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}, "correct_answer": "খ", "difficulty": "medium", "question_type": "who_question", "explanation": "Exp"},
                            {"fact_id": "F2", "question": "Q3 from F2?", "options": {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}, "correct_answer": "গ", "difficulty": "medium", "question_type": "numeric_ranking", "explanation": "Exp"},
                            {"fact_id": "F2", "question": "Q4 from F2?", "options": {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}, "correct_answer": "ঘ", "difficulty": "medium", "question_type": "where_question", "explanation": "Exp"},
                        ]
                    })
                if "Answer these MCQs" in user or "Evaluate these MCQs" in user:
                    return json.dumps({"answers": [], "evaluations": []})
                return json.dumps({"mcqs": []})
            mock_call.side_effect = side_effect

            gen = MCQGenerator(hf_api_key="fake-key", seen_questions_path=None)
            facts = [
                {"fact_id": "F1", "text": "Bangladesh independence in 1971.",
                 "topic": "History", "mcq_suitable_for": ["when", "who"],
                 "mcq_readiness": 0.9, "composite_score": 0.8, "source_reliability": 0.9},
                {"fact_id": "F2", "text": "Dhaka is the capital of Bangladesh.",
                 "topic": "Geography", "mcq_suitable_for": ["where", "numeric"],
                 "mcq_readiness": 0.9, "composite_score": 0.8, "source_reliability": 0.9},
            ]
            result = gen.generate_from_facts(facts, difficulty="medium")
            assert len(result.mcqs) == 4
            f1_ids = [m.fact_id for m in result.mcqs].count("F1")
            f2_ids = [m.fact_id for m in result.mcqs].count("F2")
            assert f1_ids == 2
            assert f2_ids == 2

    def test_type_distribution_scaled_with_total_mcqs(self):
        agent = ChallengerAgent(client="fake-key")
        facts = [
            {"fact_id": "F1", "text": "Fact one.", "mcq_suitable_for": ["factual"]},
            {"fact_id": "F2", "text": "Fact two.", "mcq_suitable_for": ["factual"]},
        ]
        with patch("bcs.generators.mcq_generator.call_llm",
                   return_value='{"mcqs":[]}') as mock_call:
            agent.generate(facts)
            prompt = mock_call.call_args[0][3]
            total_mcqs = 4
            n_factual = max(1, round(total_mcqs * 0.75))
            n_who = max(1, round(total_mcqs * 0.15))
            assert str(n_factual) in prompt
            assert str(n_who) in prompt
