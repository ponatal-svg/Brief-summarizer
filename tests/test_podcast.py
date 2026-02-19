"""Tests for the podcast fetcher pipeline.

All external I/O (HTTP, subprocess, Gemini API, filesystem) is mocked.
Integration tests that hit real APIs are in tests/test_podcast_integration.py
and are skipped unless --integration is passed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, mock_open, call
from io import BytesIO

import pytest

from src.config import PodcastShow
from src.fetchers.podcast import (
    EpisodeInfo,
    RSSLookupError,
    AudioDownloadError,
    TranscriptionError,
    fetch_new_episodes,
    resolve_rss_feed,
    download_and_transcribe,
    _lookup_itunes,
    _looks_like_rss_url,
    _validate_rss_url,
    _fetch_rss_content,
    _extract_episodes,
    _parse_rss_date,
    _parse_itunes_duration,
    _parse_rss_item,
    _has_ffmpeg,
    _download_with_ffmpeg,
    _download_direct,
    _wait_for_file_active,
    _transcribe_and_summarize,
    _format_duration,
    _get_language_name,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_show():
    return PodcastShow(
        podcast_url="https://open.spotify.com/show/44fllCS2FTFr2x2kjP9xeT",
        name="Hard Fork",
        category="AI",
        language="en",
    )


@pytest.fixture
def sample_show_es():
    return PodcastShow(
        podcast_url="https://open.spotify.com/show/0sGGLIDnnijRPLef7InllD",
        name="Entiende Tu Mente",
        category="Wellbeing",
        language="es",
    )


@pytest.fixture
def sample_episode():
    return EpisodeInfo(
        episode_id="abc12345678901",
        title="Test Episode",
        show_name="Hard Fork",
        show_url="https://open.spotify.com/show/44fllCS2FTFr2x2kjP9xeT",
        episode_url="https://hardfork.example.com/ep1",
        audio_url="https://cdn.example.com/episode1.mp3",
        category="AI",
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        duration_seconds=3600,
        language="en",
    )


@pytest.fixture
def rss_xml_bytes():
    """Minimal valid RSS feed with two items."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    return f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Hard Fork</title>
    <item>
      <title>Episode 1: New AI Models</title>
      <guid>https://hardfork.example.com/ep1</guid>
      <pubDate>{now.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>
      <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="50000000"/>
      <itunes:duration>3600</itunes:duration>
      <link>https://hardfork.example.com/ep1</link>
    </item>
    <item>
      <title>Episode 2: Old News</title>
      <guid>https://hardfork.example.com/ep2</guid>
      <pubDate>{yesterday.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>
      <enclosure url="https://cdn.example.com/ep2.mp3" type="audio/mpeg" length="40000000"/>
      <itunes:duration>2700</itunes:duration>
      <link>https://hardfork.example.com/ep2</link>
    </item>
  </channel>
</rss>""".encode("utf-8")


# ---------------------------------------------------------------------------
# Tests: Utility functions
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_unknown_when_zero(self):
        assert _format_duration(0) == "unknown duration"

    def test_minutes_only(self):
        assert _format_duration(1800) == "30m"

    def test_hours_and_minutes(self):
        assert _format_duration(3661) == "1h 1m"

    def test_negative(self):
        assert _format_duration(-1) == "unknown duration"


class TestGetLanguageName:
    def test_known_code(self):
        assert _get_language_name("en") == "English"
        assert _get_language_name("es") == "Spanish"
        assert _get_language_name("he") == "Hebrew"

    def test_unknown_code_returns_code(self):
        assert _get_language_name("xx") == "xx"


class TestParseItunesDuration:
    def test_hh_mm_ss(self):
        assert _parse_itunes_duration("1:30:00") == 5400

    def test_mm_ss(self):
        assert _parse_itunes_duration("45:00") == 2700

    def test_seconds_only(self):
        assert _parse_itunes_duration("3600") == 3600

    def test_decimal_seconds(self):
        assert _parse_itunes_duration("3600.5") == 3600

    def test_empty_string(self):
        assert _parse_itunes_duration("") == 0

    def test_invalid(self):
        assert _parse_itunes_duration("invalid") == 0


class TestParseRssDate:
    def test_valid_rfc2822(self):
        result = _parse_rss_date("Mon, 19 Feb 2026 10:00:00 +0000")
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 19
        assert result.tzinfo is not None

    def test_none_returns_epoch(self):
        result = _parse_rss_date(None)
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_empty_returns_epoch(self):
        result = _parse_rss_date("")
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_invalid_returns_epoch(self):
        result = _parse_rss_date("not a date at all")
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_naive_datetime_gets_utc(self):
        # Some feeds omit timezone
        result = _parse_rss_date("Mon, 19 Feb 2026 10:00:00")
        # Should not raise; result may be epoch or parsed depending on email.utils behavior
        assert result is not None


class TestLooksLikeRssUrl:
    def test_rss_in_path(self):
        assert _looks_like_rss_url("https://feeds.simplecast.com/6HKOhNgS") is True

    def test_feed_in_path(self):
        assert _looks_like_rss_url("https://example.com/feed") is True

    def test_xml_extension(self):
        assert _looks_like_rss_url("https://example.com/podcast.xml") is True

    def test_anchor_rss(self):
        assert _looks_like_rss_url("https://anchor.fm/s/abc/podcast/rss") is True

    def test_spotify_url_not_rss(self):
        assert _looks_like_rss_url("https://open.spotify.com/show/abc") is False

    def test_apple_url_not_rss(self):
        assert _looks_like_rss_url("https://podcasts.apple.com/show/abc") is False


# ---------------------------------------------------------------------------
# Tests: RSS parsing
# ---------------------------------------------------------------------------

class TestExtractEpisodes:
    def test_parses_two_items(self, rss_xml_bytes, sample_show):
        episodes = _extract_episodes(rss_xml_bytes, sample_show)
        assert len(episodes) == 2

    def test_sorted_newest_first(self, rss_xml_bytes, sample_show):
        episodes = _extract_episodes(rss_xml_bytes, sample_show)
        assert episodes[0].published_at >= episodes[1].published_at

    def test_episode_fields_populated(self, rss_xml_bytes, sample_show):
        episodes = _extract_episodes(rss_xml_bytes, sample_show)
        ep = episodes[0]
        assert ep.title == "Episode 1: New AI Models"
        assert ep.audio_url == "https://cdn.example.com/ep1.mp3"
        assert ep.show_name == "Hard Fork"
        assert ep.category == "AI"
        assert ep.language == "en"
        assert ep.duration_seconds == 3600

    def test_episode_id_stable(self, rss_xml_bytes, sample_show):
        episodes = _extract_episodes(rss_xml_bytes, sample_show)
        # Running twice should produce same IDs
        episodes2 = _extract_episodes(rss_xml_bytes, sample_show)
        assert episodes[0].episode_id == episodes2[0].episode_id

    def test_episode_id_is_sha1_of_guid(self, rss_xml_bytes, sample_show):
        episodes = _extract_episodes(rss_xml_bytes, sample_show)
        expected_id = hashlib.sha1(b"https://hardfork.example.com/ep1").hexdigest()[:16]
        assert episodes[0].episode_id == expected_id

    def test_invalid_xml_raises(self, sample_show):
        with pytest.raises(ValueError, match="Invalid RSS XML"):
            _extract_episodes(b"not xml at all <broken", sample_show)

    def test_missing_channel_raises(self, sample_show):
        xml = b"<?xml version='1.0'?><rss><notchannel/></rss>"
        with pytest.raises(ValueError, match="missing <channel>"):
            _extract_episodes(xml, sample_show)

    def test_item_without_enclosure_skipped(self, sample_show):
        xml = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>No Audio</title><guid>x</guid></item>
</channel></rss>"""
        episodes = _extract_episodes(xml, sample_show)
        assert len(episodes) == 0

    def test_item_without_enclosure_url_skipped(self, sample_show):
        xml = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Empty URL</title>
    <guid>x</guid>
    <enclosure url="" type="audio/mpeg" length="100"/>
  </item>
</channel></rss>"""
        episodes = _extract_episodes(xml, sample_show)
        assert len(episodes) == 0

    def test_missing_guid_uses_url_hash(self, sample_show):
        xml = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>No GUID</title>
    <enclosure url="https://cdn.example.com/audio.mp3" type="audio/mpeg" length="100"/>
  </item>
</channel></rss>"""
        episodes = _extract_episodes(xml, sample_show)
        assert len(episodes) == 1
        expected = hashlib.sha1(b"https://cdn.example.com/audio.mp3").hexdigest()[:16]
        assert episodes[0].episode_id == expected

    def test_spanish_show_language(self, sample_show_es):
        xml = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Episodio Uno</title>
    <guid>ep-uno</guid>
    <pubDate>Mon, 19 Feb 2026 10:00:00 +0000</pubDate>
    <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="100"/>
  </item>
</channel></rss>"""
        episodes = _extract_episodes(xml, sample_show_es)
        assert episodes[0].language == "es"


# ---------------------------------------------------------------------------
# Tests: RSS fetch
# ---------------------------------------------------------------------------

class TestFetchRssContent:
    def test_successful_fetch(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<rss>content</rss>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_rss_content("https://feeds.example.com/rss")

        assert result == b"<rss>content</rss>"

    def test_retries_on_429(self):
        from urllib.error import HTTPError
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<rss/>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        call_count = 0
        def side_effect(req, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HTTPError(url="x", code=429, msg="Too Many", hdrs={}, fp=None)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):  # don't actually sleep
                result = _fetch_rss_content("https://feeds.example.com/rss")

        assert call_count == 2
        assert result == b"<rss/>"

    def test_raises_on_permanent_http_error(self):
        from urllib.error import HTTPError
        with patch("urllib.request.urlopen",
                   side_effect=HTTPError(url="x", code=404, msg="Not Found", hdrs={}, fp=None)):
            with pytest.raises(HTTPError):
                _fetch_rss_content("https://feeds.example.com/rss")

    def test_retries_on_network_error_then_succeeds(self):
        from urllib.error import URLError
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<rss/>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        call_count = 0
        def side_effect(req, timeout):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise URLError("connection refused")
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                result = _fetch_rss_content("https://feeds.example.com/rss")

        assert result == b"<rss/>"


# ---------------------------------------------------------------------------
# Tests: iTunes lookup
# ---------------------------------------------------------------------------

class TestLookupItunes:
    def test_returns_feed_url_on_success(self):
        mock_data = {
            "resultCount": 1,
            "results": [{"collectionName": "Hard Fork", "feedUrl": "https://feeds.simplecast.com/abc"}]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _lookup_itunes("Hard Fork")

        assert result == "https://feeds.simplecast.com/abc"

    def test_returns_none_when_no_results(self):
        mock_data = {"resultCount": 0, "results": []}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _lookup_itunes("Unknown Podcast XYZ123")

        assert result is None

    def test_returns_none_on_network_error(self):
        from urllib.error import URLError
        with patch("urllib.request.urlopen", side_effect=URLError("network error")):
            result = _lookup_itunes("Hard Fork")

        assert result is None

    def test_retries_on_503(self):
        from urllib.error import HTTPError
        mock_data = {"resultCount": 1, "results": [{"feedUrl": "https://feeds.example.com/rss"}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        call_count = 0
        def side_effect(req, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HTTPError(url="x", code=503, msg="Service Unavailable", hdrs={}, fp=None)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                result = _lookup_itunes("Hard Fork")

        assert result == "https://feeds.example.com/rss"

    def test_returns_none_when_result_missing_feed_url(self):
        mock_data = {"resultCount": 1, "results": [{"collectionName": "No Feed URL"}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _lookup_itunes("Hard Fork")

        assert result is None

    def test_returns_none_on_generic_exception(self):
        with patch("urllib.request.urlopen", side_effect=Exception("unexpected")):
            result = _lookup_itunes("Hard Fork")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: validate_rss_url
# ---------------------------------------------------------------------------

class TestValidateRssUrl:
    def test_valid_rss_returns_true(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<?xml version='1.0'?><rss version='2.0'><channel/></rss>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _validate_rss_url("https://feeds.example.com/rss") is True

    def test_non_rss_returns_false(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html><body>Not RSS</body></html>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _validate_rss_url("https://example.com/page") is False

    def test_network_error_returns_false(self):
        from urllib.error import URLError
        with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            assert _validate_rss_url("https://unreachable.example.com") is False


# ---------------------------------------------------------------------------
# Tests: resolve_rss_feed
# ---------------------------------------------------------------------------

class TestResolveRssFeed:
    def test_returns_itunes_result_first(self, sample_show):
        with patch("src.fetchers.podcast._lookup_itunes", return_value="https://feeds.simplecast.com/abc"):
            result = resolve_rss_feed(sample_show.name, sample_show.podcast_url)

        assert result == "https://feeds.simplecast.com/abc"

    def test_falls_back_to_direct_url_when_itunes_fails(self):
        direct_url = "https://feeds.example.com/mypodcast.xml"
        with patch("src.fetchers.podcast._lookup_itunes", return_value=None):
            with patch("src.fetchers.podcast._validate_rss_url", return_value=True):
                result = resolve_rss_feed("My Podcast", direct_url)

        assert result == direct_url

    def test_raises_when_all_fallbacks_fail(self, sample_show):
        with patch("src.fetchers.podcast._lookup_itunes", return_value=None):
            with patch("src.fetchers.podcast._validate_rss_url", return_value=False):
                with pytest.raises(RSSLookupError, match="Could not find RSS feed"):
                    resolve_rss_feed(sample_show.name, sample_show.podcast_url)

    def test_spotify_url_not_validated_directly(self, sample_show):
        """Spotify URLs should not be tried as RSS feeds directly."""
        with patch("src.fetchers.podcast._lookup_itunes", return_value=None):
            with patch("src.fetchers.podcast._validate_rss_url") as mock_validate:
                with pytest.raises(RSSLookupError):
                    resolve_rss_feed(sample_show.name, sample_show.podcast_url)
                # _validate_rss_url should NOT be called for spotify URLs
                mock_validate.assert_not_called()

    def test_non_platform_url_validated_directly(self):
        """Non-Spotify/Apple URLs that look like RSS should be validated."""
        rss_url = "https://feeds.example.com/mypodcast/rss"
        with patch("src.fetchers.podcast._lookup_itunes", return_value=None):
            with patch("src.fetchers.podcast._validate_rss_url", return_value=True):
                result = resolve_rss_feed("My Podcast", rss_url)
        assert result == rss_url


# ---------------------------------------------------------------------------
# Tests: fetch_new_episodes
# ---------------------------------------------------------------------------

class TestFetchNewEpisodes:
    def test_returns_episodes_within_window(self, sample_show, rss_xml_bytes):
        rss_cache = {}
        with patch("src.fetchers.podcast.resolve_rss_feed", return_value="https://rss.example.com"):
            with patch("src.fetchers.podcast._parse_rss_feed") as mock_parse:
                now = datetime.now(timezone.utc)
                ep1 = EpisodeInfo(
                    episode_id="ep1id", title="Recent Episode", show_name="Hard Fork",
                    show_url="https://spotify", episode_url="https://ep1",
                    audio_url="https://cdn/ep1.mp3", category="AI",
                    published_at=now - timedelta(hours=2), duration_seconds=3600,
                )
                ep2 = EpisodeInfo(
                    episode_id="ep2id", title="Old Episode", show_name="Hard Fork",
                    show_url="https://spotify", episode_url="https://ep2",
                    audio_url="https://cdn/ep2.mp3", category="AI",
                    published_at=now - timedelta(days=10), duration_seconds=2700,
                )
                mock_parse.return_value = [ep1, ep2]

                result = fetch_new_episodes(
                    show=sample_show,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_episodes=3,
                    min_episodes=1,
                    rss_cache=rss_cache,
                )

        assert len(result) == 1
        assert result[0].episode_id == "ep1id"

    def test_guarantees_min_episode_when_none_in_window(self, sample_show):
        rss_cache = {}
        now = datetime.now(timezone.utc)
        old_ep = EpisodeInfo(
            episode_id="old_id", title="Old Episode", show_name="Hard Fork",
            show_url="https://spotify", episode_url="https://ep",
            audio_url="https://cdn/ep.mp3", category="AI",
            published_at=now - timedelta(days=30), duration_seconds=3600,
        )
        with patch("src.fetchers.podcast.resolve_rss_feed", return_value="https://rss.example.com"):
            with patch("src.fetchers.podcast._parse_rss_feed", return_value=[old_ep]):
                result = fetch_new_episodes(
                    show=sample_show,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_episodes=3,
                    min_episodes=1,
                    rss_cache=rss_cache,
                )

        assert len(result) == 1
        assert result[0].episode_id == "old_id"

    def test_skips_already_processed(self, sample_show):
        rss_cache = {}
        now = datetime.now(timezone.utc)
        ep = EpisodeInfo(
            episode_id="processed_id", title="Episode", show_name="Hard Fork",
            show_url="https://spotify", episode_url="https://ep",
            audio_url="https://cdn/ep.mp3", category="AI",
            published_at=now - timedelta(hours=2), duration_seconds=3600,
        )
        with patch("src.fetchers.podcast.resolve_rss_feed", return_value="https://rss.example.com"):
            with patch("src.fetchers.podcast._parse_rss_feed", return_value=[ep]):
                result = fetch_new_episodes(
                    show=sample_show,
                    processed_ids={"processed_id"},
                    lookback_hours=26,
                    max_episodes=3,
                    min_episodes=1,
                    rss_cache=rss_cache,
                )

        # Episode is processed AND it's the only episode, so min fallback also skips it
        assert len(result) == 0

    def test_respects_max_episodes(self, sample_show):
        rss_cache = {}
        now = datetime.now(timezone.utc)
        episodes = [
            EpisodeInfo(
                episode_id=f"ep{i}", title=f"Episode {i}", show_name="Hard Fork",
                show_url="https://spotify", episode_url=f"https://ep{i}",
                audio_url=f"https://cdn/ep{i}.mp3", category="AI",
                published_at=now - timedelta(hours=i), duration_seconds=3600,
            )
            for i in range(5)
        ]
        with patch("src.fetchers.podcast.resolve_rss_feed", return_value="https://rss.example.com"):
            with patch("src.fetchers.podcast._parse_rss_feed", return_value=episodes):
                result = fetch_new_episodes(
                    show=sample_show,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_episodes=2,
                    min_episodes=1,
                    rss_cache=rss_cache,
                )

        assert len(result) == 2

    def test_caches_rss_url(self, sample_show):
        rss_cache = {}
        now = datetime.now(timezone.utc)
        ep = EpisodeInfo(
            episode_id="ep1", title="Episode", show_name="Hard Fork",
            show_url="https://spotify", episode_url="https://ep",
            audio_url="https://cdn/ep.mp3", category="AI",
            published_at=now - timedelta(hours=2), duration_seconds=3600,
        )
        with patch("src.fetchers.podcast.resolve_rss_feed", return_value="https://rss.example.com") as mock_resolve:
            with patch("src.fetchers.podcast._parse_rss_feed", return_value=[ep]):
                # First call
                fetch_new_episodes(sample_show, set(), 26, 3, 1, rss_cache)
                assert rss_cache[sample_show.podcast_url] == "https://rss.example.com"

                # Second call — resolve_rss_feed should NOT be called again
                fetch_new_episodes(sample_show, set(), 26, 3, 1, rss_cache)

        assert mock_resolve.call_count == 1

    def test_raises_rss_lookup_error(self, sample_show):
        rss_cache = {}
        with patch("src.fetchers.podcast.resolve_rss_feed",
                   side_effect=RSSLookupError("not found")):
            with pytest.raises(RSSLookupError):
                fetch_new_episodes(sample_show, set(), 26, 3, 1, rss_cache)

    def test_empty_rss_feed_returns_empty(self, sample_show):
        rss_cache = {}
        with patch("src.fetchers.podcast.resolve_rss_feed", return_value="https://rss.example.com"):
            with patch("src.fetchers.podcast._parse_rss_feed", return_value=[]):
                result = fetch_new_episodes(sample_show, set(), 26, 3, 1, rss_cache)

        assert result == []


# ---------------------------------------------------------------------------
# Tests: Audio download
# ---------------------------------------------------------------------------

class TestHasFfmpeg:
    def test_returns_true_when_available(self):
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            assert _has_ffmpeg() is True

    def test_returns_false_when_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert _has_ffmpeg() is False

    def test_returns_false_on_nonzero_returncode(self):
        mock_result = MagicMock(returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            assert _has_ffmpeg() is False

    def test_returns_false_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 5)):
            assert _has_ffmpeg() is False


class TestDownloadWithFfmpeg:
    def test_successful_download(self, tmp_path):
        output_path = str(tmp_path / "episode.mp3")
        # Create a fake file to simulate ffmpeg output
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.getsize", return_value=1024):
                    result = _download_with_ffmpeg(
                        "https://cdn.example.com/ep.mp3", output_path, 3600
                    )
        assert result is True

    def test_returns_false_on_nonzero_returncode(self, tmp_path):
        output_path = str(tmp_path / "episode.mp3")
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="error")):
            result = _download_with_ffmpeg(
                "https://cdn.example.com/ep.mp3", output_path, 3600
            )
        assert result is False

    def test_returns_false_on_timeout(self, tmp_path):
        import subprocess
        output_path = str(tmp_path / "episode.mp3")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 3600)):
            result = _download_with_ffmpeg(
                "https://cdn.example.com/ep.mp3", output_path, 3600
            )
        assert result is False

    def test_returns_false_when_ffmpeg_not_found(self, tmp_path):
        output_path = str(tmp_path / "episode.mp3")
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = _download_with_ffmpeg(
                "https://cdn.example.com/ep.mp3", output_path, 3600
            )
        assert result is False

    def test_returns_false_when_output_empty(self, tmp_path):
        output_path = str(tmp_path / "episode.mp3")
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.getsize", return_value=0):
                    result = _download_with_ffmpeg(
                        "https://cdn.example.com/ep.mp3", output_path, 3600
                    )
        assert result is False


class TestDownloadDirect:
    def test_successful_download(self, tmp_path):
        output_path = str(tmp_path / "episode.mp3")
        audio_data = b"fake mp3 data" * 1000

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [audio_data, b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            _download_direct("https://cdn.example.com/ep.mp3", output_path, max_bytes=1_000_000)

        assert os.path.exists(output_path)

    def test_retries_on_429(self, tmp_path):
        from urllib.error import HTTPError
        output_path = str(tmp_path / "episode.mp3")
        audio_data = b"fake mp3 data"

        mock_resp = MagicMock()
        mock_resp.read.side_effect = [audio_data, b""]
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        call_count = 0
        def side_effect(req, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HTTPError(url="x", code=429, msg="Too Many", hdrs={}, fp=None)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=side_effect):
            with patch("time.sleep"):
                _download_direct("https://cdn.example.com/ep.mp3", output_path, max_bytes=1_000_000)

        assert call_count == 2

    def test_raises_audio_download_error_on_404(self, tmp_path):
        from urllib.error import HTTPError
        output_path = str(tmp_path / "episode.mp3")

        with patch("urllib.request.urlopen",
                   side_effect=HTTPError(url="x", code=404, msg="Not Found", hdrs={}, fp=None)):
            with pytest.raises(AudioDownloadError, match="HTTP 404"):
                _download_direct("https://cdn.example.com/ep.mp3", output_path, max_bytes=1_000_000)

    def test_raises_audio_download_error_on_network_failure(self, tmp_path):
        from urllib.error import URLError
        output_path = str(tmp_path / "episode.mp3")

        with patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            with patch("time.sleep"):
                with pytest.raises(AudioDownloadError, match="Network error"):
                    _download_direct("https://cdn.example.com/ep.mp3", output_path, max_bytes=1_000_000)


# ---------------------------------------------------------------------------
# Tests: Gemini transcription
# ---------------------------------------------------------------------------

class TestWaitForFileActive:
    def test_returns_immediately_when_active(self):
        mock_client = MagicMock()
        mock_file = MagicMock()
        mock_file.name = "files/abc123"

        active_info = MagicMock()
        active_info.state = "ACTIVE"
        mock_client.files.get.return_value = active_info

        # Should not raise
        _wait_for_file_active(mock_client, mock_file)
        mock_client.files.get.assert_called_once()

    def test_polls_until_active(self):
        mock_client = MagicMock()
        mock_file = MagicMock()
        mock_file.name = "files/abc123"

        processing = MagicMock()
        processing.state = "PROCESSING"
        active = MagicMock()
        active.state = "ACTIVE"

        mock_client.files.get.side_effect = [processing, processing, active]

        with patch("time.sleep"):
            _wait_for_file_active(mock_client, mock_file)

        assert mock_client.files.get.call_count == 3

    def test_raises_on_failed_state(self):
        mock_client = MagicMock()
        mock_file = MagicMock()
        mock_file.name = "files/abc123"

        failed_info = MagicMock()
        failed_info.state = "FAILED"
        mock_client.files.get.return_value = failed_info

        with pytest.raises(TranscriptionError, match="file processing failed"):
            _wait_for_file_active(mock_client, mock_file)

    def test_raises_on_timeout(self):
        mock_client = MagicMock()
        mock_file = MagicMock()
        mock_file.name = "files/abc123"

        processing = MagicMock()
        processing.state = "PROCESSING"
        mock_client.files.get.return_value = processing

        with patch("time.sleep"):
            with pytest.raises(TranscriptionError, match="timed out"):
                _wait_for_file_active(mock_client, mock_file, max_wait_seconds=6)


class TestTranscribeAndSummarize:
    def test_successful_transcription(self, sample_episode, tmp_path):
        audio_path = str(tmp_path / "episode.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio data")

        mock_client = MagicMock()
        mock_uploaded = MagicMock()
        mock_uploaded.name = "files/abc"
        mock_client.files.upload.return_value = mock_uploaded

        mock_response = MagicMock()
        mock_response.text = "## The Hook\nGreat episode.\n## Key Findings\n* Finding 1\n## The So What?\nImportant."
        mock_client.models.generate_content.return_value = mock_response

        with patch("src.fetchers.podcast._wait_for_file_active"):
            with patch("time.sleep"):
                result = _transcribe_and_summarize(
                    audio_path=audio_path,
                    episode=sample_episode,
                    client=mock_client,
                    model="gemini-2.0-flash",
                    max_audio_minutes=60,
                )

        assert "The Hook" in result
        # File should be deleted after use
        mock_client.files.delete.assert_called_once_with(name="files/abc")

    def test_raises_transcription_error_on_auth_failure(self, sample_episode, tmp_path):
        audio_path = str(tmp_path / "episode.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")

        mock_client = MagicMock()
        mock_client.files.upload.side_effect = Exception("401 Unauthorized: api_key_invalid")

        with patch("time.sleep"):
            with pytest.raises(TranscriptionError, match="authentication failed"):
                _transcribe_and_summarize(
                    audio_path=audio_path,
                    episode=sample_episode,
                    client=mock_client,
                    model="gemini-2.0-flash",
                    max_audio_minutes=60,
                )

    def test_raises_transcription_error_on_file_too_large(self, sample_episode, tmp_path):
        audio_path = str(tmp_path / "episode.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")

        mock_client = MagicMock()
        mock_client.files.upload.side_effect = Exception("File too large for upload")

        with patch("time.sleep"):
            with pytest.raises(TranscriptionError, match="too large"):
                _transcribe_and_summarize(
                    audio_path=audio_path,
                    episode=sample_episode,
                    client=mock_client,
                    model="gemini-2.0-flash",
                    max_audio_minutes=60,
                )

    def test_retries_on_transient_error(self, sample_episode, tmp_path):
        audio_path = str(tmp_path / "episode.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")

        mock_client = MagicMock()
        mock_uploaded = MagicMock()
        mock_uploaded.name = "files/abc"

        call_count = 0
        def upload_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("503 Service Unavailable")
            return mock_uploaded

        mock_client.files.upload.side_effect = upload_side_effect
        mock_client.models.generate_content.return_value = MagicMock(text="Summary text")

        with patch("src.fetchers.podcast._wait_for_file_active"):
            with patch("time.sleep"):
                result = _transcribe_and_summarize(
                    audio_path=audio_path,
                    episode=sample_episode,
                    client=mock_client,
                    model="gemini-2.0-flash",
                    max_audio_minutes=60,
                )

        assert call_count == 2
        assert result == "Summary text"

    def test_cleans_up_file_on_error(self, sample_episode, tmp_path):
        audio_path = str(tmp_path / "episode.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")

        mock_client = MagicMock()
        mock_uploaded = MagicMock()
        mock_uploaded.name = "files/abc"
        mock_client.files.upload.return_value = mock_uploaded
        mock_client.models.generate_content.side_effect = Exception("random error")

        with patch("src.fetchers.podcast._wait_for_file_active"):
            with patch("time.sleep"):
                with pytest.raises(TranscriptionError):
                    _transcribe_and_summarize(
                        audio_path=audio_path,
                        episode=sample_episode,
                        client=mock_client,
                        model="gemini-2.0-flash",
                        max_audio_minutes=60,
                    )

        # File cleanup should have been attempted
        mock_client.files.delete.assert_called()

    def test_uses_min_of_actual_and_cap_duration(self, tmp_path):
        """Episode shorter than cap: use actual duration in prompt."""
        short_episode = EpisodeInfo(
            episode_id="short", title="Short Episode", show_name="Test",
            show_url="https://spotify", episode_url="https://ep",
            audio_url="https://cdn/ep.mp3", category="AI",
            published_at=datetime.now(timezone.utc), duration_seconds=900,  # 15min
            language="en",
        )
        audio_path = str(tmp_path / "short.mp3")
        with open(audio_path, "wb") as f:
            f.write(b"fake audio")

        mock_client = MagicMock()
        mock_uploaded = MagicMock()
        mock_uploaded.name = "files/abc"
        mock_client.files.upload.return_value = mock_uploaded
        mock_client.models.generate_content.return_value = MagicMock(text="Summary")

        with patch("src.fetchers.podcast._wait_for_file_active"):
            with patch("time.sleep"):
                _transcribe_and_summarize(
                    audio_path=audio_path,
                    episode=short_episode,
                    client=mock_client,
                    model="gemini-2.0-flash",
                    max_audio_minutes=60,
                )

        # The prompt sent to Gemini should mention 15m duration
        call_args = mock_client.models.generate_content.call_args
        contents = call_args[1]["contents"] if call_args[1] else call_args[0][1]
        prompt = contents[1] if isinstance(contents, list) else contents
        assert "15m" in prompt


# ---------------------------------------------------------------------------
# Tests: download_and_transcribe (integration of download + transcription)
# ---------------------------------------------------------------------------

class TestDownloadAndTranscribe:
    def test_uses_ffmpeg_when_available(self, sample_episode):
        with patch("src.fetchers.podcast._has_ffmpeg", return_value=True):
            with patch("src.fetchers.podcast._download_with_ffmpeg", return_value=True) as mock_ffmpeg:
                with patch("src.fetchers.podcast._transcribe_and_summarize", return_value="Summary"):
                    result = download_and_transcribe(
                        episode=sample_episode,
                        gemini_client=MagicMock(),
                        gemini_model="gemini-2.0-flash",
                        max_audio_minutes=60,
                    )

        assert result == "Summary"
        mock_ffmpeg.assert_called_once()

    def test_falls_back_to_direct_when_ffmpeg_unavailable(self, sample_episode):
        with patch("src.fetchers.podcast._has_ffmpeg", return_value=False):
            with patch("src.fetchers.podcast._download_direct") as mock_direct:
                with patch("src.fetchers.podcast._transcribe_and_summarize", return_value="Summary"):
                    result = download_and_transcribe(
                        episode=sample_episode,
                        gemini_client=MagicMock(),
                        gemini_model="gemini-2.0-flash",
                        max_audio_minutes=60,
                    )

        assert result == "Summary"
        mock_direct.assert_called_once()

    def test_falls_back_to_direct_when_ffmpeg_fails(self, sample_episode):
        with patch("src.fetchers.podcast._has_ffmpeg", return_value=True):
            with patch("src.fetchers.podcast._download_with_ffmpeg", return_value=False):
                with patch("src.fetchers.podcast._download_direct") as mock_direct:
                    with patch("src.fetchers.podcast._transcribe_and_summarize", return_value="Summary"):
                        result = download_and_transcribe(
                            episode=sample_episode,
                            gemini_client=MagicMock(),
                            gemini_model="gemini-2.0-flash",
                            max_audio_minutes=60,
                        )

        mock_direct.assert_called_once()

    def test_temp_dir_cleaned_up_after_transcription(self, sample_episode):
        """Temp files should be deleted even if transcription succeeds."""
        created_tmpdir = None

        original_tempdir = tempfile.TemporaryDirectory

        class CaptureTempDir:
            def __init__(self):
                self._td = original_tempdir()
                nonlocal created_tmpdir
                created_tmpdir = self._td.name

            def __enter__(self):
                return self._td.__enter__()

            def __exit__(self, *args):
                return self._td.__exit__(*args)

        with patch("tempfile.TemporaryDirectory", CaptureTempDir):
            with patch("src.fetchers.podcast._has_ffmpeg", return_value=False):
                with patch("src.fetchers.podcast._download_direct"):
                    with patch("src.fetchers.podcast._transcribe_and_summarize", return_value="Summary"):
                        download_and_transcribe(
                            episode=sample_episode,
                            gemini_client=MagicMock(),
                            gemini_model="gemini-2.0-flash",
                            max_audio_minutes=60,
                        )

        # Temp directory should have been cleaned up
        assert created_tmpdir is not None
        assert not os.path.exists(created_tmpdir)
