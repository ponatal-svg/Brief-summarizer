"""Tests for state management."""

from __future__ import annotations

import json

import pytest

from src.state import load_state, save_state, get_processed_ids


class TestLoadState:
    def test_load_existing(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"v1": "2026-02-16"}))

        result = load_state(state_path)
        assert result == {"v1": "2026-02-16"}

    def test_load_missing_file(self, tmp_path):
        result = load_state(tmp_path / "nonexistent.json")
        assert result == {}


class TestSaveState:
    def test_save_and_reload(self, tmp_path):
        state_path = tmp_path / "state.json"
        state = {"v1": "2026-02-16", "v2": "2026-02-15"}

        save_state(state_path, state)

        result = json.loads(state_path.read_text())
        assert result == state

    def test_overwrites_existing(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"old": "data"}))

        save_state(state_path, {"new": "data"})

        result = json.loads(state_path.read_text())
        assert result == {"new": "data"}


class TestGetProcessedIds:
    def test_returns_set_of_keys(self):
        state = {"v1": "2026-02-16", "v2": "2026-02-15"}
        result = get_processed_ids(state)
        assert result == {"v1", "v2"}

    def test_empty_state(self):
        assert get_processed_ids({}) == set()
