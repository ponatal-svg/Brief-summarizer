"""Fetch podcast episodes, download audio, and transcribe via Gemini."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.error import URLError, HTTPError

from google import genai

from src.config import PodcastShow
from src.summarizer import LANGUAGE_NAMES

logger = logging.getLogger(__name__)

# iTunes Search API — keyless, public
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"

# Retry / throttle settings (shared with summarizer approach)
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

# Namespace map for iTunes RSS extensions
_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


class RSSLookupError(Exception):
    """Raised when we cannot find an RSS feed for a show after all fallbacks."""


class AudioDownloadError(Exception):
    """Raised when audio download fails unrecoverably."""


class TranscriptionError(Exception):
    """Raised when Gemini transcription fails unrecoverably."""


@dataclass(frozen=True)
class EpisodeInfo:
    episode_id: str           # Stable unique ID (guid or url hash)
    title: str
    show_name: str
    show_url: str             # Original podcast_url from config
    episode_url: str          # Direct episode URL (Spotify/etc) or RSS episode page
    audio_url: str            # Direct .mp3 / .m4a download URL
    category: str
    published_at: datetime
    duration_seconds: int
    language: str = "en"
    transcript: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_new_episodes(
    show: PodcastShow,
    processed_ids: set,
    lookback_hours: int,
    max_episodes: int,
    min_episodes: int,
    rss_cache: dict,
) -> list[EpisodeInfo]:
    """Fetch new episodes for a podcast show.

    Guarantees at least min_episodes if any unprocessed episode exists,
    regardless of publication date (same logic as YouTube fetcher).

    Args:
        show: PodcastShow config entry.
        processed_ids: Set of already-processed episode IDs.
        lookback_hours: Only include episodes newer than this window.
        max_episodes: Maximum episodes to return.
        min_episodes: Minimum episodes to always return (recency bypass).
        rss_cache: Mutable dict mapping podcast_url -> rss_feed_url; updated in-place.

    Returns:
        List of EpisodeInfo with audio_url populated (transcript is None here —
        transcription happens in main pipeline).
    """
    logger.info(f"Fetching episodes for {show.name} ({show.podcast_url})")

    # Step 1: Resolve RSS feed URL (use cache to avoid repeated API calls)
    rss_url = rss_cache.get(show.podcast_url)
    if not rss_url:
        try:
            rss_url = resolve_rss_feed(show.name, show.podcast_url)
            rss_cache[show.podcast_url] = rss_url
            logger.info(f"  RSS resolved: {rss_url}")
        except RSSLookupError as e:
            logger.error(
                f"  Cannot find RSS feed for '{show.name}': {e}\n"
                f"  Fix: Add the show to a podcast directory (podcastindex.org) "
                f"or provide the RSS URL directly in config.yaml."
            )
            raise

    # Step 2: Parse RSS and get episodes
    try:
        all_episodes = _parse_rss_feed(rss_url, show)
    except Exception as e:
        logger.error(f"  Failed to parse RSS feed for '{show.name}': {e}")
        raise

    if not all_episodes:
        logger.info(f"  No episodes found in RSS feed for {show.name}")
        return []

    # Step 3: Filter by lookback window, exclude already processed
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    within_window = [
        ep for ep in all_episodes
        if ep.episode_id not in processed_ids and ep.published_at >= cutoff
    ]
    within_window = within_window[:max_episodes]

    # Step 4: Guarantee minimum — if window returned nothing, use the latest unprocessed
    # episode, but only within a 30-day hard cap to avoid surfacing archive content
    # (e.g. a 2022 episode when all recent ones are already processed).
    fallback_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    if len(within_window) < min_episodes:
        for ep in all_episodes:
            if ep.published_at < fallback_cutoff:
                break  # all_episodes is newest-first; nothing older is worth including
            if ep.episode_id not in processed_ids and ep not in within_window:
                within_window.append(ep)
                logger.info(
                    f"  Fallback: '{ep.title}' included as latest "
                    f"(outside {lookback_hours}h window)"
                )
                if len(within_window) >= min_episodes:
                    break

    logger.info(f"  {len(within_window)} new episode(s) from {show.name}")
    return within_window


def download_and_transcribe(
    episode: EpisodeInfo,
    gemini_client: genai.Client,
    gemini_model: str,
    max_audio_minutes: int,
) -> str:
    """Download episode audio and transcribe+summarize using Gemini.

    Downloads to a temp file (deleted after use), clips to max_audio_minutes,
    then sends to Gemini audio API.

    Returns the summary text.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = _download_audio(episode.audio_url, tmpdir, max_audio_minutes)
        summary = _transcribe_and_summarize(
            audio_path=audio_path,
            episode=episode,
            client=gemini_client,
            model=gemini_model,
            max_audio_minutes=max_audio_minutes,
        )
    return summary


