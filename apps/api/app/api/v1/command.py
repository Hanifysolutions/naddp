"""``GET /v1/command/today`` -- the command centre, scoped to the caller.

PROMPT Phase 4 VERIFY item 4. One route, one permission (``read:command``), and a payload
whose *shape* is constant while its *content* is decided entirely by the caller's
permissions and clearance.

The handler is deliberately three lines of work: resolve the principal, call
``app.services.command.build_command_today``, project. Every decision about which tile is
computed and what predicate its ``COUNT`` carries lives in that service (``CLAUDE.md``
rule 5), which is what makes "the query never ran" an assertable property rather than a
claim about the handler.

**Not registered in the audit middleware's route registry, and that is a decision.** This
endpoint serves aggregate counts and never an entity, so no privileged *object* is read;
registering it would append an ``access.privileged_read`` row every time a consular officer
loaded their dashboard, which is exactly the row-per-page-view flood
``app.audit.middleware`` argues against. Refusals are still recorded -- a denial is a row
regardless of the registry -- so the interesting half is in the log either way. If the
architect wants dashboard loads recorded, it is one ``_rule(...)`` entry in
``DEFAULT_RULES`` plus a ``record_access`` call here; noted in ``docs/OPEN_QUESTIONS.md``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.schemas.command import CommandTodayResponse
from app.security.deps import require
from app.security.permissions import Permission
from app.security.principal import Principal
from app.services.command import CommandToday, build_command_today

router = APIRouter(prefix="/command", tags=["command"])

#: The principal this route admits. ``read:command`` is held by all six roles: the command
#: centre is the front door, and what differs is what is behind it.
CommandReader = Annotated[Principal, Depends(require(Permission.READ_COMMAND))]


def _response(today: CommandToday) -> CommandTodayResponse:
    """Project the service result onto the wire shape.

    ``model_validate`` on each tile rather than one call on the whole aggregate: the tiles
    are nullable, and validating them individually keeps a ``None`` a ``None`` instead of
    asking Pydantic to guess at a union.
    """
    return CommandTodayResponse.model_validate(
        {
            "generated_at": today.generated_at,
            "visible_tiles": list(today.visible_tiles),
            "readable_classifications": list(today.readable_classifications),
            "opportunities": today.opportunities,
            "consular": today.consular,
            "stakeholders": today.stakeholders,
            "diaspora": today.diaspora,
            "intelligence": today.intelligence,
            "meetings": today.meetings,
        }
    )


@router.get(
    "/today",
    response_model=CommandTodayResponse,
    summary="The command centre for the current role",
    response_description="Counts for every tile this principal's permissions admit.",
    responses={403: {"description": "You do not hold read:command, or have no session."}},
)
def read_command_today(
    principal: CommandReader,
    db: Annotated[Session, Depends(get_session)],
) -> CommandTodayResponse:
    """Return today's counts, filtered by permission and by clearance.

    A tile is ``null`` when the caller does not hold its read permission, and the
    corresponding tables are never queried. Within a tile that *is* computed, every
    ``COUNT`` carries ``WHERE classification IN (...)`` for the caller's cleared zones, so
    a total can never disclose how many rows the caller was not cleared to see.

    On an empty database every count is 0 and the response is otherwise identical. The
    seed is loaded separately; a dashboard that fails before its data arrives is a
    dashboard nobody can build against.
    """
    return _response(build_command_today(db, principal))
