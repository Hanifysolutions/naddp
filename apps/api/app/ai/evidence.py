"""The citation registry, and the stage 3 retrieval authorisation filter.

Two jobs, both about the same file:

* **The registry.** ``data/demo-seed/citations.json`` is, in its own words, "the ONLY
  permitted source of external URLs in the seed". This module loads it once, indexes it by
  id, and exposes only the entries whose ``verification.status`` is ``VERIFIED`` --
  because the registry's own rules say "the seed may cite ONLY entries whose
  verification.status is VERIFIED. A TODO_VERIFY entry is research output, not demo
  content." A ``TODO_VERIFY`` id is therefore not merely unpreferred here; it does not
  exist as far as an AI answer is concerned.

* **Stage 3.** ADR-0001 stage 3 requires candidate evidence to be fetched "through the same
  authorisation filter the REST list endpoints use, applied *in the query*, not after it".
  In Week 1 there is no vector index and no documents table to query, so the candidate set
  is the citation registry itself -- but the *filter* is real: it applies the principal's
  readable classifications (``app.security.principal.readable_classifications_for``) before
  anything is selected, and the filter it applied is recorded verbatim into
  ``ai_traces.retrieval_filter`` so the claim is checkable rather than asserted. When
  pgvector retrieval lands in Week 2 the predicate moves into SQL and this module's public
  surface does not change.

**A note on why this loader fails loudly.** A missing or malformed registry means the
Gateway cannot verify a single citation. Serving answers anyway would produce exactly the
failure the citation post-check exists to prevent, so a broken registry raises at first use
rather than degrading to "nothing is verifiable, so everything passes".

No ``anthropic``, no SQLAlchemy, no network.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from app.ai.schemas import EvidenceRef
from app.core.config import CITATIONS_FILE
from app.core.logging import get_logger
from app.domain.enums import Classification
from app.security.principal import Principal, readable_classifications_for

__all__ = [
    "VERIFIED_STATUS",
    "AuthorisedEvidence",
    "CitationEntry",
    "CitationRegistryError",
    "authorise_evidence",
    "citation_registry",
    "evidence_refs_for",
    "reset_citation_registry",
    "unverified_ids",
]

_logger = get_logger(__name__)

#: The one verification status a citation may carry and still be usable.
VERIFIED_STATUS: Final[str] = "VERIFIED"


class CitationRegistryError(RuntimeError):
    """``data/demo-seed/citations.json`` is missing, unreadable or malformed."""


@dataclass(frozen=True, slots=True)
class CitationEntry:
    """One registry entry, reduced to what the Gateway actually uses."""

    id: str
    url: str
    title: str
    publisher: str
    source_type: str
    jurisdiction: str
    classification: Classification
    sector_codes: frozenset[str]
    hero_thread: bool
    status: str
    #: The claims this page actually supports, verbatim from the registry. The first is
    #: rendered as the evidence quote so a generated brief item cites the same kind of
    #: material a seeded one does -- one evidence shape, not two (OPEN_QUESTIONS Q-23).
    supports_claims: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """Whether this entry may be cited by an AI answer."""
        return self.status == VERIFIED_STATUS

    def to_evidence_ref(self) -> EvidenceRef:
        """Render as the envelope's :class:`~app.ai.schemas.EvidenceRef`."""
        return EvidenceRef(
            id=self.id,
            title=self.title,
            url=self.url,
            source=self.publisher,
            citation_id=self.id,
            quote=self.supports_claims[0] if self.supports_claims else None,
        )


@dataclass(frozen=True, slots=True)
class AuthorisedEvidence:
    """The result of stage 3: what this caller was allowed to retrieve, and under what rule.

    ``filter_description`` is written straight into ``ai_traces.retrieval_filter``. It is
    the evidence that the authorisation predicate ran *before* selection; without it,
    ``CLAUDE.md`` rule 5 is an unverifiable claim about code nobody is reading during a
    demo.
    """

    entries: tuple[CitationEntry, ...]
    filter_description: Mapping[str, Any]
    prompt_limit: int

    @property
    def ids(self) -> frozenset[str]:
        """Ids the caller is authorised to be shown.

        This is the set the stage 8 citation post-check tests against, and it is the
        **whole** authorised set -- not the bounded slice that reaches the prompt.
        Conflating the two would be a real defect: an answer citing an authorised source
        that happened to fall past the prompt budget would be refused as unauthorised, and
        the trace would say the caller was not cleared for something they are cleared for.
        Authorisation and prompt size are different questions and get different answers.
        """
        return frozenset(entry.id for entry in self.entries)

    @property
    def prompt_entries(self) -> tuple[CitationEntry, ...]:
        """The bounded slice bound into the prompt, so a prompt cannot grow without limit."""
        return self.entries[: self.prompt_limit]