# ---------------------------------------------------------------------------
# RSS Resolution
# ---------------------------------------------------------------------------

def resolve_rss_feed(show_name: str, podcast_url: str) -> str:
    """Find the RSS feed URL for a show using multiple strategies.

    Strategy order:
    1. iTunes Search API (primary — keyless, covers ~90% of podcasts)
    2. If podcast_url looks like a direct RSS feed, use it directly
    3. Try fetching podcast_url directly as RSS

    Raises RSSLookupError if no feed found after all fallbacks.
    """
    # Strategy 1: iTunes Search API
    rss_url = _lookup_itunes(show_name)
    if rss_url:
        return rss_url

    # Strategy 2: If the podcast_url itself is an RSS feed (not Spotify/Apple)
    if _looks_like_rss_url(podcast_url):
        if _validate_rss_url(podcast_url):
            return podcast_url

    # Strategy 3: Try fetching the URL directly as RSS
    if not podcast_url.startswith("https://open.spotify.com") and \
       not podcast_url.startswith("https://podcasts.apple.com"):
        if _validate_rss_url(podcast_url):
            return podcast_url

    raise RSSLookupError(
        f"Could not find RSS feed for '{show_name}' (url: {podcast_url}). "
        f"Tried iTunes Search API. "
        f"Manual fix: find the RSS feed at podcastindex.org and add it directly "
        f"as podcast_url in config.yaml."
    )


def _lookup_itunes(show_name: str) -> Optional[str]:
    """Search iTunes Podcast Directory for an RSS feed URL.

    Returns the feedUrl of the best match, or None on failure.
    """
    params = urllib.parse.urlencode({
        "term": show_name,
        "entity": "podcast",
        "limit": "5",
        "media": "podcast",
    })
    url = f"{ITUNES_SEARCH_URL}?{params}"

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "MorningBrief/1.0 (podcast RSS resolver)"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results = data.get("results", [])
                if results:
                    feed_url = results[0].get("feedUrl")
                    if feed_url:
                        logger.debug(f"  iTunes found: {results[0].get('collectionName')} -> {feed_url}")
                        return feed_url
            return None
        except HTTPError as e:
            if e.code == 503 and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
            logger.warning(f"  iTunes search HTTP error {e.code} for '{show_name}': {e}")
            return None
        except URLError as e:
            logger.warning(f"  iTunes search network error for '{show_name}': {e}")
            return None
        except Exception as e:
            logger.warning(f"  iTunes search failed for '{show_name}': {e}")
            return None

    return None


def _looks_like_rss_url(url: str) -> bool:
    """Heuristic: does this URL look like a direct RSS feed?"""
    lower = url.lower()
    return any(kw in lower for kw in ["/rss", "/feed", ".xml", "rss.libsyn", "feeds.", "anchor.fm/s/"])


