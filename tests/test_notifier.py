"""Tests for src/notifier.py — email notification module."""

from __future__ import annotations

import email
import email.header
import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.fetchers.youtube import VideoInfo
from src.fetchers.podcast import EpisodeInfo
from src.notifier import send_run_notification, _build_plain, _build_html


def _decode_subject(raw_mime: str) -> str:
    """Parse a raw MIME message string and return the decoded Subject header."""
    msg = email.message_from_string(raw_mime)
    subject_raw = msg.get("Subject", "")
    parts = email.header.decode_header(subject_raw)
    decoded = ""
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded += part.decode(charset or "utf-8")
        else:
            decoded += part
    return decoded


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_video(title="Test Video", channel="Test Channel", vid_id="vid1"):
    return VideoInfo(
        video_id=vid_id, title=title,
        url=f"https://youtube.com/watch?v={vid_id}",
        channel_name=channel, category="AI",
        upload_date=datetime.now(timezone.utc), duration_seconds=600,
        transcript="transcript",
    )


def _make_episode(title="Test Episode", show="Test Podcast"):
    return EpisodeInfo(
        episode_id="ep1", title=title, show_name=show,
        show_url="https://spotify.com/show/abc",
        episode_url="https://podcast.example.com/ep1",
        audio_url="https://cdn.example.com/ep1.mp3",
        category="AI",
        published_at=datetime.now(timezone.utc), duration_seconds=3600,
    )


def _make_skipped(type_="youtube", source="AI Explained", title="Some Video",
                  reason="IP block", action="Refresh cookies.txt"):
    return {
        "type": type_,
        "source": source,
        "title": title,
        "url": "https://youtube.com/watch?v=abc",
        "reason": reason,
        "action": action,
    }


# ---------------------------------------------------------------------------
# Tests: send_run_notification
# ---------------------------------------------------------------------------

