"""Citizen-service outcomes: counted in the Consular context, under ``read:consular_case``.

Cases are ``CONSULAR_SENSITIVE``, so the zone predicate is what keeps these counts honest: a
reader without the consular compartment would count nothing even if they held the permission.
Resolution is measured on the case's own business-day clock (``app.services.cases.case_slas``),
which pauses while a case waits on the citizen. Nothing narrative leaves this module: no subject,
no summary, no determination text -- counts, and the hero case's type, status and clock.

Imports no model from any other context (``tests/test_outcomes_board.py``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.case_types import case_type
from app.domain.enums import CaseStatus
from app.domain.sla import SlaSnapshot, SlaState
from app.models.consular import Case
from app.security.permissions import Permission
from app.security.principal import Principal, readable_classifications_for
from app.services.cases import case_slas
from app.services.outcomes.anchors import HeroThread
from app.services.outcomes.common import (
    OutcomeFigure,
    OutcomeSection,
    ThreadFact,
    ThreadStep,
    Tone,
    authorise,
    humanise,
    unfound_step,
    withheld_section,
    withheld_step,
)

__all__ = ["BOUNDED_CONTEXT", "citizen_service_section", "citizen_service_step"]

BOUNDED_CONTEXT: Final[str] = "consular"

_CONCLUDED: Final[frozenset[CaseStatus]] = frozenset({CaseStatus.RESOLVED, CaseStatus.CLOSED})

_TITLE: Final[str] = "Citizen service"
_SUMMARY: Final[str] = (
    "Consular casework concluded, measured against the service level, and the backlog behind it."
)
_STEP_TITLE: Final[str] = "Citizen service"


def citizen_service_section(
    session: Session, principal: Principal, now: datetime
) -> OutcomeSection:
    """Cases resolved, resolution inside the service level, and the open backlog's health."""
    gate = authorise(principal, Permission.READ_CONSULAR_CASE)
    if not gate.granted:
        return withheld_section(
            key="citizen_service",
            title=_TITLE,
            bounded_context=BOUNDED_CONTEXT,
            summary=_SUMMARY,
            gate=gate,
        )

    zones = tuple(readable_classifications_for(principal))
    cases = list(
        session.scalars(select(Case).where(Case.classification.in_(zones)).order_by(Case.id))
    )
    clocks = case_slas(session, cases, now=now)

    concluded = [clocks[case.id] for case in cases if case.status in _CONCLUDED]
    measured = [clock for clock in concluded if clock.met is not None]
    met = sum(1 for clock in measured if clock.met)
    running = [clocks[case.id] for case in cases if case.status not in _CONCLUDED]
    breached = sum(1 for clock in running if clock.state is SlaState.BREACHED)
    due_soon = sum(1 for clock in running if clock.state is SlaState.DUE_SOON)
    paused = sum(1 for clock in running if clock.state is SlaState.PAUSED)

    # "N of M within the service level" is a statement of record, not a breach, so it is
    # neutral unless every measured case met its budget -- and then it is a quiet --ok.
    # What is genuinely at risk on this section is the open backlog and the cases already
    # past their service level, below (DESIGN_SYSTEM.md: semantic colour is for STATE).
    within_tone: Tone = "ok" if measured and met == len(measured) else "neutral"

    return OutcomeSection(
        key="citizen_service",
        title=_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        summary=_SUMMARY,
        gate=gate,
        counted_across=zones,
        figures=(
            OutcomeFigure(
                key="cases_resolved",
                label="Cases resolved",
                gate=gate,
                value=len(concluded),
                detail="Resolved or closed on a named officer's determination, never by the "
                "platform.",
                tone="ok" if concluded else "neutral",
            ),
            OutcomeFigure(
                key="resolved_within_service_level",
                label="Resolved within the service level",
                gate=gate,
                value=met,
                of=len(measured),
                detail="Measured on the business-day clock, which pauses while a case waits on "
                "the citizen.",
                tone=within_tone,
            ),
            OutcomeFigure(
                key="open_backlog",
                label="Open cases",
                gate=gate,
                value=len(running),
                detail=f"{due_soon} due soon, and {paused} paused while waiting on the citizen.",
                tone="warn" if due_soon else "neutral",
            ),
            OutcomeFigure(
                key="past_service_level",
                label="Open past the service level",
                gate=gate,
                value=breached,
                detail="Running cases whose clock has passed its budget. A paused case is never "
                "counted here.",
                tone="risk" if breached else "neutral",
            ),
        ),
        href="/consular",
    )


def _service_level(clock: SlaSnapshot) -> str:
    remaining = clock.remaining_business_days
    left = "" if remaining is None else f", {remaining:.1f} business days left"
    match clock.state:
        case SlaState.BREACHED:
            return "Past the service level"
        case SlaState.DUE_SOON:
            return f"Due soon{left}"
        case SlaState.ON_TRACK:
            return f"On track{left}"
        case SlaState.PAUSED:
            return "Paused while waiting on the citizen"
        case SlaState.STOPPED:
            return (
                "Resolved within the service level"
                if clock.met
                else "Resolved outside the service level"
            )
        case _:
            return "No service level applies"


def _clock_tone(clock: SlaSnapshot) -> Tone:
    if clock.state is SlaState.BREACHED:
        return "risk"
    if clock.state in {SlaState.DUE_SOON, SlaState.PAUSED}:
        return "warn"
    if clock.state is SlaState.STOPPED:
        return "ok" if clock.met else "warn"
    return "neutral"


def citizen_service_step(
    session: Session, principal: Principal, now: datetime, thread: HeroThread
) -> ThreadStep:
    """The corridor's citizen-service case: type, status and clock. Metadata only."""
    gate = authorise(principal, Permission.READ_CONSULAR_CASE)
    if not gate.granted:
        return withheld_step(
            key="consular", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    case = None
    if thread.case_id is not None:
        case = session.scalars(
            select(Case).where(
                Case.id == thread.case_id,
                Case.classification.in_(readable_classifications_for(principal)),
            )
        ).one_or_none()
    if case is None:
        return unfound_step(
            key="consular", title=_STEP_TITLE, bounded_context=BOUNDED_CONTEXT, gate=gate
        )

    clock = case_slas(session, [case], now=now)[case.id]
    spec = case_type(case.case_type_code)
    return ThreadStep(
        key="consular",
        title=_STEP_TITLE,
        bounded_context=BOUNDED_CONTEXT,
        gate=gate,
        found=True,
        headline=spec.label if spec is not None else humanise(case.case_type_code),
        facts=(
            ThreadFact("Reference", case.public_ref),
            ThreadFact("Status", humanise(case.status.value)),
            ThreadFact("Service level", _service_level(clock)),
        ),
        note="Metadata only. The subject and the case narrative stay in the case workspace.",
        tone=_clock_tone(clock),
        href=f"/consular/cases/{case.id}",
    )
