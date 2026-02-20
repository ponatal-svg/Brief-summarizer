"""Integration tests for the podcast pipeline.

These tests hit real external services (iTunes API, RSS feeds).
They are SKIPPED by default and must be run explicitly with:

    python3 -m pytest tests/test_podcast_integration.py --integration -v

This ensures CI stays fast and doesn't depend on network availability.

Prerequisites:
- Internet access
- GEMINI_API_KEY env var set (for full transcription tests only)
"""

from __future__ import annotations

import os
import pytest

# ---------------------------------------------------------------------------
# Pytest hook: add --integration flag
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    """Add --integration flag to pytest CLI (may already be defined elsewhere)."""
    try:
        parser.addoption(
            "--integration",
            action="store_true",
            default=False,
            help="Run integration tests that hit real external APIs",
        )
    except ValueError:
        pass  # Already defined in conftest.py


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (deselected by default)"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--integration", default=False):
        skip_integration = pytest.mark.skip(
            reason="Integration tests skipped. Run with --integration to enable."
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_integration)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestItunesApiLive:
    """Verify iTunes Search API works for real shows."""

    def test_hard_fork_rss_found(self):
        from src.fetchers.podcast import _lookup_itunes
        result = _lookup_itunes("Hard Fork")
        assert result is not None
        assert result.startswith("http")
        assert "simplecast" in result.lower() or "rss" in result.lower() or "feed" in result.lower()

    def test_entiende_tu_mente_rss_found(self):
        from src.fetchers.podcast import _lookup_itunes
        result = _lookup_itunes("Entiende Tu Mente")
        assert result is not None
        assert result.startswith("http")

    def test_nonexistent_show_returns_none(self):
        from src.fetchers.podcast import _lookup_itunes
        result = _lookup_itunes("ZZZ_NONEXISTENT_SHOW_XYZ_12345_ABC")
        assert result is None


@pytest.mark.integration
class TestResolveFeedLive:
    """Verify full RSS resolution for configured shows."""

    def test_resolve_hard_fork(self):
        from src.fetchers.podcast import resolve_rss_feed
        rss = resolve_rss_feed(
            "Hard Fork",
            "https://open.spotify.com/show/44fllCS2FTFr2x2kjP9xeT",
        )
        assert rss is not None
        assert rss.startswith("http")

    def test_resolve_entiende_tu_mente(self):
        from src.fetchers.podcast import resolve_rss_feed
        rss = resolve_rss_feed(
            "Entiende Tu Mente",
            "https://open.spotify.com/show/0sGGLIDnnijRPLef7InllD",
        )
        assert rss is not None
        assert rss.startswith("http")


@pytest.mark.integration
class TestFetchEpisodesLive:
    """Verify we can fetch real episodes from RSS feeds."""

    def test_fetch_hard_fork_episodes(self):
        from src.config import PodcastShow
        from src.fetchers.podcast import fetch_new_episodes

        show = PodcastShow(
            podcast_url="https://open.spotify.com/show/44fllCS2FTFr2x2kjP9xeT",
            name="Hard Fork",
            category="AI",
        )
        rss_cache = {}
        episodes = fetch_new_episodes(
            show=show,
            processed_ids=set(),
            lookback_hours=26,
            max_episodes=3,
            min_episodes=1,
            rss_cache=rss_cache,
        )

        # Guaranteed at least 1 (min_episodes fallback)
        assert len(episodes) >= 1
        ep = episodes[0]
        assert ep.title
        assert ep.audio_url.startswith("http")
        assert ep.show_name == "Hard Fork"
        assert ep.category == "AI"
        assert ep.episode_id  # Should be 16 hex chars
        # RSS URL should be cached now
        assert show.podcast_url in rss_cache

    def test_fetch_entiende_tu_mente_episodes(self):
        from src.config import PodcastShow
        from src.fetchers.podcast import fetch_new_episodes

        show = PodcastShow(
            podcast_url="https://open.spotify.com/show/0sGGLIDnnijRPLef7InllD",
            name="Entiende Tu Mente",
            category="Wellbeing",
            language="es",
        )
        rss_cache = {}
        episodes = fetch_new_episodes(
            show=show,
            processed_ids=set(),
            lookback_hours=26,
            max_episodes=3,
            min_episodes=1,
            rss_cache=rss_cache,
        )

        assert len(episodes) >= 1
        assert episodes[0].language == "es"


@pytest.mark.integration
class TestTranscriptionLive:
    """Verify full download + transcription pipeline.

    Requires GEMINI_API_KEY and takes several minutes per test.
    Only run manually when validating a new show.
    """

    @pytest.fixture(autouse=True)
    def require_api_key(self):
        if not os.environ.get("GEMINI_API_KEY"):
            pytest.skip("GEMINI_API_KEY not set")

    def test_transcribe_hard_fork_latest(self):
        """Fetch latest Hard Fork episode and transcribe it (first 10 min to save cost)."""
        from src.config import PodcastShow
        from src.fetchers.podcast import fetch_new_episodes, download_and_transcribe
        from src.summarizer import create_client

        show = PodcastShow(
            podcast_url="https://open.spotify.com/show/44fllCS2FTFr2x2kjP9xeT",
            name="Hard Fork",
            category="AI",
        )
        rss_cache = {}
        episodes = fetch_new_episodes(
            show=show,
            processed_ids=set(),
            lookback_hours=26 * 7,  # wider window for testing
            max_episodes=1,
            min_episodes=1,
            rss_cache=rss_cache,
        )
        assert episodes, "No episodes found for Hard Fork"

        client = create_client()
        summary = download_and_transcribe(
            episode=episodes[0],
            gemini_client=client,
            gemini_model="gemini-2.5-flash",
            max_audio_minutes=10,  # short for test cost
        )

        assert summary
        assert len(summary) > 100
        # Should have our structured format
        assert "Hook" in summary or "Finding" in summary or "What" in summary
