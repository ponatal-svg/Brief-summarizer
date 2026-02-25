"""Load and validate the Morning Brief configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass(frozen=True)
class Category:
    name: str
    color: str


@dataclass(frozen=True)
class YouTubeSource:
    channel_url: str
    name: str
    category: str
    language: str = "en"


@dataclass(frozen=True)
class PodcastShow:
    podcast_url: str
    name: str
    category: str
    language: str = "en"


@dataclass(frozen=True)
class Settings:
    max_age_days: int = 7
    gemini_model: str = "gemini-2.5-flash"
    max_videos_per_channel: int = 3
    lookback_hours: int = 26
    max_episodes_per_show: int = 3
    min_episodes_per_show: int = 1
    max_audio_minutes: int = 60
    notify_email: Optional[str] = None


@dataclass(frozen=True)
class Config:
    categories: list[Category]
    youtube_sources: list[YouTubeSource]
    podcast_shows: list[PodcastShow]
    settings: Settings

    @property
    def category_names(self) -> set[str]:
        return {c.name for c in self.categories}


class ConfigError(Exception):
    """Raised when configuration is invalid."""


def load_config(path: Path) -> Config:
    """Load and validate configuration from a YAML file."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping")

    return _parse_config(raw)


def _parse_config(raw: dict) -> Config:
    categories = _parse_categories(raw.get("categories", []))
    settings = _parse_settings(raw.get("settings", {}))
    youtube_sources = _parse_youtube_sources(raw.get("sources", {}))
    podcast_shows = _parse_podcast_shows(raw.get("sources", {}))

    # Validate that all source categories reference defined categories
    category_names = {c.name for c in categories}
    for src in youtube_sources:
        if src.category not in category_names:
            raise ConfigError(
                f"YouTube source '{src.name}' references undefined "
                f"category '{src.category}'. "
                f"Defined categories: {sorted(category_names)}"
            )
    for show in podcast_shows:
        if show.category not in category_names:
            raise ConfigError(
                f"Podcast show '{show.name}' references undefined "
                f"category '{show.category}'. "
                f"Defined categories: {sorted(category_names)}"
            )

    return Config(
        categories=categories,
        youtube_sources=youtube_sources,
        podcast_shows=podcast_shows,
        settings=settings,
    )


def _parse_categories(raw: list) -> list[Category]:
    if not isinstance(raw, list) or len(raw) == 0:
        raise ConfigError("At least one category must be defined")

    categories = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ConfigError(f"Category must be a mapping, got: {item}")
        name = item.get("name")
        color = item.get("color", "#888888")
        if not name or not isinstance(name, str):
            raise ConfigError(f"Category missing 'name': {item}")
        if name in seen:
            raise ConfigError(f"Duplicate category name: '{name}'")
        seen.add(name)
        categories.append(Category(name=name, color=color))

    return categories


def _parse_youtube_sources(raw: dict) -> list[YouTubeSource]:
    if not isinstance(raw, dict):
        raise ConfigError("'sources' must be a mapping")

    youtube_raw = raw.get("youtube", [])
    if not isinstance(youtube_raw, list):
        raise ConfigError("'sources.youtube' must be a list")

    sources = []
    for item in youtube_raw:
        if not isinstance(item, dict):
            raise ConfigError(f"YouTube source must be a mapping, got: {item}")
        channel_url = item.get("channel_url")
        name = item.get("name")
        category = item.get("category")
        if not channel_url or not isinstance(channel_url, str):
            raise ConfigError(f"YouTube source missing 'channel_url': {item}")
        if not name or not isinstance(name, str):
            raise ConfigError(f"YouTube source missing 'name': {item}")
        if not category or not isinstance(category, str):
            raise ConfigError(f"YouTube source missing 'category': {item}")
        sources.append(YouTubeSource(
            channel_url=channel_url, name=name, category=category,
            language=item.get("language", "en"),
        ))

    return sources


def _parse_podcast_shows(raw: dict) -> list[PodcastShow]:
    if not isinstance(raw, dict):
        raise ConfigError("'sources' must be a mapping")

    podcasts_raw = raw.get("podcasts") or []  # None or missing both become []
    if not isinstance(podcasts_raw, list):
        raise ConfigError("'sources.podcasts' must be a list")

    shows = []
    for item in podcasts_raw:
        if not isinstance(item, dict):
            raise ConfigError(f"Podcast show must be a mapping, got: {item}")
        podcast_url = item.get("podcast_url")
        name = item.get("name")
        category = item.get("category")
        if not podcast_url or not isinstance(podcast_url, str):
            raise ConfigError(f"Podcast show missing 'podcast_url': {item}")
        if not name or not isinstance(name, str):
            raise ConfigError(f"Podcast show missing 'name': {item}")
        if not category or not isinstance(category, str):
            raise ConfigError(f"Podcast show missing 'category': {item}")
        shows.append(PodcastShow(
            podcast_url=podcast_url, name=name, category=category,
            language=item.get("language", "en"),
        ))

    return shows


def _parse_settings(raw: dict) -> Settings:
    if not isinstance(raw, dict):
        raise ConfigError("'settings' must be a mapping")

    kwargs = {}
    field_types = {
        "max_age_days": int,
        "gemini_model": str,
        "max_videos_per_channel": int,
        "lookback_hours": int,
        "max_episodes_per_show": int,
        "min_episodes_per_show": int,
        "max_audio_minutes": int,
    }

    for key, expected_type in field_types.items():
        if key in raw:
            val = raw[key]
            if not isinstance(val, expected_type):
                raise ConfigError(
                    f"Setting '{key}' must be {expected_type.__name__}, got: {type(val).__name__}"
                )
            if expected_type is int and val <= 0:
                raise ConfigError(f"Setting '{key}' must be positive, got: {val}")
            kwargs[key] = val

    # notify_email is optional string
    if "notify_email" in raw:
        val = raw["notify_email"]
        if val is not None and not isinstance(val, str):
            raise ConfigError(f"Setting 'notify_email' must be a string, got: {type(val).__name__}")
        kwargs["notify_email"] = val or None

    return Settings(**kwargs)
