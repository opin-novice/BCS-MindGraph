import pytest
from bcs.pipeline.kg_builder import KnowledgeGraphBuilder


@pytest.fixture
def kg():
    return KnowledgeGraphBuilder()


def test_add_entity(kg):
    eid = kg.add_entity("Bangladesh", "COUNTRY")
    assert eid.startswith("ENTITY_")
    assert kg.graph.has_node(eid)
    assert kg.graph.nodes[eid]["type"] == "ENTITY"


def test_add_fact(kg):
    fid = kg.add_fact("Bangladesh became independent in 1971.")
    assert fid.startswith("FACT_")
    assert kg.graph.has_node(fid)
    assert kg.graph.nodes[fid]["text"] == "Bangladesh became independent in 1971."


def test_add_topic(kg):
    tid = kg.add_topic("History")
    assert tid == "TOPIC_HISTORY"
    assert tid in kg.topic_stats


def test_insert_fact_pipeline(kg):
    fid = kg.insert_fact_pipeline(
        fact_text="Bangladesh became independent in 1971.",
        subject_entities=[("Bangladesh", "COUNTRY")],
        object_entities=[("1971", "EVENT")],
        topic="History",
        source_url="https://example.com",
        publisher="Test",
    )
    assert fid.startswith("FACT_")
    assert kg.graph.has_node(fid)
    facts = kg.get_facts_by_topic("History")
    assert fid in facts


def test_get_fact_data(kg):
    fid = kg.add_fact("Test fact")
    data = kg.get_fact_data(fid)
    assert data is not None
    assert data["text"] == "Test fact"
    assert kg.get_fact_data("NONEXISTENT") is None


def test_get_facts_by_topic_empty(kg):
    assert kg.get_facts_by_topic("Nonexistent") == []


def test_update_fact_attribute(kg):
    fid = kg.add_fact("Test fact")
    kg.update_fact_attribute(fid, "composite_score", 0.85)
    assert kg.graph.nodes[fid]["composite_score"] == 0.85


def test_link(kg):
    fid = kg.add_fact("Fact")
    tid = kg.add_topic("Science")
    kg.link(fid, tid, "ABOUT")
    edges = list(kg.graph.edges(fid, data=True))
    assert any(e[2].get("relation") == "ABOUT" for e in edges)


def test_get_topic_stats(kg):
    kg.add_topic("History")
    stats = kg.get_topic_stats()
    assert "TOPIC_HISTORY" in stats


def test_remove_fact(kg):
    fid = kg.add_fact("To remove")
    kg.remove_fact(fid)
    assert not kg.graph.has_node(fid)
