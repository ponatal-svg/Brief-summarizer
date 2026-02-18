"""Tests for markdown generator."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config import Category
from src.fetchers.youtube import VideoInfo
from src.generator import (
    slugify,
    generate_summary_files,
    generate_daily_digest,
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
        Category(name="Health", color="#27AE60"),
    ]


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
        assert "1 new item(s)" in content
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
