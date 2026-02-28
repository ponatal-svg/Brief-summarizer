"""Tests to verify that podcast summary .md files follow the required format.

Required structure for every podcast summary:
  ## The Hook       — exactly this heading at ## level (no colon, no custom title)
  ## Key Findings   — exactly this heading at ## level (no colon)
  ## The So What?   — exactly this heading at ## level

These tests serve as a regression guard:
  - Unit tests verify the format-checker logic itself.
  - Output validation tests scan the real output/podcast-summaries/ tree and
    fail if any file has non-conforming headers (like Gemini sometimes generates).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers shared between unit and integration tests
# ---------------------------------------------------------------------------

_REQUIRED_SECTIONS = [
    ("The Hook",        re.compile(r"^## The Hook\s*$",         re.MULTILINE)),
    ("Key Findings",    re.compile(r"^## Key Findings\s*$",     re.MULTILINE)),
    ("The So What?",    re.compile(r"^## The So What\?\s*$",    re.MULTILINE)),
]

_BAD_PATTERNS = [
    # Wrong heading level (###)
    ("triple-hash heading", re.compile(r"^###\s+(The Hook|Key Findings|The So What)", re.MULTILINE)),
    # Trailing colon on any required section header
    ("trailing colon", re.compile(r"^##\s+(The Hook|Key Findings|The So What\??)\s*:", re.MULTILINE)),
    # Custom hook header: ## followed by anything that isn't one of the required names
    ("custom hook header", re.compile(
        r"^## (?!The Hook|Key Findings|The So What|$).*$", re.MULTILINE
    )),
]


def _check_summary_format(content: str, filename: str = "<unnamed>") -> list[str]:
    """Return a list of format errors for a podcast summary file.

    An empty list means the file is well-formed.
    """
    errors: list[str] = []

    # Must have all required sections
    for name, pattern in _REQUIRED_SECTIONS:
        if not pattern.search(content):
            errors.append(f"Missing required section '## {name}'")

    # Must NOT have known bad patterns
    for label, pattern in _BAD_PATTERNS:
        m = pattern.search(content)
        if m:
            errors.append(f"Bad format ({label}): {m.group(0)!r}")

    return errors


# ---------------------------------------------------------------------------
# Unit tests — format-checker logic
# ---------------------------------------------------------------------------

class TestFormatChecker:
    """Unit tests for _check_summary_format."""

    def _well_formed(self) -> str:
        return (
            "# Episode Title\n\n"
            "**Show:** My Show | **Category:** AI\n\n"
            "---\n\n"
            "## The Hook\n\n"
            "Great intro.\n\n"
            "## Key Findings\n\n"
            "* Bullet one\n"
            "* Bullet two\n\n"
            "## The So What?\n\n"
            "Final thought.\n"
        )

    def test_well_formed_has_no_errors(self):
        assert _check_summary_format(self._well_formed()) == []

    def test_missing_hook(self):
        content = self._well_formed().replace("## The Hook", "## The Big Reveal")
        errors = _check_summary_format(content)
        assert any("Missing required section '## The Hook'" in e for e in errors)

    def test_missing_key_findings(self):
        content = self._well_formed().replace("## Key Findings", "## The Details")
        errors = _check_summary_format(content)
        assert any("Key Findings" in e for e in errors)

    def test_missing_so_what(self):
        content = self._well_formed().replace("## The So What?", "## Conclusion")
        errors = _check_summary_format(content)
        assert any("The So What?" in e for e in errors)

    def test_triple_hash_hook(self):
        content = self._well_formed().replace("## The Hook", "### The Hook")
        errors = _check_summary_format(content)
        # Missing ## The Hook AND bad triple-hash
        assert len(errors) >= 1
        assert any("triple-hash" in e or "Missing" in e for e in errors)

    def test_trailing_colon_on_findings(self):
        content = self._well_formed().replace("## Key Findings", "## Key Findings:")
        errors = _check_summary_format(content)
        assert any("trailing colon" in e for e in errors)

    def test_custom_hook_header_detected(self):
        """Gemini sometimes generates '## The SaaSocalypse Shakes Silicon Valley' instead of '## The Hook'."""
        content = self._well_formed().replace("## The Hook", "## The SaaSocalypse Shakes Silicon Valley")
        errors = _check_summary_format(content)
        assert any("custom hook header" in e or "Missing" in e for e in errors)

    def test_all_three_wrong_level(self):
        content = (
            self._well_formed()
            .replace("## The Hook", "### The Hook")
            .replace("## Key Findings", "### Key Findings:")
            .replace("## The So What?", "### The So What?")
        )
        errors = _check_summary_format(content)
        assert len(errors) >= 3  # missing all three + bad patterns


# ---------------------------------------------------------------------------
# Output validation — scan actual podcast summary files
# ---------------------------------------------------------------------------

PODCAST_SUMMARIES_DIR = Path(__file__).parent.parent / "output" / "podcast-summaries"


def _iter_summary_files():
    """Yield all .md files under output/podcast-summaries/."""
    if not PODCAST_SUMMARIES_DIR.exists():
        return
    yield from PODCAST_SUMMARIES_DIR.rglob("*.md")


# Build the parametrize list at collection time (empty if dir doesn't exist)
_summary_files = list(_iter_summary_files())


@pytest.mark.skipif(
    not PODCAST_SUMMARIES_DIR.exists(),
    reason="output/podcast-summaries/ not present (run pipeline first)",
)
class TestOutputSummaryFormat:
    """Validate that every generated podcast summary file conforms to the required format.

    Run after the pipeline to catch Gemini-generated format variations early.
    """

    @pytest.mark.parametrize(
        "summary_path",
        _summary_files,
        ids=[p.name for p in _summary_files],
    )
    def test_summary_has_correct_structure(self, summary_path: Path):
        content = summary_path.read_text(encoding="utf-8")
        errors = _check_summary_format(content, summary_path.name)
        assert errors == [], (
            f"{summary_path.relative_to(PODCAST_SUMMARIES_DIR)} has format errors:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
