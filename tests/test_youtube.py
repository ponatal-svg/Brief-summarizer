"""Tests for YouTube fetcher."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.config import YouTubeSource
from src.fetchers.youtube import (
    VideoInfo,
    fetch_new_videos,
    _get_channel_entries,
    _get_transcript,
    _get_video_upload_date,
    _parse_upload_date,
    _is_within_lookback,
    _sample_segments,
)


# ---------------------------------------------------------------------------
# Helper: fake snippet objects matching youtube-transcript-api v1.x dataclass
# ---------------------------------------------------------------------------

@dataclass
class FakeSnippet:
    """Mimics youtube_transcript_api._transcripts.FetchedTranscriptSnippet."""
    text: str
    start: float
    duration: float = 1.0


def make_snippets(*items):
    """Build a list of FakeSnippet from (text, start) pairs."""
    return [FakeSnippet(text=t, start=s) for t, s in items]


@pytest.fixture
def sample_source():
    return YouTubeSource(
        channel_url="https://www.youtube.com/@TestChannel",
        name="Test Channel",
        category="AI",
    )


@pytest.fixture
def sample_entry():
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return {
        "id": "abc123",
        "title": "Test Video",
        "upload_date": today,
        "duration": 600,
    }


class TestParseUploadDate:
    def test_valid_date(self):
        result = _parse_upload_date("20260215")
        assert result == datetime(2026, 2, 15, tzinfo=timezone.utc)

    def test_none_input(self):
        assert _parse_upload_date(None) is None

    def test_empty_string(self):
        assert _parse_upload_date("") is None

    def test_invalid_format(self):
        assert _parse_upload_date("2026-02-15") is None

    def test_non_string(self):
        assert _parse_upload_date(20260215) is None


class TestIsWithinLookback:
    def test_recent_date(self):
        now = datetime.now(timezone.utc)
        recent = now - timedelta(hours=1)
        assert _is_within_lookback(recent, 26) is True

    def test_old_date(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=48)
        assert _is_within_lookback(old, 26) is False

    def test_just_inside_boundary(self):
        now = datetime.now(timezone.utc)
        just_inside = now - timedelta(hours=25, minutes=59)
        assert _is_within_lookback(just_inside, 26) is True

    def test_just_outside_boundary(self):
        now = datetime.now(timezone.utc)
        just_outside = now - timedelta(hours=26, minutes=1)
        assert _is_within_lookback(just_outside, 26) is False


class TestSampleSegments:
    """Tests for _sample_segments() — the timestamp index builder."""

    def test_empty_returns_empty_tuple(self):
        assert _sample_segments([]) == ()

    def test_single_snippet(self):
        raw = make_snippets(("Hello world", 0.0))
        result = _sample_segments(raw)
        assert result == ((0, "Hello world"),)

    def test_samples_every_30s_by_default(self):
        # 3 snippets: 0s, 15s, 30s — only 0s and 30s should be sampled
        raw = make_snippets(("First", 0.0), ("Middle", 15.0), ("Third", 30.0))
        result = _sample_segments(raw)
        starts = [s for s, _ in result]
        assert 0 in starts
        assert 30 in starts
        assert 15 not in starts

    def test_custom_interval(self):
        raw = make_snippets(("A", 0.0), ("B", 10.0), ("C", 20.0))
        result = _sample_segments(raw, interval_seconds=10)
        starts = [s for s, _ in result]
        assert 0 in starts
        assert 10 in starts
        assert 20 in starts

    def test_returns_tuple_of_tuples(self):
        raw = make_snippets(("Hi", 5.0))
        result = _sample_segments(raw)
        assert isinstance(result, tuple)
        assert isinstance(result[0], tuple)
        assert result[0] == (5, "Hi")

    def test_skips_empty_text(self):
        raw = make_snippets(("", 0.0), ("   ", 1.0), ("Real text", 2.0))
        result = _sample_segments(raw)
        # Only "Real text" has non-empty text and falls in the first window
        assert len(result) == 1
        assert result[0][1] == "Real text"

    def test_start_seconds_are_integers(self):
        raw = make_snippets(("Test", 12.7))
        result = _sample_segments(raw)
        assert isinstance(result[0][0], int)
        assert result[0][0] == 12


class TestGetTranscript:
    """Tests for _get_transcript().

    Patches the module-level _yta instance (youtube-transcript-api v1.x).
    Snippets are FakeSnippet dataclass objects with .text and .start attributes.
    Returns (text, segments) tuple.
    """

    def test_successful_fetch_returns_text(self):
        snippets = make_snippets(("Hello", 0.0), ("world", 1.0))

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.return_value = snippets
            text, segments = _get_transcript("abc123")

        assert text == "Hello world"
        mock_yta.fetch.assert_called_once_with("abc123", languages=["en", "en-US", "en-GB"])

    def test_successful_fetch_returns_segments(self):
        snippets = make_snippets(("Hello", 0.0), ("world", 45.0))

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.return_value = snippets
            text, segments = _get_transcript("abc123")

        assert isinstance(segments, tuple)
        assert len(segments) >= 1
        # First segment at t=0 should always be included
        assert segments[0][0] == 0
        assert segments[0][1] == "Hello"

    def test_no_transcript_available(self):
        from youtube_transcript_api._errors import TranscriptsDisabled

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.side_effect = TranscriptsDisabled("abc123")
            text, segments = _get_transcript("abc123")

        assert text is None
        assert segments == ()

    def test_no_transcript_found_falls_back_to_list(self):
        from youtube_transcript_api._errors import NoTranscriptFound

        snippets = make_snippets(("Hola", 0.0))
        mock_transcript = MagicMock()
        mock_transcript.language_code = "es"
        mock_transcript.fetch.return_value = snippets

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.side_effect = NoTranscriptFound("abc123", [], [])
            mock_yta.list.return_value = [mock_transcript]
            text, segments = _get_transcript("abc123")

        assert text == "Hola"
        assert isinstance(segments, tuple)

    def test_video_unavailable(self):
        from youtube_transcript_api._errors import VideoUnavailable

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.side_effect = VideoUnavailable("abc123")
            text, segments = _get_transcript("abc123")

        assert text is None
        assert segments == ()

    def test_generic_error_falls_back_to_list(self):
        """A generic error on fetch() tries list() next."""
        snippets = make_snippets(("fallback", 0.0))
        mock_transcript = MagicMock()
        mock_transcript.language_code = "en"
        mock_transcript.fetch.return_value = snippets

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.side_effect = RuntimeError("network error")
            mock_yta.list.return_value = [mock_transcript]
            text, segments = _get_transcript("abc123")

        assert text == "fallback"

    def test_both_attempts_fail_returns_none(self):
        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.side_effect = RuntimeError("fail")
            mock_yta.list.side_effect = RuntimeError("also fail")
            text, segments = _get_transcript("abc123")

        assert text is None
        assert segments == ()

    def test_empty_transcript_returns_none(self):
        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.return_value = []
            text, segments = _get_transcript("abc123")

        assert text is None


class TestGetChannelEntries:
    def test_successful_fetch(self):
        entries = [
            {"id": "v1", "title": "Video 1"},
            {"id": "v2", "title": "Video 2"},
        ]
        stdout = "\n".join(json.dumps(e) for e in entries)
        mock_result = MagicMock(returncode=0, stdout=stdout, stderr="")

        with patch("subprocess.run", return_value=mock_result):
            result = _get_channel_entries("https://www.youtube.com/@Test", 3)
            assert len(result) == 2
            assert result[0]["id"] == "v1"

    def test_yt_dlp_error(self):
        mock_result = MagicMock(returncode=1, stdout="", stderr="Error")
        with patch("subprocess.run", return_value=mock_result):
            result = _get_channel_entries("https://www.youtube.com/@Test", 3)
            assert result == []

    def test_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            result = _get_channel_entries("https://www.youtube.com/@Test", 3)
            assert result == []

    def test_yt_dlp_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            result = _get_channel_entries("https://www.youtube.com/@Test", 3)
            assert result == []


class TestFetchNewVideos:
    @pytest.fixture(autouse=True)
    def mock_real_date(self):
        """By default, _get_video_upload_date returns None so flat-playlist date is used."""
        with patch("src.fetchers.youtube._get_video_upload_date", return_value=None):
            yield

    def test_skips_processed_ids(self, sample_source, sample_entry):
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=("text", ())):
                result = fetch_new_videos(
                    sample_source,
                    processed_ids={"abc123"},
                    lookback_hours=26,
                    max_videos=3,
                )
                assert len(result) == 0

    def test_returns_new_videos(self, sample_source, sample_entry):
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=("transcript text", ())):
                result = fetch_new_videos(
                    sample_source,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_videos=3,
                )
                assert len(result) == 1
                assert result[0].video_id == "abc123"
                assert result[0].title == "Test Video"
                assert result[0].transcript == "transcript text"
                assert result[0].category == "AI"

    def test_handles_no_transcript(self, sample_source, sample_entry):
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=(None, ())):
                result = fetch_new_videos(
                    sample_source,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_videos=3,
                )
                assert len(result) == 1
                assert result[0].transcript is None

    def test_skips_old_videos_but_falls_back_to_latest(self, sample_source):
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y%m%d")
        old_entry = {
            "id": "old1",
            "title": "Old Video",
            "upload_date": old_date,
            "duration": 300,
        }
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[old_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=(None, ())):
                result = fetch_new_videos(
                    sample_source,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_videos=3,
                )
                # Fallback: latest video included even though it's outside lookback
                assert len(result) == 1
                assert result[0].video_id == "old1"
                assert result[0].transcript is None

    def test_fallback_skips_already_processed(self, sample_source):
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y%m%d")
        old_entry = {
            "id": "old1",
            "title": "Old Video",
            "upload_date": old_date,
            "duration": 300,
        }
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[old_entry]):
            result = fetch_new_videos(
                sample_source,
                processed_ids={"old1"},  # already processed
                lookback_hours=26,
                max_videos=3,
            )
            # Both normal path and fallback skip it — already processed
            assert len(result) == 0

    def test_fallback_not_triggered_when_videos_found(self, sample_source, sample_entry):
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y%m%d")
        old_entry = {
            "id": "old1",
            "title": "Old Video",
            "upload_date": old_date,
            "duration": 300,
        }
        # sample_entry is within lookback, old_entry is not
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry, old_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=("text", ())):
                result = fetch_new_videos(
                    sample_source,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_videos=3,
                )
                # Only the recent one — fallback not needed
                assert len(result) == 1
                assert result[0].video_id == "abc123"

    def test_empty_entries_returns_empty(self, sample_source):
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[]):
            result = fetch_new_videos(
                sample_source,
                processed_ids=set(),
                lookback_hours=26,
                max_videos=3,
            )
            assert len(result) == 0

    def test_video_info_fields(self, sample_source, sample_entry):
        segs = ((0, "Hello"),)
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=("text", segs)):
                with patch("src.fetchers.youtube._get_video_upload_date", return_value=None):
                    result = fetch_new_videos(
                        sample_source,
                        processed_ids=set(),
                        lookback_hours=26,
                        max_videos=3,
                    )
                    video = result[0]
                    assert video.url == "https://www.youtube.com/watch?v=abc123"
                    assert video.channel_name == "Test Channel"
                    assert video.duration_seconds == 600
                    assert video.language == "en"  # default from source
                    assert video.transcript_segments == segs

    def test_real_upload_date_overrides_flat_playlist(self, sample_source, sample_entry):
        """When yt-dlp per-video fetch returns a date, it should override the flat-playlist date."""
        real_date = datetime(2026, 2, 14, tzinfo=timezone.utc)
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=("text", ())):
                with patch("src.fetchers.youtube._get_video_upload_date", return_value=real_date):
                    result = fetch_new_videos(
                        sample_source,
                        processed_ids=set(),
                        lookback_hours=168,
                        max_videos=3,
                    )
                    assert result[0].upload_date == real_date

    def test_falls_back_to_flat_playlist_date_when_real_fails(self, sample_source, sample_entry):
        """When per-video date fetch fails, should use the flat-playlist date."""
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=("text", ())):
                with patch("src.fetchers.youtube._get_video_upload_date", return_value=None):
                    result = fetch_new_videos(
                        sample_source,
                        processed_ids=set(),
                        lookback_hours=26,
                        max_videos=3,
                    )
                    # Should use the flat-playlist date (today) since real fetch returned None
                    assert result[0].upload_date is not None
                    assert result[0].upload_date.date() == datetime.now(timezone.utc).date()

    def test_video_inherits_source_language(self, sample_entry):
        source = YouTubeSource(
            channel_url="https://www.youtube.com/@TestChannel",
            name="Test Channel",
            category="AI",
            language="es",
        )
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_transcript", return_value=("texto", ())):
                result = fetch_new_videos(
                    source,
                    processed_ids=set(),
                    lookback_hours=26,
                    max_videos=3,
                )
                assert result[0].language == "es"


class TestGetTranscriptLanguage:
    def test_english_default_languages(self):
        snippets = make_snippets(("Hello", 0.0))

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.return_value = snippets
            _get_transcript("abc123", language="en")

        mock_yta.fetch.assert_called_once_with("abc123", languages=["en", "en-US", "en-GB"])

    def test_spanish_language_priority(self):
        snippets = make_snippets(("Hola", 0.0))

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.return_value = snippets
            text, _ = _get_transcript("abc123", language="es")

        mock_yta.fetch.assert_called_once_with("abc123", languages=["es", "en", "en-US", "en-GB"])
        assert text == "Hola"

    def test_hebrew_language_priority(self):
        snippets = make_snippets(("שלום", 0.0))

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.return_value = snippets
            text, _ = _get_transcript("abc123", language="he")

        mock_yta.fetch.assert_called_once_with("abc123", languages=["he", "en", "en-US", "en-GB"])
        assert text == "שלום"

    def test_falls_back_to_any_available_language(self):
        """When preferred languages fail, should try listing all transcripts."""
        from youtube_transcript_api._errors import NoTranscriptFound

        snippets = make_snippets(("Hola mundo", 0.0))
        mock_transcript_obj = MagicMock()
        mock_transcript_obj.language_code = "es"
        mock_transcript_obj.fetch.return_value = snippets

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.side_effect = NoTranscriptFound("abc123", [], [])
            mock_yta.list.return_value = [mock_transcript_obj]
            text, _ = _get_transcript("abc123", language="es")

        assert text == "Hola mundo"
        mock_yta.list.assert_called_once_with("abc123")

    def test_fallback_returns_none_when_no_transcripts_at_all(self):
        """When no transcripts exist at all, should return None gracefully."""
        from youtube_transcript_api._errors import NoTranscriptFound

        with patch("src.fetchers.youtube._yta") as mock_yta:
            mock_yta.fetch.side_effect = NoTranscriptFound("abc123", [], [])
            mock_yta.list.return_value = []
            text, segments = _get_transcript("abc123", language="es")

        assert text is None
        assert segments == ()


class TestGetVideoUploadDate:
    def test_successful_date_fetch(self):
        mock_result = MagicMock(returncode=0, stdout="20260215\n", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _get_video_upload_date("abc123")

        assert result == datetime(2026, 2, 15, tzinfo=timezone.utc)

    def test_uses_correct_yt_dlp_command(self):
        mock_result = MagicMock(returncode=0, stdout="20260215\n", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _get_video_upload_date("abc123")

        cmd = mock_run.call_args[0][0]
        assert "--no-download" in cmd
        assert "%(upload_date)s" in cmd
        assert "https://www.youtube.com/watch?v=abc123" in cmd

    def test_returns_none_on_error(self):
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with patch("subprocess.run", return_value=mock_result):
            result = _get_video_upload_date("abc123")
        assert result is None

    def test_returns_none_on_timeout(self):
        import subprocess as sp
        with patch("subprocess.run", side_effect=sp.TimeoutExpired("cmd", 30)):
            result = _get_video_upload_date("abc123")
        assert result is None

    def test_returns_none_on_invalid_date(self):
        mock_result = MagicMock(returncode=0, stdout="NA\n", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _get_video_upload_date("abc123")
        assert result is None

    def test_returns_none_on_empty_output(self):
        mock_result = MagicMock(returncode=0, stdout="\n", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = _get_video_upload_date("abc123")
        assert result is None


class TestFetchNewVideosDateFallback:
    """Test that fetch_new_videos fetches the real date when flat-playlist omits it."""

    def test_fetches_real_date_when_flat_playlist_has_none(self, sample_source):
        entry_no_date = {
            "id": "vid1",
            "title": "Video Without Date",
            "upload_date": None,
            "duration": 300,
        }
        real_date = datetime.now(timezone.utc) - timedelta(hours=2)

        with patch("src.fetchers.youtube._get_channel_entries", return_value=[entry_no_date]):
            with patch("src.fetchers.youtube._get_video_upload_date", return_value=real_date) as mock_date:
                with patch("src.fetchers.youtube._get_transcript", return_value=("text", ())):
                    result = fetch_new_videos(
                        sample_source,
                        processed_ids=set(),
                        lookback_hours=26,
                        max_videos=3,
                    )

        mock_date.assert_called_once_with("vid1")
        assert len(result) == 1
        assert result[0].upload_date == real_date

    def test_uses_flat_playlist_date_when_real_fetch_fails(self, sample_source, sample_entry):
        """When _get_video_upload_date returns None, should use flat-playlist date."""
        with patch("src.fetchers.youtube._get_channel_entries", return_value=[sample_entry]):
            with patch("src.fetchers.youtube._get_video_upload_date", return_value=None) as mock_date:
                with patch("src.fetchers.youtube._get_transcript", return_value=("text", ())):
                    result = fetch_new_videos(
                        sample_source,
                        processed_ids=set(),
                        lookback_hours=26,
                        max_videos=3,
                    )

        mock_date.assert_called_once_with("abc123")
        assert len(result) == 1
        # Uses flat-playlist date (today) since real fetch returned None
        assert result[0].upload_date.date() == datetime.now(timezone.utc).date()

    def test_falls_back_to_now_when_both_fail(self, sample_source):
        entry_no_date = {
            "id": "vid1",
            "title": "Video No Date Anywhere",
            "upload_date": None,
            "duration": 300,
        }

        with patch("src.fetchers.youtube._get_channel_entries", return_value=[entry_no_date]):
            with patch("src.fetchers.youtube._get_video_upload_date", return_value=None):
                with patch("src.fetchers.youtube._get_transcript", return_value=("text", ())):
                    result = fetch_new_videos(
                        sample_source,
                        processed_ids=set(),
                        lookback_hours=26,
                        max_videos=3,
                    )

        assert len(result) == 1
        diff = datetime.now(timezone.utc) - result[0].upload_date
        assert diff.total_seconds() < 60

    def test_fallback_path_also_fetches_real_date(self, sample_source):
        """The guaranteed-latest-video fallback should also fetch real dates."""
        old_entry_no_date = {
            "id": "old1",
            "title": "Old Video",
            "upload_date": None,
            "duration": 300,
        }
        real_date = datetime.now(timezone.utc) - timedelta(days=30)

        with patch("src.fetchers.youtube._get_channel_entries", return_value=[old_entry_no_date]):
            with patch("src.fetchers.youtube._get_video_upload_date", return_value=real_date):
                with patch("src.fetchers.youtube._get_transcript", return_value=(None, ())):
                    result = fetch_new_videos(
                        sample_source,
                        processed_ids=set(),
                        lookback_hours=26,
                        max_videos=3,
                    )

        assert len(result) == 1
        assert result[0].upload_date == real_date
