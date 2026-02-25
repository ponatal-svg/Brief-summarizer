"""Tests for state management."""

from __future__ import annotations

import json

import pytest

from src.state import (
    load_state,
    save_state,
    get_processed_ids,
    get_processed_podcast_ids,
    get_rss_cache,
    get_ip_blocked,
    mark_youtube_processed,
    mark_podcast_processed,
    mark_ip_blocked,
    promote_ip_blocked,
    expire_ip_blocked,
    update_rss_cache,
    _IP_BLOCKED_TTL_DAYS,
)


class TestLoadState:
    def test_load_existing(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"v1": "2026-02-16"}))

        result = load_state(state_path)
        assert result == {"v1": "2026-02-16"}

    def test_load_missing_file(self, tmp_path):
        result = load_state(tmp_path / "nonexistent.json")
        assert result == {}

    def test_corrupted_json_returns_empty(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text("not valid json {{{")
        result = load_state(state_path)
        assert result == {}


class TestSaveState:
    def test_save_and_reload(self, tmp_path):
        state_path = tmp_path / "state.json"
        state = {"youtube": {"v1": "2026-02-16"}, "podcasts": {"ep1": "2026-02-16"}}

        save_state(state_path, state)

        result = json.loads(state_path.read_text())
        assert result == state

    def test_overwrites_existing(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"old": "data"}))

        save_state(state_path, {"new": "data"})

        result = json.loads(state_path.read_text())
        assert result == {"new": "data"}

    def test_atomic_write_via_tmp_file(self, tmp_path):
        """Save should write to .tmp then rename, not truncate in place."""
        state_path = tmp_path / "state.json"
        state = {"youtube": {"v1": "2026-02-16"}}
        save_state(state_path, state)
        # .tmp file should be gone after successful save
        assert not (tmp_path / "state.tmp").exists()
        assert state_path.exists()


class TestGetProcessedIds:
    def test_nested_format(self):
        state = {"youtube": {"v1": "2026-02-16", "v2": "2026-02-15"}}
        result = get_processed_ids(state)
        assert result == {"v1", "v2"}

    def test_legacy_flat_format(self):
        # Pre-podcast state files had flat {video_id: date} format
        state = {"v1": "2026-02-16", "v2": "2026-02-15"}
        result = get_processed_ids(state)
        assert result == {"v1", "v2"}

    def test_legacy_excludes_reserved_keys(self):
        state = {
            "v1": "2026-02-16",
            "podcasts": {"ep1": "2026-02-16"},
            "rss_cache": {},
        }
        result = get_processed_ids(state)
        assert result == {"v1"}

    def test_empty_state(self):
        assert get_processed_ids({}) == set()


class TestGetProcessedPodcastIds:
    def test_returns_episode_ids(self):
        state = {"podcasts": {"ep1": "2026-02-16", "ep2": "2026-02-15"}}
        result = get_processed_podcast_ids(state)
        assert result == {"ep1", "ep2"}

    def test_empty_when_no_podcasts_key(self):
        assert get_processed_podcast_ids({}) == set()

    def test_empty_podcasts_section(self):
        assert get_processed_podcast_ids({"podcasts": {}}) == set()


class TestGetRssCache:
    def test_returns_cache(self):
        state = {"rss_cache": {"https://spotify/show1": "https://feeds.example.com/rss"}}
        result = get_rss_cache(state)
        assert result == {"https://spotify/show1": "https://feeds.example.com/rss"}

    def test_empty_when_missing(self):
        assert get_rss_cache({}) == {}

    def test_returns_copy_not_reference(self):
        state = {"rss_cache": {"k": "v"}}
        cache = get_rss_cache(state)
        cache["new_key"] = "new_val"
        # Should not mutate state
        assert "new_key" not in state["rss_cache"]


class TestMarkYoutubeProcessed:
    def test_adds_to_youtube_section(self):
        state = {}
        mark_youtube_processed(state, "v1", "2026-02-16", channel="Test Ch", title="My Video")
        entry = state["youtube"]["v1"]
        assert entry["date"] == "2026-02-16"
        assert entry["channel"] == "Test Ch"
        assert entry["title"] == "My Video"

    def test_adds_without_optional_fields(self):
        state = {}
        mark_youtube_processed(state, "v1", "2026-02-16")
        entry = state["youtube"]["v1"]
        assert entry["date"] == "2026-02-16"
        assert entry["channel"] == ""
        assert entry["title"] == ""

    def test_migrates_legacy_flat_state(self):
        # Legacy state with flat video IDs at root level
        state = {"v_old": "2026-02-01"}
        mark_youtube_processed(state, "v_new", "2026-02-16")
        # Old entries should be migrated into youtube section
        assert "v_old" in state["youtube"]
        assert "v_new" in state["youtube"]
        assert state["youtube"]["v_new"]["date"] == "2026-02-16"
        # Root level should be clean
        assert "v_old" not in state

    def test_appends_to_existing_youtube_section(self):
        state = {"youtube": {"v1": "2026-02-15"}}  # legacy value
        mark_youtube_processed(state, "v2", "2026-02-16")
        assert "v1" in state["youtube"]
        assert state["youtube"]["v2"]["date"] == "2026-02-16"


