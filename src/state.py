"""Manage processed video state to avoid re-processing."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_state(state_path: Path) -> dict:
    """Load the state file. Returns empty dict if file doesn't exist."""
    if not state_path.exists():
        return {}
    with open(state_path) as f:
        return json.load(f)


def save_state(state_path: Path, state: dict) -> None:
    """Save state to file."""
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    logger.info(f"State saved: {len(state)} entries")


def get_processed_ids(state: dict) -> set:
    """Extract the set of processed video IDs from state."""
    return set(state.keys())
