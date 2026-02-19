"""Tests for markdown generator."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config import Category
from src.fetchers.youtube import VideoInfo
from src.fetchers.podcast import EpisodeInfo
from src.generator import (
    slugify,
    generate_summary_files,
    generate_podcast_summary_files,
    generate_daily_digest,
    generate_podcast_daily_digest,
    generate_error_report,
    _format_duration,
    _relative_path,
)


@pytest.fixture
def sample_video():
    return VideoInfo(
        video_id="abc123",
        title="Understanding Neural Networks",
        url="https://www.youtube.com/watch?v=abc123",
        channel_name="AI Channel",
        category="AI",
        upload_date=datetime(2026, 2, 16, tzinfo=timezone.utc),
        duration_seconds=1234,
        transcript="Some transcript text",
    )


@pytest.fixture
def sample_summary():
    return "## The Hook\nNeural networks explained.\n\n## Key Findings\n- Point 1\n- Point 2\n\n## The So What?\nImplications here."


@pytest.fixture
def categories():
    return [
        Category(name="AI", color="#4A90D9"),
        Category(name="Wellbeing", color="#27AE60"),
    ]


@pytest.fixture
def sample_episode():
    return EpisodeInfo(
        episode_id="ep_abc123",
        title="AI in 2026",
        show_name="Hard Fork",
        show_url="https://open.spotify.com/show/abc",
        episode_url="https://hardfork.example.com/ep1",
        audio_url="https://cdn.example.com/ep1.mp3",
        category="AI",
        published_at=datetime(2026, 2, 19, tzinfo=timezone.utc),
        duration_seconds=3600,
        language="en",
    )


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_characters(self):
        assert slugify("What's New? #AI & ML!") == "whats-new-ai-ml"

    def test_long_text_truncated(self):
        long_title = "A" * 100
        assert len(slugify(long_title)) <= 80

    def test_empty_string(self):
        assert slugify("") == ""

    def test_unicode(self):
        result = slugify("Cafe & Music")
        assert "cafe" in result

    def test_multiple_spaces(self):
        assert slugify("hello   world") == "hello-world"


class TestFormatDuration:
    def test_seconds_only(self):
        assert _format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "2m 5s"

    def test_hours_and_minutes(self):
        assert _format_duration(3720) == "1h 2m"

    def test_zero(self):
        assert _format_duration(0) == "Unknown duration"

    def test_negative(self):
        assert _format_duration(-10) == "Unknown duration"

    def test_exact_minute(self):
        assert _format_duration(60) == "1m 0s"


class TestGenerateSummaryFiles:
    def test_creates_file(self, tmp_path, sample_video, sample_summary):
        result = generate_summary_files(
            video=sample_video,
            summary=sample_summary,
            output_dir=tmp_path,
            date_str="2026-02-16",
        )

        assert result["summary_path"].exists()
        assert result["slug"] == "understanding-neural-networks"

    def test_summary_content(self, tmp_path, sample_video, sample_summary):
        result = generate_summary_files(
            video=sample_video,
            summary=sample_summary,
            output_dir=tmp_path,
            date_str="2026-02-16",
        )

        content = result["summary_path"].read_text()
        assert "Understanding Neural Networks" in content
        assert "AI Channel" in content
        assert "The Hook" in content
        assert "Key Findings" in content
        assert "20m 34s" in content
        assert "**Language:** en" in content

    def test_directory_structure(self, tmp_path, sample_video, sample_summary):
        generate_summary_files(
            video=sample_video,
            summary=sample_summary,
            output_dir=tmp_path,
            date_str="2026-02-16",
        )

        assert (tmp_path / "summaries" / "2026-02-16").is_dir()


class TestGenerateDailyDigest:
    def test_empty_entries(self, tmp_path, categories):
        path = generate_daily_digest([], tmp_path, "2026-02-16", categories)
        content = path.read_text()
        assert "No new content found today" in content

    def test_with_entries(self, tmp_path, sample_video, categories):
        summary_path = tmp_path / "summaries" / "2026-02-16" / "test.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.touch()

        entries = [
            {
                "video": sample_video,
                "paths": {
                    "summary_path": summary_path,
                    "slug": "test",
                },
                "error": None,
            }
        ]

        path = generate_daily_digest(entries, tmp_path, "2026-02-16", categories)
        content = path.read_text()

        assert "Morning Brief - 2026-02-16" in content
        assert "## AI" in content
        assert "Understanding Neural Networks" in content
        assert "AI Channel" in content
        assert "1 item(s)" in content
        assert "[Summary]" in content
        assert "2026-02-16" in content  # publish date in metadata

    def test_entry_with_error(self, tmp_path, sample_video, categories):
        entries = [
            {
                "video": sample_video,
                "paths": None,
                "error": "No captions available",
            }
        ]

        path = generate_daily_digest(entries, tmp_path, "2026-02-16", categories)
        content = path.read_text()
        assert "No captions available" in content

    def test_groups_by_category(self, tmp_path, categories):
        video_ai = VideoInfo(
            video_id="v1", title="AI Video", url="https://yt.com/v1",
            channel_name="Ch1", category="AI",
            upload_date=datetime.now(timezone.utc),
            duration_seconds=300, transcript=None,
        )
        video_health = VideoInfo(
            video_id="v2", title="Health Video", url="https://yt.com/v2",
            channel_name="Ch2", category="Health",
            upload_date=datetime.now(timezone.utc),
            duration_seconds=600, transcript=None,
        )

        entries = [
            {"video": video_health, "paths": None, "error": "err"},
            {"video": video_ai, "paths": None, "error": "err"},
        ]

        path = generate_daily_digest(entries, tmp_path, "2026-02-16", categories)
        content = path.read_text()

        # AI should come before Health (config order)
        ai_pos = content.index("## AI")
        health_pos = content.index("## Health")
        assert ai_pos < health_pos

    def test_creates_daily_directory(self, tmp_path, categories):
        generate_daily_digest([], tmp_path, "2026-02-16", categories)
        assert (tmp_path / "daily").is_dir()


class TestGenerateDailyDigestMerge:
    """Tests for merging YouTube digest with existing entries."""

    def test_merges_existing_entries(self, tmp_path, categories):
        """New entries should be merged with existing entries in today's digest."""
        existing = (
            "# Morning Brief - 2026-02-19\n\n"
            "## AI\n\n"
            "### Old Video\n"
            "**Old Channel** | 10m 0s | 2026-02-19 | [Watch](https://youtube.com/watch?v=oldvid)\n\n"
            "[Summary](summaries/2026-02-19/old-video.md)\n\n"
        )
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir()
        (daily_dir / "2026-02-19.md").write_text(existing)

        video = VideoInfo(
            video_id="newvid", title="New Video", url="https://youtube.com/watch?v=newvid",
            channel_name="New Channel", category="AI",
            upload_date=datetime(2026, 2, 19, tzinfo=timezone.utc),
            duration_seconds=300, transcript="text",
        )
        summary_path = tmp_path / "summaries" / "2026-02-19" / "new-video.md"
        summary_path.parent.mkdir(parents=True)
        summary_path.touch()

        entries = [{"video": video, "paths": {"summary_path": summary_path, "slug": "new-video"}, "error": None}]
        path = generate_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()

        assert "Old Video" in content
        assert "New Video" in content

    def test_existing_entry_used_as_stub(self, tmp_path, categories):
        """_existing entries should use summary_rel from paths dict."""
        existing = (
            "# Morning Brief - 2026-02-19\n\n"
            "## AI\n\n"
            "### Existing Video\n"
            "**Channel** | 5m 0s | 2026-02-19 | [Watch](https://youtube.com/watch?v=ex1)\n\n"
            "[Summary](summaries/2026-02-19/existing-video.md)\n\n"
        )
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir()
        (daily_dir / "2026-02-19.md").write_text(existing)

        # No new videos — empty entries list forces re-read of existing
        path = generate_daily_digest([], tmp_path, "2026-02-19", categories)
        content = path.read_text()
        assert "Existing Video" in content

    def test_unconfigured_category_youtube(self, tmp_path, categories):
        video = VideoInfo(
            video_id="v_unk", title="Unknown Cat Video",
            url="https://youtube.com/watch?v=v_unk",
            channel_name="Channel", category="UnknownCategory",
            upload_date=datetime.now(timezone.utc), duration_seconds=300, transcript=None,
        )
        entries = [{"video": video, "paths": None, "error": "err"}]
        path = generate_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()
        assert "## UnknownCategory" in content


