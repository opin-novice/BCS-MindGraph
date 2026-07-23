import pytest
import os
import tempfile
from fastapi.testclient import TestClient

from bcs.api.main import app
from bcs.api.routes import get_pipeline
from bcs.pipeline.main_pipeline import Pipeline
from bcs.pipeline.kg_builder import KnowledgeGraphBuilder
from bcs.pipeline.episodic_store import EpisodicMemory


@pytest.fixture
def test_pipeline():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    kg = KnowledgeGraphBuilder()
    kg.insert_fact_pipeline(
        fact_text="Bangladesh became independent in 1971.",
        subject_entities=[("Bangladesh", "COUNTRY")],
        object_entities=[("1971", "EVENT")],
        topic="History",
        source_url="https://example.com",
    )
    mem = EpisodicMemory(db_path=tmp.name)
    p = Pipeline.__new__(Pipeline)
    p.kg = kg
    p.memory = mem
    p.mcq_gen = None
    p.normalizer = None
    p.intent_builder = None
    yield p
    mem.close()
    os.unlink(tmp.name)


@pytest.fixture
def client(test_pipeline):
    app.dependency_overrides = {}

    def override():
        return test_pipeline

    app.dependency_overrides[get_pipeline] = override
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["kg_nodes"] >= 1


def test_topics(client):
    resp = client.get("/api/v1/topics")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_metrics(client):
    resp = client.get("/api/v1/metrics")
    assert resp.status_code == 200


def test_feedback_missing_episode(client):
    resp = client.post("/api/v1/feedback", json={
        "episode_id": "nonexistent",
        "mcq_id": "mcq1",
        "rating": 4,
    })
    assert resp.status_code == 200
    assert resp.json()["message"] == "Feedback recorded"


def test_feedback_invalid_rating(client):
    resp = client.post("/api/v1/feedback", json={
        "episode_id": "ep1",
        "mcq_id": "mcq1",
        "rating": 6,
    })
    assert resp.status_code == 422


def test_feedback_stats(client):
    resp = client.get("/api/v1/feedback/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
