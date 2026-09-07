"""The citation registry and the taxonomy, loaded and guarded.

Two rules are enforced here rather than trusted to the dataset modules.

**Only VERIFIED citations may be referenced.** ``data/demo-seed/citations.json`` holds 163
entries; 138 carry ``verification.status == "VERIFIED"``, 23 are ``TODO_VERIFY`` (an
Akamai-fronted origin the build network cannot reach) and 2 are ``DO_NOT_CITE``. A signal
resting on either of the latter is a demo-breaking bug -- winning moment #1 is the
Ambassador clicking a citation and a real page opening -- so :func:`verified` raises on
anything else, at seed time, by name.

**Only taxonomy codes that exist may be used.** ``sectors``, ``expertise_tags`` and
``consular_case_types`` are plain codes on the domain rows rather than foreign keys, so
nothing in the database catches a typo. :func:`require_sector` and friends do.

``seed.py`` contains no URL literal and constructs none (``data/demo-seed/README.md``
section 1.1): every URL in the dataset is read from the registry through this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

from app.core.config import CITATIONS_FILE, TAXONOMY_DIR
from app.domain.enums import Classification, Jurisdiction, SourceType

__all__ = [
    "VERIFIED",
    "Citation",
    "case_type",
    "case_type_codes",
    "citations",
    "expertise_tag_codes",
    "expertise_tags",
    "require_expertise",
    "require_sector",
    "sector_codes",
    "sectors",
    "verified",
    "verified_citations",
]

VERIFIED: Final[str] = "VERIFIED"


@dataclass(frozen=True, slots=True)
class Citation:
    """One entry of ``citations.json``, in the shape the seed needs.

    A narrow projection on purpose. ``app.ai.evidence.CitationEntry`` models the same file
    for the Gateway; duplicating a *reader* is cheap, and coupling the seed to the AI
    track's dataclass so that a field it adds becomes a seed change is not.
    """

    id: str
    url: str
    publisher: str
    title: str
    source_type: SourceType
    jurisdiction: Jurisdiction
    classification: Classification
    sector_codes: tuple[str, ...]
    hero_thread: bool
    is_pdf: bool
    published_date: str | None
    summary: str
    supports_claims: tuple[str, ...]
    snippet: str | None
    status: str

    @property
    def is_verified(self) -> bool:
        """Whether a human opened this URL and confirmed it says what we claim."""
        return self.status == VERIFIED

    @property
    def source_code(self) -> str:
        """Stable slug for the ``sources`` row this citation's publisher becomes."""
        return _publisher_slug(self.publisher)

    @property
    def base_url(self) -> str:
        """Scheme and host of :attr:`url` -- the publisher origin, never a guess."""
        scheme, _, rest = self.url.partition("://")
        host = rest.partition("/")[0]
        return f"{scheme}://{host}"

    def claim(self, index: int) -> str:
        """Return the ``index``-th claim this page supports.

        Raises:
            IndexError: with the citation id named, because a signal whose body asserts a
                claim the page does not carry is attribution laundering -- the failure
                mode ``evals/grounding/README.md`` exists to catch.
        """
        try:
            return self.supports_claims[index]
        except IndexError as exc:
            msg = (
                f"citation {self.id!r} supports {len(self.supports_claims)} claims; "
                f"claim {index} was requested. Cite a claim the page actually makes."
            )
            raise IndexError(msg) from exc


def _publisher_slug(publisher: str) -> str:
    """Slugify a publisher name into a stable, bounded ``sources.code``.

    Bounded at 88 characters (the column is ``String(96)``) with a short digest suffix, so
    two publishers whose names agree for the first 88 characters -- the several
    ``Australian High Commission, Nigeria (DFAT)`` variants come close -- cannot collide
    into one source row and silently merge their documents.
    """
    lowered = "".join(character if character.isalnum() else "-" for character in publisher.lower())
    collapsed = "-".join(part for part in lowered.split("-") if part)
    if len(collapsed) <= 88:
        return collapsed
    digest = hashlib.blake2b(publisher.encode(), digest_size=3).hexdigest()
    return f"{collapsed[:81]}-{digest}"


def _read_json(path: Path) -> dict[str, Any]:
    import json

    document: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"{path} must contain a JSON object at the top level."
        raise RuntimeError(msg)
    return document


