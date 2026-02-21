"""Generate markdown output files: daily digest and individual summaries."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from src.fetchers.youtube import VideoInfo
from src.fetchers.podcast import EpisodeInfo

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
    """Write the summary markdown file for a YouTube video.

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

    logger.info(f"  Generated summary for: {video.title}")

    return {
        "summary_path": summary_path,
        "slug": slug,
    }


def generate_podcast_summary_files(
    episode: EpisodeInfo,
    summary: str,
    output_dir: Path,
    date_str: str,
) -> dict:
    """Write the summary markdown file for a podcast episode.

    Returns a dict with path to the generated file.
    """
    summaries_dir = output_dir / "podcast-summaries" / date_str
    summaries_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(f"{episode.show_name}-{episode.title}")
    summary_path = summaries_dir / f"{slug}.md"

    duration_str = _format_duration(episode.duration_seconds)

    content = _build_podcast_summary_md(
        episode=episode,
        summary_text=summary,
        duration_str=duration_str,
    )

    summary_path.write_text(content, encoding="utf-8")

    logger.info(f"  Generated podcast summary for: {episode.title}")

    return {
        "summary_path": summary_path,
        "slug": slug,
    }


def _build_summary_md(
    video: VideoInfo,
    summary_text: str,
    duration_str: str,
) -> str:
    """Build markdown content for a YouTube summary file."""
    # Convert [t=Xs] markers to clickable YouTube timestamp links
    linked_summary = re.sub(
        r"\[t=(\d+)s\]",
        lambda m: f"[[t={m.group(1)}s]](https://www.youtube.com/watch?v={video.video_id}&t={m.group(1)})",
        summary_text,
    )
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
        linked_summary,
        "",
    ]
    return "\n".join(lines)


def _build_podcast_summary_md(
    episode: EpisodeInfo,
    summary_text: str,
    duration_str: str,
) -> str:
    """Build markdown content for a podcast summary file."""
    pub_date = episode.published_at.strftime("%Y-%m-%d")
    lines = [
        f"# {episode.title}",
        "",
        f"**Show:** {episode.show_name} | "
        f"**Category:** {episode.category} | "
        f"**Duration:** {duration_str} | "
        f"**Published:** {pub_date} | "
        f"**Language:** {episode.language}",
        "",
        f"**Source:** [{episode.episode_url}]({episode.episode_url})",
        "",
        f"---",
        "",
        summary_text,
        "",
    ]
    return "\n".join(lines)


