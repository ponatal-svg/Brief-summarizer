"""Integration tests for the YouTube fetcher pipeline.

These tests hit real external services (yt-dlp → YouTube).
They are SKIPPED by default and must be run explicitly with:

    python3 -m pytest tests/test_youtube_integration.py --integration -v

Design principles to avoid IP bans / rate-limiting:
- Only ONE real channel is queried per test class (AI Explained — public, active).
- Transcript fetching (YouTubeTranscriptApi) is NEVER called live here;
  it's mocked out. The unit tests in test_youtube.py cover that path.
- yt-dlp calls are kept to a minimum: 1 channel list + 1 video-date lookup.

Prerequisites:
- Internet access
- yt-dlp installed (`pip install yt-dlp`)
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetChannelEntriesLive:
    """Verify yt-dlp can list videos from a real channel."""

    def test_ai_explained_returns_entries(self):
        from src.fetchers.youtube import _get_channel_entries

        entries = _get_channel_entries(
            "https://www.youtube.com/@aiexplained-official",
            max_videos=2,
        )

        assert len(entries) >= 1
        first = entries[0]
        assert first.get("id"), "Entry should have a video id"
        assert first.get("title"), "Entry should have a title"

    def test_respects_max_videos(self):
        from src.fetchers.youtube import _get_channel_entries

        entries = _get_channel_entries(
            "https://www.youtube.com/@aiexplained-official",
            max_videos=1,
        )

        assert len(entries) <= 1

    def test_invalid_channel_returns_empty(self):
        from src.fetchers.youtube import _get_channel_entries

        entries = _get_channel_entries(
            "https://www.youtube.com/@CHANNEL_THAT_DOES_NOT_EXIST_XYZ99999",
            max_videos=2,
        )

        assert entries == []


@pytest.mark.integration
class TestGetVideoUploadDateLive:
    """Verify yt-dlp can fetch the real upload date of a single video."""

    # A stable, old public video unlikely to be taken down.
    # "Me at the zoo" — first ever YouTube video, uploaded 2005-04-23.
    STABLE_VIDEO_ID = "jNQXAC9IVRw"

    def test_returns_datetime(self):
        from src.fetchers.youtube import _get_video_upload_date

        result = _get_video_upload_date(self.STABLE_VIDEO_ID)

        assert result is not None
        assert isinstance(result, datetime)
        assert result.tzinfo is not None  # should be UTC-aware

    def test_correct_date_for_known_video(self):
        from src.fetchers.youtube import _get_video_upload_date

        result = _get_video_upload_date(self.STABLE_VIDEO_ID)

        assert result is not None
        assert result.year == 2005
        assert result.month == 4
        # YouTube may report Apr 23 or Apr 24 depending on timezone stored;
        # either is acceptable — just verify it's in the right ballpark.
        assert result.day in (23, 24)

    def test_invalid_video_id_returns_none(self):
        from src.fetchers.youtube import _get_video_upload_date

        result = _get_video_upload_date("INVALID_ID_THAT_DOES_NOT_EXIST")

        assert result is None


@pytest.mark.integration
class TestFetchNewVideosLive:
    """Verify fetch_new_videos works end-to-end for a real channel.

    Transcript fetching is mocked out to avoid hammering YouTube's
    caption API and triggering rate limits.
    """

    def test_fetch_ai_explained_no_transcript_spam(self):
        """Fetch 1 video from AI Explained; transcript call is mocked."""
        from src.config import YouTubeSource
        from src.fetchers.youtube import fetch_new_videos

        source = YouTubeSource(
            channel_url="https://www.youtube.com/@aiexplained-official",
            name="AI Explained",
            category="AI",
        )

        # Mock transcript to avoid hitting YouTube caption API
        with patch("src.fetchers.youtube._get_transcript", return_value="[transcript mocked]"):
            videos = fetch_new_videos(
                source=source,
                processed_ids=set(),
                lookback_hours=24 * 365,  # wide window — guarantee at least 1 result
                max_videos=1,
            )

        assert len(videos) >= 1
        video = videos[0]
        assert video.video_id
        assert video.title
        assert video.url.startswith("https://www.youtube.com/watch?v=")
        assert video.channel_name == "AI Explained"
        assert video.category == "AI"
        assert isinstance(video.upload_date, datetime)
        assert video.upload_date.tzinfo is not None
        assert video.transcript == "[transcript mocked]"

    def test_already_processed_videos_skipped(self):
        """Videos in processed_ids are filtered out; fallback kicks in."""
        from src.config import YouTubeSource
        from src.fetchers.youtube import _get_channel_entries, fetch_new_videos

        source = YouTubeSource(
            channel_url="https://www.youtube.com/@aiexplained-official",
            name="AI Explained",
            category="AI",
        )

        # Grab the real latest video id so we can mark it as processed
        entries = _get_channel_entries(source.channel_url, max_videos=1)
        assert entries, "Need at least one entry for this test"
        latest_id = entries[0]["id"]

        with patch("src.fetchers.youtube._get_transcript", return_value=None):
            # If the only video is already processed AND outside lookback,
            # fetch_new_videos returns [] (no forced fallback for already-seen)
            videos = fetch_new_videos(
                source=source,
                processed_ids={latest_id},
                lookback_hours=1,   # very short so only truly new videos pass
                max_videos=1,
            )

        # latest video was processed — result is either empty or a different video
        for v in videos:
            assert v.video_id != latest_id