_REGISTRY_LOCK: Final[threading.Lock] = threading.Lock()
_REGISTRY: dict[str, CitationEntry] | None = None


def _coerce_entry(raw: object) -> CitationEntry | None:
    """Shape one registry record, or return ``None`` if it is unusable.

    A single malformed entry is skipped and logged rather than taking the whole registry
    down: the file is 163 hand-curated records and losing one citation degrades a brief,
    while refusing to start loses the demo. A malformed *file* is a different matter and
    does raise.
    """
    if not isinstance(raw, dict):
        return None
    identifier = raw.get("id")
    url = raw.get("url")
    title = raw.get("title")
    if not isinstance(identifier, str) or not isinstance(url, str) or not isinstance(title, str):
        return None

    verification = raw.get("verification")
    status = ""
    if isinstance(verification, dict):
        candidate = verification.get("status")
        if isinstance(candidate, str):
            status = candidate

    try:
        classification = Classification(raw.get("classification", Classification.PUBLIC.value))
    except ValueError:
        _logger.warning(
            "ai.citation_registry.unknown_classification",
            citation_id=identifier,
            value=raw.get("classification"),
        )
        return None

    sectors = raw.get("sector_codes")
    sector_codes = (
        frozenset(item for item in sectors if isinstance(item, str))
        if isinstance(sectors, list)
        else frozenset()
    )

    return CitationEntry(
        id=identifier,
        url=url,
        title=title,
        publisher=str(raw.get("publisher") or ""),
        source_type=str(raw.get("source_type") or ""),
        jurisdiction=str(raw.get("jurisdiction") or ""),
        classification=classification,
        sector_codes=sector_codes,
        hero_thread=bool(raw.get("hero_thread")),
        status=status,
        supports_claims=tuple(
            claim
            for claim in (raw.get("supports_claims") or [])
            if isinstance(claim, str) and claim
        ),
    )


def _load_registry() -> dict[str, CitationEntry]:
    """Read and index ``citations.json``. Raises on a missing or malformed file."""
    try:
        raw_text = CITATIONS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        msg = (
            f"Cannot read the citation registry at {CITATIONS_FILE}. It is the only "
            "permitted source of external URLs (CLAUDE.md 2.6) and the AI Gateway cannot "
            "verify a single citation without it."
        )
        raise CitationRegistryError(msg) from exc

    try:
        document: object = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        msg = f"{CITATIONS_FILE} is not valid JSON: {exc}"
        raise CitationRegistryError(msg) from exc

    if not isinstance(document, dict):
        msg = f"{CITATIONS_FILE} must contain a JSON object at the top level."
        raise CitationRegistryError(msg)

    records = document.get("citations")
    if not isinstance(records, list) or not records:
        msg = f"{CITATIONS_FILE} has no non-empty 'citations' array."
        raise CitationRegistryError(msg)

    index: dict[str, CitationEntry] = {}
    skipped = 0
    for record in records:
        entry = _coerce_entry(record)
        if entry is None:
            skipped += 1
            continue
        index[entry.id] = entry

    if skipped:
        _logger.warning("ai.citation_registry.entries_skipped", count=skipped)
    if not index:
        msg = f"{CITATIONS_FILE} yielded no usable citation entries."
        raise CitationRegistryError(msg)

    _logger.info(
        "ai.citation_registry.loaded",
        total=len(index),
        verified=sum(1 for entry in index.values() if entry.verified),
    )
    return index


