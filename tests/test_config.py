"""Tests for configuration loading and validation."""

import pytest
import yaml
from pathlib import Path

from src.config import (
    Config, Category, YouTubeSource, Settings,
    ConfigError, load_config, _parse_config,
)


@pytest.fixture
def valid_raw_config():
    return {
        "categories": [
            {"name": "AI", "color": "#4A90D9"},
            {"name": "Health", "color": "#27AE60"},
        ],
        "sources": {
            "youtube": [
                {
                    "channel_url": "https://www.youtube.com/@test",
                    "name": "Test Channel",
                    "category": "AI",
                },
            ],
        },
        "settings": {
            "max_age_days": 7,
            "gemini_model": "gemini-2.0-flash",
            "max_videos_per_channel": 3,
            "lookback_hours": 26,
        },
    }


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path, valid_raw_config):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(valid_raw_config))

        config = load_config(config_path)

        assert len(config.categories) == 2
        assert config.categories[0].name == "AI"
        assert len(config.youtube_sources) == 1
        assert config.settings.max_age_days == 7

    def test_file_not_found(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_content(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("just a string")

        with pytest.raises(ConfigError, match="must be a YAML mapping"):
            load_config(config_path)


class TestParseCategories:
    def test_no_categories(self):
        with pytest.raises(ConfigError, match="At least one category"):
            _parse_config({"categories": [], "sources": {}, "settings": {}})

    def test_duplicate_category(self):
        raw = {
            "categories": [
                {"name": "AI", "color": "#000"},
                {"name": "AI", "color": "#111"},
            ],
            "sources": {"youtube": []},
            "settings": {},
        }
        with pytest.raises(ConfigError, match="Duplicate category"):
            _parse_config(raw)

    def test_category_missing_name(self):
        raw = {
            "categories": [{"color": "#000"}],
            "sources": {"youtube": []},
            "settings": {},
        }
        with pytest.raises(ConfigError, match="missing 'name'"):
            _parse_config(raw)

    def test_default_color(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.categories[0].color == "#888888"


class TestParseYouTubeSources:
    def test_valid_source(self, valid_raw_config):
        config = _parse_config(valid_raw_config)
        src = config.youtube_sources[0]
        assert src.channel_url == "https://www.youtube.com/@test"
        assert src.name == "Test Channel"
        assert src.category == "AI"

    def test_undefined_category(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {
                "youtube": [
                    {
                        "channel_url": "https://youtube.com/@x",
                        "name": "X",
                        "category": "NonExistent",
                    }
                ]
            },
            "settings": {},
        }
        with pytest.raises(ConfigError, match="undefined category"):
            _parse_config(raw)

    def test_missing_channel_url(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {
                "youtube": [{"name": "X", "category": "AI"}]
            },
            "settings": {},
        }
        with pytest.raises(ConfigError, match="missing 'channel_url'"):
            _parse_config(raw)

    def test_missing_name(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {
                "youtube": [
                    {"channel_url": "https://youtube.com/@x", "category": "AI"}
                ]
            },
            "settings": {},
        }
        with pytest.raises(ConfigError, match="missing 'name'"):
            _parse_config(raw)

    def test_missing_category(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {
                "youtube": [
                    {"channel_url": "https://youtube.com/@x", "name": "X"}
                ]
            },
            "settings": {},
        }
        with pytest.raises(ConfigError, match="missing 'category'"):
            _parse_config(raw)

    def test_empty_youtube_list(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.youtube_sources == []


class TestParseSettings:
    def test_defaults(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.settings.max_age_days == 7
        assert config.settings.gemini_model == "gemini-2.0-flash"
        assert config.settings.max_videos_per_channel == 3
        assert config.settings.lookback_hours == 26

    def test_custom_settings(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {"max_age_days": 14, "lookback_hours": 48},
        }
        config = _parse_config(raw)
        assert config.settings.max_age_days == 14
        assert config.settings.lookback_hours == 48

    def test_invalid_type(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {"max_age_days": "seven"},
        }
        with pytest.raises(ConfigError, match="must be int"):
            _parse_config(raw)

    def test_non_positive_int(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {"max_age_days": 0},
        }
        with pytest.raises(ConfigError, match="must be positive"):
            _parse_config(raw)


class TestConfigProperties:
    def test_category_names(self, valid_raw_config):
        config = _parse_config(valid_raw_config)
        assert config.category_names == {"AI", "Health"}
