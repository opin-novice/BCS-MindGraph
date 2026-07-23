from unittest.mock import patch, MagicMock
import pytest

from bcs.pipeline.web_scraper import WebScraper
from bcs.cache import web_cache


@pytest.fixture(autouse=True)
def clear_web_cache():
    web_cache.clear()
    yield


class FakeDDGS:
    def __init__(self, results=None, fail_count=0):
        self._results = results or []
        self._call_count = 0
        self._fail_count = fail_count

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def text(self, query, max_results=5):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise Exception("ratelimit exceeded")
        for r in self._results:
            yield r


class TestWebSearchRetry:
    @pytest.fixture(autouse=True)
    def no_sleep(self):
        with patch("bcs.pipeline.web_scraper.time.sleep"):
            yield

    def test_ddg_retries_on_ratelimit(self):
        scraper = WebScraper()
        fake_ddgs = FakeDDGS(
            results=[{"href": "https://example.com", "title": "Test", "body": "Snippet"}],
            fail_count=2,
        )
        with patch("bcs.pipeline.web_scraper.DDGS", return_value=fake_ddgs):
            results = scraper._ddg_search("test query")
        assert len(results) == 1
        assert fake_ddgs._call_count == 3

    def test_ddg_succeeds_on_first_try(self):
        scraper = WebScraper()
        fake_ddgs = FakeDDGS(
            results=[{"href": "https://example.com", "title": "Test", "body": "Snippet"}],
            fail_count=0,
        )
        with patch("bcs.pipeline.web_scraper.DDGS", return_value=fake_ddgs):
            results = scraper._ddg_search("test query")
        assert len(results) == 1
        assert fake_ddgs._call_count == 1

    def test_ddg_gives_up_after_3_failures(self):
        scraper = WebScraper()
        fake_ddgs = FakeDDGS(results=[], fail_count=5)
        with patch("bcs.pipeline.web_scraper.DDGS", return_value=fake_ddgs):
            results = scraper._ddg_search("test query")
        assert len(results) == 0
        assert fake_ddgs._call_count >= 3


class FakeBlueprint:
    def __init__(self, topic="Test", bangla_query="", english_query="",
                 search_keywords=None, entities=None):
        self.topic = topic
        self.bangla_query = bangla_query
        self.english_query = english_query
        self.search_keywords = search_keywords or []
        self.entities = entities or []


class TestWebSearchCache:
    def test_cache_hit_returns_cached_result(self):
        scraper = WebScraper()
        bp = FakeBlueprint(topic="History", bangla_query="বাংলার ইতিহাস")
        key = scraper._make_cache_key(bp)

        fake_sentences = [MagicMock(sentence_hash="a1b2c3"), MagicMock(sentence_hash="d4e5f6")]
        cached_result = MagicMock()
        cached_result.sentences = fake_sentences
        web_cache.set(key, cached_result)

        with patch.object(scraper, "_search", return_value=[]) as mock_search:
            with patch.object(scraper, "_fetch_and_extract", return_value=[]):
                result = scraper.scrape_for_blueprint(bp)
                mock_search.assert_not_called()
                assert result.sentences == fake_sentences

    def test_cache_miss_calls_search(self):
        scraper = WebScraper()
        bp = FakeBlueprint(topic="History", bangla_query="বাংলার ইতিহাস")

        with patch.object(scraper, "_search", return_value=[]) as mock_search:
            with patch.object(scraper, "_fetch_and_extract", return_value=[]):
                scraper.scrape_for_blueprint(bp)
                mock_search.assert_called_once()

    def test_cache_stored_after_miss(self):
        scraper = WebScraper()
        bp = FakeBlueprint(topic="Geography", english_query="Bangladesh rivers")
        key = scraper._make_cache_key(bp)

        with patch.object(scraper, "_search", return_value=[]):
            with patch.object(scraper, "_fetch_and_extract", return_value=[]):
                scraper.scrape_for_blueprint(bp)

        cached = web_cache.get(key)
        assert cached is not None
        assert cached.topic == "Geography"

    def test_different_blueprints_have_different_cache_keys(self):
        scraper = WebScraper()
        bp1 = FakeBlueprint(topic="History", bangla_query="বাংলার ইতিহাস")
        bp2 = FakeBlueprint(topic="Geography", bangla_query="বাংলার নদী")
        key1 = scraper._make_cache_key(bp1)
        key2 = scraper._make_cache_key(bp2)
        assert key1 != key2

    def test_same_blueprint_produces_same_key(self):
        scraper = WebScraper()
        bp1 = FakeBlueprint(topic="History", bangla_query="বাংলার ইতিহাস",
                            search_keywords=["Bengal"])
        bp2 = FakeBlueprint(topic="History", bangla_query="বাংলার ইতিহাস",
                            search_keywords=["Bengal"])
        assert scraper._make_cache_key(bp1) == scraper._make_cache_key(bp2)
