"""Diaspora capability search as pure functions: who in the consented directory fits, and where.

``AiPurpose.DIASPORA_MATCH`` returns people, so two disciplines apply before any ranking rule does.

1. **Consent is the gate, inside the query.** ``app.services.diaspora.directory_scope`` puts
   ``consent_status IN ('GIVEN_DIRECTORY_ONLY', 'GIVEN_CONTACTABLE') AND NOT is_tombstoned`` (and
   the caller's zones) into the WHERE clause that loads profiles. A profile whose consent is not
   given or withdrawn is never read, so nothing below ever sees one: it cannot be ranked, counted,
   weighted into a term's rarity or returned, however well it would have fitted.
2. **Location is coarse.** A result says "Western Australia, Australia", never the city
   (:func:`coarse_location`). Languages, the narrative summary and anything that could profile a
   person beyond their consented capability are not part of a result at all.

The ranking itself is deliberately simple and explainable. A requirement's key terms (Postgres
``english`` lexemes, framing words removed) are weighted by rarity across the consented profiles
in scope. A profile qualifies with at least two of them (or all of them, for a one-word
requirement). Selection then covers the requirement's facets first -- a lithium-processing
engineer AND a migration-pathway academic for a requirement that asks for both -- and fills the
rest by strength. The result is a candidate set, never an outreach list.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from app.domain.enums import ConsentStatus

__all__ = [
    "FRAMING_TERMS",
    "MAX_CANDIDATES",
    "MIN_MATCHED_TERMS",
    "RELATIVE_FLOOR",
    "CandidateExpertise",
    "DirectoryCandidate",
    "DirectorySearch",
    "ScoredProfile",
    "coarse_location",
    "not_consulted_search",
    "required_matches",
    "select_candidates",
    "split_requirement_terms",
]

#: The most candidates one search returns.
MAX_CANDIDATES: Final[int] = 6

#: A profile qualifies with at least this many of the requirement's key terms.
MIN_MATCHED_TERMS: Final[int] = 2

#: After the facets are covered, further candidates must reach this share of the best score.
#:
#: 0.65 rather than 0.5 (W4.4): at half, a profile matching two generic terms of a six-term
#: requirement -- "migration" and "skills" from the corridor search, say -- cleared the bar and
#: sat in the hero set beside people who match the capability itself. The facets are still
#: covered first, so raising the floor cannot cost the set a capability nobody else brings; it
#: only drops the weakest of the profiles competing for the remaining places.
RELATIVE_FLOOR: Final[float] = 0.65

#: Lexemes that frame a capability request rather than say what capability is wanted. Removed
#: before scoring and listed on the trace.
FRAMING_TERMS: Final[frozenset[str]] = frozenset(
    {
        "advis",
        "can",
        "capabl",
        "carri",
        "could",
        "diaspora",
        "expert",
        "expertis",
        "find",
        "help",
        "know",
        "look",
        "need",
        "nigerian",
        "peopl",
        "person",
        "profession",
        "search",
        "someon",
        "want",
        "would",
    }
)

_COUNTRY_NAMES: Final[Mapping[str, str]] = MappingProxyType({"AU": "Australia", "NG": "Nigeria"})

#: City of residence to its state or territory. The only use of a city: it is resolved to a
#: region here and never leaves this function.
_REGION_BY_CITY: Final[Mapping[tuple[str, str], str]] = MappingProxyType(
    {
        ("AU", "perth"): "Western Australia",
        ("AU", "kwinana"): "Western Australia",
        ("AU", "kalgoorlie"): "Western Australia",
        ("AU", "port hedland"): "Western Australia",
        ("AU", "canberra"): "Australian Capital Territory",
        ("AU", "sydney"): "New South Wales",
        ("AU", "wagga wagga"): "New South Wales",
        ("AU", "melbourne"): "Victoria",
        ("AU", "brisbane"): "Queensland",
        ("AU", "adelaide"): "South Australia",
        ("AU", "darwin"): "Northern Territory",
        ("AU", "hobart"): "Tasmania",
        ("NG", "lagos"): "Lagos State",
        ("NG", "abuja"): "Federal Capital Territory",
        ("NG", "jos"): "Plateau State",
    }
)


def coarse_location(country: str, city: str | None) -> str:
    """State or territory and country, or the country alone. Never the city."""
    code = country.strip().upper()
    country_name = _COUNTRY_NAMES.get(code, code)
    region = _REGION_BY_CITY.get((code, (city or "").strip().lower()))
    return f"{region}, {country_name}" if region else country_name


def split_requirement_terms(lexemes: Iterable[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(terms scored, framing terms ignored)``."""
    terms = frozenset(lexemes)
    return terms - FRAMING_TERMS, terms & FRAMING_TERMS


