from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from bcs.pipeline.main_pipeline import Pipeline


class FakeScrapedResult:
    def __init__(self, sentences):
        self.sentences = sentences

    def as_fact_dicts(self):
        return [
            {"text": s, "source_url": "https://example.com", "publisher": "Test", "extraction_date": None}
            for s in self.sentences
        ]


@pytest.fixture
def pipeline():
    p = Pipeline(
        hf_api_key="test-key",
        db_path=":memory:",
        data_dir="data",
        web_search_enabled=True,
        web_min_facts=3,
        web_quality_threshold=0.50,
        web_max_sentences=10,
    )
    p.kg = MagicMock()
    p.quality_gate = MagicMock()
    return p


class TestWebSearchIntegration:
    def test_disabled_returns_zero(self, pipeline):
        pipeline.web_search_enabled = False
        blueprint = MagicMock()
        plog = MagicMock()
        assert pipeline._web_search_and_integrate(blueprint, plog) == 0
        plog.log_stage.assert_not_called()

    def test_scraper_returns_no_sentences(self, pipeline):
        blueprint = MagicMock()
        plog = MagicMock()
        with patch("bcs.pipeline.main_pipeline.WebScraper") as MockScraper:
            MockScraper.return_value.scrape_for_blueprint.return_value = FakeScrapedResult([])
            assert pipeline._web_search_and_integrate(blueprint, plog) == 0

    def test_scraper_exception_logged_gracefully(self, pipeline):
        blueprint = MagicMock()
        plog = MagicMock()
        with patch("bcs.pipeline.main_pipeline.WebScraper") as MockScraper:
            MockScraper.return_value.scrape_for_blueprint.side_effect = RuntimeError("Network error")
            assert pipeline._web_search_and_integrate(blueprint, plog) == 0

    def test_all_facts_below_threshold_skipped(self, pipeline):
        blueprint = MagicMock()
        blueprint.topic = "Test"
        plog = MagicMock()
        pipeline.quality_gate.score_fact.return_value = {"composite_score": 0.30}
        with patch("bcs.pipeline.main_pipeline.WebScraper") as MockScraper:
            MockScraper.return_value.scrape_for_blueprint.return_value = FakeScrapedResult(
                ["Low quality fact one", "Low quality fact two"]
            )
            result = pipeline._web_search_and_integrate(blueprint, plog)
            assert result == 0
            pipeline.kg.insert_fact_pipeline.assert_not_called()

    def test_facts_above_threshold_inserted(self, pipeline):
        blueprint = MagicMock()
        blueprint.topic = "Test"
        plog = MagicMock()
        pipeline.quality_gate.score_fact.return_value = {"composite_score": 0.75}
        with patch("bcs.pipeline.main_pipeline.WebScraper") as MockScraper:
            MockScraper.return_value.scrape_for_blueprint.return_value = FakeScrapedResult(
                ["Bangladesh has 64 districts.", "Dhaka is the capital city."]
            )
            result = pipeline._web_search_and_integrate(blueprint, plog)
            assert result == 2
            assert pipeline.kg.insert_fact_pipeline.call_count == 2

    def test_mixed_quality_facts_partially_inserted(self, pipeline):
        blueprint = MagicMock()
        blueprint.topic = "Test"
        plog = MagicMock()

        def score_side_effect(text, **kwargs):
            if "good" in text.lower():
                return {"composite_score": 0.85}
            return {"composite_score": 0.30}

        pipeline.quality_gate.score_fact.side_effect = score_side_effect
        with patch("bcs.pipeline.main_pipeline.WebScraper") as MockScraper:
            MockScraper.return_value.scrape_for_blueprint.return_value = FakeScrapedResult(
                ["Good quality fact", "Bad quality fact"]
            )
            result = pipeline._web_search_and_integrate(blueprint, plog)
            assert result == 1
            pipeline.kg.insert_fact_pipeline.assert_called_once()

    def test_plog_stage_called_with_counts(self, pipeline):
        blueprint = MagicMock()
        blueprint.topic = "Test"
        plog = MagicMock()
        pipeline.quality_gate.score_fact.return_value = {"composite_score": 0.80}
        with patch("bcs.pipeline.main_pipeline.WebScraper") as MockScraper:
            MockScraper.return_value.scrape_for_blueprint.return_value = FakeScrapedResult(
                ["Fact A", "Fact B"]
            )
            pipeline._web_search_and_integrate(blueprint, plog)
            plog.log_stage.assert_called_once_with(
                "web_search",
                input_data={"topic": "Test"},
                output_data={"scraped_count": 2, "integrated": 2},
            )

    def test_web_min_facts_trigger_activated(self, pipeline):
        pipeline.normalizer.normalize = MagicMock(return_value="Unknown Topic")
        pipeline.intent_builder.build_blueprint = MagicMock()
        blueprint = MagicMock()
        blueprint.topic = "Unknown Topic"
        blueprint.intent = "factual_recall"
        pipeline.intent_builder.build_blueprint.return_value = blueprint
        pipeline.quality_gate = MagicMock()
        pipeline.mcq_gen.generate_from_facts = MagicMock()
        pipeline.mcq_gen.generate_from_facts.return_value.mcqs = []
        pipeline.mcq_gen.generate_from_facts.return_value.crj_rounds = 0
        pipeline.mcq_gen.generate_from_facts.return_value.duplicate_count = 0
        pipeline.mcq_gen.generate_from_facts.return_value.overall_score = 0.5
        pipeline.mcq_gen.generate_from_facts.return_value.fact_ids = []
        pipeline.mcq_gen.generate_from_facts.return_value.accepted = False
        pipeline.mcq_gen.generate_from_facts.return_value.generation_config = {}
        pipeline._web_search_and_integrate = MagicMock(return_value=3)
        pipeline.memory.get_high_performing_topics = MagicMock(return_value=[])

        fake_facts = [{"fact_id": f"f{i}", "text": f"Fact {i}"} for i in range(1)]
        with patch("bcs.pipeline.main_pipeline.facts_from_kg", return_value=fake_facts):
            result = pipeline.run(topic="Unknown Topic", difficulty="medium", count=1, max_facts=5)
            pipeline._web_search_and_integrate.assert_called_once()
            assert "error" not in result

    def test_web_min_facts_not_triggered_when_sufficient(self, pipeline):
        pipeline.normalizer.normalize = MagicMock(return_value="Known Topic")
        pipeline.intent_builder.build_blueprint = MagicMock()
        blueprint = MagicMock()
        blueprint.topic = "Known Topic"
        blueprint.intent = "factual_recall"
        pipeline.intent_builder.build_blueprint.return_value = blueprint
        pipeline.quality_gate = MagicMock()
        pipeline.mcq_gen.generate_from_facts = MagicMock()
        pipeline.mcq_gen.generate_from_facts.return_value.mcqs = []
        pipeline.mcq_gen.generate_from_facts.return_value.crj_rounds = 0
        pipeline.mcq_gen.generate_from_facts.return_value.duplicate_count = 0
        pipeline.mcq_gen.generate_from_facts.return_value.overall_score = 0.5
        pipeline.mcq_gen.generate_from_facts.return_value.fact_ids = []
        pipeline.mcq_gen.generate_from_facts.return_value.accepted = False
        pipeline.mcq_gen.generate_from_facts.return_value.generation_config = {}
        pipeline._web_search_and_integrate = MagicMock()
        pipeline.memory.get_high_performing_topics = MagicMock(return_value=[])

        fake_facts = [{"fact_id": f"f{i}", "text": f"Fact {i}"} for i in range(5)]
        with patch("bcs.pipeline.main_pipeline.facts_from_kg", return_value=fake_facts):
            pipeline.run(topic="Known Topic", difficulty="medium", count=1, max_facts=5)
            pipeline._web_search_and_integrate.assert_not_called()

    def test_web_search_disabled_still_handles_low_facts(self, pipeline):
        pipeline.web_search_enabled = False
        pipeline.normalizer.normalize = MagicMock(return_value="Sparse Topic")
        pipeline.intent_builder.build_blueprint = MagicMock()
        blueprint = MagicMock()
        blueprint.topic = "Sparse Topic"
        blueprint.intent = "factual_recall"
        pipeline.intent_builder.build_blueprint.return_value = blueprint
        pipeline.quality_gate = MagicMock()
        pipeline.mcq_gen.generate_from_facts = MagicMock()
        pipeline.mcq_gen.generate_from_facts.return_value.mcqs = []
        pipeline.mcq_gen.generate_from_facts.return_value.crj_rounds = 0
        pipeline.mcq_gen.generate_from_facts.return_value.duplicate_count = 0
        pipeline.mcq_gen.generate_from_facts.return_value.overall_score = 0.5
        pipeline.mcq_gen.generate_from_facts.return_value.fact_ids = []
        pipeline.mcq_gen.generate_from_facts.return_value.accepted = False
        pipeline.mcq_gen.generate_from_facts.return_value.generation_config = {}
        pipeline.memory.get_high_performing_topics = MagicMock(return_value=[])
        pipeline._web_search_and_integrate = MagicMock(return_value=0)

        fake_facts = [{"fact_id": f"f{i}"} for i in range(1)]
        with patch("bcs.pipeline.main_pipeline.facts_from_kg", return_value=fake_facts):
            result = pipeline.run(topic="Sparse Topic", difficulty="medium", count=1, max_facts=5)
            pipeline._web_search_and_integrate.assert_called_once()
            assert result["count"] == 0

    def test_as_fact_dicts_fallback_to_sentences(self, pipeline):
        class FakeWithoutDicts:
            sentences = ["Raw sentence A", "Raw sentence B"]

            def as_fact_dicts(self):
                return []

        blueprint = MagicMock()
        blueprint.topic = "Test"
        plog = MagicMock()
        pipeline.quality_gate.score_fact.return_value = {"composite_score": 0.80}
        with patch("bcs.pipeline.main_pipeline.WebScraper") as MockScraper:
            MockScraper.return_value.scrape_for_blueprint.return_value = FakeWithoutDicts()
            result = pipeline._web_search_and_integrate(blueprint, plog)
            assert result == 2
            assert pipeline.kg.insert_fact_pipeline.call_count == 2