def citation_registry() -> Mapping[str, CitationEntry]:
    """Return the registry, loading it on first use.

    Cached because the file does not change while the process runs, and invalidatable
    through :func:`reset_citation_registry` because a test and ``make demo-reset`` both
    need to be able to say otherwise. Guarded by a lock: FastAPI serves requests on a
    thread pool and two concurrent first calls would otherwise each parse the file.
    """
    global _REGISTRY
    registry = _REGISTRY
    if registry is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = _load_registry()
            registry = _REGISTRY
    return MappingProxyType(registry)


def reset_citation_registry() -> None:
    """Drop the cached registry so the next call re-reads the file."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = None


def unverified_ids(ids: Iterable[str]) -> tuple[str, ...]:
    """Return the subset of ``ids`` that is unknown or not ``VERIFIED``, sorted.

    The primitive behind the stage 8 citation post-check and behind the snapshot test that
    refuses a ``TODO_VERIFY`` id. Unknown and unverified are deliberately one answer: both
    mean "this id may not appear under a claim", and distinguishing them at the call site
    invites a code path that tolerates one of them.
    """
    registry = citation_registry()
    offenders = {
        identifier
        for identifier in ids
        if identifier not in registry or not registry[identifier].verified
    }
    return tuple(sorted(offenders))


def evidence_refs_for(ids: Sequence[str]) -> list[EvidenceRef]:
    """Hydrate evidence ids into envelope refs, preserving order and dropping duplicates.

    Unknown ids are skipped: this function builds what the UI renders, and stage 8 is what
    decides whether an unknown id is fatal. Rendering "title unavailable" next to a
    fabricated id would put an unresolvable citation on screen, which is the specific thing
    winning moment #1 promises never happens.
    """
    registry = citation_registry()
    seen: set[str] = set()
    refs: list[EvidenceRef] = []
    for identifier in ids:
        if identifier in seen:
            continue
        seen.add(identifier)
        entry = registry.get(identifier)
        if entry is not None:
            refs.append(entry.to_evidence_ref())
    return refs


def authorise_evidence(
    principal: Principal,
    *,
    sector_codes: Sequence[str] = (),
    prompt_limit: int = 24,
) -> AuthorisedEvidence:
    """Stage 3. Return the evidence this principal may be shown, and the filter used.

    The predicate, in order:

    1. ``verification.status == VERIFIED`` -- the registry's own rule 2.
    2. ``classification`` is one the principal may read (ADR-0006), computed from the
       principal *before* anything is selected.
    3. When ``sector_codes`` are given, the entry must carry at least one of them.

    Ordering is deterministic -- hero-thread entries first, then by id -- so two runs of the
    same demo beat retrieve the same candidates in the same order. A random or
    insertion-ordered candidate list would make the trace drawer unreproducible and would
    defeat screenshot-based regression checking.

    Args:
        principal: The caller. Its clearance is the filter; nothing here trusts a caller-
            supplied classification.
        sector_codes: Narrowing hint from the purpose or the context.
        prompt_limit: How many of the authorised entries are bound into the prompt. It
            bounds the *prompt*, never the authorisation: see
            :attr:`AuthorisedEvidence.ids`.
    """
    readable = readable_classifications_for(principal)
    readable_set = frozenset(readable)
    wanted = frozenset(sector_codes)

    candidates = [
        entry
        for entry in citation_registry().values()
        if entry.verified
        and entry.classification in readable_set
        and (not wanted or (entry.sector_codes & wanted))
    ]
    candidates.sort(key=lambda entry: (not entry.hero_thread, entry.id))
    selected = tuple(candidates)

    description: dict[str, Any] = {
        "stage": "retrieval_authorisation",
        "source": "data/demo-seed/citations.json",
        "actor_role": principal.role.value,
        "clearance_rank": principal.clearance_rank,
        "compartments": sorted(principal.compartments),
        "classifications": [zone.value for zone in readable],
        "verified_only": True,
        "sector_codes": sorted(wanted),
        "authorised_count": len(selected),
        "prompt_limit": prompt_limit,
        "bound_into_prompt": min(prompt_limit, len(selected)),
        "applied": "before_selection",
    }
    return AuthorisedEvidence(
        entries=selected,
        filter_description=MappingProxyType(description),
        prompt_limit=prompt_limit,
    )
