from bcs.api.schemas import (
    GenerateRequest, GenerateResponse, MCQOut, OptionOut,
    TopicOut, HealthResponse, FeedbackRequest, FeedbackResponse,
    FeedbackStatsResponse,
)


def test_generate_request_defaults():
    req = GenerateRequest()
    assert req.topic == "Bangladesh Geography"
    assert req.difficulty == "medium"
    assert req.count == 1
    assert req.max_facts == 5


def test_generate_request_custom():
    req = GenerateRequest(topic="History", difficulty="hard", count=3, max_facts=10)
    assert req.topic == "History"
    assert req.difficulty == "hard"


def test_option_out():
    o = OptionOut(key="ক", text="Dhaka")
    assert o.key == "ক"
    assert o.text == "Dhaka"


def test_mcq_out():
    m = MCQOut(question="Q?", options=[OptionOut(key="ক", text="A")], correct_answer="ক", difficulty="easy", quality_score=0.9, explanation="Exp")
    assert m.question == "Q?"
    assert m.quality_score == 0.9


def test_generate_response():
    resp = GenerateResponse(mcqs=[], topic="History", difficulty="easy", count=0)
    assert resp.topic == "History"


def test_topic_out():
    t = TopicOut(topic="History", fact_count=10)
    assert t.fact_count == 10


def test_health_response():
    h = HealthResponse(status="ok", kg_nodes=100, kg_facts=50, memory_size=5, model="test")
    assert h.status == "ok"


def test_feedback_request():
    req = FeedbackRequest(episode_id="ep1", mcq_id="mcq1", rating=4)
    assert req.rating == 4


def test_feedback_response():
    r = FeedbackResponse(id=1, message="Recorded")
    assert r.message == "Recorded"


def test_feedback_stats_response():
    s = FeedbackStatsResponse(total=5, avg_rating=4.0, distribution={"5": 3, "4": 2}, categories=[])
    assert s.total == 5
