"""The sector taxonomy, read once from ``data/taxonomy/sectors.json``.

Sectors are versioned demo data rather than an enum, so the diaspora search and its responses
read labels through this module instead of carrying a copy. The seed loader keeps its own reader
in ``data/demo-seed/seed_parts/registry.py``; both read the one file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from types import MappingProxyType
from typing import Final

from app.core.config import taxonomy_path

__all__ = ["SECTORS_FILE", "sector_label", "sector_labels"]

SECTORS_FILE: Final[str] = "sectors.json"


@cache
def sector_labels() -> Mapping[str, str]:
    """Every sector label, keyed by code. Raises at first use if the file is unreadable."""
    path = taxonomy_path(SECTORS_FILE)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"Cannot read the sector taxonomy at {path}: {exc}"
        raise RuntimeError(msg) from exc
    entries = document.get("sectors") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        msg = f"{path} has no 'sectors' array."
        raise RuntimeError(msg)
    try:
        return MappingProxyType({str(entry["code"]): str(entry["label"]) for entry in entries})
    except (KeyError, TypeError) as exc:
        msg = f"{path}: malformed sector entry: {exc}"
        raise RuntimeError(msg) from exc


def sector_label(code: str) -> str:
    """The label for ``code``, or the code itself when the taxonomy does not know it."""
    return sector_labels().get(code, code)
