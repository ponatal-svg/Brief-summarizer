"""Tests for static viewer generation.

All tests are pure unit tests — no network calls, no external APIs.
They validate the generated HTML structure, CSS, and JavaScript
to ensure all visual and interactive features work correctly.
"""

from __future__ import annotations

import json
import re

import pytest

from src.config import Category
from src.viewer import generate_viewer, VIEWER_HTML


class _FakeConfig:
    """Minimal config-like object for testing."""
    def __init__(self, categories):
        self.categories = categories


# ===== FIXTURES =====

@pytest.fixture
def single_cat_config():
    return _FakeConfig([Category(name="AI", color="#4A90D9")])


@pytest.fixture
def multi_cat_config():
    return _FakeConfig([
        Category(name="AI", color="#4A90D9"),
        Category(name="Health & Wellness", color="#27AE60"),
        Category(name="Photography", color="#E67E22"),
    ])


@pytest.fixture
def generated_html(tmp_path, multi_cat_config):
    """Generate viewer and return HTML content."""
    generate_viewer(multi_cat_config, tmp_path)
    return (tmp_path / "youtube.html").read_text()


# ===== FILE GENERATION =====

class TestFileGeneration:
    def test_creates_youtube_html(self, tmp_path, single_cat_config):
        generate_viewer(single_cat_config, tmp_path)
        assert (tmp_path / "youtube.html").exists()

    def test_creates_categories_json(self, tmp_path, multi_cat_config):
        generate_viewer(multi_cat_config, tmp_path)
        cat_path = tmp_path / "categories.json"
        assert cat_path.exists()
        data = json.loads(cat_path.read_text())
        assert data == {
            "AI": "#4A90D9",
            "Health & Wellness": "#27AE60",
            "Photography": "#E67E22",
        }

    def test_creates_digest_index_empty(self, tmp_path, single_cat_config):
        generate_viewer(single_cat_config, tmp_path)
        index_path = tmp_path / "digest-index.json"
        assert index_path.exists()
        assert json.loads(index_path.read_text()) == []

    def test_digest_index_with_existing_digests(self, tmp_path, single_cat_config):
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir()
        (daily_dir / "2026-02-15.md").write_text("# test")
        (daily_dir / "2026-02-16.md").write_text("# test")

        generate_viewer(single_cat_config, tmp_path)
        data = json.loads((tmp_path / "digest-index.json").read_text())
        assert data == ["2026-02-15", "2026-02-16"]

    def test_digest_index_ignores_non_date_files(self, tmp_path, single_cat_config):
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir()
        (daily_dir / "2026-02-15.md").write_text("# test")
        (daily_dir / "notes.md").write_text("# not a date")

        generate_viewer(single_cat_config, tmp_path)
        data = json.loads((tmp_path / "digest-index.json").read_text())
        assert data == ["2026-02-15"]

    def test_creates_output_dir_if_missing(self, tmp_path, single_cat_config):
        new_dir = tmp_path / "new_output"
        generate_viewer(single_cat_config, new_dir)
        assert new_dir.exists()
        assert (new_dir / "youtube.html").exists()

    def test_overwrites_existing_files(self, tmp_path, single_cat_config):
        generate_viewer(single_cat_config, tmp_path)
        first_content = (tmp_path / "youtube.html").read_text()
        generate_viewer(single_cat_config, tmp_path)
        second_content = (tmp_path / "youtube.html").read_text()
        assert first_content == second_content


# ===== HTML STRUCTURE =====

class TestHTMLStructure:
    def test_has_morning_brief_title(self, generated_html):
        assert "<title>Morning Brief</title>" in generated_html

    def test_has_header_with_title(self, generated_html):
        assert "Morning Brief" in generated_html
        assert "/ youtube" in generated_html

    def test_has_date_buttons_container(self, generated_html):
        assert 'id="dateButtons"' in generated_html

    def test_has_filter_container(self, generated_html):
        assert 'id="filters"' in generated_html

    def test_has_toolbar_separator(self, generated_html):
        assert "toolbar-sep" in generated_html

    def test_has_expand_all_button(self, generated_html):
        assert 'id="expandAllBtn"' in generated_html
        assert "expand all" in generated_html
        assert "toggleExpandAll()" in generated_html

    def test_has_summary_count(self, generated_html):
        assert 'id="visibleCount"' in generated_html
        assert "summaries" in generated_html

    def test_has_content_grid(self, generated_html):
        assert 'id="content"' in generated_html

    def test_has_loading_indicator(self, generated_html):
        assert "loading..." in generated_html


# ===== DARK MODE =====

class TestDarkMode:
    def test_has_dark_mode_media_query(self, generated_html):
        assert "prefers-color-scheme: dark" in generated_html

    def test_dark_mode_overrides_bg(self, generated_html):
        # The dark mode block should redefine --bg
        dark_block = generated_html[generated_html.index("prefers-color-scheme: dark"):]
        assert "--bg:" in dark_block
        assert "--text:" in dark_block
        assert "--border:" in dark_block

    def test_warm_light_background(self, generated_html):
        # Not pure white — warmer tone
        assert "--bg: #F7F7F5" in generated_html


