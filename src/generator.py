"""Generate markdown output files: daily digest and individual summaries."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.fetchers.youtube import VideoInfo

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def generate_summary_files(
    video: VideoInfo,
    summary: str,
    output_dir: Path,
    date_str: str,
) -> dict:
    """Write the summary markdown file.

    Returns a dict with path to the generated file.
    """
    summaries_dir = output_dir / "summaries" / date_str
    summaries_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(video.title)
    summary_path = summaries_dir / f"{slug}.md"

    duration_str = _format_duration(video.duration_seconds)

    content = _build_summary_md(
        video=video,
        summary_text=summary,
        duration_str=duration_str,
    )

    summary_path.write_text(content, encoding="utf-8")

    logger.info(f"  Generated summaries for: {video.title}")

    return {
        "summary_path": summary_path,
        "slug": slug,
    }


def _build_summary_md(
    video: VideoInfo,
    summary_text: str,
    duration_str: str,
) -> str:
    """Build markdown content for a summary file."""
    lines = [
        f"# {video.title}",
        "",
        f"**Channel:** {video.channel_name} | "
        f"**Category:** {video.category} | "
        f"**Duration:** {duration_str} | "
        f"**Language:** {video.language}",
        "",
        f"**Source:** [{video.url}]({video.url})",
        "",
        f"---",
        "",
        summary_text,
        "",
    ]
    return "\n".join(lines)


def generate_daily_digest(
    entries: list,
    output_dir: Path,
    date_str: str,
    categories: list,
) -> Path:
    """Generate the daily digest markdown file.

    Args:
        entries: List of dicts with keys: video (VideoInfo), paths (dict from
                 generate_summary_files), error (Optional[str]).
        output_dir: Base output directory.
        date_str: Date string like "2026-02-16".
        categories: List of Category objects for ordering.

    Returns:
        Path to the generated digest file.
    """
    daily_dir = output_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    digest_path = daily_dir / f"{date_str}.md"

    # Group entries by category
    by_category = {}
    for entry in entries:
        cat = entry["video"].category
        by_category.setdefault(cat, []).append(entry)

    lines = [
        f"# Morning Brief - {date_str}",
        "",
    ]

    if not entries:
        lines.append("No new content found today.")
        lines.append("")
    else:
        lines.append(f"**{len(entries)} new item(s)**")
        lines.append("")

        # Output in category order
        category_names = [c.name for c in categories]
        for cat_name in category_names:
            cat_entries = by_category.get(cat_name, [])
            if not cat_entries:
                continue

            lines.append(f"## {cat_name}")
            lines.append("")

            for entry in cat_entries:
                video = entry["video"]
                paths = entry.get("paths")
                error = entry.get("error")

                duration = _format_duration(video.duration_seconds)
                pub_date = video.upload_date.strftime("%Y-%m-%d")
                lines.append(f"### {video.title}")
                lines.append(
                    f"**{video.channel_name}** | {duration} | {pub_date} | "
                    f"[Watch]({video.url})"
                )
                lines.append("")

                if error:
                    lines.append(f"> Error: {error}")
                    lines.append("")
                elif paths:
                    summary_rel = _relative_path(paths["summary_path"], output_dir)
                    lines.append(
                        f"[Summary]({summary_rel})"
                    )
                    lines.append("")

        # Include any categories not in the config order
        for cat_name in sorted(by_category.keys()):
            if cat_name not in category_names:
                cat_entries = by_category[cat_name]
                lines.append(f"## {cat_name}")
                lines.append("")
                for entry in cat_entries:
                    video = entry["video"]
                    lines.append(f"### {video.title}")
                    lines.append(f"**{video.channel_name}** | [Watch]({video.url})")
                    lines.append("")

    digest_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Generated daily digest: {digest_path}")
    return digest_path


def generate_error_report(errors: list, output_dir: Path, date_str: str) -> Optional[Path]:
    """Generate an error report if there were any failures.

    Args:
        errors: List of dicts with keys: source (str), message (str).
        output_dir: Base output directory.
        date_str: Date string.

    Returns:
        Path to error report, or None if no errors.
    """
    if not errors:
        return None

    errors_dir = output_dir / "errors"
    errors_dir.mkdir(parents=True, exist_ok=True)
    error_path = errors_dir / f"{date_str}-errors.md"

    lines = [
        f"# Errors - {date_str}",
        "",
    ]

    for err in errors:
        lines.append(f"- **{err['source']}**: {err['message']}")

    lines.append("")

    error_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Generated error report: {error_path}")
    return error_path


def _format_duration(seconds: int) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds <= 0:
        return "Unknown duration"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _relative_path(path: Path, base: Path) -> str:
    """Get the relative path from base to path."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
