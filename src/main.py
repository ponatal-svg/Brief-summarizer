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
from src.generator import (
    generate_summary_files,
    generate_daily_digest,
    generate_error_report,
)
from src.state import load_state, save_state, get_processed_ids
from src.summarizer import create_client, summarize
from src.viewer import generate_viewer

logger = logging.getLogger(__name__)


def run(config_path: Path, output_dir: Path, state_path: Path, dry_run: bool = False) -> None:
    """Run the full Morning Brief pipeline."""
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
    processed_ids = get_processed_ids(state)
    logger.info(f"Previously processed: {len(processed_ids)} videos")

    # Create Gemini client (skip in dry-run mode)
    gemini_client = None
    if not dry_run:
        try:
            gemini_client = create_client()
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

    # Fetch and process YouTube videos
    digest_entries = []
    errors = []

    for source in config.youtube_sources:
        try:
            videos = fetch_new_videos(
                source=source,
                processed_ids=processed_ids,
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

            # Skip videos with no transcript — summarizer can't do anything useful
            if not video.transcript:
                msg = f"No transcript available for '{video.title}' — skipping"
                logger.warning(msg)
                errors.append({
                    "source": f"Transcript/{video.channel_name}",
                    "message": msg,
                })
                continue

            # Summarize
            try:
                summary = summarize(
                    client=gemini_client,
                    model=config.settings.gemini_model,
                    title=video.title,
                    channel_name=video.channel_name,
                    transcript=video.transcript,
                    duration_seconds=video.duration_seconds,
                    language=video.language,
                )
            except Exception as e:
                msg = f"Summarization failed for '{video.title}': {e}"
                logger.error(msg)
                errors.append({
                    "source": f"Gemini/{video.channel_name}",
                    "message": msg,
                })
                digest_entries.append({
                    "video": video,
                    "paths": None,
                    "error": str(e),
                })
                continue

            # Generate markdown files
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
                errors.append({
                    "source": f"Generator/{video.channel_name}",
                    "message": msg,
                })
                digest_entries.append({
                    "video": video,
                    "paths": None,
                    "error": str(e),
                })
                continue

            digest_entries.append({
                "video": video,
                "paths": paths,
                "error": None,
            })

            # Update state
            state[video.video_id] = date_str
            processed_ids.add(video.video_id)

    if dry_run:
        logger.info("Dry run complete. No files generated.")
        return

    # Generate daily digest
    generate_daily_digest(
        entries=digest_entries,
        output_dir=output_dir,
        date_str=date_str,
        categories=config.categories,
    )

    # Generate error report if needed
    generate_error_report(errors, output_dir, date_str)

    # Generate/update viewer
    generate_viewer(config, output_dir)

    # Save state
    save_state(state_path, state)

    # Cleanup old content
    removed = cleanup_old_content(output_dir, config.settings.max_age_days)
    cleanup_state(state_path, config.settings.max_age_days)

    logger.info(
        f"Run complete: {len(digest_entries)} items processed, "
        f"{len(errors)} errors, {len(removed)} expired files removed"
    )


def main():
    parser = argparse.ArgumentParser(description="Morning Brief - Content Summarizer")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output"),
        help="Output directory (default: output)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("state.json"),
        help="State file path (default: state.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch but don't summarize or generate files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
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
