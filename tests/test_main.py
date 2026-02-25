"""Tests for main.py pipeline orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.config import Config, Category, YouTubeSource, PodcastShow, Settings
from src.fetchers.youtube import VideoInfo, IpBlockedError
from src.fetchers.podcast import EpisodeInfo, RSSLookupError, TranscriptionError
from src.main import run, _save_and_generate
from src.summarizer import QuotaExhaustedError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return Config(
        categories=[Category(name="AI", color="#4A90D9"), Category(name="Wellbeing", color="#27AE60")],
        youtube_sources=[
            YouTubeSource(channel_url="https://youtube.com/@test", name="Test Channel", category="AI"),
        ],
        podcast_shows=[
            PodcastShow(podcast_url="https://open.spotify.com/show/abc", name="Test Podcast", category="AI"),
        ],
        settings=Settings(
            max_age_days=7,
            gemini_model="gemini-2.5-flash",
            max_videos_per_channel=3,
            lookback_hours=26,
            max_episodes_per_show=3,
            min_episodes_per_show=1,
            max_audio_minutes=60,
        ),
    )


@pytest.fixture
def sample_video():
    return VideoInfo(
        video_id="vid1", title="Test Video",
        url="https://youtube.com/watch?v=vid1",
        channel_name="Test Channel", category="AI",
        upload_date=datetime.now(timezone.utc), duration_seconds=600,
        transcript="some transcript text here",
    )


@pytest.fixture
def sample_episode():
    return EpisodeInfo(
        episode_id="ep_abc", title="Test Episode", show_name="Test Podcast",
        show_url="https://open.spotify.com/show/abc",
        episode_url="https://podcast.example.com/ep1",
        audio_url="https://cdn.example.com/ep1.mp3",
        category="AI",
        published_at=datetime.now(timezone.utc), duration_seconds=3600,
    )


def _mock_config_file(tmp_path: Path, config_dict: dict) -> Path:
    import yaml
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config_dict))
    return path


# ---------------------------------------------------------------------------
# Tests: dry-run mode
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_no_files_generated(self, tmp_path, config):
        state_path = tmp_path / "state.json"

        with patch("src.main.load_config", return_value=config):
            with patch("src.main.fetch_new_videos", return_value=[MagicMock(title="v1")]):
                with patch("src.main.fetch_new_episodes", return_value=[MagicMock(title="e1")]):
                    run(
                        config_path=tmp_path / "config.yaml",
                        output_dir=tmp_path / "output",
                        state_path=state_path,
                        dry_run=True,
                    )

        # No output files should be generated
        assert not (tmp_path / "output").exists()
        assert not state_path.exists()


# ---------------------------------------------------------------------------
# Tests: YouTube pipeline
# ---------------------------------------------------------------------------

class TestYouTubePipeline:
    def test_processes_video_successfully(self, tmp_path, config, sample_video):
        state_path = tmp_path / "state.json"
        output_dir = tmp_path / "output"

        mock_paths = {
            "summary_path": output_dir / "summaries/2026-02-19/test.md",
            "slug": "test",
        }

        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[sample_video]):
                    with patch("src.main.fetch_new_episodes", return_value=[]):
                        with patch("src.main.summarize", return_value="## Summary text"):
                            with patch("src.main.generate_summary_files", return_value=mock_paths):
                                with patch("src.main.generate_daily_digest"):
                                    with patch("src.main.generate_podcast_daily_digest"):
                                        with patch("src.main.generate_error_report"):
                                            with patch("src.main.generate_viewer"):
                                                with patch("src.main.save_state"):
                                                    with patch("src.main.cleanup_old_content", return_value=[]):
                                                        with patch("src.main.cleanup_state"):
                                                            run(
                                                                config_path=tmp_path / "config.yaml",
                                                                output_dir=output_dir,
                                                                state_path=state_path,
                                                            )

    def test_skips_video_without_transcript(self, tmp_path, config, sample_video):
        """Videos with no transcript are skipped — error logged, no Gemini call made."""
        no_transcript_video = VideoInfo(
            video_id="vid1", title="No Transcript",
            url="https://youtube.com/watch?v=vid1",
            channel_name="Test Channel", category="AI",
            upload_date=datetime.now(timezone.utc), duration_seconds=600,
            transcript=None,
        )

        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[no_transcript_video]):
                    with patch("src.main.fetch_new_episodes", return_value=[]):
                        with patch("src.main.summarize") as mock_summarize:
                            with patch("src.main.generate_daily_digest"):
                                with patch("src.main.generate_podcast_daily_digest"):
                                    with patch("src.main.generate_error_report"):
                                        with patch("src.main.generate_viewer"):
                                            with patch("src.main.save_state"):
                                                with patch("src.main.cleanup_old_content", return_value=[]):
                                                    with patch("src.main.cleanup_state"):
                                                        with pytest.raises(SystemExit):
                                                            run(
                                                                config_path=tmp_path / "config.yaml",
                                                                output_dir=tmp_path / "output",
                                                                state_path=tmp_path / "state.json",
                                                            )

        mock_summarize.assert_not_called()

    def test_youtube_fetch_error_logged_and_continues(self, tmp_path, config):
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", side_effect=Exception("Network error")):
                    with patch("src.main.fetch_new_episodes", return_value=[]):
                        with patch("src.main.generate_daily_digest") as mock_digest:
                            with patch("src.main.generate_podcast_daily_digest"):
                                with patch("src.main.generate_error_report") as mock_errors:
                                    with patch("src.main.generate_viewer"):
                                        with patch("src.main.save_state"):
                                            with patch("src.main.cleanup_old_content", return_value=[]):
                                                with patch("src.main.cleanup_state"):
                                                    with pytest.raises(SystemExit):
                                                        run(
                                                            config_path=tmp_path / "config.yaml",
                                                            output_dir=tmp_path / "output",
                                                            state_path=tmp_path / "state.json",
                                                        )

        # Pipeline should continue and generate output despite error
        mock_digest.assert_called_once()

    def test_quota_exhausted_saves_partial_progress(self, tmp_path, config, sample_video):
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[sample_video]):
                    with patch("src.main.fetch_new_episodes", return_value=[]):
                        with patch("src.main.summarize",
                                   side_effect=QuotaExhaustedError("daily quota")):
                            with patch("src.main.generate_daily_digest") as mock_digest:
                                with patch("src.main.generate_podcast_daily_digest"):
                                    with patch("src.main.generate_error_report"):
                                        with patch("src.main.generate_viewer"):
                                            with patch("src.main.save_state") as mock_save:
                                                with patch("src.main.cleanup_old_content", return_value=[]):
                                                    with patch("src.main.cleanup_state"):
                                                        run(
                                                            config_path=tmp_path / "config.yaml",
                                                            output_dir=tmp_path / "output",
                                                            state_path=tmp_path / "state.json",
                                                        )

        # State should be saved even on quota exhaustion
        mock_save.assert_called_once()
        mock_digest.assert_called_once()

    def test_summarize_error_logged_not_in_digest(self, tmp_path, config, sample_video):
        """Summarization failures go to error report only — not shown as cards in the digest."""
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[sample_video]):
                    with patch("src.main.fetch_new_episodes", return_value=[]):
                        with patch("src.main.summarize", side_effect=RuntimeError("model error")):
                            with patch("src.main.generate_daily_digest") as mock_digest:
                                with patch("src.main.generate_podcast_daily_digest"):
                                    with patch("src.main.generate_error_report") as mock_errors:
                                        with patch("src.main.generate_viewer"):
                                            with patch("src.main.save_state"):
                                                with patch("src.main.cleanup_old_content", return_value=[]):
                                                    with patch("src.main.cleanup_state"):
                                                        with pytest.raises(SystemExit):
                                                            run(
                                                                config_path=tmp_path / "config.yaml",
                                                                output_dir=tmp_path / "output",
                                                                state_path=tmp_path / "state.json",
                                                            )

        # Digest should have NO error entries (errors go to log only)
        call_args = mock_digest.call_args[0]
        entries = call_args[0]
        assert len(entries) == 0
        # Error report should capture it
        error_call_args = mock_errors.call_args[0]
        errors = error_call_args[0]
        assert any("model error" in e["message"] for e in errors)


    def test_ip_blocked_error_recorded_in_state(self, tmp_path, config):
        """When fetch_new_videos raises IpBlockedError, video is recorded in ip_blocked state."""
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", side_effect=IpBlockedError("vid_blocked")):
                    with patch("src.main.fetch_new_episodes", return_value=[]):
                        with patch("src.main.generate_daily_digest"):
                            with patch("src.main.generate_podcast_daily_digest"):
                                with patch("src.main.generate_error_report"):
                                    with patch("src.main.generate_viewer"):
                                        with patch("src.main.save_state") as mock_save:
                                            with patch("src.main.cleanup_old_content", return_value=[]):
                                                with patch("src.main.cleanup_state"):
                                                    with pytest.raises(SystemExit):
                                                        run(
                                                            config_path=tmp_path / "config.yaml",
                                                            output_dir=tmp_path / "output",
                                                            state_path=tmp_path / "state.json",
                                                        )

        # State should have been saved with the ip_blocked entry
        assert mock_save.called
        saved_state = mock_save.call_args[0][1]  # second positional arg is the state dict
        assert "ip_blocked" in saved_state
        assert "vid_blocked" in saved_state["ip_blocked"]


# ---------------------------------------------------------------------------
# Tests: Podcast pipeline
# ---------------------------------------------------------------------------

class TestPodcastPipeline:
    def test_processes_episode_successfully(self, tmp_path, config, sample_episode):
        output_dir = tmp_path / "output"
        mock_paths = {
            "summary_path": output_dir / "podcast-summaries/2026-02-19/test.md",
            "slug": "test",
        }

        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[]):
                    with patch("src.main.fetch_new_episodes", return_value=[sample_episode]):
                        with patch("src.main.download_and_transcribe", return_value="## Summary"):
                            with patch("src.main.generate_podcast_summary_files", return_value=mock_paths):
                                with patch("src.main.generate_daily_digest"):
                                    with patch("src.main.generate_podcast_daily_digest") as mock_pd:
                                        with patch("src.main.generate_error_report"):
                                            with patch("src.main.generate_viewer"):
                                                with patch("src.main.save_state"):
                                                    with patch("src.main.cleanup_old_content", return_value=[]):
                                                        with patch("src.main.cleanup_state"):
                                                            run(
                                                                config_path=tmp_path / "config.yaml",
                                                                output_dir=output_dir,
                                                                state_path=tmp_path / "state.json",
                                                            )

        # Podcast digest should be called with the episode
        call_args = mock_pd.call_args[0]
        entries = call_args[0]
        assert len(entries) == 1
        assert entries[0]["episode"] == sample_episode
        assert entries[0]["error"] is None

    def test_rss_lookup_error_skips_show(self, tmp_path, config):
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[]):
                    with patch("src.main.fetch_new_episodes",
                               side_effect=RSSLookupError("no feed found")):
                        with patch("src.main.generate_daily_digest"):
                            with patch("src.main.generate_podcast_daily_digest") as mock_pd:
                                with patch("src.main.generate_error_report") as mock_err:
                                    with patch("src.main.generate_viewer"):
                                        with patch("src.main.save_state"):
                                            with patch("src.main.cleanup_old_content", return_value=[]):
                                                with patch("src.main.cleanup_state"):
                                                    with pytest.raises(SystemExit):
                                                        run(
                                                            config_path=tmp_path / "config.yaml",
                                                            output_dir=tmp_path / "output",
                                                            state_path=tmp_path / "state.json",
                                                        )

        # Error should be recorded
        error_call_args = mock_err.call_args[0]
        errors = error_call_args[0]
        assert any("RSS" in e["source"] for e in errors)

        # Podcast digest still called (with empty entries)
        call_args = mock_pd.call_args[0]
        assert call_args[0] == []

    def test_transcription_error_logged_not_in_digest(self, tmp_path, config, sample_episode):
        """Transcription failures go to error report only — not shown as cards in the podcast digest."""
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[]):
                    with patch("src.main.fetch_new_episodes", return_value=[sample_episode]):
                        with patch("src.main.download_and_transcribe",
                                   side_effect=TranscriptionError("audio failed")):
                            with patch("src.main.generate_daily_digest"):
                                with patch("src.main.generate_podcast_daily_digest") as mock_pd:
                                    with patch("src.main.generate_error_report") as mock_errors:
                                        with patch("src.main.generate_viewer"):
                                            with patch("src.main.save_state"):
                                                with patch("src.main.cleanup_old_content", return_value=[]):
                                                    with patch("src.main.cleanup_state"):
                                                        with pytest.raises(SystemExit):
                                                            run(
                                                                config_path=tmp_path / "config.yaml",
                                                                output_dir=tmp_path / "output",
                                                                state_path=tmp_path / "state.json",
                                                            )

        # Podcast digest should have NO error entries
        call_args = mock_pd.call_args[0]
        entries = call_args[0]
        assert len(entries) == 0
        # Error report should capture it
        error_call_args = mock_errors.call_args[0]
        errors = error_call_args[0]
        assert any("audio failed" in e["message"] for e in errors)

    def test_podcast_quota_exhausted_saves_progress(self, tmp_path, config, sample_episode):
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", return_value=MagicMock()):
                with patch("src.main.fetch_new_videos", return_value=[]):
                    with patch("src.main.fetch_new_episodes", return_value=[sample_episode]):
                        with patch("src.main.download_and_transcribe",
                                   side_effect=QuotaExhaustedError("daily quota")):
                            with patch("src.main.generate_daily_digest"):
                                with patch("src.main.generate_podcast_daily_digest"):
                                    with patch("src.main.generate_error_report"):
                                        with patch("src.main.generate_viewer"):
                                            with patch("src.main.save_state") as mock_save:
                                                with patch("src.main.cleanup_old_content", return_value=[]):
                                                    with patch("src.main.cleanup_state"):
                                                        run(
                                                            config_path=tmp_path / "config.yaml",
                                                            output_dir=tmp_path / "output",
                                                            state_path=tmp_path / "state.json",
                                                        )

        mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Config errors
# ---------------------------------------------------------------------------

class TestConfigErrors:
    def test_invalid_config_exits(self, tmp_path):
        (tmp_path / "config.yaml").write_text("not a mapping")

        with pytest.raises(SystemExit):
            run(
                config_path=tmp_path / "config.yaml",
                output_dir=tmp_path / "output",
                state_path=tmp_path / "state.json",
            )

    def test_missing_config_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            run(
                config_path=tmp_path / "nonexistent.yaml",
                output_dir=tmp_path / "output",
                state_path=tmp_path / "state.json",
            )

    def test_missing_api_key_exits(self, tmp_path, config):
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.create_client", side_effect=ValueError("GEMINI_API_KEY not set")):
                with pytest.raises(SystemExit):
                    run(
                        config_path=tmp_path / "config.yaml",
                        output_dir=tmp_path / "output",
                        state_path=tmp_path / "state.json",
                    )


# ---------------------------------------------------------------------------
# Tests: dry-run empty table path
# ---------------------------------------------------------------------------

class TestDryRunEmptyTable:
    def test_dry_run_with_no_pending_items_prints_nothing_to_process(self, tmp_path, config, capsys):
        """When no new content exists, dry-run prints the 'nothing to process' message."""
        with patch("src.main.load_config", return_value=config):
            with patch("src.main.fetch_new_videos", return_value=[]):
                with patch("src.main.fetch_new_episodes", return_value=[]):
                    run(
                        config_path=tmp_path / "config.yaml",
                        output_dir=tmp_path / "output",
                        state_path=tmp_path / "state.json",
                        dry_run=True,
                    )

        out = capsys.readouterr().out
        assert "nothing to process" in out.lower()


# ---------------------------------------------------------------------------
# Tests: auth error paths (Gemini 401/403 in YouTube and Podcast)
# ---------------------------------------------------------------------------

def _std_patches(tmp_path, config, videos=None, episodes=None):
    """Return a context manager stack for a standard run with controllable mocks."""
    import contextlib
    videos = videos or []
    episodes = episodes or []

    @contextlib.contextmanager
    def _ctx():
        with patch("src.main.load_config", return_value=config), \
             patch("src.main.create_client", return_value=MagicMock()), \
             patch("src.main.fetch_new_videos", return_value=videos), \
             patch("src.main.fetch_new_episodes", return_value=episodes), \
             patch("src.main.generate_daily_digest"), \
             patch("src.main.generate_podcast_daily_digest"), \
             patch("src.main.generate_error_report"), \
             patch("src.main.generate_viewer"), \
             patch("src.main.save_state"), \
             patch("src.main.cleanup_old_content", return_value=[]), \
             patch("src.main.cleanup_state"):
            yield

    return _ctx()


class TestAuthErrors:
    def test_youtube_auth_error_exits(self, tmp_path, config, sample_video):
        """Gemini 401 during YouTube summarization causes immediate sys.exit(1)."""
        with _std_patches(tmp_path, config, videos=[sample_video]):
            with patch("src.main.summarize", side_effect=RuntimeError("401 Unauthorized")):
                with pytest.raises(SystemExit):
                    run(
                        config_path=tmp_path / "config.yaml",
                        output_dir=tmp_path / "output",
                        state_path=tmp_path / "state.json",
                    )

    def test_podcast_auth_error_exits(self, tmp_path, config, sample_episode):
        """Gemini 403 during podcast transcription causes immediate sys.exit(1)."""
        with _std_patches(tmp_path, config, episodes=[sample_episode]):
            with patch("src.main.download_and_transcribe",
                       side_effect=RuntimeError("403 permission_denied")):
                with pytest.raises(SystemExit):
                    run(
                        config_path=tmp_path / "config.yaml",
                        output_dir=tmp_path / "output",
                        state_path=tmp_path / "state.json",
                    )


# ---------------------------------------------------------------------------
# Tests: file-write failure path
# ---------------------------------------------------------------------------

class TestFileWriteFailure:
    def test_summary_file_write_failure_continues(self, tmp_path, config, sample_video):
        """If generate_summary_files raises, the video is skipped but run continues."""
        with _std_patches(tmp_path, config, videos=[sample_video]):
            with patch("src.main.summarize", return_value="summary text"):
                with patch("src.main.generate_summary_files",
                           side_effect=OSError("disk full")):
                    with pytest.raises(SystemExit):
                        run(
                            config_path=tmp_path / "config.yaml",
                            output_dir=tmp_path / "output",
                            state_path=tmp_path / "state.json",
                        )

    def test_podcast_file_write_failure_entry_recorded_with_error(
        self, tmp_path, config, sample_episode
    ):
        """If generate_podcast_summary_files raises, entry is added with error=str."""
        with patch("src.main.load_config", return_value=config), \
             patch("src.main.create_client", return_value=MagicMock()), \
             patch("src.main.fetch_new_videos", return_value=[]), \
             patch("src.main.fetch_new_episodes", return_value=[sample_episode]), \
             patch("src.main.download_and_transcribe", return_value="summary"), \
             patch("src.main.generate_podcast_summary_files",
                   side_effect=OSError("no space left")), \
             patch("src.main.generate_daily_digest"), \
             patch("src.main.generate_podcast_daily_digest") as mock_pd, \
             patch("src.main.generate_error_report"), \
             patch("src.main.generate_viewer"), \
             patch("src.main.save_state"), \
             patch("src.main.cleanup_old_content", return_value=[]), \
             patch("src.main.cleanup_state"):
            with pytest.raises(SystemExit):
                run(
                    config_path=tmp_path / "config.yaml",
                    output_dir=tmp_path / "output",
                    state_path=tmp_path / "state.json",
                )

        entries = mock_pd.call_args[0][0]
        assert len(entries) == 1
        assert entries[0]["error"] == "no space left"
        assert entries[0]["paths"] is None


# ---------------------------------------------------------------------------
# Tests: IP-blocked retry queue
# ---------------------------------------------------------------------------

class TestIpBlockedRetry:
    def test_blocked_video_retried_and_promoted_on_success(
        self, tmp_path, config, sample_video
    ):
        """A video in ip_blocked state is retried; on success it moves to youtube state."""
        state_with_blocked = {
            "youtube": {},
            "podcasts": {},
            "rss_cache": {},
            "ip_blocked": {
                "vid_blocked": {
                    "date": "2026-02-20",
                    "title": "Blocked Video",
                    "url": "https://youtube.com/watch?v=vid_blocked",
                    "channel": "Test Channel",
                }
            },
        }

        with patch("src.main.load_config", return_value=config), \
             patch("src.main.load_state", return_value=state_with_blocked), \
             patch("src.main.create_client", return_value=MagicMock()), \
             patch("src.main.fetch_new_videos", return_value=[]), \
             patch("src.main.fetch_new_episodes", return_value=[]), \
             patch("src.main._get_transcript",
                   return_value=("recovered transcript", ())), \
             patch("src.main.summarize", return_value="summary"), \
             patch("src.main.generate_summary_files",
                   return_value={"summary_path": tmp_path / "s.md", "slug": "s"}), \
             patch("src.main.generate_daily_digest"), \
             patch("src.main.generate_podcast_daily_digest"), \
             patch("src.main.generate_error_report"), \
             patch("src.main.generate_viewer"), \
             patch("src.main.save_state") as mock_save, \
             patch("src.main.cleanup_old_content", return_value=[]), \
             patch("src.main.cleanup_state"):
            run(
                config_path=tmp_path / "config.yaml",
                output_dir=tmp_path / "output",
                state_path=tmp_path / "state.json",
            )

        saved = mock_save.call_args[0][1]
        # Video should have been promoted to youtube state
        assert "vid_blocked" in saved.get("youtube", {})
        # And removed from ip_blocked
        assert "vid_blocked" not in saved.get("ip_blocked", {})

    def test_still_blocked_video_stays_in_ip_blocked(self, tmp_path, config):
        """A video that's still IP-blocked on retry stays in ip_blocked state."""
        state_with_blocked = {
            "youtube": {},
            "podcasts": {},
            "rss_cache": {},
            "ip_blocked": {
                "vid_still_blocked": {
                    "date": "2026-02-20",
                    "title": "Still Blocked",
                    "url": "https://youtube.com/watch?v=vid_still_blocked",
                    "channel": "Test Channel",
                }
            },
        }

        with patch("src.main.load_config", return_value=config), \
             patch("src.main.load_state", return_value=state_with_blocked), \
             patch("src.main.create_client", return_value=MagicMock()), \
             patch("src.main.fetch_new_videos", return_value=[]), \
             patch("src.main.fetch_new_episodes", return_value=[]), \
             patch("src.main._get_transcript",
                   side_effect=IpBlockedError("vid_still_blocked")), \
             patch("src.main.generate_daily_digest"), \
             patch("src.main.generate_podcast_daily_digest"), \
             patch("src.main.generate_error_report"), \
             patch("src.main.generate_viewer"), \
             patch("src.main.save_state") as mock_save, \
             patch("src.main.cleanup_old_content", return_value=[]), \
             patch("src.main.cleanup_state"):
            run(
                config_path=tmp_path / "config.yaml",
                output_dir=tmp_path / "output",
                state_path=tmp_path / "state.json",
            )

        saved = mock_save.call_args[0][1]
        # Should still be in ip_blocked, NOT promoted
        assert "vid_still_blocked" in saved.get("ip_blocked", {})
        assert "vid_still_blocked" not in saved.get("youtube", {})

    def test_expired_ip_blocked_entries_removed(self, tmp_path, config):
        """ip_blocked entries older than TTL are expired at run start."""
        state_with_old_blocked = {
            "youtube": {},
            "podcasts": {},
            "rss_cache": {},
            "ip_blocked": {
                "old_vid": {
                    "date": "2020-01-01",  # way past TTL
                    "title": "Old Blocked",
                    "url": "https://youtube.com/watch?v=old_vid",
                    "channel": "Test Channel",
                }
            },
        }

        with patch("src.main.load_config", return_value=config), \
             patch("src.main.load_state", return_value=state_with_old_blocked), \
             patch("src.main.create_client", return_value=MagicMock()), \
             patch("src.main.fetch_new_videos", return_value=[]), \
             patch("src.main.fetch_new_episodes", return_value=[]), \
             patch("src.main.generate_daily_digest"), \
             patch("src.main.generate_podcast_daily_digest"), \
             patch("src.main.generate_error_report"), \
             patch("src.main.generate_viewer"), \
             patch("src.main.save_state") as mock_save, \
             patch("src.main.cleanup_old_content", return_value=[]), \
             patch("src.main.cleanup_state"):
            run(
                config_path=tmp_path / "config.yaml",
                output_dir=tmp_path / "output",
                state_path=tmp_path / "state.json",
            )

        saved = mock_save.call_args[0][1]
        assert "old_vid" not in saved.get("ip_blocked", {})


