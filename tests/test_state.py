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
    mark_youtube_processed,
    mark_podcast_processed,
    update_rss_cache,
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
        mark_youtube_processed(state, "v1", "2026-02-16")
        assert state["youtube"]["v1"] == "2026-02-16"

    def test_migrates_legacy_flat_state(self):
        # Legacy state with flat video IDs at root level
        state = {"v_old": "2026-02-01"}
        mark_youtube_processed(state, "v_new", "2026-02-16")
        # Old entries should be migrated into youtube section
        assert state["youtube"]["v_old"] == "2026-02-01"
        assert state["youtube"]["v_new"] == "2026-02-16"
        # Root level should be clean
        assert "v_old" not in state

    def test_appends_to_existing_youtube_section(self):
        state = {"youtube": {"v1": "2026-02-15"}}
        mark_youtube_processed(state, "v2", "2026-02-16")
        assert state["youtube"]["v1"] == "2026-02-15"
        assert state["youtube"]["v2"] == "2026-02-16"


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
