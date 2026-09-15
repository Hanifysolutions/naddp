"""``/v1/diaspora`` -- the consented directory a caller may search, in counts.

One thin read behind ``read:diaspora_profile``, which AMBASSADOR, DEPUTY, TRADE_OFFICER and
DIASPORA_OFFICER hold and CONSULAR_OFFICER and ADMIN do not -- a refusal the audit middleware
records and the web renders as a deny state. The search itself is ``POST /v1/ai/diaspora/match``
(``search:diaspora_profile``); this route is what lets the screen state the consent gate honestly:
how many consented profiles are searchable, how many of them may be approached, and nothing about
anyone who has not consented.

**No contact route exists.** There is no outreach, message or contact endpoint in this router or
anywhere in the API. The platform returns candidates; the mission approaches people through its
own process.

**Not a privileged read.** Counts of consented profiles are ``MISSION_INTERNAL``, below the zone
at which a read is recorded -- the ``GET /v1/command/today`` precedent (OPEN_QUESTIONS A-09,
A-15). Refusals are still recorded by the middleware's denial branch.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.domain.diaspora import MAX_CANDIDATES, MIN_MATCHED_TERMS, RELATIVE_FLOOR
from app.schemas.diaspora import (
    DiasporaOverviewResponse,
    DiasporaSelectionRuleResponse,
    SuggestedSearchResponse,
)
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.diaspora import diaspora_overview

router = APIRouter(prefix="/diaspora", tags=["diaspora"])

ReadPrincipal = Annotated[Principal, Depends(require(Permission.READ_DIASPORA_PROFILE))]
DbSession = Annotated[Session, Depends(get_session)]


@router.get(
    "",
    response_model=DiasporaOverviewResponse,
    summary="The consented diaspora directory this caller may search",
    response_description="Searchable consented profiles by consent, and demo searches.",
    responses={403: {"description": "You do not hold read:diaspora_profile, or have no session."}},
)
def read_overview(principal: ReadPrincipal, db: DbSession) -> DiasporaOverviewResponse:
    """Return counts inside the consent gate the search uses, and the role's demo searches."""
    overview = diaspora_overview(db, principal)
    return DiasporaOverviewResponse(
        searchable_count=overview.searchable_count,
        contactable_count=overview.contactable_count,
        directory_only_count=overview.directory_only_count,
        suggested_searches=[
            SuggestedSearchResponse.model_validate(
                {"id": search.id, "requirement": search.requirement, "expect": search.expect}
            )
            for search in overview.suggested_searches
        ],
        selection=DiasporaSelectionRuleResponse(
            min_matched_terms=MIN_MATCHED_TERMS,
            relative_floor=RELATIVE_FLOOR,
            max_candidates=MAX_CANDIDATES,
        ),
        measured_at=overview.measured_at,
    )