@cache
def citations() -> Mapping[str, Citation]:
    """Every entry of the registry, verified or not, keyed by id."""
    document = _read_json(CITATIONS_FILE)
    records = document.get("citations")
    if not isinstance(records, list) or not records:
        msg = f"{CITATIONS_FILE} has no non-empty 'citations' array."
        raise RuntimeError(msg)

    index: dict[str, Citation] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        verification = record.get("verification")
        status = verification.get("status", "") if isinstance(verification, dict) else ""
        claims = record.get("supports_claims") or []
        sectors_raw = record.get("sector_codes") or []
        index[str(record["id"])] = Citation(
            id=str(record["id"]),
            url=str(record["url"]),
            publisher=str(record.get("publisher") or ""),
            title=str(record.get("title") or ""),
            source_type=SourceType(str(record.get("source_type") or "other")),
            jurisdiction=Jurisdiction(str(record.get("jurisdiction") or "INTL")),
            classification=Classification(str(record.get("classification") or "PUBLIC")),
            sector_codes=tuple(str(item) for item in sectors_raw),
            hero_thread=bool(record.get("hero_thread")),
            is_pdf=bool(record.get("is_pdf")),
            published_date=(
                str(record["published_date"]) if record.get("published_date") is not None else None
            ),
            summary=str(record.get("summary") or ""),
            supports_claims=tuple(str(item) for item in claims),
            snippet=(str(record["snippet"]) if record.get("snippet") is not None else None),
            status=str(status),
        )
    return index


@cache
def verified_citations() -> tuple[Citation, ...]:
    """Every VERIFIED entry, in registry order. The only material the seed may cite."""
    return tuple(entry for entry in citations().values() if entry.is_verified)


def verified(citation_id: str) -> Citation:
    """Return the VERIFIED citation ``citation_id``, or raise.

    Raises:
        KeyError: when the id is unknown, or when it is present but ``TODO_VERIFY`` /
            ``DO_NOT_CITE``. Both are seed bugs, and both must stop ``make seed`` rather
            than reach a screen.
    """
    entry = citations().get(citation_id)
    if entry is None:
        msg = f"unknown citation id {citation_id!r}: it is not in {CITATIONS_FILE.name}."
        raise KeyError(msg)
    if not entry.is_verified:
        msg = (
            f"citation {citation_id!r} is {entry.status!r}, not VERIFIED. "
            "Only VERIFIED entries may be seeded: an unverified citation on stage is a "
            "demo failure (CLAUDE.md 2.6)."
        )
        raise KeyError(msg)
    return entry


@cache
def sectors() -> Mapping[str, Mapping[str, Any]]:
    """The sector taxonomy, keyed by code."""
    document = _read_json(TAXONOMY_DIR / "sectors.json")
    return {str(entry["code"]): entry for entry in document["sectors"]}


@cache
def expertise_tags() -> tuple[Mapping[str, Any], ...]:
    """The diaspora expertise taxonomy, in file order."""
    document = _read_json(TAXONOMY_DIR / "expertise_tags.json")
    return tuple(document["expertise_tags"])


@cache
def case_types() -> Mapping[str, Mapping[str, Any]]:
    """The consular case-type taxonomy, keyed by code."""
    document = _read_json(TAXONOMY_DIR / "consular_case_types.json")
    return {str(entry["code"]): entry for entry in document["case_types"]}


def sector_codes() -> tuple[str, ...]:
    """Every sector code, top level and sub-sector."""
    return tuple(sectors())


def expertise_tag_codes() -> tuple[str, ...]:
    """Every expertise-tag code."""
    return tuple(str(tag["code"]) for tag in expertise_tags())


def case_type_codes() -> tuple[str, ...]:
    """Every consular case-type code."""
    return tuple(case_types())


def case_type(code: str) -> Mapping[str, Any]:
    """Return one case type, or raise naming the code."""
    try:
        return case_types()[code]
    except KeyError as exc:
        msg = f"unknown consular case type {code!r} (data/taxonomy/consular_case_types.json)."
        raise KeyError(msg) from exc


def require_sector(*codes: str) -> list[str]:
    """Return ``codes`` unchanged, having checked every one exists in the taxonomy.

    Sector codes are plain strings on ``signals``, ``opportunities`` and ``organisations``
    -- the taxonomy is versioned seed data shared with the web client, not a table -- so
    nothing in the database rejects a typo. This does.
    """
    known = sectors()
    unknown = [code for code in codes if code not in known]
    if unknown:
        msg = f"unknown sector code(s) {unknown!r} (data/taxonomy/sectors.json)."
        raise KeyError(msg)
    return list(codes)


def require_expertise(*codes: str) -> list[str]:
    """Return ``codes`` unchanged, having checked every one exists in the taxonomy."""
    known = set(expertise_tag_codes())
    unknown = [code for code in codes if code not in known]
    if unknown:
        msg = f"unknown expertise tag(s) {unknown!r} (data/taxonomy/expertise_tags.json)."
        raise KeyError(msg)
    return list(codes)