class TestSendRunNotification:
    def test_skips_silently_when_no_smtp_env(self):
        """Should not raise if SMTP_USER/SMTP_PASSWORD not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Should complete without error
            send_run_notification(
                to_addr="test@example.com",
                date_str="2026-02-23",
                digest_entries=[],
                podcast_entries=[],
                skipped_items=[],
                errors=[],
            )

    def test_sends_email_when_smtp_configured(self):
        """Should call SMTP send when credentials are present."""
        env = {"SMTP_USER": "bot@gmail.com", "SMTP_PASSWORD": "secret"}
        mock_smtp = MagicMock()
        mock_smtp_cls = MagicMock(return_value=mock_smtp)
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        with patch.dict(os.environ, env), \
             patch("smtplib.SMTP", return_value=mock_smtp):
            send_run_notification(
                to_addr="user@example.com",
                date_str="2026-02-23",
                digest_entries=[{"video": _make_video(), "paths": {}, "error": None}],
                podcast_entries=[],
                skipped_items=[],
                errors=[],
            )
        mock_smtp.sendmail.assert_called_once()

    def test_raises_on_smtp_failure(self):
        """SMTP errors should be re-raised so main.py can log them."""
        env = {"SMTP_USER": "bot@gmail.com", "SMTP_PASSWORD": "secret"}
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        mock_smtp.sendmail.side_effect = OSError("connection refused")

        with patch.dict(os.environ, env), \
             patch("smtplib.SMTP", return_value=mock_smtp):
            with pytest.raises(RuntimeError, match="SMTP send failed"):
                send_run_notification(
                    to_addr="user@example.com",
                    date_str="2026-02-23",
                    digest_entries=[],
                    podcast_entries=[],
                    skipped_items=[],
                    errors=[],
                )

    def test_subject_ok_when_no_skipped(self):
        """Subject should show ✅ when nothing was skipped."""
        env = {"SMTP_USER": "bot@gmail.com", "SMTP_PASSWORD": "secret"}
        sent_messages = []

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        mock_smtp.sendmail.side_effect = lambda f, t, m: sent_messages.append(m)

        with patch.dict(os.environ, env), \
             patch("smtplib.SMTP", return_value=mock_smtp):
            send_run_notification(
                to_addr="user@example.com",
                date_str="2026-02-23",
                digest_entries=[{"video": _make_video(), "paths": {}, "error": None}],
                podcast_entries=[],
                skipped_items=[],
                errors=[],
            )

        assert sent_messages
        subject = _decode_subject(sent_messages[0])
        assert "✅" in subject
        assert "⚠️" not in subject

    def test_subject_warning_when_skipped(self):
        """Subject should show ⚠️ when items were skipped."""
        env = {"SMTP_USER": "bot@gmail.com", "SMTP_PASSWORD": "secret"}
        sent_messages = []

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        mock_smtp.sendmail.side_effect = lambda f, t, m: sent_messages.append(m)

        with patch.dict(os.environ, env), \
             patch("smtplib.SMTP", return_value=mock_smtp):
            send_run_notification(
                to_addr="user@example.com",
                date_str="2026-02-23",
                digest_entries=[],
                podcast_entries=[],
                skipped_items=[_make_skipped()],
                errors=[],
            )

        assert sent_messages
        subject = _decode_subject(sent_messages[0])
        assert "⚠️" in subject


# ---------------------------------------------------------------------------
# Tests: _build_plain
# ---------------------------------------------------------------------------

class TestBuildPlain:
    def test_processed_videos_appear(self):
        entries = [{"video": _make_video("My Video", "My Channel"), "paths": {}, "error": None}]
        text = _build_plain("2026-02-23", entries, [], [], [])
        assert "My Video" in text
        assert "My Channel" in text

    def test_processed_podcasts_appear(self):
        entries = [{"episode": _make_episode("My Episode", "My Show"), "paths": {}, "error": None}]
        text = _build_plain("2026-02-23", [], entries, [], [])
        assert "My Episode" in text
        assert "My Show" in text

    def test_skipped_items_show_reason_and_action(self):
        skipped = [_make_skipped(reason="IP block detected", action="Refresh cookies.txt")]
        text = _build_plain("2026-02-23", [], [], skipped, [])
        assert "IP block detected" in text
        assert "Refresh cookies.txt" in text
        assert "ACTION REQUIRED" in text

    def test_no_skipped_shows_success_message(self):
        text = _build_plain("2026-02-23", [], [], [], [])
        assert "No skipped items" in text

    def test_errors_listed(self):
        errors = [{"source": "Gemini/AI Explained", "message": "timeout"}]
        text = _build_plain("2026-02-23", [], [], [], errors)
        assert "Gemini/AI Explained" in text
        assert "timeout" in text

    def test_podcast_skipped_separated_from_youtube(self):
        skipped = [
            _make_skipped(type_="youtube", source="Cold Fusion"),
            _make_skipped(type_="podcast", source="Hard Fork"),
        ]
        text = _build_plain("2026-02-23", [], [], skipped, [])
        assert "YouTube:" in text
        assert "Podcasts:" in text


# ---------------------------------------------------------------------------
# Tests: _build_html
# ---------------------------------------------------------------------------

class TestBuildHtml:
    def test_is_valid_html(self):
        html = _build_html("2026-02-23", [], [], [], [])
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_skipped_table_appears(self):
        skipped = [_make_skipped(reason="IP block", action="Refresh cookies")]
        html = _build_html("2026-02-23", [], [], skipped, [])
        assert "IP block" in html
        assert "Refresh cookies" in html
        assert "<table" in html

    def test_no_skipped_shows_success(self):
        html = _build_html("2026-02-23", [], [], [], [])
        assert "No skipped items" in html

    def test_errors_in_details_block(self):
        errors = [{"source": "Gemini/X", "message": "quota exceeded"}]
        html = _build_html("2026-02-23", [], [], [], errors)
        assert "<details>" in html
        assert "quota exceeded" in html

    def test_processed_items_linked(self):
        entries = [{"video": _make_video("Great Video", "Great Channel", "abc123"), "paths": {}, "error": None}]
        html = _build_html("2026-02-23", entries, [], [], [])
        assert "Great Video" in html
        assert "abc123" in html  # video ID in URL
