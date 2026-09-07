"""``/v1/stakeholders`` -- the organisation index and the Stakeholder 360 dossier.

Thin, like every route module here: authorise, parse, delegate to
``app.services.stakeholders``, serialise.

**This module is a composition root.** ADR-0001 forbids ``app.services`` from importing
``app.ai``, so the dossier's citation resolver is bound here and passed in. That keeps the
dependency edge acyclic while still letting a dossier show a source's real title and URL --
the same pattern the brief uses for its evidence resolver.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.ai.evidence import citation_registry
from app.core.db import get_session
from app.schemas.stakeholders import (
    DossierResponse,
    OrganisationListResponse,
    OrganisationRowResponse,
)
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.stakeholders import (
    SourceLike,
    list_organisations,
    organisation_dossier,
    stakeholder_dossier,
)

router = APIRouter(prefix="/stakeholders", tags=["stakeholders"])

ReadPrincipal = Annotated[Principal, Depends(require(Permission.READ_STAKEHOLDER))]
DbSession = Annotated[Session, Depends(get_session)]


def _citations() -> Mapping[str, SourceLike]:
    """Bind the registry to the service's structural port.

    The cast is safe by construction rather than by assertion: ``CitationEntry`` carries
    ``id``, ``title``, ``url``, ``publisher`` and ``verified``, which is exactly what
    :class:`SourceLike` declares, and mypy checks that here at the seam.
    """
    return citation_registry()


@router.get(
    "/organisations",
    response_model=OrganisationListResponse,
    summary="List organisations the caller may read",
    response_description="The organisation index, narrowed to the caller's zones.",
)
def list_organisations_endpoint(
    principal: ReadPrincipal,
    db: DbSession,
    country: Annotated[
        str | None,
        Query(min_length=2, max_length=2, description="ISO 3166-1 alpha-2, e.g. AU."),
    ] = None,
    q: Annotated[
        str | None, Query(max_length=120, description="Case-insensitive name fragment.")
    ] = None,
) -> OrganisationListResponse:
    """Return the organisation index.

    The clearance predicate is part of the SQL (``CLAUDE.md`` rule 5), so ``total`` counts
    what this caller may read and nothing else -- it cannot be differenced against another
    role's total to learn how many rows were withheld.
    """
    rows = list_organisations(db, principal, country=country, query=q)
    return OrganisationListResponse(
        items=[OrganisationRowResponse.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.get(
    "/organisations/{organisation_id}",
    response_model=DossierResponse,
    summary="Stakeholder 360 for an organisation",
    response_description="The dossier: people, timeline, linked opportunities and sources.",
    responses={404: {"description": "No such organisation, or it is outside the caller's zones."}},
)
def read_organisation_dossier(
    principal: ReadPrincipal,
    db: DbSession,
    organisation_id: Annotated[uuid.UUID, Path(description="The organisation's id.")],
) -> DossierResponse:
    """Return the organisation dossier.

    A row outside the caller's zones returns **404, not 403**, and the difference is
    deliberate. The opportunity detail route returns 403 naming the zone because the caller
    already asserted that id and a legible refusal is what makes the control demonstrable.
    Here the caller arrives from a list they were served, so a 403 would confirm the
    existence of an organisation the list correctly declined to show them.
    """
    dossier = organisation_dossier(db, principal, organisation_id, resolve_citations=_citations)
    return DossierResponse.model_validate(dossier)


@router.get(
    "/people/{stakeholder_id}",
    response_model=DossierResponse,
    summary="Stakeholder 360 for a person",
    response_description="The dossier for one named contact.",
    responses={
        404: {"description": "No such stakeholder, or they are outside the caller's zones."}
    },
)
def read_person_dossier(
    principal: ReadPrincipal,
    db: DbSession,
    stakeholder_id: Annotated[uuid.UUID, Path(description="The stakeholder's id.")],
) -> DossierResponse:
    """Return one person's dossier, on the same terms as the organisation view."""
    dossier = stakeholder_dossier(db, principal, stakeholder_id, resolve_citations=_citations)
    return DossierResponse.model_validate(dossier)
