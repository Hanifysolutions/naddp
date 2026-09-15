"""The hero thread's anchors: seed slugs, resolved to the loaded dataset's keys.

``data/demo-seed/hero_thread.json`` names the corridor the board follows -- one opportunity,
stakeholder, meeting, diaspora search and consular case -- by stable seed slug.
``data/demo-seed/manifest.json`` (written by ``make seed``, git-ignored) maps those slugs to the
keys of the dataset actually loaded, which is the documented way for anything outside the seed to
name a seeded record (``data/demo-seed/README.md``).

**An anchor grants nothing.** Knowing an id is not permission to read the row behind it: each
context module checks its own permission and applies the caller's clearance in the WHERE clause
before it reads an anchored record. An anchor that does not resolve -- no manifest yet, or a slug
the dataset lacks -- becomes a step that says it was not found, never an error.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Final

from app.core.config import seed_path
from app.core.logging import get_logger

__all__ = [
    "HERO_THREAD_FILE",
    "SEED_MANIFEST_FILE",
    "DiasporaFacet",
    "HeroThread",
    "ThreadSpec",
    "hero_thread",
    "thread_spec",
]

HERO_THREAD_FILE: Final[str] = "hero_thread.json"
SEED_MANIFEST_FILE: Final[str] = "manifest.json"

_ANCHOR_KINDS: Final[tuple[str, ...]] = ("opportunity", "stakeholder", "meeting", "case")

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DiasporaFacet:
    """One capability the corridor needs, identified by its expertise tag."""

    label: str
    expertise_tag: str


@dataclass(frozen=True, slots=True)
class ThreadSpec:
    """The committed thread definition: slugs, not keys."""

    id: str
    title: str
    anchor_slugs: Mapping[str, str]
    diaspora_search_id: str
    diaspora_facets: tuple[DiasporaFacet, ...]


@dataclass(frozen=True, slots=True)
class HeroThread:
    """The thread with its anchors resolved. ``None`` where a slug does not resolve."""

    id: str
    title: str
    opportunity_id: uuid.UUID | None
    stakeholder_id: uuid.UUID | None
    meeting_id: uuid.UUID | None
    case_id: uuid.UUID | None
    diaspora_search_id: str
    diaspora_facets: tuple[DiasporaFacet, ...]


@cache
def thread_spec() -> ThreadSpec:
    """The committed thread definition. Raises at first use if the file is malformed."""
    path = seed_path(HERO_THREAD_FILE)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        anchors = document["anchors"]
        slugs = {kind: str(anchors[kind]) for kind in _ANCHOR_KINDS}
        return ThreadSpec(
            id=str(document["id"]),
            title=str(document["title"]),
            anchor_slugs=slugs,
            diaspora_search_id=str(anchors["diaspora_search"]),
            diaspora_facets=tuple(
                DiasporaFacet(label=str(item["label"]), expertise_tag=str(item["expertise_tag"]))
                for item in document["diaspora_facets"]
            ),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        msg = f"Cannot read the hero thread at {path}: {exc!r}"
        raise RuntimeError(msg) from exc


@lru_cache(maxsize=4)
def _manifest_entries(path: Path, modified_ns: int) -> Mapping[str, Mapping[str, str]]:
    """The manifest's ``entries``, re-read whenever ``make seed`` rewrites the file."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning(
            "outcomes.manifest_unreadable", path=str(path), modified_ns=modified_ns, error=str(exc)
        )
        return {}
    entries = document.get("entries") if isinstance(document, dict) else None
    return entries if isinstance(entries, dict) else {}


def _manifest() -> Mapping[str, Mapping[str, str]]:
    path = seed_path(SEED_MANIFEST_FILE)
    try:
        modified = path.stat().st_mtime_ns
    except OSError as exc:
        _logger.warning("outcomes.manifest_missing", path=str(path), error=str(exc))
        return {}
    return _manifest_entries(path, modified)


def _resolve(entries: Mapping[str, Mapping[str, str]], kind: str, slug: str) -> uuid.UUID | None:
    rows = entries.get(kind)
    raw = rows.get(slug) if isinstance(rows, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        _logger.warning("outcomes.anchor_malformed", kind=kind, slug=slug, error=str(exc))
        return None


def hero_thread() -> HeroThread:
    """Resolve the committed thread against the loaded dataset."""
    spec = thread_spec()
    entries = _manifest()
    return HeroThread(
        id=spec.id,
        title=spec.title,
        opportunity_id=_resolve(entries, "opportunity", spec.anchor_slugs["opportunity"]),
        stakeholder_id=_resolve(entries, "stakeholder", spec.anchor_slugs["stakeholder"]),
        meeting_id=_resolve(entries, "meeting", spec.anchor_slugs["meeting"]),
        case_id=_resolve(entries, "case", spec.anchor_slugs["case"]),
        diaspora_search_id=spec.diaspora_search_id,
        diaspora_facets=spec.diaspora_facets,
    )
