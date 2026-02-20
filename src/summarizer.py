"""Generate adaptive summaries using Gemini API."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from google import genai

logger = logging.getLogger(__name__)


class QuotaExhaustedError(Exception):
    """Raised when the Gemini daily quota is exhausted."""
    pass


# Retry settings
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 5
# Throttle: pause between API calls to stay under 15 RPM free tier
THROTTLE_SECONDS = 5

SUMMARY_PROMPT = """You are a precise content summarizer. Create a summary of the following video transcript.

CRITICAL ACCURACY RULES:
- ONLY state facts, names, numbers, model versions, and claims that are EXPLICITLY mentioned in the transcript.
- NEVER infer, guess, or fill in version numbers, names, dates, or statistics that are not directly stated.
- If the transcript mentions a name or version number, reproduce it EXACTLY as spoken. Do not substitute, round, or correct it.
- If something is unclear or ambiguous in the transcript, say so rather than guessing.
- Attribute claims to the speaker (e.g., "According to the presenter...") when they are opinions or interpretations, not established facts.

Adapt the summary length based on the video duration ({duration_str}):
- Short videos (under 5 min): 100-150 words. Focus on the bottom line and immediate impact.
- Medium videos (10-20 min): 300-500 words. Include key data points, specific benchmarks, and the reasoning behind conclusions.
- Long videos/podcasts (60+ min): 600-800 words. Categorize themes, include notable quotes, and provide a timeline of topics.

Structure the summary using this architecture:

## The Hook
1-2 sentences explaining exactly why this content matters now.

## Key Findings
3-5 bullet points containing the core substance: data, specific numbers, unexpected results, and actionable insights. Only include numbers and version names that appear verbatim in the transcript.

TIMESTAMP CITATIONS: A sparse index of the transcript is provided below (format: [t=Xs] "snippet...").
For each Key Findings bullet, append the single most relevant timestamp citation in the format [t=Xs] at the END of the bullet, where X is the start time in seconds from the index. Choose the timestamp whose snippet best matches the content of that bullet. Only cite timestamps that are genuinely relevant — omit the citation if none fits well.

Example bullet with citation:
* Researchers found a 40% performance improvement on benchmark X. [t=142s]

## The So What?
A concluding thought on how this fits into the broader landscape or what the viewer should do with this information.

Additional requirements:
- Write the ENTIRE summary in {language_name}. The section headers (The Hook, Key Findings, The So What?) must remain in English, but all content must be in {language_name}.
- Use plain language, avoid jargon unless essential
- Preserve important nuances and caveats
- The summary should never take more than 10% of the video's length to read
- Do NOT include any preamble like "Here is a summary"

Video title: {title}
Channel: {channel_name}

Timestamp index (sparse, ~every 30s):
{timestamp_index}

Transcript:
{transcript}"""

NO_TRANSCRIPT_PROMPT = """You are a content summarizer. Based only on the video title and channel name below, write a brief placeholder note.

Title: {title}
Channel: {channel_name}

Requirements:
- Write the response in {language_name}
- State what the video appears to be about based on the title
- Note that no transcript was available for a full summary
- Keep it to 2-3 sentences
- Do NOT include any preamble"""


def create_client(api_key: Optional[str] = None) -> genai.Client:
    """Create a Gemini API client."""
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY not set. Provide it as argument or set the environment variable."
        )
    return genai.Client(api_key=key)


def _format_duration_for_prompt(seconds: int) -> str:
    """Format duration for the prompt context."""
    if seconds <= 0:
        return "unknown duration"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# Map BCP-47 language codes to human-readable names for the prompt
LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "he": "Hebrew",
    "ar": "Arabic",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ru": "Russian",
    "hi": "Hindi",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "uk": "Ukrainian",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ro": "Romanian",
    "cs": "Czech",
    "el": "Greek",
    "hu": "Hungarian",
}


def _get_language_name(code: str) -> str:
    """Convert a BCP-47 language code to a human-readable name."""
    return LANGUAGE_NAMES.get(code, code)


def summarize(
    client: genai.Client,
    model: str,
    title: str,
    channel_name: str,
    transcript: Optional[str],
    duration_seconds: int = 0,
    language: str = "en",
    transcript_segments: tuple = (),
) -> str:
    """Generate an adaptive summary for a video.

    Returns the summary text as a string.
    If transcript is None or too short, generates a placeholder based on title/channel.
    transcript_segments is a tuple of (start_seconds, text) pairs used to inject
    timestamp citations into Key Findings bullets.
    """
    language_name = _get_language_name(language)

    if transcript and len(transcript.strip()) > 50:
        duration_str = _format_duration_for_prompt(duration_seconds)
        timestamp_index = _format_timestamp_index(transcript_segments)
        prompt = SUMMARY_PROMPT.format(
            transcript=transcript,
            duration_str=duration_str,
            title=title,
            channel_name=channel_name,
            language_name=language_name,
            timestamp_index=timestamp_index,
        )
    else:
        prompt = NO_TRANSCRIPT_PROMPT.format(
            title=title, channel_name=channel_name,
            language_name=language_name,
        )

    return _call_gemini(client, model, prompt)


def _format_timestamp_index(segments: tuple) -> str:
    """Format transcript_segments into a compact index string for the prompt.

    Each line: [t=Xs] "first ~8 words of the snippet..."
    Returns "(none available)" if segments is empty.
    """
    if not segments:
        return "(none available)"
    lines = []
    for start_sec, text in segments:
        # Truncate to ~8 words for brevity
        words = text.split()
        snippet = " ".join(words[:8])
        if len(words) > 8:
            snippet += "..."
        lines.append(f'[t={start_sec}s] "{snippet}"')
    return "\n".join(lines)


def _call_gemini(client: genai.Client, model: str, prompt: str) -> str:
    """Make a single Gemini API call with retry and throttle."""
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            backoff = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.info(f"  Retry {attempt}/{MAX_RETRIES} after {backoff}s backoff...")
            time.sleep(backoff)
        else:
            # Throttle between calls to stay under 15 RPM
            time.sleep(THROTTLE_SECONDS)

        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            return response.text or ""
        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # --- Authentication / bad key (401/403) — abort immediately, no point retrying ---
            if "401" in str(e) or "403" in str(e) or "api_key_invalid" in error_str or "permission_denied" in error_str:
                logger.error(f"Gemini authentication error — check your API key: {e}")
                raise

            # --- Daily quota exhausted (RPD) — abort run, save partial progress ---
            if "429" in str(e) or "resource_exhausted" in error_str:
                if "daily" in error_str or "per day" in error_str or "quota exceeded" in error_str:
                    logger.error("Gemini daily quota exhausted — aborting run to avoid wasted retries")
                    raise QuotaExhaustedError("Gemini daily quota exhausted") from e
                # Per-minute rate limit (RPM) — retryable
                logger.warning(f"  Rate limited (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                continue

            # --- Server errors (5xx) — retryable ---
            if "500" in str(e) or "502" in str(e) or "503" in str(e) or "unavailable" in error_str:
                logger.warning(f"  Gemini server error, retrying (attempt {attempt + 1}/{MAX_RETRIES + 1}): {e}")
                continue

            # --- Any other error — not retryable ---
            logger.error(f"Gemini API error: {e}")
            raise

    logger.error(f"Gemini API error after {MAX_RETRIES + 1} attempts: {last_error}")
    raise last_error
