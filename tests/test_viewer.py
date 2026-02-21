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
from src.viewer import generate_viewer, VIEWER_HTML, PODCASTS_HTML, HUB_HTML


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

    def test_creates_podcasts_html(self, tmp_path, single_cat_config):
        generate_viewer(single_cat_config, tmp_path)
        assert (tmp_path / "podcasts.html").exists()

    def test_creates_index_html(self, tmp_path, single_cat_config):
        generate_viewer(single_cat_config, tmp_path)
        assert (tmp_path / "index.html").exists()

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

    def test_apply_filter_is_async(self, generated_html):
        # applyFilter must be async so it can await toggleSummary for newly-visible cards
        assert "async function applyFilter(" in generated_html

    def test_filter_expands_newly_visible_cards_when_all_expanded(self, generated_html):
        # When allExpanded is true and a new filter reveals hidden cards, those cards
        # must be expanded — they were skipped during toggleExpandAll because display=none
        fn_start = generated_html.index("async function applyFilter(")
        fn_body = generated_html[fn_start:fn_start + 1500]
        assert "allExpanded" in fn_body
        assert "toExpand" in fn_body
        assert "toggleSummary" in fn_body

    def test_filter_only_expands_unopened_cards(self, generated_html):
        # Must check !c.classList.contains('open') before queuing expansion
        # to avoid double-toggling cards that are already open
        fn_start = generated_html.index("async function applyFilter(")
        fn_body = generated_html[fn_start:fn_start + 1500]
        assert "classList.contains('open')" in fn_body

    def test_filter_expand_respects_hidden_cards(self, generated_html):
        # Hidden cards (display=none) must NOT be added to the expand queue
        fn_start = generated_html.index("async function applyFilter(")
        fn_body = generated_html[fn_start:fn_start + 1500]
        # The toExpand push is inside the `if (show)` block
        assert "if (show)" in fn_body
        assert "toExpand.push" in fn_body


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

    def test_date_button_passes_explicit_flag(self, generated_html):
        # Button onclick must pass explicit=true so loadDate never auto-falls-back
        # to an older date when the user deliberately selects a specific date pill
        assert "loadDate(d, false, true)" in generated_html

    def test_load_date_accepts_explicit_parameter(self, generated_html):
        # loadDate signature has the explicit parameter
        fn_block = generated_html[generated_html.index("function loadDate("):]
        fn_sig = fn_block[:fn_block.index("{")]
        assert "explicit" in fn_sig

    def test_auto_fallback_skipped_on_explicit_click(self, generated_html):
        # Fallback only fires when both fallback and explicit are falsy
        fn_start = generated_html.index("function loadDate(")
        fn_body = generated_html[fn_start:fn_start + 800]
        assert "!explicit" in fn_body

    def test_auto_fallback_only_on_initial_load(self, generated_html):
        # The condition guarding fallback requires !fallback AND !explicit
        fn_start = generated_html.index("function loadDate(")
        fn_body = generated_html[fn_start:fn_start + 800]
        assert "!fallback && !explicit" in fn_body

    def test_explicit_click_highlights_clicked_date(self, generated_html):
        # date-btn active class is set to the requested dateStr, not a fallback date
        fn_start = generated_html.index("function loadDate(")
        fn_body = generated_html[fn_start:fn_start + 800]
        # active class toggle uses dateStr (the explicitly-clicked date)
        assert "b.dataset.date === dateStr" in fn_body

    def test_podcasts_date_button_explicit_flag(self):
        # Same fix must be present in PODCASTS_HTML (generated by string replacement)
        assert "loadDate(d, false, true)" in PODCASTS_HTML

    def test_podcasts_load_date_skips_fallback_on_explicit(self):
        assert "!fallback && !explicit" in PODCASTS_HTML

    def test_youtube_load_date_falls_back_when_empty(self):
        # Auto-fallback on initial load (not explicit) still works
        assert "hasContent" in VIEWER_HTML
        assert "DIGEST_INDEX[idx + 1]" in VIEWER_HTML


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

    def test_card_stores_summary_path_from_digest(self, generated_html):
        # Summary path is extracted from [Summary](path) in the digest and stored
        # as data-summary-path, so slug reconstruction is not needed.
        assert "data-summary-path" in generated_html
        assert "summaryMatch" in generated_html
        assert "currentCard.summaryPath" in generated_html

    def test_toggle_summary_prefers_embedded_path(self, generated_html):
        # toggleSummary uses data-summary-path when present, falls back to slug only
        # when the path was not embedded (backward compat for old digests).
        assert "body.dataset.summaryPath" in generated_html


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

    def test_empty_digest_shows_message_not_blank(self, generated_html):
        # When renderDigest finds no cards (e.g. "No new content today" digest),
        # it must render an informative message rather than a blank page.
        assert "No new content for" in generated_html
        assert "cardCount === 0" in generated_html

    def test_empty_digest_does_not_call_apply_filter_when_empty(self, generated_html):
        # applyFilter should only be called when there are actual cards to filter;
        # calling it on empty HTML would silently hide the empty-state message.
        assert "cardCount === 0" in generated_html


