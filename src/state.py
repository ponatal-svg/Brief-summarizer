"""Manage processed content state to avoid re-processing."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Top-level keys in the state file
_KEY_YOUTUBE = "youtube"
_KEY_PODCASTS = "podcasts"
_KEY_RSS_CACHE = "rss_cache"
_KEY_IP_BLOCKED = "ip_blocked"

# Videos stuck in ip_blocked longer than this are dropped (likely deleted / too old)
_IP_BLOCKED_TTL_DAYS = 7


def load_state(state_path: Path) -> dict:
    """Load the state file. Returns empty dict if file doesn't exist."""
    if not state_path.exists():
        return {}
    try:
        with open(state_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"State file corrupted or unreadable ({e}), starting fresh")
        return {}


def save_state(state_path: Path, state: dict) -> None:
    """Save state to file atomically (write to .tmp, then rename)."""
    tmp_path = state_path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        tmp_path.replace(state_path)
    except OSError as e:
        logger.error(f"Failed to save state: {e}")
        raise
    total = (
        len(state.get(_KEY_YOUTUBE, {})) +
        len(state.get(_KEY_PODCASTS, {}))
    )
    logger.info(f"State saved: {total} entries")


def get_processed_ids(state: dict) -> set:
    """Extract the set of processed YouTube video IDs from state.

    Supports both legacy flat format {video_id: date} and new nested format.
    """
    youtube_state = state.get(_KEY_YOUTUBE)
    if youtube_state is not None:
        return set(youtube_state.keys())
    # Legacy: flat dict at root level (pre-podcast state files)
    # Filter out known non-video keys
    reserved = {_KEY_YOUTUBE, _KEY_PODCASTS, _KEY_RSS_CACHE, _KEY_IP_BLOCKED}
    return {k for k in state.keys() if k not in reserved}


def get_youtube_entries(state: dict) -> dict:
    """Return full youtube state: {video_id: {"date": str, "channel": str, "title": str}}.

    Entries written before the rich-format migration have the value as a plain
    date string — those are returned as {"date": date_str, "channel": "", "title": ""}.
    """
    raw = state.get(_KEY_YOUTUBE, {})
    result = {}
    for vid, val in raw.items():
        if isinstance(val, dict):
            result[vid] = val
        else:
            # Legacy: val is a plain date string
            result[vid] = {"date": str(val), "channel": "", "title": ""}
    return result


def get_processed_podcast_ids(state: dict) -> set:
    """Extract the set of processed podcast episode IDs from state."""
    return set(state.get(_KEY_PODCASTS, {}).keys())


def get_rss_cache(state: dict) -> dict:
    """Get the cached RSS feed URL mapping (podcast_url -> rss_url)."""
    return dict(state.get(_KEY_RSS_CACHE, {}))


def mark_youtube_processed(
    state: dict,
    video_id: str,
    date_str: str,
    channel: str = "",
    title: str = "",
) -> None:
    """Record a YouTube video as processed.

    Stores rich metadata {date, channel, title} so the status script can show
    per-channel history without making network calls.  channel and title are
    optional for backward-compat with callers that only have the video_id.
    """
    if _KEY_YOUTUBE not in state:
        # Migrate legacy flat entries to nested format
        reserved = {_KEY_YOUTUBE, _KEY_PODCASTS, _KEY_RSS_CACHE, _KEY_IP_BLOCKED}
        legacy = {k: v for k, v in state.items() if k not in reserved}
        for k in legacy:
            del state[k]
        state[_KEY_YOUTUBE] = legacy
    state[_KEY_YOUTUBE][video_id] = {"date": date_str, "channel": channel, "title": title}


def mark_podcast_processed(state: dict, episode_id: str, date_str: str) -> None:
    """Record a podcast episode as processed."""
    if _KEY_PODCASTS not in state:
        state[_KEY_PODCASTS] = {}
    state[_KEY_PODCASTS][episode_id] = date_str


def update_rss_cache(state: dict, rss_cache: dict) -> None:
    """Persist RSS feed URL cache back into state."""
    state[_KEY_RSS_CACHE] = rss_cache


# ---------------------------------------------------------------------------
# IP-blocked video tracking
# ---------------------------------------------------------------------------

def get_ip_blocked(state: dict) -> dict:
    """Return the ip_blocked dict: {video_id: {"date": YYYY-MM-DD, "title": str, "url": str}}."""
    return dict(state.get(_KEY_IP_BLOCKED, {}))


def mark_ip_blocked(
    state: dict, video_id: str, title: str, url: str, date_str: str, channel: str = "",
) -> None:
    """Record a video as IP-blocked so it is retried on the next run."""
    if _KEY_IP_BLOCKED not in state:
        state[_KEY_IP_BLOCKED] = {}
    state[_KEY_IP_BLOCKED][video_id] = {
        "date": date_str, "title": title, "url": url, "channel": channel,
    }


def promote_ip_blocked(
    state: dict, video_id: str, date_str: str,
    channel: str = "", title: str = "",
) -> None:
    """Move a video from ip_blocked to processed (transcript successfully fetched)."""
    info = state.get(_KEY_IP_BLOCKED, {}).pop(video_id, {})
    # Use stored title/channel from the blocked entry if not provided by caller
    mark_youtube_processed(
        state, video_id, date_str,
        channel=channel or info.get("channel", ""),
        title=title or info.get("title", ""),
    )


def expire_ip_blocked(state: dict) -> list[str]:
    """Remove entries older than _IP_BLOCKED_TTL_DAYS. Returns list of expired video_ids."""
    blocked = state.get(_KEY_IP_BLOCKED, {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=_IP_BLOCKED_TTL_DAYS)
    expired = []
    for video_id, info in list(blocked.items()):
        try:
            recorded = datetime.strptime(info["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            expired.append(video_id)
            continue
        if recorded < cutoff:
            expired.append(video_id)
    for video_id in expired:
        blocked.pop(video_id, None)
    return expired