# ===== TYPOGRAPHY =====

class TestTypography:
    def test_uses_dm_sans_font(self, generated_html):
        assert "DM+Sans" in generated_html or "DM Sans" in generated_html

    def test_uses_jetbrains_mono(self, generated_html):
        assert "JetBrains+Mono" in generated_html or "JetBrains Mono" in generated_html

    def test_no_generic_fonts_as_primary(self, generated_html):
        # DM Sans should be primary, not Inter/Roboto/Arial
        assert "font-family: 'DM Sans'" in generated_html


# ===== TTS =====

class TestTTS:
    def test_has_speech_synthesis(self, generated_html):
        assert "speechSynthesis" in generated_html

    def test_has_four_speed_values(self, generated_html):
        assert "[1.0, 1.2, 1.5, 2.0]" in generated_html

    def test_speed_buttons_have_data_attributes(self, generated_html):
        # Speed buttons are JS-generated; check that data-speed is in the template
        assert "data-speed" in generated_html

    def test_speed_buttons_rendered_with_x_suffix(self, generated_html):
        # The JS template appends 'x</button>' for each speed value
        assert "x</button>'" in generated_html

    def test_set_speed_function_exists(self, generated_html):
        assert "function setSpeed(" in generated_html

    def test_set_speed_toggles_active_class(self, generated_html):
        # setSpeed should toggle .active on speed buttons
        assert "classList.toggle('active'" in generated_html

    def test_speed_active_class_has_styling(self, generated_html):
        assert ".speed-btn.active" in generated_html

    def test_listen_button_exists(self, generated_html):
        assert "listen-btn" in generated_html
        assert "listen" in generated_html.lower()

    def test_listen_button_toggles_playing(self, generated_html):
        assert "classList.add('playing')" in generated_html
        assert "classList.remove('playing')" in generated_html

    def test_playing_button_has_red_style(self, generated_html):
        assert ".listen-btn.playing" in generated_html
        assert "#e74c3c" in generated_html

    def test_tts_reads_title_then_key_findings(self, generated_html):
        # The plainText should be built as: title + channel + key findings + so what
        assert "ttsIntro = title" in generated_html
        assert "', by '" in generated_html
        assert "ttsIntro + plainBody" in generated_html
        # extractTTSBody function should exist and extract only findings/sowhat
        assert "function extractTTSBody(" in generated_html
        assert "'Key findings. '" in generated_html
        assert "'So what. '" in generated_html

    def test_tts_strips_urls(self, generated_html):
        # URLs should be stripped from TTS text
        assert "https?:" in generated_html  # regex to strip URLs

    def test_tts_controls_hidden_by_default(self, generated_html):
        # .tts-controls has display: none by default
        assert ".tts-controls {" in generated_html
        assert "display: none" in generated_html

    def test_tts_controls_visible_class(self, generated_html):
        assert ".tts-controls.visible" in generated_html
        assert "display: flex" in generated_html

    def test_stop_tts_function(self, generated_html):
        assert "function stopTTS()" in generated_html
        assert "speechSynthesis.cancel()" in generated_html

    def test_speed_label_present(self, generated_html):
        assert "tts-speed-label" in generated_html


# ===== EXPAND / COLLAPSE =====

class TestExpandCollapse:
    def test_toggle_summary_function(self, generated_html):
        assert "function toggleSummary(" in generated_html

    def test_card_open_class_toggles(self, generated_html):
        assert "classList.contains('open')" in generated_html
        assert "classList.add('open')" in generated_html
        assert "classList.remove('open')" in generated_html

    def test_card_body_hidden_by_default(self, generated_html):
        assert ".card-body {" in generated_html
        # card-body has display:none
        body_css = generated_html[generated_html.index(".card-body {"):]
        body_css = body_css[:body_css.index("}")]
        assert "display: none" in body_css

    def test_card_body_visible_when_open(self, generated_html):
        assert ".card.open .card-body" in generated_html

    def test_expand_arrow_rotates_on_open(self, generated_html):
        assert ".card.open .card-expand .arrow" in generated_html
        assert "rotate(90deg)" in generated_html

    def test_expand_button_text_changes(self, generated_html):
        # Button text toggles between "read summary" and "collapse"
        assert "'read summary'" in generated_html
        assert "'collapse'" in generated_html

    def test_expand_all_toggles_state(self, generated_html):
        assert "function toggleExpandAll()" in generated_html
        assert "allExpanded = !allExpanded" in generated_html

    def test_expand_all_button_text_updates(self, generated_html):
        assert "function updateExpandBtn()" in generated_html
        assert "'collapse all'" in generated_html
        assert "'expand all'" in generated_html

    def test_expand_all_button_arrow_rotates(self, generated_html):
        assert ".expand-all-btn.expanded .arrow" in generated_html

    def test_collapse_all_stops_tts(self, generated_html):
        # When collapsing all, should stop any TTS
        assert "if (!allExpanded) stopTTS()" in generated_html


