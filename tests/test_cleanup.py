"""Tests for cleanup module."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.cleanup import cleanup_old_content, cleanup_state, _parse_date_from_name


def _date_str(days_ago: int) -> str:
    """Helper to get a date string N days ago."""
    d = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return d.strftime("%Y-%m-%d")


class TestParseDateFromName:
    def test_valid_date(self):
        result = _parse_date_from_name("2026-02-16")
        assert result == datetime(2026, 2, 16).date()

    def test_date_with_suffix(self):
        result = _parse_date_from_name("2026-02-16-errors")
        assert result == datetime(2026, 2, 16).date()

    def test_invalid_format(self):
        assert _parse_date_from_name("not-a-date") is None

    def test_empty_string(self):
        assert _parse_date_from_name("") is None

    def test_short_string(self):
        assert _parse_date_from_name("2026") is None


class TestCleanupOldContent:
    def test_removes_old_summary_dir(self, tmp_path):
        old_date = _date_str(10)
        old_dir = tmp_path / "summaries" / old_date
        old_dir.mkdir(parents=True)
        (old_dir / "test-brief.md").write_text("old content")

        removed = cleanup_old_content(tmp_path, max_age_days=7)

        assert not old_dir.exists()
        assert len(removed) == 1

    def test_keeps_recent_summary_dir(self, tmp_path):
        recent_date = _date_str(3)
        recent_dir = tmp_path / "summaries" / recent_date
        recent_dir.mkdir(parents=True)
        (recent_dir / "test-brief.md").write_text("recent content")

        removed = cleanup_old_content(tmp_path, max_age_days=7)

        assert recent_dir.exists()
        assert len(removed) == 0

    def test_removes_old_daily_digest(self, tmp_path):
        old_date = _date_str(10)
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir(parents=True)
        old_file = daily_dir / f"{old_date}.md"
        old_file.write_text("old digest")

        removed = cleanup_old_content(tmp_path, max_age_days=7)

        assert not old_file.exists()
        assert len(removed) == 1

    def test_keeps_recent_daily_digest(self, tmp_path):
        recent_date = _date_str(3)
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir(parents=True)
        recent_file = daily_dir / f"{recent_date}.md"
        recent_file.write_text("recent digest")

        removed = cleanup_old_content(tmp_path, max_age_days=7)

        assert recent_file.exists()
        assert len(removed) == 0

    def test_removes_old_error_report(self, tmp_path):
        old_date = _date_str(10)
        errors_dir = tmp_path / "errors"
        errors_dir.mkdir(parents=True)
        old_file = errors_dir / f"{old_date}-errors.md"
        old_file.write_text("old errors")

        removed = cleanup_old_content(tmp_path, max_age_days=7)

        assert not old_file.exists()
        assert len(removed) == 1

    def test_handles_missing_directories(self, tmp_path):
        removed = cleanup_old_content(tmp_path, max_age_days=7)
        assert removed == []

    def test_ignores_non_date_directories(self, tmp_path):
        other_dir = tmp_path / "summaries" / "not-a-date"
        other_dir.mkdir(parents=True)

        removed = cleanup_old_content(tmp_path, max_age_days=7)

        assert other_dir.exists()
        assert len(removed) == 0

    def test_mixed_old_and_recent(self, tmp_path):
        old_date = _date_str(10)
        recent_date = _date_str(2)

        for d in [old_date, recent_date]:
            dir_path = tmp_path / "summaries" / d
            dir_path.mkdir(parents=True)
            (dir_path / "test.md").write_text("content")

        removed = cleanup_old_content(tmp_path, max_age_days=7)

        assert not (tmp_path / "summaries" / old_date).exists()
        assert (tmp_path / "summaries" / recent_date).exists()
        assert len(removed) == 1


class TestCleanupState:
    def test_removes_old_entries(self, tmp_path):
        state_path = tmp_path / "state.json"
        old_date = _date_str(10)
        recent_date = _date_str(2)
        state = {"old_video": old_date, "recent_video": recent_date}
        state_path.write_text(json.dumps(state))

        cleanup_state(state_path, max_age_days=7)

        result = json.loads(state_path.read_text())
        assert "old_video" not in result
        assert "recent_video" in result

    def test_keeps_all_recent(self, tmp_path):
        state_path = tmp_path / "state.json"
        recent_date = _date_str(2)
        state = {"v1": recent_date, "v2": recent_date}
        state_path.write_text(json.dumps(state))

        cleanup_state(state_path, max_age_days=7)

        result = json.loads(state_path.read_text())
        assert len(result) == 2

    def test_handles_missing_file(self, tmp_path):
        state_path = tmp_path / "state.json"
        # Should not raise
        cleanup_state(state_path, max_age_days=7)

    def test_removes_all_expired(self, tmp_path):
        state_path = tmp_path / "state.json"
        old_date = _date_str(10)
        state = {"v1": old_date, "v2": old_date}
        state_path.write_text(json.dumps(state))

        cleanup_state(state_path, max_age_days=7)

        result = json.loads(state_path.read_text())
        assert len(result) == 0
