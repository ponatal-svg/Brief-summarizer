"""Tests for configuration loading and validation."""

import pytest
import yaml
from pathlib import Path

from src.config import (
    Config, Category, YouTubeSource, PodcastShow, Settings,
    ConfigError, load_config, _parse_config,
    _parse_podcast_shows,
)


@pytest.fixture
def valid_raw_config():
    return {
        "categories": [
            {"name": "AI", "color": "#4A90D9"},
            {"name": "Wellbeing", "color": "#27AE60"},
        ],
        "sources": {
            "youtube": [
                {
                    "channel_url": "https://www.youtube.com/@test",
                    "name": "Test Channel",
                    "category": "AI",
                },
            ],
            "podcasts": [
                {
                    "podcast_url": "https://open.spotify.com/show/abc123",
                    "name": "Test Podcast",
                    "category": "AI",
                },
            ],
        },
        "settings": {
            "max_age_days": 7,
            "gemini_model": "gemini-2.0-flash",
            "max_videos_per_channel": 3,
            "lookback_hours": 26,
            "max_episodes_per_show": 3,
            "min_episodes_per_show": 1,
            "max_audio_minutes": 60,
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


class TestParsePodcastShows:
    def test_valid_podcast_show(self, valid_raw_config):
        config = _parse_config(valid_raw_config)
        assert len(config.podcast_shows) == 1
        show = config.podcast_shows[0]
        assert show.podcast_url == "https://open.spotify.com/show/abc123"
        assert show.name == "Test Podcast"
        assert show.category == "AI"
        assert show.language == "en"  # default

    def test_podcast_language_override(self):
        raw = {
            "categories": [{"name": "Wellbeing"}],
            "sources": {
                "podcasts": [
                    {
                        "podcast_url": "https://open.spotify.com/show/xyz",
                        "name": "Spanish Podcast",
                        "category": "Wellbeing",
                        "language": "es",
                    }
                ]
            },
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.podcast_shows[0].language == "es"

    def test_missing_podcast_url(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {
                "podcasts": [{"name": "No URL Podcast", "category": "AI"}]
            },
            "settings": {},
        }
        with pytest.raises(ConfigError, match="missing 'podcast_url'"):
            _parse_config(raw)

    def test_missing_podcast_name(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {
                "podcasts": [
                    {"podcast_url": "https://spotify.com/show/x", "category": "AI"}
                ]
            },
            "settings": {},
        }
        with pytest.raises(ConfigError, match="missing 'name'"):
            _parse_config(raw)

    def test_undefined_podcast_category(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {
                "podcasts": [
                    {
                        "podcast_url": "https://spotify.com/show/x",
                        "name": "Test",
                        "category": "NonExistent",
                    }
                ]
            },
            "settings": {},
        }
        with pytest.raises(ConfigError, match="undefined category"):
            _parse_config(raw)

    def test_empty_podcasts_list(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"podcasts": []},
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.podcast_shows == []

    def test_null_podcasts_treated_as_empty(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"podcasts": None},
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.podcast_shows == []

    def test_no_podcasts_key(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.podcast_shows == []

    def test_podcast_not_list(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"podcasts": {"not": "a list"}},
            "settings": {},
        }
        with pytest.raises(ConfigError, match="must be a list"):
            _parse_config(raw)


class TestPodcastSettings:
    def test_podcast_setting_defaults(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {},
        }
        config = _parse_config(raw)
        assert config.settings.max_episodes_per_show == 3
        assert config.settings.min_episodes_per_show == 1
        assert config.settings.max_audio_minutes == 60

    def test_custom_podcast_settings(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {
                "max_episodes_per_show": 5,
                "min_episodes_per_show": 2,
                "max_audio_minutes": 30,
            },
        }
        config = _parse_config(raw)
        assert config.settings.max_episodes_per_show == 5
        assert config.settings.min_episodes_per_show == 2
        assert config.settings.max_audio_minutes == 30

    def test_invalid_max_audio_minutes_type(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {"max_audio_minutes": "sixty"},
        }
        with pytest.raises(ConfigError, match="must be int"):
            _parse_config(raw)

    def test_non_positive_max_audio_minutes(self):
        raw = {
            "categories": [{"name": "AI"}],
            "sources": {"youtube": []},
            "settings": {"max_audio_minutes": 0},
        }
        with pytest.raises(ConfigError, match="must be positive"):
            _parse_config(raw)


class TestConfigProperties:
    def test_category_names(self, valid_raw_config):
        config = _parse_config(valid_raw_config)
        assert config.category_names == {"AI", "Wellbeing"}