# ===== FILTER =====

class TestFilter:
    def test_apply_filter_function(self, generated_html):
        assert "function applyFilter(" in generated_html

    def test_filter_toggles_active_class(self, generated_html):
        # Should toggle .active on filter buttons
        fn_block = generated_html[generated_html.index("function applyFilter("):]
        assert "classList.toggle('active'" in fn_block

    def test_filter_hides_non_matching_cards(self, generated_html):
        assert "c.style.display" in generated_html

    def test_filter_updates_count(self, generated_html):
        assert "visibleCount" in generated_html

    def test_build_filters_from_categories(self, generated_html):
        assert "function buildFilters()" in generated_html
        assert "CATEGORIES" in generated_html

    def test_all_filter_is_default(self, generated_html):
        # The 'all' button is created with 'active' class
        assert "'filter-btn active'" in generated_html
        assert "textContent = 'all'" in generated_html


# ===== DATE NAVIGATION =====

class TestDateNavigation:
    def test_load_date_function(self, generated_html):
        assert "function loadDate(" in generated_html

    def test_date_buttons_get_active_class(self, generated_html):
        fn_block = generated_html[generated_html.index("function loadDate("):]
        assert "classList.toggle('active'" in fn_block

    def test_date_pills_in_header(self, generated_html):
        assert 'class="date-pills"' in generated_html or 'id="dateButtons"' in generated_html

    def test_fetches_daily_digest(self, generated_html):
        assert "fetch('daily/'" in generated_html

    def test_empty_state_message(self, generated_html):
        assert "No digests available yet" in generated_html


# ===== SUMMARY PARSING =====

class TestSummaryParsing:
    def test_parses_hook_section(self, generated_html):
        assert "'## The Hook'" in generated_html

    def test_parses_key_findings(self, generated_html):
        assert "'## Key Findings'" in generated_html

    def test_parses_so_what(self, generated_html):
        assert "'## The So What?'" in generated_html

    def test_numbered_findings(self, generated_html):
        assert "finding-num" in generated_html
        assert "padStart(2, '0')" in generated_html

    def test_fallback_to_raw_markdown(self, generated_html):
        assert "summary-fallback" in generated_html
        assert "function md2html(" in generated_html

    def test_extracts_source_url(self, generated_html):
        assert "Source:" in generated_html
        assert "action-link" in generated_html

    def test_slug_generation(self, generated_html):
        assert "function findSlugForCard(" in generated_html
        assert "toLowerCase()" in generated_html
        assert "substring(0, 80)" in generated_html


# ===== CARD STRUCTURE =====

class TestCardStructure:
    def test_card_has_accent_bar(self, generated_html):
        assert "card-accent" in generated_html

    def test_card_has_category_tag(self, generated_html):
        assert "card-tag" in generated_html

    def test_card_has_source_name(self, generated_html):
        assert "card-source" in generated_html

    def test_card_has_duration(self, generated_html):
        assert "card-duration" in generated_html

    def test_card_has_data_cat_attribute(self, generated_html):
        assert "data-cat" in generated_html

    def test_card_stores_title_for_tts(self, generated_html):
        assert "data-title" in generated_html

    def test_card_stores_channel_for_tts(self, generated_html):
        assert "data-channel" in generated_html

    def test_card_stores_language(self, generated_html):
        assert "data-language" in generated_html


# ===== LANGUAGE SUPPORT =====

class TestLanguageSupport:
    def test_tts_utterance_has_lang_property(self, generated_html):
        assert "utterance.lang = lang" in generated_html

    def test_speak_text_accepts_lang_parameter(self, generated_html):
        assert "function speakText(text, btn, lang)" in generated_html

    def test_language_extracted_from_summary_metadata(self, generated_html):
        assert "Language:" in generated_html
        assert "summaryLang" in generated_html

    def test_language_defaults_to_english(self, generated_html):
        # Default fallback in multiple places
        assert "|| 'en'" in generated_html

    def test_language_passed_to_speak_text(self, generated_html):
        # toggleTTS should read language from body data
        assert "body.dataset.language" in generated_html


# ===== RESPONSIVE =====

class TestResponsive:
    def test_has_viewport_meta(self, generated_html):
        assert 'name="viewport"' in generated_html
        assert "width=device-width" in generated_html

    def test_has_mobile_breakpoint(self, generated_html):
        assert "@media (max-width: 640px)" in generated_html

    def test_duration_hidden_on_mobile(self, generated_html):
        mobile_css = generated_html[generated_html.index("max-width: 640px"):]
        assert "card-duration" in mobile_css
        assert "display: none" in mobile_css


# ===== ERROR HANDLING =====

class TestErrorHandling:
    def test_handles_fetch_errors(self, generated_html):
        assert "catch" in generated_html
        assert "Could not load summary" in generated_html

    def test_error_text_class(self, generated_html):
        assert "error-text" in generated_html

    def test_no_digest_message(self, generated_html):
        assert "No digest for" in generated_html