# ---------------------------------------------------------------------------
# Tests: notify_email path
# ---------------------------------------------------------------------------

class TestNotifyEmail:
    def test_notification_sent_when_email_configured(self, tmp_path, sample_video):
        config_with_email = Config(
            categories=[Category(name="AI", color="#4A90D9")],
            youtube_sources=[
                YouTubeSource(channel_url="https://youtube.com/@test",
                              name="Test Channel", category="AI"),
            ],
            podcast_shows=[],
            settings=Settings(
                max_age_days=7, gemini_model="gemini-2.5-flash",
                max_videos_per_channel=3, lookback_hours=26,
                max_episodes_per_show=3, min_episodes_per_show=1,
                max_audio_minutes=60, notify_email="test@example.com",
            ),
        )
        mock_paths = {
            "summary_path": tmp_path / "s.md",
            "slug": "s",
        }

        with patch("src.main.load_config", return_value=config_with_email), \
             patch("src.main.create_client", return_value=MagicMock()), \
             patch("src.main.fetch_new_videos", return_value=[sample_video]), \
             patch("src.main.fetch_new_episodes", return_value=[]), \
             patch("src.main.summarize", return_value="## Summary"), \
             patch("src.main.generate_summary_files", return_value=mock_paths), \
             patch("src.main.generate_daily_digest"), \
             patch("src.main.generate_podcast_daily_digest"), \
             patch("src.main.generate_error_report"), \
             patch("src.main.generate_viewer"), \
             patch("src.main.save_state"), \
             patch("src.main.cleanup_old_content", return_value=[]), \
             patch("src.main.cleanup_state"), \
             patch("src.main.send_run_notification") as mock_notify:
            run(
                config_path=tmp_path / "config.yaml",
                output_dir=tmp_path / "output",
                state_path=tmp_path / "state.json",
            )

        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args[1]
        assert call_kwargs["to_addr"] == "test@example.com"

    def test_notification_failure_does_not_crash_run(self, tmp_path, config, sample_video):
        """Email send errors are swallowed — they are non-fatal."""
        config_with_email = Config(
            categories=config.categories,
            youtube_sources=config.youtube_sources,
            podcast_shows=[],
            settings=Settings(
                max_age_days=7, gemini_model="gemini-2.5-flash",
                max_videos_per_channel=3, lookback_hours=26,
                max_episodes_per_show=3, min_episodes_per_show=1,
                max_audio_minutes=60, notify_email="test@example.com",
            ),
        )
        mock_paths = {"summary_path": tmp_path / "s.md", "slug": "s"}

        with patch("src.main.load_config", return_value=config_with_email), \
             patch("src.main.create_client", return_value=MagicMock()), \
             patch("src.main.fetch_new_videos", return_value=[sample_video]), \
             patch("src.main.fetch_new_episodes", return_value=[]), \
             patch("src.main.summarize", return_value="summary"), \
             patch("src.main.generate_summary_files", return_value=mock_paths), \
             patch("src.main.generate_daily_digest"), \
             patch("src.main.generate_podcast_daily_digest"), \
             patch("src.main.generate_error_report"), \
             patch("src.main.generate_viewer"), \
             patch("src.main.save_state"), \
             patch("src.main.cleanup_old_content", return_value=[]), \
             patch("src.main.cleanup_state"), \
             patch("src.main.send_run_notification",
                   side_effect=Exception("SMTP error")):
            # Should NOT raise — email failure is non-fatal
            run(
                config_path=tmp_path / "config.yaml",
                output_dir=tmp_path / "output",
                state_path=tmp_path / "state.json",
            )


