"""Diaspora-capability outcomes: counted in the Diaspora context, consent-gated inside the query.

Two gates, taken separately. ``read:diaspora_profile`` admits the reach figures; the figure and
the thread step that name experts for the corridor also need ``search:diaspora_profile``, because
they run the consent-gated capability search (``app.services.diaspora.search_directory``) -- a
reader who may count the directory is not thereby allowed to search it. Every count uses
``directory_scope``, so a profile without consent is neither counted nor matched.

Imports no model from any other context (``tests/test_outcomes_board.py``).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.diaspora import DirectorySearch
from app.domain.enums import ConsentStatus
from app.models.diaspora import DiasporaExpertise, DiasporaProfile
from app.security.permissions import Permission
from app.security.principal import Principal, readable_classifications_for
from app.services.diaspora import directory_scope, search_directory, suggested_searches
from app.services.outcomes.anchors import HeroThread
from app.services.outcomes.common import (
    OutcomeFigure,
    OutcomeSection,
    ThreadFact,
    ThreadStep,
    authorise,
    count,
    unfound_step,
    withheld_section,
    withheld_step,
)

__all__ = ["BOUNDED_CONTEXT", "diaspora_section", "diaspora_step"]

BOUNDED_CONTEXT: Final[str] = "diaspora"

_TITLE: Final[str] = "Diaspora capability"
_SUMMARY: Final[str] = "Consented expertise the mission can reach, and who fits the corridor."
_STEP_TITLE: Final[str] = "Diaspora expertise"


def _corridor_search(
    session: Session, principal: Principal, thread: HeroThread
) -> DirectorySearch | None:
    spec = next(
        (search for search in suggested_searches() if search.id == thread.diaspora_search_id),
        None,
    )
    return None if spec is None else search_directory(session, principal, spec.requirement)


def diaspora_section(session: Session, principal: Principal, thread: HeroThread) -> OutcomeSection:
    """Consented reach, the corridor match, contactable profiles and expertise breadth."""
    gate = authorise(principal, Permission.READ_DIASPORA_PROFILE)
    if not gate.granted:
        return withheld_section(
            key="diaspora",
            title=_TITLE,
            bounded_context=BOUNDED_CONTEXT,
            summary=_SUMMARY,
            gate=gate,
        )

    scope = directory_scope(principal)
    consented = count(session, select(func.count()).select_from(DiasporaProfile).where(scope))
    contactable = count(
        session,
        select(func.count())
        .select_from(DiasporaProfile)
        .where(scope, DiasporaProfile.consent_status == ConsentStatus.GIVEN_CONTACTABLE),
    )
    areas = count(
        session,
        select(func.count(func.distinct(DiasporaExpertise.expertise_tag_id)))
        .select_from(DiasporaExpertise)
        .join(DiasporaProfile, DiasporaProfile.id == DiasporaExpertise.diaspora_profile_id)
        .where(scope),
    )

    match_gate = authorise(
        principal, Permission.READ_DIASPORA_PROFILE, Permission.SEARCH_DIASPORA_PROFILE
    )
    if match_gate.granted:
        search = _corridor_search(session, principal, thread)
        matched = 0 if search is None else len(search.candidates)
        matched_figure = OutcomeFigure(
            key="matched_to_corridor",
            label="Experts matched to the corridor",
            gate=match_gate,
            value=matched,
            detail="A consent-gated capability search for the corridor requirement, filtered "
            "inside the query. Candidates only: the platform contacts no one.",
            tone="ok" if matched else "neutral",
        )
    else:
        matched_figure = OutcomeFigure(
            key="matched_to_corridor",
            label="Experts matched to the corridor",
            gate=match_gate,
            value=None,
            detail="Matching experts runs a capability search, which this role may not do.",
        )

    return OutcomeSection(
        key="diaspora",
        title=_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        summary=_SUMMARY,
        gate=gate,
        counted_across=tuple(readable_classifications_for(principal)),
        figures=(
            OutcomeFigure(
                key="consented_reachable",
                label="Consented profiles reachable",
                gate=gate,
                value=consented,
                detail="Consented to directory listing or to be approached, and not withdrawn. "
                "Profiles without consent are never counted.",
            ),
            matched_figure,
            OutcomeFigure(
                key="contactable",
                label="Consented to be approached",
                gate=gate,
                value=contactable,
                detail="The rest consented to listing only. Consent is recorded, never inferred.",
            ),
            OutcomeFigure(
                key="expertise_areas",
                label="Expertise areas held",
                gate=gate,
                value=areas,
                detail="Distinct capability tags across consented profiles only.",
            ),
        ),
        href="/diaspora",
    )


def diaspora_step(session: Session, principal: Principal, thread: HeroThread) -> ThreadStep:
    """Who in the consented directory fits the corridor: one expert per required capability."""
    gate = authorise(
        principal, Permission.READ_DIASPORA_PROFILE, Permission.SEARCH_DIASPORA_PROFILE
    )
    if not gate.granted:
        return withheld_step(
            key="diaspora", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    search = _corridor_search(session, principal, thread)
    if search is None or not search.consulted:
        return unfound_step(
            key="diaspora", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    facts = [ThreadFact("Consented candidates", str(len(search.candidates)))]
    every_facet_found = bool(search.candidates)
    for facet in thread.diaspora_facets:
        expert = next(
            (
                candidate
                for candidate in search.candidates
                if any(item.code == facet.expertise_tag for item in candidate.expertise)
            ),
            None,
        )
        if expert is None:
            every_facet_found = False
            facts.append(ThreadFact(facet.label, "No consented match"))
            continue
        consent = "contactable" if expert.contactable else "directory only"
        facts.append(
            ThreadFact(facet.label, f"{expert.display_name}, {expert.coarse_location}, {consent}")
        )

    return ThreadStep(
        key="diaspora",
        title=_STEP_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        gate=gate,
        found=True,
        headline="Consented experts matched to the corridor",
        facts=tuple(facts),
        note="Consent-gated inside the query. Candidates only: the platform contacts no one.",
        tone="ok" if every_facet_found else "warn",
        href="/diaspora",
    )
