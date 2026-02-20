"""Main orchestrator for the Morning Brief pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.cleanup import cleanup_old_content, cleanup_state
from src.config import load_config, ConfigError
from src.fetchers.youtube import fetch_new_videos
from src.fetchers.podcast import (
    fetch_new_episodes,
    download_and_transcribe,
    RSSLookupError,
    TranscriptionError,
)
from src.generator import (
    generate_summary_files,
    generate_podcast_summary_files,
    generate_daily_digest,
    generate_podcast_daily_digest,
    generate_error_report,
)
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
from src.summarizer import create_client, summarize, QuotaExhaustedError
from src.viewer import generate_viewer

logger = logging.getLogger(__name__)


def run(config_path: Path, output_dir: Path, state_path: Path, dry_run: bool = False) -> None:
    """Run the full Morning Brief pipeline (YouTube + Podcasts)."""
    # Load config
    try:
        config = load_config(config_path)
    except ConfigError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info(f"Morning Brief run: {date_str}")

    # Load state
    state = load_state(state_path)
    processed_video_ids = get_processed_ids(state)
    processed_episode_ids = get_processed_podcast_ids(state)
    rss_cache = get_rss_cache(state)
    logger.info(
        f"Previously processed: {len(processed_video_ids)} videos, "
        f"{len(processed_episode_ids)} podcast episodes"
    )

    # Create Gemini client (skip in dry-run mode)
    gemini_client = None
    if not dry_run:
        try:
            gemini_client = create_client()
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

    digest_entries = []
    podcast_entries = []
    errors = []

    # -----------------------------------------------------------------------
    # YouTube pipeline
    # -----------------------------------------------------------------------
    for source in config.youtube_sources:
        try:
            videos = fetch_new_videos(
                source=source,
                processed_ids=processed_video_ids,
                lookback_hours=config.settings.lookback_hours,
                max_videos=config.settings.max_videos_per_channel,
            )
        except Exception as e:
            msg = f"Failed to fetch {source.name}: {e}"
            logger.error(msg)
            errors.append({"source": f"YouTube/{source.name}", "message": str(e)})
            continue

        for video in videos:
            if dry_run:
                logger.info(f"  [DRY RUN] Would process: {video.title}")
                continue

            if not video.transcript:
                logger.warning(f"  No transcript for '{video.title}' — summarising from title only")

            try:
                summary = summarize(
                    client=gemini_client,
                    model=config.settings.gemini_model,
                    title=video.title,
                    channel_name=video.channel_name,
                    transcript=video.transcript,
                    duration_seconds=video.duration_seconds,
                    language=video.language,
                    transcript_segments=video.transcript_segments,
                )
            except QuotaExhaustedError as e:
                logger.error("Daily Gemini quota exhausted — saving progress and stopping early")
                errors.append({"source": "Gemini/QuotaExhausted", "message": str(e)})
                _save_and_generate(
                    state, state_path, rss_cache, digest_entries, podcast_entries,
                    errors, output_dir, date_str, config,
                )
                return
            except Exception as e:
                error_str = str(e).lower()
                if "401" in str(e) or "403" in str(e) or "api_key_invalid" in error_str or "permission_denied" in error_str:
                    logger.error(f"UNRECOVERABLE: Gemini auth failed — check GEMINI_API_KEY: {e}")
                    errors.append({"source": "Gemini/AuthError", "message": str(e)})
                    generate_error_report(errors, output_dir, date_str)
                    sys.exit(1)
                msg = f"Summarization failed for '{video.title}': {e}"
                logger.error(msg)
                errors.append({"source": f"Gemini/{video.channel_name}", "message": msg})
                # Do not add to digest_entries — errors stay in logs, not on the UI
                continue

            try:
                paths = generate_summary_files(
                    video=video,
                    summary=summary,
                    output_dir=output_dir,
                    date_str=date_str,
                )
            except Exception as e:
                msg = f"File generation failed for '{video.title}': {e}"
                logger.error(msg)
                errors.append({"source": f"Generator/{video.channel_name}", "message": msg})
                digest_entries.append({"video": video, "paths": None, "error": str(e)})
                continue

            digest_entries.append({"video": video, "paths": paths, "error": None})
            mark_youtube_processed(state, video.video_id, date_str)
            processed_video_ids.add(video.video_id)

    # -----------------------------------------------------------------------
    # Podcast pipeline
    # -----------------------------------------------------------------------
    for show in config.podcast_shows:
        try:
            episodes = fetch_new_episodes(
                show=show,
                processed_ids=processed_episode_ids,
                lookback_hours=config.settings.lookback_hours,
                max_episodes=config.settings.max_episodes_per_show,
                min_episodes=config.settings.min_episodes_per_show,
                rss_cache=rss_cache,
            )
        except RSSLookupError as e:
            errors.append({"source": f"Podcast/RSS/{show.name}", "message": str(e)})
            continue
        except Exception as e:
            msg = f"Failed to fetch episodes for '{show.name}': {e}"
            logger.error(msg)
            errors.append({"source": f"Podcast/{show.name}", "message": msg})
            continue

        for episode in episodes:
            if dry_run:
                logger.info(f"  [DRY RUN] Would process podcast: {episode.title}")
                continue

            try:
                summary = download_and_transcribe(
                    episode=episode,
                    gemini_client=gemini_client,
                    gemini_model=config.settings.gemini_model,
                    max_audio_minutes=config.settings.max_audio_minutes,
                )
            except QuotaExhaustedError as e:
                logger.error("Daily Gemini quota exhausted — saving progress and stopping early")
                errors.append({"source": "Gemini/QuotaExhausted", "message": str(e)})
                _save_and_generate(
                    state, state_path, rss_cache, digest_entries, podcast_entries,
                    errors, output_dir, date_str, config,
                )
                return
            except TranscriptionError as e:
                msg = str(e)
                logger.error(f"  Transcription failed for '{episode.title}': {msg}")
                errors.append({"source": f"Podcast/Transcription/{show.name}", "message": msg})
                # Do not add to podcast_entries — errors stay in logs, not on the UI
                continue
            except Exception as e:
                error_str = str(e).lower()
                if "401" in str(e) or "403" in str(e) or "api_key_invalid" in error_str:
                    logger.error(f"UNRECOVERABLE: Gemini auth failed — check GEMINI_API_KEY: {e}")
                    errors.append({"source": "Gemini/AuthError", "message": str(e)})
                    generate_error_report(errors, output_dir, date_str)
                    sys.exit(1)
                msg = f"Processing failed for '{episode.title}': {e}"
                logger.error(msg)
                errors.append({"source": f"Podcast/{show.name}", "message": msg})
                # Do not add to podcast_entries — errors stay in logs, not on the UI
                continue

            try:
                paths = generate_podcast_summary_files(
                    episode=episode,
                    summary=summary,
                    output_dir=output_dir,
                    date_str=date_str,
                )
            except Exception as e:
                msg = f"File generation failed for '{episode.title}': {e}"
                logger.error(msg)
                errors.append({"source": f"Generator/Podcast/{show.name}", "message": msg})
                podcast_entries.append({"episode": episode, "paths": None, "error": str(e)})
                continue

            podcast_entries.append({"episode": episode, "paths": paths, "error": None})
            mark_podcast_processed(state, episode.episode_id, date_str)
            processed_episode_ids.add(episode.episode_id)

    if dry_run:
        logger.info("Dry run complete. No files generated.")
        return

    _save_and_generate(
        state, state_path, rss_cache, digest_entries, podcast_entries,
        errors, output_dir, date_str, config,
    )

    total_items = len(digest_entries) + len(podcast_entries)
    logger.info(
        f"Run complete: {len(digest_entries)} YouTube + {len(podcast_entries)} podcast items, "
        f"{len(errors)} errors"
    )


def _save_and_generate(
    state: dict,
    state_path: Path,
    rss_cache: dict,
    digest_entries: list,
    podcast_entries: list,
    errors: list,
    output_dir: Path,
    date_str: str,
    config,
) -> None:
    """Persist state and generate all output files."""
    update_rss_cache(state, rss_cache)
    save_state(state_path, state)

    generate_daily_digest(digest_entries, output_dir, date_str, config.categories)
    generate_podcast_daily_digest(podcast_entries, output_dir, date_str, config.categories)
    generate_error_report(errors, output_dir, date_str)
    generate_viewer(config, output_dir)

    removed = cleanup_old_content(output_dir, config.settings.max_age_days)
    cleanup_state(state_path, config.settings.max_age_days)
    if removed:
        logger.info(f"Cleaned up {len(removed)} expired files")


def main():
    parser = argparse.ArgumentParser(description="Morning Brief - Content Summarizer")
    parser.add_argument(
        "--config", type=Path, default=Path("config.yaml"),
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("output"),
        help="Output directory (default: output)",
    )
    parser.add_argument(
        "--state", type=Path, default=Path("state.json"),
        help="State file path (default: state.json)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch but don't summarize or generate files",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    run(
        config_path=args.config,
        output_dir=args.output,
        state_path=args.state,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