class TestGenerateErrorReport:
    def test_no_errors_returns_none(self, tmp_path):
        result = generate_error_report([], tmp_path, "2026-02-16")
        assert result is None

    def test_with_errors(self, tmp_path):
        errors = [
            {"source": "YouTube/TestChannel", "message": "No captions available"},
            {"source": "Gemini API", "message": "Rate limit exceeded"},
        ]

        path = generate_error_report(errors, tmp_path, "2026-02-16")

        assert path is not None
        content = path.read_text()
        assert "Errors - 2026-02-16" in content
        assert "YouTube/TestChannel" in content
        assert "No captions available" in content
        assert "Rate limit exceeded" in content

    def test_creates_errors_directory(self, tmp_path):
        errors = [{"source": "test", "message": "test error"}]
        generate_error_report(errors, tmp_path, "2026-02-16")
        assert (tmp_path / "errors").is_dir()


class TestRelativePath:
    def test_valid_relative(self, tmp_path):
        child = tmp_path / "a" / "b" / "file.md"
        result = _relative_path(child, tmp_path)
        assert result == "a/b/file.md"

    def test_unrelated_paths(self):
        result = _relative_path(Path("/foo/bar"), Path("/baz"))
        assert result == "/foo/bar"


class TestGeneratePodcastSummaryFiles:
    def test_creates_file(self, tmp_path, sample_episode):
        result = generate_podcast_summary_files(
            episode=sample_episode,
            summary="## The Hook\nGreat episode.\n## Key Findings\n* One\n## The So What?\nImportant.",
            output_dir=tmp_path,
            date_str="2026-02-19",
        )

        assert result["summary_path"].exists()
        assert "hard-fork" in result["slug"]
        assert "ai-in-2026" in result["slug"]

    def test_summary_content(self, tmp_path, sample_episode):
        result = generate_podcast_summary_files(
            episode=sample_episode,
            summary="## The Hook\nGreat.\n## Key Findings\n* Finding\n## The So What?\nMatters.",
            output_dir=tmp_path,
            date_str="2026-02-19",
        )

        content = result["summary_path"].read_text()
        assert "AI in 2026" in content
        assert "Hard Fork" in content
        assert "**Language:** en" in content
        assert "2026-02-19" in content  # published date
        assert "1h 0m" in content       # formatted duration
        assert "hardfork.example.com" in content  # episode URL

    def test_directory_structure(self, tmp_path, sample_episode):
        generate_podcast_summary_files(
            episode=sample_episode,
            summary="Summary text",
            output_dir=tmp_path,
            date_str="2026-02-19",
        )
        assert (tmp_path / "podcast-summaries" / "2026-02-19").is_dir()


