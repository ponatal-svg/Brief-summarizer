"""Clean up expired content and state entries."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_old_content(output_dir: Path, max_age_days: int) -> list:
    """Remove output files older than max_age_days.

    Cleans up:
    - output/summaries/YYYY-MM-DD/ directories
    - output/daily/YYYY-MM-DD.md files
    - output/errors/YYYY-MM-DD-errors.md files

    Returns list of removed paths (for logging).
    """
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max_age_days)
    removed = []

    # Clean summary directories
    summaries_dir = output_dir / "summaries"
    if summaries_dir.exists():
        for date_dir in summaries_dir.iterdir():
            if not date_dir.is_dir():
                continue
            date = _parse_date_from_name(date_dir.name)
            if date and date < cutoff:
                shutil.rmtree(date_dir)
                removed.append(str(date_dir))
                logger.info(f"Removed expired summaries: {date_dir.name}")

    # Clean daily digest files
    daily_dir = output_dir / "daily"
    if daily_dir.exists():
        for md_file in daily_dir.glob("*.md"):
            date = _parse_date_from_name(md_file.stem)
            if date and date < cutoff:
                md_file.unlink()
                removed.append(str(md_file))
                logger.info(f"Removed expired digest: {md_file.name}")

    # Clean error report files
    errors_dir = output_dir / "errors"
    if errors_dir.exists():
        for md_file in errors_dir.glob("*.md"):
            # Error files are named YYYY-MM-DD-errors.md
            date_part = md_file.stem.replace("-errors", "")
            date = _parse_date_from_name(date_part)
            if date and date < cutoff:
                md_file.unlink()
                removed.append(str(md_file))
                logger.info(f"Removed expired error report: {md_file.name}")

    return removed


def cleanup_state(state_path: Path, max_age_days: int) -> None:
    """Remove entries from state.json that are older than max_age_days."""
    if not state_path.exists():
        return

    with open(state_path) as f:
        state = json.load(f)

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max_age_days)
    original_count = len(state)

    cleaned = {}
    for video_id, date_str in state.items():
        date = _parse_date_from_name(date_str)
        if date and date >= cutoff:
            cleaned[video_id] = date_str

    removed_count = original_count - len(cleaned)
    if removed_count > 0:
        with open(state_path, "w") as f:
            json.dump(cleaned, f, indent=2)
        logger.info(f"Removed {removed_count} expired state entries")


def _parse_date_from_name(name: str) -> datetime.date:
    """Parse a YYYY-MM-DD date from a string. Returns None on failure."""
    try:
        return datetime.strptime(name[:10], "%Y-%m-%d").date()
    except (ValueError, IndexError):
        return None
