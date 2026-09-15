"""The two results ``DIASPORA_MATCH`` can give without a model: a candidate set, or nobody.

Both are built from a :class:`~app.domain.diaspora.DirectorySearch` that stage 3 produced under
the caller's identity, and so both contain consented profiles only -- the search never loaded
anyone else.

* :func:`candidate_set` lists the candidates the rules selected, with what a result may show:
  expertise, sector, institution, coarse location and consent state. It is the deterministic
  path for a requirement the directory can meet. A stored snapshot would name people who are not
  in the directory, answering some other requirement.
* :func:`no_candidates` is what the purpose returns when no consented profile fits. It names
  nobody and says why.
* :func:`verify_candidates` is the stage 8 record check: every candidate in a result must be one
  the consent-gated query returned, with its consent stated exactly as recorded. A live model
  cannot add a person, promote a directory-only profile to contactable, or move anyone.

**Candidates only.** No result here carries a contact detail or an outreach step, because none
exists to carry.

ADR-0001: pure functions over already-authorised data. No database, no model, no clock.
"""

from __future__ import annotations

from typing import Final

from app.ai.schemas import (
    DiasporaExpertiseRef,
    DiasporaMatch,
    DiasporaMatchResult,
    DiasporaNoMatch,
    GatewayContext,
    GroundedResult,
)
from app.domain.diaspora import DirectoryCandidate, DirectorySearch, required_matches

__all__ = [
    "CANDIDATES_ONLY_NOTE",
    "candidate_set",
    "no_candidates",
    "verify_candidates",
]

#: Said on every candidate set, in the words the Diaspora screen repeats.
CANDIDATES_ONLY_NOTE: Final[str] = (
    "Candidates only. The platform contacts no one: the mission approaches people through its "
    "own process, and only a profile marked contactable may be approached at all."
)

_NEVER_LOADED_NOTE: Final[str] = (
    "Profiles whose consent is not given or has been withdrawn were never loaded, so they cannot "
    "appear however well they would fit."
)

_LABEL_MAX: Final[int] = 200


def _clip(text: str, limit: int = _LABEL_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _requirement(context: GatewayContext) -> str:
    return (context.question or "").strip() or "(no requirement was given)"


def _match(candidate: DirectoryCandidate) -> DiasporaMatch:
    labels = [item.label for item in candidate.expertise]
    holds = f" Consented expertise: {'; '.join(labels)}." if labels else ""
    return DiasporaMatch(
        profile_ref=candidate.profile_ref,
        display_name=_clip(candidate.display_name),
        headline=_clip(candidate.headline),
        expertise_tags=[item.code for item in candidate.expertise][:8],
        expertise=[
            DiasporaExpertiseRef(
                code=item.code, label=_clip(item.label), proficiency=item.proficiency
            )
            for item in candidate.expertise[:8]
        ],
        sector_code=candidate.sector_code,
        sector_label=_clip(candidate.sector_label),
        institution=_clip(candidate.institution) if candidate.institution else None,
        organisation=_clip(candidate.organisation) if candidate.organisation else None,
        coarse_location=candidate.coarse_location,
        availability=_clip(candidate.availability) if candidate.availability else None,
        why_matched=f"{candidate.headline}.{holds}",
        matched_terms=list(candidate.matched_terms[:16]),
        consent_status=candidate.consent_status,
        contactable=candidate.contactable,
        citations=[],
    )


def candidate_set(context: GatewayContext, search: DirectorySearch) -> DiasporaMatchResult:
    """The selected consented candidates. Raises when there are none."""
    if not search.candidates:
        msg = "No consented candidate fits; use no_candidates."
        raise ValueError(msg)
    count = len(search.candidates)
    return DiasporaMatchResult(
        query=_requirement(context),
        matches=[_match(candidate) for candidate in search.candidates],
        rationale=(
            f"{count} consented profile{'' if count == 1 else 's'} of the "
            f"{search.searchable_count} searchable in your scope fit this requirement, strongest "
            f"first, covering {search.coverage:.0%} of its key terms between them. "
            f"{CANDIDATES_ONLY_NOTE} {_NEVER_LOADED_NOTE}"
        ),
        confidence=round(search.coverage, 2),
        no_match=None,
    )


def _no_match_reason(search: DirectorySearch) -> str:
    if not search.consulted:
        return f"The consented directory could not be searched ({search.not_consulted_reason})."
    if search.searchable_count == 0:
        return "No consented profile is within your scope."
    if not search.requirement_terms:
        return "The requirement carries no terms the directory could be searched on."
    need = required_matches(len(search.requirement_terms))
    return (
        f"None of the {search.searchable_count} consented profiles in your scope holds at least "
        f"{need} of the requirement's key terms."
    )


def no_candidates(context: GatewayContext, search: DirectorySearch) -> DiasporaMatchResult:
    """Nobody fits. Names nobody, and says why."""
    return DiasporaMatchResult(
        query=_requirement(context),
        matches=[],
        rationale=f"No candidate is returned. {_NEVER_LOADED_NOTE}",
        confidence=1.0,
        no_match=DiasporaNoMatch(reason=_no_match_reason(search)),
    )


def verify_candidates(result: GroundedResult, search: DirectorySearch) -> tuple[bool, str]:
    """Stage 8's record check: only consented candidates the query returned, as recorded."""
    if not isinstance(result, DiasporaMatchResult):
        return False, "The result is not a diaspora candidate set."
    by_ref = {candidate.profile_ref: candidate for candidate in search.candidates}
    for match in result.matches:
        candidate = by_ref.get(match.profile_ref)
        if candidate is None:
            return (
                False,
                f"{match.profile_ref} is not among the consented candidates the query returned.",
            )
        if match.consent_status is not candidate.consent_status or (
            match.contactable is not candidate.contactable
        ):
            return False, f"{match.profile_ref} states a consent other than the one recorded."
        if match.coarse_location not in (None, candidate.coarse_location):
            return False, f"{match.profile_ref} states a location other than the coarse one."
    if not result.matches:
        return True, "No candidate is named."
    return (
        True,
        f"All {len(result.matches)} candidate(s) are consented profiles the in-query filter "
        "returned, with consent stated as recorded.",
    )