# ---------------------------------------------------------------------------
# Tests: podcast generic Exception (non-auth, non-quota, non-TranscriptionError)
# ---------------------------------------------------------------------------

class TestPodcastGenericError:
    def test_podcast_generic_exception_logged_continues(
        self, tmp_path, config, sample_episode
    ):
        """A generic RuntimeError during download_and_transcribe is caught and logged."""
        with _std_patches(tmp_path, config, episodes=[sample_episode]):
            with patch("src.main.download_and_transcribe",
                       side_effect=RuntimeError("random failure")):
                with pytest.raises(SystemExit):
                    run(
                        config_path=tmp_path / "config.yaml",
                        output_dir=tmp_path / "output",
                        state_path=tmp_path / "state.json",
                    )

    def test_podcast_generic_fetch_exception_logged(self, tmp_path, config):
        """A generic Exception during fetch_new_episodes is caught and run continues."""
        with _std_patches(tmp_path, config):
            with patch("src.main.fetch_new_episodes",
                       side_effect=RuntimeError("connection reset")):
                with pytest.raises(SystemExit):
                    run(
                        config_path=tmp_path / "config.yaml",
                        output_dir=tmp_path / "output",
                        state_path=tmp_path / "state.json",
                    )


# ---------------------------------------------------------------------------
# Tests: main() CLI entrypoint
# ---------------------------------------------------------------------------

class TestMainEntrypoint:
    def test_main_calls_run_with_defaults(self, tmp_path, monkeypatch):
        """main() parses args and calls run() with correct defaults."""
        import sys
        monkeypatch.setattr(sys, "argv", ["src.main"])

        with patch("src.main.run") as mock_run:
            from src.main import main
            main()

        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        assert kwargs["config_path"] == Path("config.yaml")
        assert kwargs["output_dir"] == Path("output")
        assert kwargs["state_path"] == Path("state.json")
        assert kwargs["dry_run"] is False

    def test_main_dry_run_flag(self, tmp_path, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "argv", ["src.main", "--dry-run"])

        with patch("src.main.run") as mock_run:
            from src.main import main
            main()

        assert mock_run.call_args[1]["dry_run"] is True
