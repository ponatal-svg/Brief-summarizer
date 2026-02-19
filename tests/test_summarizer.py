"""Tests for Gemini summarizer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from src.summarizer import (
    create_client,
    summarize,
    _call_gemini,
    _format_duration_for_prompt,
    _get_language_name,
    QuotaExhaustedError,
    SUMMARY_PROMPT,
    NO_TRANSCRIPT_PROMPT,
)


class TestCreateClient:
    def test_with_explicit_key(self):
        with patch("src.summarizer.genai.Client") as mock_client_cls:
            create_client(api_key="test-key")
            mock_client_cls.assert_called_once_with(api_key="test-key")

    def test_with_env_var(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "env-key"}):
            with patch("src.summarizer.genai.Client") as mock_client_cls:
                create_client()
                mock_client_cls.assert_called_once_with(api_key="env-key")

    def test_no_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="GEMINI_API_KEY not set"):
                create_client()


class TestCallGemini:
    @patch("src.summarizer.time.sleep")
    def test_successful_call(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Summary text"
        mock_client.models.generate_content.return_value = mock_response

        result = _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        assert result == "Summary text"
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-2.0-flash",
            contents="test prompt",
        )
        # Should throttle before first call
        mock_sleep.assert_called_once_with(5)

    @patch("src.summarizer.time.sleep")
    def test_empty_response(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = None
        mock_client.models.generate_content.return_value = mock_response

        result = _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")
        assert result == ""

    @patch("src.summarizer.time.sleep")
    def test_non_retryable_error_propagates(self, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        # Should not retry non-429 errors
        assert mock_client.models.generate_content.call_count == 1

    @patch("src.summarizer.time.sleep")
    def test_retries_on_rate_limit(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Success after retry"

        # Fail twice with 429, then succeed
        mock_client.models.generate_content.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            mock_response,
        ]

        result = _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")
        assert result == "Success after retry"
        assert mock_client.models.generate_content.call_count == 3

    @patch("src.summarizer.time.sleep")
    def test_retry_backoff_increases(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "OK"

        mock_client.models.generate_content.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            mock_response,
        ]

        _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        # sleep calls: throttle(5), backoff(5), backoff(10), backoff(20)
        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == [5, 5, 10, 20]

    @patch("src.summarizer.time.sleep")
    def test_gives_up_after_max_retries(self, mock_sleep):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED"
        )

        with pytest.raises(RuntimeError, match="429"):
            _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        # 1 initial + 4 retries = 5 attempts
        assert mock_client.models.generate_content.call_count == 5

    @patch("src.summarizer.time.sleep")
    def test_daily_quota_raises_quota_exhausted_error(self, mock_sleep):
        """RPD (daily) quota hit → QuotaExhaustedError raised immediately, no retries."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED: daily quota exceeded"
        )

        with pytest.raises(QuotaExhaustedError):
            _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        # Should raise immediately, no retries
        assert mock_client.models.generate_content.call_count == 1

    @patch("src.summarizer.time.sleep")
    def test_per_day_quota_raises_quota_exhausted_error(self, mock_sleep):
        """'per day' in error message also triggers QuotaExhaustedError."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric 'per day'"
        )

        with pytest.raises(QuotaExhaustedError):
            _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        assert mock_client.models.generate_content.call_count == 1

    @patch("src.summarizer.time.sleep")
    def test_auth_error_401_raises_immediately(self, mock_sleep):
        """Invalid API key → raises immediately, no retries."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            "401 API_KEY_INVALID"
        )

        with pytest.raises(RuntimeError, match="401"):
            _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        assert mock_client.models.generate_content.call_count == 1

    @patch("src.summarizer.time.sleep")
    def test_auth_error_403_raises_immediately(self, mock_sleep):
        """Permission denied → raises immediately, no retries."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError(
            "403 PERMISSION_DENIED"
        )

        with pytest.raises(RuntimeError, match="403"):
            _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")

        assert mock_client.models.generate_content.call_count == 1

    @patch("src.summarizer.time.sleep")
    def test_server_error_500_retries(self, mock_sleep):
        """5xx server errors are retried like rate limits."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "OK after server error"

        mock_client.models.generate_content.side_effect = [
            RuntimeError("500 Internal Server Error"),
            RuntimeError("503 Service Unavailable"),
            mock_response,
        ]

        result = _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")
        assert result == "OK after server error"
        assert mock_client.models.generate_content.call_count == 3

    @patch("src.summarizer.time.sleep")
    def test_rpm_rate_limit_does_not_raise_quota_error(self, mock_sleep):
        """Generic 429 without 'daily'/'per day' is RPM — retried, not treated as quota exhausted."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "OK"

        mock_client.models.generate_content.side_effect = [
            RuntimeError("429 RESOURCE_EXHAUSTED: rate limit"),
            mock_response,
        ]

        result = _call_gemini(mock_client, "gemini-2.0-flash", "test prompt")
        assert result == "OK"
        assert mock_client.models.generate_content.call_count == 2


class TestFormatDurationForPrompt:
    def test_short_video(self):
        assert _format_duration_for_prompt(180) == "3m"

    def test_long_video(self):
        assert _format_duration_for_prompt(3720) == "1h 2m"

    def test_zero(self):
        assert _format_duration_for_prompt(0) == "unknown duration"

    def test_negative(self):
        assert _format_duration_for_prompt(-10) == "unknown duration"


class TestGetLanguageName:
    def test_english(self):
        assert _get_language_name("en") == "English"

    def test_spanish(self):
        assert _get_language_name("es") == "Spanish"

    def test_hebrew(self):
        assert _get_language_name("he") == "Hebrew"

    def test_unknown_returns_code(self):
        assert _get_language_name("xx") == "xx"


class TestSummarize:
    @patch("src.summarizer.time.sleep")
    def test_with_transcript(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Generated summary"
        mock_client.models.generate_content.return_value = mock_response

        result = summarize(
            client=mock_client,
            model="gemini-2.5-flash",
            title="Test Video",
            channel_name="Test Channel",
            transcript="This is a long enough transcript for testing purposes here with enough content.",
            duration_seconds=600,
        )

        assert isinstance(result, str)
        assert result == "Generated summary"
        # Single API call now (not two)
        assert mock_client.models.generate_content.call_count == 1

        calls = mock_client.models.generate_content.call_args_list
        prompt = calls[0].kwargs["contents"]
        assert "long enough transcript" in prompt
        assert "10m" in prompt  # duration included in prompt
        assert "Test Video" in prompt  # title included
        assert "Test Channel" in prompt  # channel included
        assert "ONLY state facts" in prompt  # accuracy rules present
        assert "English" in prompt  # default language

    @patch("src.summarizer.time.sleep")
    def test_with_transcript_spanish(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Resumen generado"
        mock_client.models.generate_content.return_value = mock_response

        result = summarize(
            client=mock_client,
            model="gemini-2.5-flash",
            title="Video de Prueba",
            channel_name="Canal de Prueba",
            transcript="Esta es una transcripción lo suficientemente larga para fines de prueba aquí.",
            duration_seconds=600,
            language="es",
        )

        assert result == "Resumen generado"
        calls = mock_client.models.generate_content.call_args_list
        prompt = calls[0].kwargs["contents"]
        assert "Spanish" in prompt

    @patch("src.summarizer.time.sleep")
    def test_without_transcript(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Placeholder note"
        mock_client.models.generate_content.return_value = mock_response

        result = summarize(
            client=mock_client,
            model="gemini-2.5-flash",
            title="Test Video",
            channel_name="Test Channel",
            transcript=None,
        )

        assert result == "Placeholder note"
        calls = mock_client.models.generate_content.call_args_list
        prompt = calls[0].kwargs["contents"]
        assert "Test Video" in prompt
        assert "Test Channel" in prompt
        assert "English" in prompt  # default language in no-transcript prompt

    @patch("src.summarizer.time.sleep")
    def test_with_short_transcript_uses_no_transcript_prompt(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Placeholder"
        mock_client.models.generate_content.return_value = mock_response

        summarize(
            client=mock_client,
            model="gemini-2.5-flash",
            title="Test",
            channel_name="Channel",
            transcript="too short",
        )

        calls = mock_client.models.generate_content.call_args_list
        prompt = calls[0].kwargs["contents"]
        assert "no transcript was available" in prompt

    @patch("src.summarizer.time.sleep")
    def test_with_empty_transcript(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Placeholder"
        mock_client.models.generate_content.return_value = mock_response

        summarize(
            client=mock_client,
            model="gemini-2.5-flash",
            title="Test",
            channel_name="Channel",
            transcript="   ",
        )

        calls = mock_client.models.generate_content.call_args_list
        prompt = calls[0].kwargs["contents"]
        assert "no transcript was available" in prompt

    @patch("src.summarizer.time.sleep")
    def test_prompt_includes_duration_context(self, mock_sleep):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Summary"
        mock_client.models.generate_content.return_value = mock_response

        summarize(
            client=mock_client,
            model="gemini-2.5-flash",
            title="Long Podcast",
            channel_name="Channel",
            transcript="A " * 100,  # long enough
            duration_seconds=3600,
        )

        calls = mock_client.models.generate_content.call_args_list
        prompt = calls[0].kwargs["contents"]
        assert "1h 0m" in prompt