def generate_podcast_daily_digest(
    entries: list,
    output_dir: Path,
    date_str: str,
    categories: list,
) -> Path:
    """Generate the daily podcast digest markdown file.

    Args:
        entries: List of dicts with keys: episode (EpisodeInfo), paths (dict), error (Optional[str]).
        output_dir: Base output directory.
        date_str: Date string like "2026-02-16".
        categories: List of Category objects for ordering.

    Returns:
        Path to the generated digest file.
    """
    daily_dir = output_dir / "podcast-daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    digest_path = daily_dir / f"{date_str}.md"

    # Merge with existing digest for today
    existing_entries = _parse_existing_podcast_digest(digest_path, output_dir) if digest_path.exists() else []
    new_episode_ids = {e["episode"].episode_id for e in entries}
    merged_entries = [e for e in existing_entries if e["episode_id"] not in new_episode_ids]
    all_entries = entries + [_stub_podcast_entry(e) for e in merged_entries]

    by_category: dict = {}
    for entry in all_entries:
        cat = entry["episode"].category
        by_category.setdefault(cat, []).append(entry)

    lines = [f"# Morning Brief Podcasts - {date_str}", ""]

    if not all_entries:
        lines.append("No new podcast episodes found today.")
        lines.append("")
    else:
        lines.append(f"**{len(all_entries)} episode(s)**")
        lines.append("")

        category_names = [c.name for c in categories]
        for cat_name in category_names:
            cat_entries = by_category.get(cat_name, [])
            if not cat_entries:
                continue
            cat_entries = sorted(cat_entries, key=lambda e: e.get("_existing", False))

            lines.append(f"## {cat_name}")
            lines.append("")

            for entry in cat_entries:
                ep = entry["episode"]
                paths = entry.get("paths")
                error = entry.get("error")

                duration = _format_duration(ep.duration_seconds)
                pub_date = ep.published_at.strftime("%Y-%m-%d")
                lines.append(f"### {ep.title}")
                lines.append(
                    f"**{ep.show_name}** | {duration} | {pub_date} | "
                    f"[Listen]({ep.episode_url})"
                )
                lines.append("")

                if error:
                    lines.append(f"> Error: {error}")
                    lines.append("")
                elif paths:
                    if entry.get("_existing"):
                        summary_rel = paths.get("summary_rel", "")
                    else:
                        summary_rel = _relative_path(paths["summary_path"], output_dir)
                    if summary_rel:
                        lines.append(f"[Summary]({summary_rel})")
                        lines.append("")

        for cat_name in sorted(by_category.keys()):
            if cat_name not in category_names:
                cat_entries = by_category[cat_name]
                lines.append(f"## {cat_name}")
                lines.append("")
                for entry in cat_entries:
                    ep = entry["episode"]
                    lines.append(f"### {ep.title}")
                    lines.append(f"**{ep.show_name}** | [Listen]({ep.episode_url})")
                    lines.append("")

    digest_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Generated podcast daily digest: {digest_path}")
    return digest_path


def _parse_existing_podcast_digest(digest_path: Path, output_dir: Path) -> list:
    """Parse an existing podcast digest to extract entry stubs for merging."""
    entries = []
    try:
        text = digest_path.read_text(encoding="utf-8")
    except OSError:
        return entries

    current_category = None
    current: dict = {}

    for line in text.splitlines():
        if line.startswith("## "):
            current_category = line[3:].strip()
        elif line.startswith("### "):
            if current:
                entries.append(current)
            current = {
                "category": current_category,
                "title": line[4:].strip(),
                "show_name": "", "duration": "", "listen_url": "",
                "pub_date": "", "summary_rel": "", "episode_id": "",
            }
        elif current and line.startswith("**") and "|" in line:
            parts = line.replace("**", "").split("|")
            current["show_name"] = parts[0].strip() if len(parts) > 0 else ""
            current["pub_date"] = parts[2].strip() if len(parts) > 2 else ""
            listen_match = re.search(r'\[Listen\]\(([^)]+)\)', line)
            if listen_match:
                current["listen_url"] = listen_match.group(1)
                # Use URL hash as stable ID for existing entries
                import hashlib
                current["episode_id"] = hashlib.sha1(
                    current["listen_url"].encode()
                ).hexdigest()[:16]
        elif current and line.startswith("[Summary]"):
            match = re.search(r'\[Summary\]\(([^)]+)\)', line)
            if match:
                current["summary_rel"] = match.group(1)

    if current:
        entries.append(current)

    return entries


