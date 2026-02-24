#!/usr/bin/env python3
"""Show processing status for all configured sources.

Reads config.yaml and state.json only — no network calls.

Usage:
    python3 scripts/status.py
    python3 scripts/status.py --config config.yaml --state state.json --lookback 48
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.state import load_state, get_ip_blocked, get_youtube_entries


# ── ANSI colours ────────────────────────────────────────────────────────────
G = "\033[32m"   # green
Y = "\033[33m"   # yellow
R = "\033[31m"   # red
B = "\033[1m"    # bold
D = "\033[2m"    # dim
X = "\033[0m"    # reset


def _ago(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - d).days
        return "today" if days == 0 else "yesterday" if days == 1 else f"{days}d ago"
    except ValueError:
        return date_str or "?"


def _within(date_str: str, hours: int) -> bool:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return d >= datetime.now(timezone.utc) - timedelta(hours=hours)
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Show source processing status (no network)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--state", default="state.json")
    parser.add_argument("--lookback", type=int, default=None,
                        help="Override lookback hours from config")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    state  = load_state(Path(args.state))

    lookback      = args.lookback or config.settings.lookback_hours
    cutoff_str    = (datetime.now(timezone.utc) - timedelta(hours=lookback)).strftime("%Y-%m-%d")
    ip_blocked    = get_ip_blocked(state)            # {video_id: {date,title,url,channel}}
    yt_entries    = get_youtube_entries(state)       # {video_id: {date,channel,title}}
    podcast_state = state.get("podcasts", {})        # {episode_id: date_str}
    rss_cache     = state.get("rss_cache", {})       # {podcast_url: rss_url}

    print(f"\n{B}Morning Brief — Source Status{X}")
    print(f"{D}Lookback: {lookback}h (since {cutoff_str})  |  {args.state}{X}\n")

    # ── YouTube ──────────────────────────────────────────────────────────────
    print(f"{B}YOUTUBE{X}  {D}({len(config.youtube_sources)} channels){X}")

    # Index by channel name for O(1) lookup
    by_channel: dict[str, list[dict]] = {}
    for vid, info in yt_entries.items():
        ch = info.get("channel") or ""
        by_channel.setdefault(ch, []).append({**info, "video_id": vid})

    blocked_by_channel: dict[str, list[dict]] = {}
    for vid, info in ip_blocked.items():
        ch = info.get("channel") or ""
        blocked_by_channel.setdefault(ch, []).append({**info, "video_id": vid})

    # Legacy entries (pre-rich-format) have channel="" — count them separately
    legacy_entries = by_channel.get("", [])
    legacy_recent  = [e for e in legacy_entries if _within(e["date"], lookback)]
    has_legacy     = bool(legacy_entries)

    for source in config.youtube_sources:
        name = source.name
        processed = by_channel.get(name, [])
        blocked   = blocked_by_channel.get(name, [])
        recent    = [e for e in processed if _within(e["date"], lookback)]

        print(f"\n  {B}{name}{X}  {D}[{source.category}]{X}")

        if blocked:
            for entry in blocked:
                title = entry.get("title") or entry["video_id"]
                print(f"    {Y}⏳ IP-blocked{X}  {title}")
                print(f"       {D}blocked {_ago(entry['date'])} | auto-retry on next run{X}")
                print(f"       {D}{entry.get('url', '')}{X}")
        elif recent:
            latest = max(recent, key=lambda e: e["date"])
            title  = latest.get("title") or latest["video_id"]
            print(f"    {G}✓ Processed{X}  {title}")
            print(f"       {D}{_ago(latest['date'])}{X}")
        elif processed:
            latest = max(processed, key=lambda e: e["date"])
            title  = latest.get("title") or latest["video_id"]
            print(f"    {Y}~ Outside window{X}  last: {title}  {D}({_ago(latest['date'])}){X}")
            print(f"       {D}No new video within {lookback}h — will fetch on next run{X}")
        elif has_legacy:
            # Old state entries have no channel tag — can't attribute to a specific source
            print(f"    {D}? Unknown{X}  {D}{len(legacy_entries)} untagged video(s) in state (pre-dating channel tracking){X}")
            if legacy_recent:
                print(f"       {D}{len(legacy_recent)} within lookback window — likely processed, next run will confirm{X}")
        else:
            print(f"    {R}✗ Never processed{X}  {D}(no entry in state){X}")

    # Unmatched ip_blocked (channel name not in config — stale or renamed)
    known = {s.name for s in config.youtube_sources}
    unmatched_blocked = {
        vid: info for vid, info in ip_blocked.items()
        if info.get("channel", "") not in known
    }
    if unmatched_blocked:
        print(f"\n  {Y}⚠ IP-blocked (source no longer in config):{X}")
        for vid, info in unmatched_blocked.items():
            print(f"    {info.get('title', vid)}  {D}{_ago(info.get('date',''))} | {vid}{X}")

    # ── Podcasts ─────────────────────────────────────────────────────────────
    print(f"\n{B}PODCASTS{X}  {D}({len(config.podcast_shows)} shows){X}")

    recent_ep_count = sum(1 for d in podcast_state.values() if _within(d, lookback))

    for show in config.podcast_shows:
        rss  = rss_cache.get(show.podcast_url)
        lang = f"  {D}[{show.language}]{X}" if getattr(show, "language", None) else ""
        print(f"\n  {B}{show.name}{X}  {D}[{show.category}]{X}{lang}")

        if rss:
            rss_status = f"{G}✓ RSS cached{X}"
        else:
            rss_status = f"{Y}? RSS not cached{X}  {D}(will resolve on next run){X}"
        print(f"    {rss_status}")

        # Episodes: we store {episode_id: date_str}, no show mapping.
        # Show total within-window count (cross-show) as a proxy.
        # TODO: store show name in podcast state to enable per-show breakdown.
        if recent_ep_count > 0:
            print(f"    {D}{recent_ep_count} episode(s) processed across all shows within window{X}")
        else:
            print(f"    {Y}~ No episodes recorded within lookback window{X}")

    # ── Summary ──────────────────────────────────────────────────────────────
    yt_total   = len(yt_entries)
    yt_recent  = sum(1 for e in yt_entries.values() if _within(e["date"], lookback))
    pod_total  = len(podcast_state)
    rss_ok     = sum(1 for s in config.podcast_shows if rss_cache.get(s.podcast_url))

    print(f"\n{B}SUMMARY{X}")
    print(f"  YouTube  : {yt_total:>4} total videos processed, {yt_recent} within {lookback}h window")
    if ip_blocked:
        print(f"             {Y}{len(ip_blocked)} in IP-blocked retry queue{X}")
    print(f"  Podcasts : {pod_total:>4} total episodes processed, {recent_ep_count} within {lookback}h window")
    print(f"             {rss_ok}/{len(config.podcast_shows)} shows have RSS cached")
    print(f"\n{D}For exact per-channel fetch preview: python3 -m src.main ... --dry-run{X}\n")


if __name__ == "__main__":
    main()