class TestMarkPodcastProcessed:
    def test_adds_to_podcasts_section(self):
        state = {}
        mark_podcast_processed(state, "ep123", "2026-02-16")
        assert state["podcasts"]["ep123"] == "2026-02-16"

    def test_appends_to_existing(self):
        state = {"podcasts": {"ep1": "2026-02-15"}}
        mark_podcast_processed(state, "ep2", "2026-02-16")
        assert "ep1" in state["podcasts"]
        assert "ep2" in state["podcasts"]


class TestUpdateRssCache:
    def test_sets_rss_cache(self):
        state = {}
        update_rss_cache(state, {"https://spotify/show1": "https://feeds.example.com"})
        assert state["rss_cache"]["https://spotify/show1"] == "https://feeds.example.com"

    def test_overwrites_existing_cache(self):
        state = {"rss_cache": {"old_key": "old_val"}}
        update_rss_cache(state, {"new_key": "new_val"})
        assert state["rss_cache"] == {"new_key": "new_val"}


class TestIpBlocked:
    def test_mark_and_get(self):
        state = {}
        mark_ip_blocked(state, "vid1", "Test Title", "https://yt.com/v=vid1", "2026-02-23")
        blocked = get_ip_blocked(state)
        assert "vid1" in blocked
        assert blocked["vid1"]["title"] == "Test Title"
        assert blocked["vid1"]["date"] == "2026-02-23"

    def test_get_returns_copy(self):
        """get_ip_blocked returns a shallow copy — adding/removing keys doesn't affect state."""
        state = {}
        mark_ip_blocked(state, "vid1", "T", "u", "2026-02-23")
        blocked = get_ip_blocked(state)
        blocked["new_key"] = "injected"
        assert "new_key" not in state.get("ip_blocked", {})

    def test_mark_overwrites_existing(self):
        state = {}
        mark_ip_blocked(state, "vid1", "Old", "u", "2026-02-20")
        mark_ip_blocked(state, "vid1", "New", "u", "2026-02-23")
        assert get_ip_blocked(state)["vid1"]["title"] == "New"

    def test_promote_moves_to_youtube_and_removes_from_blocked(self):
        state = {}
        mark_ip_blocked(state, "vid1", "T", "u", "2026-02-23")
        promote_ip_blocked(state, "vid1", "2026-02-23")
        assert "vid1" not in get_ip_blocked(state)
        assert "vid1" in get_processed_ids(state)

    def test_promote_noop_if_not_blocked(self):
        state = {}
        promote_ip_blocked(state, "vid_missing", "2026-02-23")  # should not raise
        assert "vid_missing" in get_processed_ids(state)

    def test_expire_removes_old_entries(self):
        from datetime import datetime, timedelta, timezone
        old_date = (datetime.now(timezone.utc) - timedelta(days=_IP_BLOCKED_TTL_DAYS + 1)).strftime("%Y-%m-%d")
        state = {}
        mark_ip_blocked(state, "old_vid", "Old", "u", old_date)
        expired = expire_ip_blocked(state)
        assert "old_vid" in expired
        assert "old_vid" not in get_ip_blocked(state)

    def test_expire_keeps_recent_entries(self):
        state = {}
        mark_ip_blocked(state, "new_vid", "New", "u", "2026-02-23")
        expired = expire_ip_blocked(state)
        assert "new_vid" not in expired
        assert "new_vid" in get_ip_blocked(state)

    def test_expire_removes_malformed_date_entries(self):
        state = {"ip_blocked": {"bad_vid": {"date": "not-a-date", "title": "X", "url": "u"}}}
        expired = expire_ip_blocked(state)
        assert "bad_vid" in expired

    def test_empty_state_no_error(self):
        state = {}
        assert get_ip_blocked(state) == {}
        assert expire_ip_blocked(state) == []


# ---------------------------------------------------------------------------
# Tests: save_state OSError
# ---------------------------------------------------------------------------

class TestSaveStateErrors:
    def test_save_state_oserror_raises(self, tmp_path):
        """save_state re-raises OSError after logging."""
        from src.state import save_state
        state_path = tmp_path / "state.json"
        state = {"youtube": {}, "podcasts": {}}

        # Make the directory read-only so write fails
        tmp_path.chmod(0o444)
        try:
            with pytest.raises(OSError):
                save_state(state_path, state)
        finally:
            tmp_path.chmod(0o755)  # restore for cleanup


# ---------------------------------------------------------------------------
# Tests: get_youtube_entries — legacy plain-string values normalised
# ---------------------------------------------------------------------------

class TestGetYoutubeEntriesLegacy:
    def test_plain_date_string_normalised_to_dict(self):
        from src.state import get_youtube_entries
        state = {"youtube": {"vid1": "2026-02-01"}}
        result = get_youtube_entries(state)
        assert result["vid1"] == {"date": "2026-02-01", "channel": "", "title": ""}

    def test_mixed_legacy_and_rich_format(self):
        from src.state import get_youtube_entries
        state = {"youtube": {
            "vid_old": "2026-02-01",
            "vid_new": {"date": "2026-02-02", "channel": "Ch", "title": "T"},
        }}
        result = get_youtube_entries(state)
        assert result["vid_old"]["date"] == "2026-02-01"
        assert result["vid_old"]["channel"] == ""
        assert result["vid_new"]["channel"] == "Ch"

    def test_empty_youtube_section(self):
        from src.state import get_youtube_entries
        assert get_youtube_entries({}) == {}
        assert get_youtube_entries({"youtube": {}}) == {}