def _stub_podcast_entry(existing: dict) -> dict:
    """Convert a parsed existing podcast digest entry back into a generator entry dict."""
    from src.fetchers.podcast import EpisodeInfo
    from datetime import datetime, timezone

    try:
        pub = datetime.strptime(existing["pub_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, KeyError):
        pub = datetime.now(timezone.utc)

    ep = EpisodeInfo(
        episode_id=existing.get("episode_id", ""),
        title=existing.get("title", ""),
        show_name=existing.get("show_name", ""),
        show_url=existing.get("listen_url", ""),
        episode_url=existing.get("listen_url", ""),
        audio_url="",
        category=existing.get("category", ""),
        published_at=pub,
        duration_seconds=0,
        language="en",
    )
    paths = {
        "summary_path": None,
        "slug": None,
        "summary_rel": existing.get("summary_rel", ""),
    }
    return {"episode": ep, "paths": paths, "error": None, "_existing": True}


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

    # Merge with existing digest entries for today (if the file already exists)
    existing_entries = _parse_existing_digest(digest_path, output_dir) if digest_path.exists() else []
    new_video_ids = {e["video"].video_id for e in entries}
    merged_entries = [e for e in existing_entries if e["video_id"] not in new_video_ids]
    all_entries = entries + [_stub_entry(e) for e in merged_entries]

    # Group entries by category
    by_category = {}
    for entry in all_entries:
        cat = entry["video"].category
        by_category.setdefault(cat, []).append(entry)

    lines = [
        f"# Morning Brief - {date_str}",
        "",
    ]

    if not all_entries:
        lines.append("No new content found today.")
        lines.append("")
    else:
        lines.append(f"**{len(all_entries)} item(s)**")
        lines.append("")

        # Output in category order
        category_names = [c.name for c in categories]
        for cat_name in category_names:
            cat_entries = by_category.get(cat_name, [])
            if not cat_entries:
                continue
            # Re-sort by category order (new entries first, then existing)
            cat_entries = sorted(cat_entries, key=lambda e: e.get("_existing", False))

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
                    if entry.get("_existing"):
                        summary_rel = paths.get("summary_rel", "")
                    else:
                        summary_rel = _relative_path(paths["summary_path"], output_dir)
                    if summary_rel:
                        lines.append(f"[Summary]({summary_rel})")
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


def _parse_existing_digest(digest_path: Path, output_dir: Path) -> list:
    """Parse an existing daily digest to extract entry stubs for merging."""
    from src.fetchers.youtube import VideoInfo
    from datetime import datetime, timezone

    entries = []
    try:
        text = digest_path.read_text(encoding="utf-8")
    except OSError:
        return entries

    current_category = None
    current: dict = {}

    for line in text.splitlines():
        if line.startswith("## "):
            current_category = line[3:].strip()
        elif line.startswith("### "):
            if current:
                entries.append(current)
            current = {"category": current_category, "title": line[4:].strip(),
                       "channel": "", "duration": "", "watch_url": "",
                       "pub_date": "", "summary_rel": "", "video_id": ""}
        elif current and line.startswith("**") and "|" in line:
            parts = line.replace("**", "").split("|")
            current["channel"] = parts[0].strip() if len(parts) > 0 else ""
            current["pub_date"] = parts[2].strip() if len(parts) > 2 else ""
            watch_match = re.search(r'\[Watch\]\(([^)]+)\)', line)
            if watch_match:
                current["watch_url"] = watch_match.group(1)
                vid_match = re.search(r'v=([A-Za-z0-9_-]+)', current["watch_url"])
                if vid_match:
                    current["video_id"] = vid_match.group(1)
        elif current and line.startswith("[Summary]"):
            match = re.search(r'\[Summary\]\(([^)]+)\)', line)
            if match:
                current["summary_rel"] = match.group(1)

    if current:
        entries.append(current)

    return entries


def _stub_entry(existing: dict) -> dict:
    """Convert a parsed existing digest entry back into a generator entry dict."""
    from src.fetchers.youtube import VideoInfo
    from datetime import datetime, timezone

    try:
        upload_date = datetime.strptime(existing["pub_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, KeyError):
        upload_date = datetime.now(timezone.utc)

    video = VideoInfo(
        video_id=existing.get("video_id", ""),
        title=existing.get("title", ""),
        url=existing.get("watch_url", ""),
        channel_name=existing.get("channel", ""),
        category=existing.get("category", ""),
        upload_date=upload_date,
        duration_seconds=0,
        transcript="",
        language="en",
    )
    paths = {"summary_path": None, "slug": None, "summary_rel": existing.get("summary_rel", "")}
    return {"video": video, "paths": paths, "error": None, "_existing": True}


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