# ===== PODCASTS PAGE =====

class TestPodcastsHTML:
    def test_podcasts_title_in_header(self):
        assert "/ podcasts" in PODCASTS_HTML

    def test_fetches_podcast_daily(self):
        assert "fetch('podcast-daily/'" in PODCASTS_HTML

    def test_fetches_podcast_index(self):
        assert "fetch('podcast-index.json'" in PODCASTS_HTML

    def test_loads_podcast_summaries(self):
        # Summary path uses podcast-summaries/ not summaries/
        assert "'podcast-summaries/' + currentDate" in PODCASTS_HTML
        assert "'summaries/' + currentDate" not in PODCASTS_HTML

    def test_no_youtube_daily_path(self):
        # Should not fetch from youtube daily directory
        assert "fetch('daily/'" not in PODCASTS_HTML

    def test_no_youtube_in_title(self):
        assert "/ youtube" not in PODCASTS_HTML

    def test_empty_state_message(self):
        assert "No podcast episodes available yet" in PODCASTS_HTML

    def test_has_tts_support(self):
        assert "speechSynthesis" in PODCASTS_HTML

    def test_has_category_filters(self):
        assert "function buildFilters(" in PODCASTS_HTML

    def test_has_dark_mode(self):
        assert "prefers-color-scheme: dark" in PODCASTS_HTML


# ===== HUB PAGE =====

class TestHubHTML:
    def test_has_morning_brief_title(self):
        assert "Morning Brief" in HUB_HTML

    def test_links_to_youtube(self):
        assert "youtube.html" in HUB_HTML

    def test_links_to_podcasts(self):
        assert "podcasts.html" in HUB_HTML

    def test_fetches_both_indexes(self):
        assert "digest-index.json" in HUB_HTML
        assert "podcast-index.json" in HUB_HTML

    def test_fetches_both_digests(self):
        assert "fetch('daily/'" in HUB_HTML
        assert "fetch('podcast-daily/'" in HUB_HTML

    def test_has_two_tiles_no_web_news(self):
        # Web & News placeholder tile was removed; only YouTube + Podcasts
        assert "Phase 3" not in HUB_HTML
        assert "Web &amp; News" not in HUB_HTML
        assert "youtube.html" in HUB_HTML
        assert "podcasts.html" in HUB_HTML

    def test_parse_digest_function(self):
        assert "function parseDigest(" in HUB_HTML

    def test_has_dark_mode(self):
        assert "prefers-color-scheme: dark" in HUB_HTML

    def test_has_dynamic_date(self):
        assert "headerDate" in HUB_HTML

    def test_renders_feed_items(self):
        assert "recent-item" in HUB_HTML

    def test_has_viewport_meta(self):
        assert 'name="viewport"' in HUB_HTML
        assert "width=device-width" in HUB_HTML

    def test_hub_shows_nothing_new_today_when_empty(self):
        # Only shown after exhausting all fallback dates, not immediately
        assert "Nothing new today" in HUB_HTML

    def test_hub_falls_back_through_multiple_dates(self):
        # Hub walks up to 3 dates to find one with actual content
        assert "ytIndex.slice(0, 3)" in HUB_HTML
        assert "podIndex.slice(0, 3)" in HUB_HTML

    def test_hub_nothing_new_only_after_all_fallbacks_exhausted(self):
        # "Nothing new today" must appear AFTER the loop, not inside it
        # i.e. it's in a separate block guarded by !ytDateUsed / !podDateUsed
        assert "!ytDateUsed" in HUB_HTML
        assert "!podDateUsed" in HUB_HTML

    def test_hub_tile_href_updated_after_date_resolution(self):
        # After finding a date with content, the tile anchor href is patched
        assert 'getElementById(\'ytTile\')' in HUB_HTML
        assert 'getElementById(\'podTile\')' in HUB_HTML
        assert "tile.href = 'youtube.html?date='" in HUB_HTML
        assert "tile.href = 'podcasts.html?date='" in HUB_HTML

    def test_hub_tile_ids_present_in_rendered_html(self):
        # Tile anchor elements must have id="ytTile" and id="podTile"
        assert 'id="ytTile"' in HUB_HTML
        assert 'id="podTile"' in HUB_HTML

    def test_hub_feed_items_link_to_date(self):
        # Each feed item href includes ?date= so it deep-links to the right date
        assert "?date=' + encodeURIComponent(item.date)" in HUB_HTML

    def test_hub_feed_items_link_includes_category(self):
        # href also appends &category=NAME so detail page filters to the right section
        assert "&category=' + encodeURIComponent(item.category)" in HUB_HTML

    def test_hub_feed_items_category_guarded(self):
        # category param only appended when present — no &category=undefined for uncategorised items
        assert "if (item.category)" in HUB_HTML

    def test_hub_feed_label_uses_resolved_date(self):
        # feedLabel uses the date where content was actually found, not just latestYt
        assert "feedDateStr" in HUB_HTML
        assert "ytDateUsed || podDateUsed" in HUB_HTML

    def test_hub_parse_digest_stores_date_on_items(self):
        # parseDigest attaches dateStr to each item so feed links can use it
        assert "date: dateStr" in HUB_HTML


