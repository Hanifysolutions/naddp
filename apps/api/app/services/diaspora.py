"""Diaspora bounded context: the consented directory, searched for capability. Consent is the gate.

Two reads, both built on :func:`directory_scope`:

* :func:`search_directory` -- stage 3 of ``AiPurpose.DIASPORA_MATCH``, called by the Gateway
  (ADR-0001: this module never imports ``app.ai``). It loads consented profiles only, then applies
  the explainable selection in ``app.domain.diaspora``.
* :func:`diaspora_overview` -- how much of the directory this caller may search, by consent, and
  the demo searches for their role.

**The consent filter is inside the query, never after it** (``app/models/diaspora.py``). Every
statement here that reads ``diaspora_profiles`` carries ``consent_status IN
('GIVEN_DIRECTORY_ONLY', 'GIVEN_CONTACTABLE') AND is_tombstoned IS FALSE`` and the caller's zones
in its WHERE clause, and the expertise rows are loaded only for the profiles that query returned.
A profile whose consent is not given or withdrawn is therefore never read into the session: it
cannot be ranked, cannot shift a term's rarity weight, cannot be counted as a near miss and
cannot be returned, however well it would fit. ``tests/test_diaspora_search.py`` asserts that no
such row ever reaches the session.

**Candidates only.** Nothing in this module, or anywhere in the API, contacts a person. A result
states each candidate's consent -- directory listing only, or contactable -- and the mission
approaches people through its own process.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import seed_path
from app.core.sectors import sector_label
from app.domain.diaspora import (
    MAX_CANDIDATES,
    RELATIVE_FLOOR,
    CandidateExpertise,
    DirectoryCandidate,
    DirectorySearch,
    ScoredProfile,
    coarse_location,
    required_matches,
    select_candidates,
    split_requirement_terms,
)
from app.domain.enums import ConsentStatus, RoleCode
from app.domain.grounding import term_weights
from app.models.diaspora import CONSENT_SEARCHABLE, DiasporaExpertise, DiasporaProfile, ExpertiseTag
from app.security.deps import readable_classifications
from app.security.principal import Principal
from app.services.lexemes import english_lexemes

__all__ = [
    "DIASPORA_OBJECT_TYPE",
    "DIASPORA_SEARCHES_FILE",
    "DiasporaOverview",
    "SuggestedSearch",
    "diaspora_overview",
    "directory_scope",
    "search_directory",
    "suggested_searches",
]

#: ``audit_events.object_type`` for a diaspora profile.
DIASPORA_OBJECT_TYPE: Final[str] = "diaspora.profile"

#: The demo searches, in ``data/demo-seed``.
DIASPORA_SEARCHES_FILE: Final[str] = "diaspora_searches.json"

_EXPECTATIONS: Final[frozenset[str]] = frozenset({"CANDIDATES", "NO_MATCH"})

#: The taxonomy file's own authoring notes ("HERO TAG - ...") are not capability text.
_AUTHORING_NOTE: Final[re.Pattern[str]] = re.compile(r"\s*HERO TAG\b.*$", re.DOTALL)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def directory_scope(principal: Principal) -> ColumnElement[bool]:
    """The consent gate and the clearance gate, as one predicate for the WHERE clause.

    Consent first, because it is the gate no clearance opens; the zone check is the second gate
    (ADR-0006). Composed into every statement that reads profiles, before any ranking.
    """
    return and_(
        DiasporaProfile.consent_status.in_(CONSENT_SEARCHABLE),
        DiasporaProfile.is_tombstoned.is_(False),
        DiasporaProfile.classification.in_(readable_classifications(principal)),
    )


def _in_sectors(codes: frozenset[str]) -> ColumnElement[bool]:
    """A profile whose primary sector, or one of whose expertise tags, is in ``codes``."""
    ordered = sorted(codes)
    tagged = exists(
        select(DiasporaExpertise.diaspora_profile_id)
        .join(ExpertiseTag, ExpertiseTag.id == DiasporaExpertise.expertise_tag_id)
        .where(
            DiasporaExpertise.diaspora_profile_id == DiasporaProfile.id,
            ExpertiseTag.sector_code.in_(ordered),
        )
    )
    return or_(DiasporaProfile.sector_code.in_(ordered), tagged)


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


def _profile_text(profile: DiasporaProfile) -> str:
    """What a requirement is matched against: headline, sector and consented capability claims."""
    parts = [profile.headline, sector_label(profile.sector_code)]
    for link in profile.expertise:
        tag = link.expertise_tag
        parts.extend((tag.label, _AUTHORING_NOTE.sub("", tag.description)))
    return "\n".join(part for part in parts if part)


def _expertise(profile: DiasporaProfile) -> tuple[CandidateExpertise, ...]:
    links = sorted(
        profile.expertise,
        key=lambda link: (link.proficiency != "PRIMARY", link.expertise_tag.code),
    )
    return tuple(
        CandidateExpertise(
            code=link.expertise_tag.code,
            label=link.expertise_tag.label,
            sector_code=link.expertise_tag.sector_code,
            proficiency=link.proficiency,
        )
        for link in links
    )


def search_directory(
    session: Session,
    principal: Principal,
    requirement: str,
    *,
    sector_codes: frozenset[str] = frozenset(),
) -> DirectorySearch:
    """Load consented profiles only, then select the candidates that fit. Possibly nobody."""
    scope = directory_scope(principal)
    if sector_codes:
        scope = and_(scope, _in_sectors(sector_codes))
    profiles = list(
        session.scalars(
            select(DiasporaProfile)
            .where(scope)
            .options(
                selectinload(DiasporaProfile.expertise).selectinload(
                    DiasporaExpertise.expertise_tag
                )
            )
            .order_by(DiasporaProfile.id)
        )
    )

    requirement_lexemes = english_lexemes(session, [requirement])[0]
    terms, framing = split_requirement_terms(requirement_lexemes)
    profile_terms = english_lexemes(session, [_profile_text(profile) for profile in profiles])
    weights = term_weights(terms, profile_terms)

    scored = [
        ScoredProfile(
            key=str(profile.id),
            matched=terms & lexemes,
            score=round(sum(weights[term] for term in terms & lexemes), 4),
            contactable=profile.consent_status is ConsentStatus.GIVEN_CONTACTABLE,
            name=profile.full_name,
        )
        for profile, lexemes in zip(profiles, profile_terms, strict=True)
    ]
    chosen = select_candidates(scored, len(terms))
    by_key = {str(profile.id): profile for profile in profiles}

    candidates = tuple(
        DirectoryCandidate(
            profile_id=by_key[entry.key].id,
            display_name=by_key[entry.key].full_name,
            headline=by_key[entry.key].headline,
            sector_code=by_key[entry.key].sector_code,
            sector_label=sector_label(by_key[entry.key].sector_code),
            institution=by_key[entry.key].institution,
            organisation=by_key[entry.key].current_organisation,
            coarse_location=coarse_location(
                by_key[entry.key].country_of_residence, by_key[entry.key].city
            ),
            consent_status=by_key[entry.key].consent_status,
            availability=by_key[entry.key].availability,
            expertise=_expertise(by_key[entry.key]),
            score=entry.score,
            matched_terms=tuple(sorted(entry.matched)),
        )
        for entry in chosen
    )

    covered = frozenset().union(*(entry.matched for entry in chosen))
    total = sum(weights.values())
    coverage = round(sum(weights[term] for term in covered) / total, 4) if total > 0 else 0.0

    description: dict[str, Any] = {
        "stage": "retrieval_authorisation",
        "source": "diaspora_profiles",
        "applied": "inside_the_query_before_ranking",
        "consulted": True,
        "actor_role": principal.role.value,
        "filter": {
            "consent_status": [status.value for status in CONSENT_SEARCHABLE],
            "is_tombstoned": False,
            "classifications": [zone.value for zone in readable_classifications(principal)],
            "sector_codes": sorted(sector_codes) or "any",
        },
        "searchable_profiles": len(profiles),
        "never_loaded": (
            "profiles whose consent is NOT_GIVEN or WITHDRAWN, and tombstoned profiles, are "
            "excluded by the WHERE clause and never read"
        ),
        "ranker": (
            "rarity-weighted match of the requirement's key terms against headline, sector and "
            "consented expertise; requirement facets covered first, then depth"
        ),
        "requirement_terms": sorted(terms),
        "ignored_framing_terms": sorted(framing),
        "selection": {
            "min_matched_terms": required_matches(len(terms)),
            "relative_floor": RELATIVE_FLOOR,
            "max_candidates": MAX_CANDIDATES,
        },
        "candidates": [
            {
                "profile_ref": candidate.profile_ref,
                "score": candidate.score,
                "matched_terms": list(candidate.matched_terms),
                "consent_status": candidate.consent_status.value,
            }
            for candidate in candidates
        ],
        "coverage": coverage,
        "returns": "candidates only; no contact or outreach action exists",
    }
    return DirectorySearch(
        consulted=True,
        requirement_terms=tuple(sorted(terms)),
        ignored_terms=tuple(sorted(framing)),
        searchable_count=len(profiles),
        candidates=candidates,
        coverage=coverage,
        filter_description=MappingProxyType(description),
    )


# ---------------------------------------------------------------------------
# The overview
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SuggestedSearch:
    """A demo capability search, and what the consent-gated search is expected to return."""

    id: str
    requirement: str
    expect: str
    must_include: tuple[str, ...]
    must_exclude: tuple[str, ...]
    roles: frozenset[RoleCode]


@dataclass(frozen=True, slots=True)
class DiasporaOverview:
    """How much of the directory this caller may search, and the demo searches for the role."""

    searchable_count: int
    contactable_count: int
    directory_only_count: int
    suggested_searches: tuple[SuggestedSearch, ...]
    measured_at: datetime


@cache
def suggested_searches() -> tuple[SuggestedSearch, ...]:
    """The demo searches. Raises at first use if the file is unreadable or malformed."""
    path = seed_path(DIASPORA_SEARCHES_FILE)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"Cannot read the diaspora demo searches at {path}: {exc}"
        raise RuntimeError(msg) from exc
    entries = document.get("searches") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        msg = f"{path} has no 'searches' array."
        raise RuntimeError(msg)
    loaded: list[SuggestedSearch] = []
    for entry in entries:
        try:
            search = SuggestedSearch(
                id=str(entry["id"]),
                requirement=str(entry["requirement"]),
                expect=str(entry["expect"]),
                must_include=tuple(str(name) for name in entry.get("must_include", [])),
                must_exclude=tuple(str(name) for name in entry.get("must_exclude", [])),
                roles=frozenset(RoleCode(str(role)) for role in entry["roles"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            msg = f"{path}: malformed search {entry!r}: {exc}"
            raise RuntimeError(msg) from exc
        if search.expect not in _EXPECTATIONS:
            msg = f"{path}: {search.id} must expect CANDIDATES or NO_MATCH."
            raise RuntimeError(msg)
        loaded.append(search)
    return tuple(loaded)


def diaspora_overview(
    session: Session,
    principal: Principal,
    *,
    now: datetime | None = None,
) -> DiasporaOverview:
    """Searchable profiles by consent, counted inside the same gate the search uses."""
    counts: Mapping[ConsentStatus, int] = {
        status: int(count)
        for status, count in session.execute(
            select(DiasporaProfile.consent_status, func.count())
            .where(directory_scope(principal))
            .group_by(DiasporaProfile.consent_status)
        )
    }
    return DiasporaOverview(
        searchable_count=sum(counts.values()),
        contactable_count=counts.get(ConsentStatus.GIVEN_CONTACTABLE, 0),
        directory_only_count=counts.get(ConsentStatus.GIVEN_DIRECTORY_ONLY, 0),
        suggested_searches=tuple(
            search for search in suggested_searches() if principal.role in search.roles
        ),
        measured_at=now or datetime.now(UTC),
    )
