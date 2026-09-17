"""The hero thread's anchors: seed slugs, resolved to the loaded dataset's keys.

``data/demo-seed/hero_thread.json`` names the corridor the board follows -- one opportunity,
stakeholder, meeting, diaspora search and consular case -- by stable seed slug. Each slug is
turned into the seeded row's primary key by :func:`app.core.ids.seed_id`, the same pure
function the seeder used to mint it.

**Computed, not looked up, and that is the point.** This used to read the slug-to-key mapping
out of ``data/demo-seed/manifest.json`` at request time. That file is written by ``make seed``
and git-ignored, so it is not in the deployed image: it existed only on the container's
ephemeral filesystem, written by a seed run inside a shell. A redeploy replaced that filesystem
while Postgres kept every row, and the whole thread went to "not found" on the closing screen of
the demo. An id that is a pure function of a committed slug cannot be lost that way.

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
from functools import cache
from typing import Final

from app.core.config import seed_path
from app.core.ids import seed_id

__all__ = [
    "HERO_THREAD_FILE",
    "DiasporaFacet",
    "HeroThread",
    "ThreadSpec",
    "hero_thread",
    "thread_spec",
]

HERO_THREAD_FILE: Final[str] = "hero_thread.json"

_ANCHOR_KINDS: Final[tuple[str, ...]] = ("opportunity", "stakeholder", "meeting", "case")


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


def hero_thread() -> HeroThread:
    """Resolve the committed thread against the seeded dataset.

    Touches no filesystem beyond the committed ``hero_thread.json`` that
    :func:`thread_spec` caches, so a restart, a redeploy or a fresh container cannot
    change the answer. The fields stay optional because a caller may still be unable to
    *read* an anchored row -- that refusal is the context module's to make, in its own
    WHERE clause, and it is not this function's business.
    """
    spec = thread_spec()
    return HeroThread(
        id=spec.id,
        title=spec.title,
        opportunity_id=seed_id(spec.anchor_slugs["opportunity"]),
        stakeholder_id=seed_id(spec.anchor_slugs["stakeholder"]),
        meeting_id=seed_id(spec.anchor_slugs["meeting"]),
        case_id=seed_id(spec.anchor_slugs["case"]),
        diaspora_search_id=spec.diaspora_search_id,
        diaspora_facets=spec.diaspora_facets,
    )