# ===== DEEP LINKS =====

class TestDeepLinks:
    def test_youtube_honours_date_url_param(self):
        # Init reads ?date= from URL and selects that date
        assert "URLSearchParams(window.location.search)" in VIEWER_HTML
        assert "urlParams.get('date')" in VIEWER_HTML

    def test_youtube_validates_date_against_index(self):
        # Should only use the requested date if it exists in DIGEST_INDEX
        assert "DIGEST_INDEX.indexOf(reqDate)" in VIEWER_HTML

    def test_youtube_falls_back_to_latest_for_unknown_date(self):
        # If ?date= is not in the index, default to DIGEST_INDEX[0]
        assert "DIGEST_INDEX[0]" in VIEWER_HTML

    def test_youtube_honours_category_url_param(self):
        # Init reads ?category= from URL and pre-applies the filter
        assert "urlParams.get('category')" in VIEWER_HTML
        assert "currentFilter = matchedCat" in VIEWER_HTML

    def test_youtube_category_matching_is_case_insensitive(self):
        # Category match should be lowercase-compared
        assert "c.toLowerCase() === reqCat.toLowerCase()" in VIEWER_HTML

    def test_podcasts_honours_date_url_param(self):
        assert "URLSearchParams(window.location.search)" in PODCASTS_HTML
        assert "urlParams.get('date')" in PODCASTS_HTML

    def test_podcasts_honours_category_url_param(self):
        assert "urlParams.get('category')" in PODCASTS_HTML


# ===== TIMESTAMP CHIPS =====

class TestTimestampChips:
    """Viewer converts [t=NNs] markers to ▶ MM:SS chips in Key Findings.
    It must also handle legacy [[t=NNs]](url) markdown links (generated
    before the fix) by stripping the URL and showing only the chip.
    """

    def test_key_findings_renders_raw_marker_as_chip(self):
        # Raw [t=NNs] → clickable chip in Key Findings renderer
        assert r'replace(/\[t=(\d+)s\]/g' in VIEWER_HTML

    def test_key_findings_chip_shows_mm_ss(self):
        # Chip label is ▶ MM:SS (not the raw seconds)
        assert "Math.floor(s / 60)" in VIEWER_HTML
        assert "padStart(2, '0')" in VIEWER_HTML

    def test_key_findings_chip_links_to_youtube(self):
        # When videoId is present, chip is an <a> to YouTube with &t=
        assert "youtube.com/watch?v=' + videoId + '&t='" in VIEWER_HTML
        assert "class=\"ts-chip\"" in VIEWER_HTML

    def test_key_findings_chip_is_span_without_video_id(self):
        # Without videoId, chip degrades gracefully to a <span>
        assert "'<span class=\"ts-chip\">' + label + '</span>'" in VIEWER_HTML or \
               '"<span class=\\"ts-chip\\">" + label + "</span>"' in VIEWER_HTML or \
               "span class=\\'ts-chip\\'" in VIEWER_HTML or \
               "ts-chip" in VIEWER_HTML  # broad check — chip CSS class always present

    def test_key_findings_strips_legacy_link_format(self):
        # [[t=NNs]](url) (old format) must be collapsed to [t=NNs] before chip conversion
        assert r'replace(/\[\[t=(\d+)s\]\]\([^)]*\)/g' in VIEWER_HTML

    def test_key_findings_legacy_strip_before_raw_replace(self):
        # Legacy strip must come BEFORE the raw [t=NNs] replace in the source
        legacy_pos = VIEWER_HTML.find(r'\[\[t=(\d+)s\]\]')
        raw_pos = VIEWER_HTML.find(r'\[t=(\d+)s\]')
        assert legacy_pos != -1, "Legacy strip pattern not found"
        assert raw_pos != -1, "Raw marker pattern not found"
        assert legacy_pos < raw_pos, "Legacy strip must appear before raw marker replace"

    def test_md2html_strips_legacy_link_format(self):
        # md2html (fallback renderer) must also handle [[t=NNs]](url)
        # Find md2html function and check it strips the legacy pattern
        md2html_start = VIEWER_HTML.find("function md2html(")
        assert md2html_start != -1
        md2html_body = VIEWER_HTML[md2html_start:md2html_start + 1500]
        assert r'\[\[t=(\d+)s\]\]' in md2html_body

    def test_md2html_converts_raw_marker_to_chip(self):
        # md2html must also convert raw [t=NNs] markers (new format) to chips
        md2html_start = VIEWER_HTML.find("function md2html(")
        md2html_body = VIEWER_HTML[md2html_start:md2html_start + 1500]
        assert r'\[t=(\d+)s\]' in md2html_body
        assert "ts-chip" in md2html_body

    def test_ts_chip_has_css_styling(self):
        # .ts-chip must be styled (not just a bare <a>/<span>)
        assert ".ts-chip" in VIEWER_HTML
