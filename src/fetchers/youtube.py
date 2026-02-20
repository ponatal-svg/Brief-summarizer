"""Fetch new YouTube videos and their transcripts."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

from src.config import YouTubeSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    url: str
    channel_name: str
    category: str
    upload_date: datetime
    duration_seconds: int
    transcript: Optional[str]
    language: str = "en"


def fetch_new_videos(
    source: YouTubeSource,
    processed_ids: set,
    lookback_hours: int,
    max_videos: int,
) -> list:
    """Fetch new videos from a YouTube channel.

    Returns videos published within lookback_hours that haven't been processed yet.
    If no videos pass the filters, the latest video is included anyway so that
    every configured channel always produces at least one result.
    """
    logger.info(f"Fetching videos from {source.name} ({source.channel_url})")

    entries = _get_channel_entries(source.channel_url, max_videos)

    if not entries:
        logger.info(f"  No entries found for {source.name}")
        return []

    videos = []
    for entry in entries:
        video_id = entry.get("id")
        if not video_id or video_id in processed_ids:
            continue

        upload_date = _parse_upload_date(entry.get("upload_date"))
        # Always fetch the real upload date — flat-playlist often returns
        # today's date instead of the actual publish date.
        real_date = _get_video_upload_date(video_id)
        if real_date:
            upload_date = real_date
        if upload_date and not _is_within_lookback(upload_date, lookback_hours):
            continue

        transcript = _get_transcript(video_id, language=source.language)

        video = VideoInfo(
            video_id=video_id,
            title=entry.get("title", "Untitled"),
            url=f"https://www.youtube.com/watch?v={video_id}",
            channel_name=source.name,
            category=source.category,
            upload_date=upload_date or datetime.now(timezone.utc),
            duration_seconds=entry.get("duration") or 0,
            transcript=transcript,
            language=source.language,
        )
        videos.append(video)
        logger.info(f"  Found: {video.title} (transcript: {'yes' if transcript else 'no'})")

    # Guarantee at least one video per channel: if all were filtered out
    # (too old or already processed), force-include the latest entry.
    if not videos and entries:
        latest = entries[0]  # yt-dlp returns newest first
        video_id = latest.get("id")
        if video_id and video_id not in processed_ids:
            upload_date = _parse_upload_date(latest.get("upload_date"))
            real_date = _get_video_upload_date(video_id)
            if real_date:
                upload_date = real_date
            transcript = _get_transcript(video_id, language=source.language)
            video = VideoInfo(
                video_id=video_id,
                title=latest.get("title", "Untitled"),
                url=f"https://www.youtube.com/watch?v={video_id}",
                channel_name=source.name,
                category=source.category,
                upload_date=upload_date or datetime.now(timezone.utc),
                duration_seconds=latest.get("duration") or 0,
                transcript=transcript,
                language=source.language,
            )
            videos.append(video)
            logger.info(f"  Fallback: {video.title} (outside lookback, included as latest)")

    logger.info(f"  {len(videos)} new video(s) from {source.name}")
    return videos


def _get_channel_entries(channel_url: str, max_videos: int) -> list:
    """Get recent video metadata from a channel using yt-dlp."""
    videos_url = f"{channel_url.rstrip('/')}/videos"
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--playlist-end", str(max_videos),
        "--no-warnings",
        videos_url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout fetching channel: {channel_url}")
        return []
    except FileNotFoundError:
        logger.error("yt-dlp not found. Install it: pip install yt-dlp")
        return []

    if result.returncode != 0:
        logger.error(f"yt-dlp error for {channel_url}: {result.stderr.strip()}")
        return []

    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse yt-dlp JSON line: {line[:100]}")

    return entries


def _get_transcript(video_id: str, language: str = "en") -> Optional[str]:
    """Fetch transcript for a video using youtube-transcript-api.

    Tries the configured language first, then English, then any available language.
    """
    # Build language priority list: configured language variants first, then English fallback
    if language == "en":
        languages = ["en", "en-US", "en-GB"]
    else:
        languages = [language, "en", "en-US", "en-GB"]

    # First attempt: try preferred languages
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
        text = " ".join(snippet["text"] for snippet in transcript)
        if text.strip():
            return text
    except (TranscriptsDisabled, VideoUnavailable):
        logger.info(f"  No transcript available for {video_id}")
        return None
    except NoTranscriptFound:
        pass  # Fall through to try any available language
    except Exception as e:
        logger.warning(f"  Transcript fetch failed for {video_id}: {e}")

    # Second attempt: try ANY available transcript language
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        available = list(transcript_list)
        if available:
            first = available[0]
            logger.info(f"  Falling back to transcript language: {first.language_code} for {video_id}")
            transcript = first.fetch()
            text = " ".join(snippet["text"] for snippet in transcript)
            if text.strip():
                return text
    except Exception as e:
        logger.warning(f"  Transcript fallback failed for {video_id}: {e}")

    return None


def _get_video_upload_date(video_id: str) -> Optional[datetime]:
    """Fetch the real upload date for a single video via yt-dlp.

    Uses --print to get just the upload_date field without downloading.
    This is needed because --flat-playlist often omits upload_date.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        "--no-download",
        "--print", "%(upload_date)s",
        "--no-warnings",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    date_str = result.stdout.strip()
    return _parse_upload_date(date_str)


def _parse_upload_date(date_str) -> Optional[datetime]:
    """Parse yt-dlp's YYYYMMDD date format."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        return datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_within_lookback(upload_date: datetime, lookback_hours: int) -> bool:
    """Check if a date is within the lookback window."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    return upload_date >= cutoff
