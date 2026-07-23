from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from bcs.api.main import app
from bcs.api.rate_limiter import reset_rate_limiter
from bcs.api.routes import get_pipeline


@pytest.fixture(autouse=True)
def reset_limits_and_mock_pipeline():
    reset_rate_limiter()

    mock_pipeline = MagicMock()
    mock_pipeline.kg.graph.number_of_nodes.return_value = 100
    mock_pipeline.kg.graph.nodes.return_value = []
    mock_pipeline.memory.get_high_performing_topics.return_value = []
    mock_pipeline.run.return_value = {
        "mcqs": [],
        "topic": "Test",
        "difficulty": "medium",
        "count": 0,
        "kg_size": 100,
        "memory_size": 0,
    }
    mock_pipeline.memory.write_feedback.return_value = 1

    app.dependency_overrides[get_pipeline] = lambda: mock_pipeline
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


class TestAPIRateLimiter:
    def test_health_not_rate_limited(self):
        for _ in range(10):
            r = client.get("/api/v1/health")
            assert r.status_code == 200

    def test_generate_limited_after_5_requests(self):
        for i in range(6):
            r = client.post("/api/v1/generate", json={
                "topic": "Bangladesh", "difficulty": "medium", "count": 1
            })
            if i < 5:
                continue
            assert r.status_code == 429

    def test_429_has_detail_and_retry_after(self):
        for _ in range(6):
            r = client.post("/api/v1/generate", json={
                "topic": "Bangladesh", "difficulty": "medium", "count": 1
            })
        assert r.status_code == 429
        data = r.json()
        assert "detail" in data
        assert "Retry-After" in r.headers
        assert int(r.headers["Retry-After"]) >= 1

    def test_feedback_limited_after_10_requests(self):
        for i in range(12):
            r = client.post("/api/v1/feedback", json={
                "episode_id": "EP_test", "mcq_id": "MCQ_test",
                "rating": 4, "fact_ids": [],
            })
            if i < 10:
                continue
            assert r.status_code == 429

    def test_different_ips_have_separate_limits(self):
        for _ in range(6):
            r = client.post("/api/v1/generate", json={
                "topic": "Bangladesh", "difficulty": "medium", "count": 1
            }, headers={"X-Forwarded-For": "10.0.0.1"})
        assert r.status_code == 429

        for _ in range(5):
            r = client.post("/api/v1/generate", json={
                "topic": "Bangladesh", "difficulty": "medium", "count": 1
            }, headers={"X-Forwarded-For": "10.0.0.2"})
        assert r.status_code != 429
