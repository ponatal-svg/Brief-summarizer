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