def _validate_rss_url(url: str) -> bool:
    """Try fetching the URL and check if it's valid RSS/XML."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MorningBrief/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read(4096)  # Just need the header
            return b"<rss" in content or b"<feed" in content
    except Exception:
        return False


# ---------------------------------------------------------------------------
# RSS Parsing
# ---------------------------------------------------------------------------

def _parse_rss_feed(rss_url: str, show: PodcastShow) -> list[EpisodeInfo]:
    """Download and parse RSS feed, returning episodes sorted newest-first."""
    content = _fetch_rss_content(rss_url)
    return _extract_episodes(content, show)


def _fetch_rss_content(rss_url: str) -> bytes:
    """Fetch raw RSS feed bytes with retry."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                rss_url,
                headers={"User-Agent": "MorningBrief/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except HTTPError as e:
            last_error = e
            if e.code in (429, 503) and attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning(f"  RSS fetch rate limited ({e.code}), retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise
        except URLError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning(f"  RSS fetch network error, retrying in {wait}s: {e}")
                time.sleep(wait)
                continue
            raise

    raise last_error


def _extract_episodes(content: bytes, show: PodcastShow) -> list[EpisodeInfo]:
    """Parse RSS XML and extract episode metadata."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f"Invalid RSS XML: {e}") from e

    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS feed missing <channel> element")

    episodes = []
    for item in channel.findall("item"):
        ep = _parse_rss_item(item, show)
        if ep is not None:
            episodes.append(ep)

    # Sort newest-first
    episodes.sort(key=lambda e: e.published_at, reverse=True)
    return episodes


def _parse_rss_item(item: ET.Element, show: PodcastShow) -> Optional[EpisodeInfo]:
    """Parse a single RSS <item> into an EpisodeInfo. Returns None if invalid."""
    # Audio URL from <enclosure>
    enclosure = item.find("enclosure")
    if enclosure is None:
        return None
    audio_url = enclosure.get("url", "").strip()
    if not audio_url:
        return None

    # Title
    title_el = item.find("title")
    title = title_el.text.strip() if title_el is not None and title_el.text else "Untitled Episode"

    # GUID (stable ID); fall back to URL hash
    guid_el = item.find("guid")
    if guid_el is not None and guid_el.text:
        raw_guid = guid_el.text.strip()
    else:
        raw_guid = audio_url
    episode_id = hashlib.sha1(raw_guid.encode()).hexdigest()[:16]

    # Publication date
    pubdate_el = item.find("pubDate")
    published_at = _parse_rss_date(pubdate_el.text if pubdate_el is not None else None)

    # Duration from itunes:duration
    duration_seconds = 0
    dur_el = item.find("itunes:duration", _NS)
    if dur_el is not None and dur_el.text:
        duration_seconds = _parse_itunes_duration(dur_el.text.strip())

    # Episode link (for display)
    link_el = item.find("link")
    episode_url = link_el.text.strip() if link_el is not None and link_el.text else show.podcast_url

    return EpisodeInfo(
        episode_id=episode_id,
        title=title,
        show_name=show.name,
        show_url=show.podcast_url,
        episode_url=episode_url,
        audio_url=audio_url,
        category=show.category,
        published_at=published_at,
        duration_seconds=duration_seconds,
        language=show.language,
    )


def _parse_rss_date(date_str: Optional[str]) -> datetime:
    """Parse RFC 2822 pubDate. Falls back to epoch on failure."""
    if not date_str:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        dt = parsedate_to_datetime(date_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        logger.debug(f"  Could not parse RSS date: {date_str!r}")
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_itunes_duration(value: str) -> int:
    """Parse itunes:duration which can be HH:MM:SS, MM:SS, or plain seconds."""
    if not value:
        return 0
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(float(value))
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# Audio Download
# ---------------------------------------------------------------------------

def _download_audio(audio_url: str, tmpdir: str, max_audio_minutes: int) -> str:
    """Download audio file and trim to max_audio_minutes.

    Uses ffmpeg if available for streaming trim; falls back to direct download
    with size cap.

    Returns path to the downloaded/trimmed audio file.
    """
    max_seconds = max_audio_minutes * 60
    output_path = os.path.join(tmpdir, "episode.mp3")

    # Prefer ffmpeg for streaming download + trim (no full file needed)
    if _has_ffmpeg():
        success = _download_with_ffmpeg(audio_url, output_path, max_seconds)
        if success:
            return output_path

    # Fallback: direct download with byte cap (~10MB ≈ ~60min @ 22kbps mono)
    # Most podcast MP3s are 128kbps: 60min = ~57MB — cap at max_audio_minutes * 1MB
    max_bytes = max_audio_minutes * 1024 * 1024  # 1MB/min rough cap
    _download_direct(audio_url, output_path, max_bytes)
    return output_path


def _has_ffmpeg() -> bool:
    """Check if ffmpeg is available in PATH."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _download_with_ffmpeg(audio_url: str, output_path: str, max_seconds: int) -> bool:
    """Use ffmpeg to stream-download and trim audio to max_seconds.

    Returns True on success, False on failure (caller should fallback).
    """
    cmd = [
        "ffmpeg",
        "-y",                    # overwrite
        "-t", str(max_seconds),  # stop after max_seconds
        "-i", audio_url,         # input URL (ffmpeg streams it)
        "-acodec", "copy",       # no re-encode, just copy audio stream
        "-vn",                   # no video
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max_seconds + 120,  # generous timeout
        )
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
        logger.warning(f"  ffmpeg returned {result.returncode}: {result.stderr[-200:]}")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("  ffmpeg timed out, falling back to direct download")
        return False
    except FileNotFoundError:
        return False


def _download_direct(audio_url: str, output_path: str, max_bytes: int) -> None:
    """Direct HTTP download with a byte cap.

    Raises AudioDownloadError on unrecoverable failure.
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                audio_url,
                headers={
                    "User-Agent": "MorningBrief/1.0",
                    "Range": f"bytes=0-{max_bytes - 1}",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                with open(output_path, "wb") as f:
                    downloaded = 0
                    chunk_size = 65536  # 64KB
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk or downloaded >= max_bytes:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
            return
        except HTTPError as e:
            last_error = e
            if e.code in (429, 503) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
            if e.code in (301, 302, 303, 307, 308):
                raise AudioDownloadError(
                    f"HTTP {e.code} redirect not followed for {audio_url}. "
                    f"The episode audio URL may have moved or expired."
                ) from e
            raise AudioDownloadError(
                f"HTTP {e.code} downloading audio from {audio_url}. "
                f"The episode URL may have expired or require authentication."
            ) from e
        except URLError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
            raise AudioDownloadError(
                f"Network error downloading audio: {e}. "
                f"Check your internet connection."
            ) from e

    raise AudioDownloadError(f"Download failed after {MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Gemini Transcription + Summarization
# ---------------------------------------------------------------------------

PODCAST_PROMPT = """You are a precise content summarizer. You will receive a podcast episode audio file.
Listen carefully and create a summary.

CRITICAL ACCURACY RULES:
- ONLY state facts, names, numbers, and claims that are EXPLICITLY said in the audio.
- NEVER infer or fabricate information not present in the audio.
- Attribute opinions to the speaker (e.g., "According to the host...").
- If the audio is unclear or inaudible in parts, note this rather than guessing.

Adapt the summary length based on the episode duration ({duration_str}):
- Short episodes (under 30 min): 200-300 words.
- Medium episodes (30-60 min): 400-600 words. Include key data points and specific claims.
- Long episodes (60+ min): 600-800 words. Categorize themes, include notable quotes.

Structure the summary using this architecture:

## The Hook
1-2 sentences explaining exactly why this episode matters now.

## Key Findings
3-5 bullet points containing the core substance: data, specific claims, insights, and actionable advice. Start each with "* ".

## The So What?
A concluding thought on how this fits into the broader landscape or what the listener should do with this information.

Additional requirements:
- Write the ENTIRE summary in {language_name}. Section headers must remain in English, but all content must be in {language_name}.
- Use plain language, avoid jargon unless essential.
- Do NOT include any preamble like "Here is a summary".

Episode title: {title}
Show: {show_name}
"""

def _get_language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "unknown duration"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _transcribe_and_summarize(
    audio_path: str,
    episode: EpisodeInfo,
    client: genai.Client,
    model: str,
    max_audio_minutes: int,
) -> str:
    """Upload audio to Gemini Files API and generate summary.

    Cleans up the uploaded file after use.
    Raises TranscriptionError on unrecoverable failure.
    """
    # Effective duration for prompt: min of actual and our cap
    effective_seconds = min(episode.duration_seconds, max_audio_minutes * 60) if episode.duration_seconds > 0 else max_audio_minutes * 60
    duration_str = _format_duration(effective_seconds)
    language_name = _get_language_name(episode.language)

    prompt = PODCAST_PROMPT.format(
        title=episode.title,
        show_name=episode.show_name,
        duration_str=duration_str,
        language_name=language_name,
    )

    uploaded_file = None
    last_error = None

    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.info(f"  Retry {attempt}/{MAX_RETRIES} after {wait}s...")
            time.sleep(wait)
        else:
            time.sleep(5)  # throttle like summarizer

        try:
            # Upload audio to Gemini Files API
            logger.info(f"  Uploading audio to Gemini ({os.path.getsize(audio_path) // 1024}KB)...")
            uploaded_file = client.files.upload(
                file=audio_path,
                config={"mime_type": "audio/mpeg"},
            )

            # Wait for file processing
            _wait_for_file_active(client, uploaded_file)

            # Generate summary
            response = client.models.generate_content(
                model=model,
                contents=[uploaded_file, prompt],
            )
            return response.text or ""

        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Auth errors — abort immediately
            if "401" in str(e) or "403" in str(e) or "api_key_invalid" in error_str or "permission_denied" in error_str:
                raise TranscriptionError(
                    f"Gemini authentication failed for '{episode.title}'. "
                    f"Check GEMINI_API_KEY: {e}"
                ) from e

            # Daily quota — propagate so main can handle gracefully
            if "429" in str(e) or "resource_exhausted" in error_str:
                if "daily" in error_str or "per day" in error_str or "quota exceeded" in error_str:
                    raise

            # File too large
            if "file too large" in error_str or "payload too large" in error_str:
                raise TranscriptionError(
                    f"Audio file too large for Gemini API. "
                    f"Reduce max_audio_minutes in config.yaml (currently {max_audio_minutes}min)."
                ) from e

            logger.warning(f"  Transcription attempt {attempt + 1} failed: {e}")
            continue

        finally:
            # Always clean up uploaded file from Gemini storage
            if uploaded_file:
                try:
                    client.files.delete(name=uploaded_file.name)
                except Exception:
                    pass  # Best-effort cleanup

    raise TranscriptionError(
        f"Gemini transcription failed after {MAX_RETRIES} attempts for '{episode.title}': {last_error}"
    )


def _wait_for_file_active(client: genai.Client, uploaded_file, max_wait_seconds: int = 300) -> None:
    """Poll until the uploaded file is ACTIVE (Gemini processes uploads async)."""
    import time as _time
    waited = 0
    poll_interval = 10

    while waited < max_wait_seconds:
        file_info = client.files.get(name=uploaded_file.name)
        state = getattr(file_info, "state", None)
        # State can be an enum or string depending on SDK version
        state_str = str(state).upper() if state else ""

        if "ACTIVE" in state_str:
            return
        if "FAILED" in state_str:
            raise TranscriptionError(
                f"Gemini file processing failed for uploaded audio. "
                f"The audio format may be unsupported. Try a different episode."
            )

        logger.debug(f"  File state: {state_str}, waiting {poll_interval}s...")
        _time.sleep(poll_interval)
        waited += poll_interval

    raise TranscriptionError(
        f"Gemini file processing timed out after {max_wait_seconds}s. "
        f"The file may be too large or the service is slow. "
        f"Try again or reduce max_audio_minutes in config.yaml."
    )