def required_matches(term_count: int) -> int:
    """How many key terms a profile must carry: two, or all of them for a shorter requirement."""
    return min(MIN_MATCHED_TERMS, term_count)


@dataclass(frozen=True, slots=True)
class ScoredProfile:
    """One consented profile's fit to the requirement, before selection."""

    key: str
    matched: frozenset[str]
    score: float
    contactable: bool
    name: str


def select_candidates(
    scored: Sequence[ScoredProfile],
    term_count: int,
) -> tuple[ScoredProfile, ...]:
    """Qualify, cover the requirement's facets, then add depth; strongest first.

    Facet cover takes, in strength order, every profile that brings a key term no earlier pick
    carried, so a requirement spanning two capabilities returns someone for each. Depth then adds
    further qualifying profiles reaching :data:`RELATIVE_FLOOR` of the best score, up to
    :data:`MAX_CANDIDATES` in all. Ties break on more terms matched, then contactable first,
    then name, so the same search always returns the same list.
    """
    need = required_matches(term_count)
    if need == 0:
        return ()
    ordered = sorted(
        (entry for entry in scored if len(entry.matched) >= need),
        key=lambda entry: (
            -entry.score,
            -len(entry.matched),
            not entry.contactable,
            entry.name,
            entry.key,
        ),
    )
    if not ordered:
        return ()

    cover: list[ScoredProfile] = []
    covered: set[str] = set()
    for entry in ordered:
        if entry.matched - covered:
            cover.append(entry)
            covered |= entry.matched

    best = ordered[0].score
    depth = [
        entry for entry in ordered if entry not in cover and entry.score >= RELATIVE_FLOOR * best
    ]
    chosen = (
        (cover + depth)[:MAX_CANDIDATES] if len(cover) < MAX_CANDIDATES else cover[:MAX_CANDIDATES]
    )
    position = {entry.key: index for index, entry in enumerate(ordered)}
    return tuple(sorted(chosen, key=lambda entry: position[entry.key]))


@dataclass(frozen=True, slots=True)
class CandidateExpertise:
    """One consented capability claim, as a result shows it."""

    code: str
    label: str
    sector_code: str
    proficiency: str | None


@dataclass(frozen=True, slots=True)
class DirectoryCandidate:
    """A consented profile that fits the requirement, carrying only what a result may show."""

    profile_id: uuid.UUID
    display_name: str
    headline: str
    sector_code: str
    sector_label: str
    institution: str | None
    organisation: str | None
    coarse_location: str
    consent_status: ConsentStatus
    availability: str | None
    expertise: tuple[CandidateExpertise, ...]
    score: float
    matched_terms: tuple[str, ...]

    @property
    def profile_ref(self) -> str:
        """The opaque reference a result carries for this profile."""
        return str(self.profile_id)

    @property
    def contactable(self) -> bool:
        """Whether this person consented to be approached. Listing is not approach."""
        return self.consent_status is ConsentStatus.GIVEN_CONTACTABLE


@dataclass(frozen=True, slots=True)
class DirectorySearch:
    """Stage 3 for a capability requirement: what was searchable, and who fits."""

    consulted: bool
    requirement_terms: tuple[str, ...]
    ignored_terms: tuple[str, ...]
    searchable_count: int
    candidates: tuple[DirectoryCandidate, ...]
    #: Rarity-weighted share of the requirement the returned candidates cover between them.
    coverage: float
    filter_description: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    not_consulted_reason: str | None = None


def not_consulted_search(reason: str) -> DirectorySearch:
    """A search that never reached the directory. It returns nobody."""
    return DirectorySearch(
        consulted=False,
        requirement_terms=(),
        ignored_terms=(),
        searchable_count=0,
        candidates=(),
        coverage=0.0,
        filter_description=MappingProxyType(
            {"stage": "retrieval_authorisation", "consulted": False, "reason": reason}
        ),
        not_consulted_reason=reason,
    )