class TestGeneratePodcastDailyDigest:
    def test_empty_entries(self, tmp_path, categories):
        path = generate_podcast_daily_digest([], tmp_path, "2026-02-19", categories)
        content = path.read_text()
        assert "No new podcast episodes found today" in content

    def test_with_entries(self, tmp_path, sample_episode, categories):
        summary_path = tmp_path / "podcast-summaries" / "2026-02-19" / "ep.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.touch()

        entries = [
            {
                "episode": sample_episode,
                "paths": {"summary_path": summary_path, "slug": "ep"},
                "error": None,
            }
        ]

        path = generate_podcast_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()

        assert "Morning Brief Podcasts - 2026-02-19" in content
        assert "## AI" in content
        assert "AI in 2026" in content
        assert "Hard Fork" in content
        assert "1 episode(s)" in content
        assert "[Summary]" in content
        assert "[Listen]" in content

    def test_entry_with_error(self, tmp_path, sample_episode, categories):
        entries = [
            {
                "episode": sample_episode,
                "paths": None,
                "error": "Audio download failed",
            }
        ]
        path = generate_podcast_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()
        assert "Audio download failed" in content

    def test_creates_podcast_daily_directory(self, tmp_path, categories):
        generate_podcast_daily_digest([], tmp_path, "2026-02-19", categories)
        assert (tmp_path / "podcast-daily").is_dir()

    def test_groups_by_category(self, tmp_path, categories):
        ep_ai = EpisodeInfo(
            episode_id="ep1", title="AI Episode", show_name="Hard Fork",
            show_url="https://spotify", episode_url="https://ep1",
            audio_url="https://cdn/ep1.mp3", category="AI",
            published_at=datetime.now(timezone.utc), duration_seconds=3600,
        )
        ep_wellbeing = EpisodeInfo(
            episode_id="ep2", title="Wellbeing Episode", show_name="Mind Podcast",
            show_url="https://spotify", episode_url="https://ep2",
            audio_url="https://cdn/ep2.mp3", category="Wellbeing",
            published_at=datetime.now(timezone.utc), duration_seconds=2700,
        )
        entries = [
            {"episode": ep_wellbeing, "paths": None, "error": "err"},
            {"episode": ep_ai, "paths": None, "error": "err"},
        ]

        path = generate_podcast_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()

        ai_pos = content.index("## AI")
        wb_pos = content.index("## Wellbeing")
        assert ai_pos < wb_pos  # AI comes first per category config order

    def test_unconfigured_category_included(self, tmp_path, categories):
        """Episodes in a category not in the config order still appear at the end."""
        ep = EpisodeInfo(
            episode_id="ep_x", title="Random Episode", show_name="Unknown Show",
            show_url="https://spotify", episode_url="https://ep_x",
            audio_url="https://cdn/ep_x.mp3", category="Unconfigured",
            published_at=datetime.now(timezone.utc), duration_seconds=1800,
        )
        entries = [{"episode": ep, "paths": None, "error": "err"}]
        path = generate_podcast_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()
        assert "## Unconfigured" in content
        assert "Random Episode" in content

    def test_existing_entry_with_bad_pub_date(self, tmp_path, categories):
        """Existing entries with unparseable pub_date should fall back gracefully."""
        existing = (
            "# Morning Brief Podcasts - 2026-02-19\n\n"
            "## AI\n\n"
            "### Old Episode\n"
            "**Hard Fork** | 1h 0m | NOT-A-DATE | [Listen](https://ep_old)\n\n"
        )
        podcast_daily = tmp_path / "podcast-daily"
        podcast_daily.mkdir()
        (podcast_daily / "2026-02-19.md").write_text(existing)

        ep = EpisodeInfo(
            episode_id="ep_new", title="New Episode", show_name="Hard Fork",
            show_url="https://spotify", episode_url="https://ep_new",
            audio_url="https://cdn/ep.mp3", category="AI",
            published_at=datetime(2026, 2, 19, tzinfo=timezone.utc), duration_seconds=3600,
        )
        entries = [{"episode": ep, "paths": None, "error": None}]
        # Should not raise despite bad date in existing
        path = generate_podcast_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()
        assert "New Episode" in content

    def test_merges_with_existing_digest(self, tmp_path, categories):
        """Episodes already in today's digest should not be duplicated."""
        ep = EpisodeInfo(
            episode_id="ep_new", title="New Episode", show_name="Hard Fork",
            show_url="https://spotify", episode_url="https://ep_new",
            audio_url="https://cdn/ep.mp3", category="AI",
            published_at=datetime(2026, 2, 19, tzinfo=timezone.utc), duration_seconds=3600,
        )

        # Write existing digest with one episode
        existing = (
            "# Morning Brief Podcasts - 2026-02-19\n\n"
            "**1 episode(s)**\n\n"
            "## AI\n\n"
            "### Old Episode\n"
            "**Hard Fork** | 1h 0m | 2026-02-18 | [Listen](https://ep_old)\n\n"
            "[Summary](podcast-summaries/2026-02-19/old-ep.md)\n\n"
        )
        podcast_daily = tmp_path / "podcast-daily"
        podcast_daily.mkdir()
        (podcast_daily / "2026-02-19.md").write_text(existing)

        entries = [{"episode": ep, "paths": None, "error": "err"}]
        path = generate_podcast_daily_digest(entries, tmp_path, "2026-02-19", categories)
        content = path.read_text()

        # Both old and new should be present
        assert "Old Episode" in content
        assert "New Episode" in content
