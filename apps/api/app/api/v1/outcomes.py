"""``GET /v1/outcomes`` -- the Unified Outcomes board, scoped to the caller.

One route behind ``read:command``, the front door all six roles hold. What is behind it is
decided section by section: ``app.services.outcomes`` asks each bounded context for its outcomes,
and each context checks its own permission and applies the caller's clearance before it counts
anything. The picture is unified; the permissions are not.

**Not registered as an audited read**, on the ``GET /v1/command/today`` precedent (OPEN_QUESTIONS
A-09, A-19): the board serves counts and a handful of summary facts, never a record set, and a row
per page view would bury the log. Refusals are still recorded by the middleware's denial branch.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.schemas.outcomes import (
    AuthorisationResponse,
    HeroThreadResponse,
    OutcomeFigureResponse,
    OutcomesBoardResponse,
    OutcomeSectionResponse,
    ThreadFactResponse,
    ThreadStepResponse,
)
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.outcomes import OutcomesBoard, build_outcomes_board
from app.services.outcomes.common import (
    Gate,
    OutcomeFigure,
    OutcomeSection,
    ThreadStep,
    section_tone,
)

router = APIRouter(prefix="/outcomes", tags=["outcomes"])

BoardReader = Annotated[Principal, Depends(require(Permission.READ_COMMAND))]
DbSession = Annotated[Session, Depends(get_session)]


def _authorisation(gate: Gate) -> AuthorisationResponse:
    return AuthorisationResponse(
        required_permissions=[permission.value for permission in gate.required],
        missing_permissions=[permission.value for permission in gate.missing],
        granted=gate.granted,
    )


def _figure(figure: OutcomeFigure) -> OutcomeFigureResponse:
    return OutcomeFigureResponse(
        key=figure.key,
        label=figure.label,
        value=figure.value,
        of=figure.of,
        detail=figure.detail,
        tone=figure.tone,
        authorisation=_authorisation(figure.gate),
    )


def _section(section: OutcomeSection) -> OutcomeSectionResponse:
    return OutcomeSectionResponse.model_validate(
        {
            "key": section.key,
            "title": section.title,
            "bounded_context": section.bounded_context,
            "summary": section.summary,
            "authorisation": _authorisation(section.gate),
            "tone": section_tone(section.figures),
            "counted_across": list(section.counted_across),
            "figures": [_figure(figure) for figure in section.figures],
            "href": section.href,
        }
    )


def _step(step: ThreadStep) -> ThreadStepResponse:
    return ThreadStepResponse.model_validate(
        {
            "key": step.key,
            "title": step.title,
            "bounded_context": step.bounded_context,
            "authorisation": _authorisation(step.gate),
            "found": step.found,
            "headline": step.headline,
            "facts": [
                ThreadFactResponse(label=fact.label, value=fact.value) for fact in step.facts
            ],
            "note": step.note,
            "tone": step.tone,
            "trace_id": step.trace_id,
            "href": step.href,
        }
    )


def _response(board: OutcomesBoard) -> OutcomesBoardResponse:
    return OutcomesBoardResponse(
        generated_at=board.generated_at,
        readable_classifications=list(board.readable_classifications),
        domains_readable=sum(1 for section in board.sections if section.gate.granted),
        domains_total=len(board.sections),
        sections=[_section(section) for section in board.sections],
        thread=HeroThreadResponse(
            id=board.thread_id,
            title=board.thread_title,
            steps=[_step(step) for step in board.thread],
        ),
    )


@router.get(
    "",
    response_model=OutcomesBoardResponse,
    summary="The Unified Outcomes board for the current role",
    response_description="Outcomes by bounded context, each under its own authorisation.",
    responses={403: {"description": "You do not hold read:command, or have no session."}},
)
def read_outcomes(principal: BoardReader, db: DbSession) -> OutcomesBoardResponse:
    """Return mission outcomes, each section counted in its own context under its own check.

    A section the caller may not read comes back with its authorisation refused, no figures and no
    zones -- it was never queried -- and a thread step the caller may not read comes back withheld.
    Neither is ever reported as zero.
    """
    return _response(build_outcomes_board(db, principal))
