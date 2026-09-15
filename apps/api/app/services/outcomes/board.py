"""Compose the Unified Outcomes board from separately-governed contexts. Never queries.

**The picture is unified; the permissions are not.** This module asks each context for its
section and its step on the hero thread, in order, and returns them side by side. It holds no
statement and no model import -- ``tests/test_outcomes_board.py`` reads its imports to keep it
that way -- so it cannot pool two contexts into one query, and it cannot compute one context's
figure from another's rows. Each context decides for itself, from the caller's own permissions and
clearance, whether to answer at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domain.enums import Classification
from app.security.principal import Principal, readable_classifications_for
from app.services.outcomes.anchors import hero_thread
from app.services.outcomes.bilateral import bilateral_section, bilateral_step
from app.services.outcomes.citizen_service import citizen_service_section, citizen_service_step
from app.services.outcomes.common import OutcomeSection, ThreadStep
from app.services.outcomes.diaspora import diaspora_section, diaspora_step
from app.services.outcomes.meetings import meetings_section, meetings_step
from app.services.outcomes.relationships import relationships_section, relationships_step

__all__ = ["OutcomesBoard", "build_outcomes_board"]


@dataclass(frozen=True, slots=True)
class OutcomesBoard:
    """One principal's view of the mission's outcomes, and of the corridor running through them."""

    generated_at: datetime
    readable_classifications: Sequence[Classification]
    sections: tuple[OutcomeSection, ...]
    thread_id: str
    thread_title: str
    thread: tuple[ThreadStep, ...]


def build_outcomes_board(
    session: Session, principal: Principal, *, now: datetime | None = None
) -> OutcomesBoard:
    """Ask each context for its outcomes and its step on the hero thread, under its own gate."""
    moment = now or datetime.now(UTC)
    thread = hero_thread()
    return OutcomesBoard(
        generated_at=moment,
        readable_classifications=readable_classifications_for(principal),
        sections=(
            bilateral_section(session, principal, moment),
            citizen_service_section(session, principal, moment),
            diaspora_section(session, principal, thread),
            relationships_section(session, principal, moment),
            meetings_section(session, principal, moment),
        ),
        thread_id=thread.id,
        thread_title=thread.title,
        thread=(
            bilateral_step(session, principal, thread),
            relationships_step(session, principal, moment, thread),
            meetings_step(session, principal, moment, thread),
            diaspora_step(session, principal, thread),
            citizen_service_step(session, principal, moment, thread),
        ),
    )
