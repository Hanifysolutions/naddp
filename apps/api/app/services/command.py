"""The command centre: what one principal may see of the mission's day, in counts.

``GET /v1/command/today`` is the first screen of the demo and the plainest demonstration
of ADR-0003 and ADR-0006 together. Two officers open the same URL in the same build and
get materially different pages -- not a page with some tiles greyed out, but a payload in
which the tiles they may not see **were never computed and never queried**.

Three rules govern every count in this module.

**1. A tile is computed only if its permission is held.** ``TRADE_OFFICER`` holds
``read:opportunity`` and not ``read:consular_case``, so the consular tile is ``None`` for
that principal and no SQL touches ``cases``. ``CONSULAR_OFFICER`` is the mirror image.
``ADMIN`` holds ``read:command`` and no content read at all, so it reaches this endpoint
and every tile is ``None`` -- which is not a bug and is the sharpest illustration in the
build of "administration is not clearance" (ADR-0003, ``docs/OPEN_QUESTIONS.md`` A-01).

**2. Every count carries the classification predicate in its ``WHERE`` clause**
(``CLAUDE.md`` rule 5, ADR-0006). A count is exactly the leak that post-filtering causes:
returning the right rows and the wrong total tells the reader precisely how many records
they are not cleared for. ``_readable`` below is applied to every statement, and there is
no code path in this module that counts rows and subtracts afterwards.

**3. Counts, never rows.** Nothing here loads an entity. The endpoint answers "how many"
and the bounded-context endpoints answer "which"; keeping that line means the dashboard
cannot become a way to page through consular cases without ``read:consular_case``.

**Metric choice is provisional.** ``docs/OPEN_QUESTIONS.md`` Q-01 (the exact tile metrics)
is open. The metrics below are the obviously defensible ones -- pipeline by stage, cases by
status and SLA state, stakeholders by relationship strength, diaspora by consent status,
signals in the last seven days, meetings in the next seven -- chosen so the architect can
correct them cheaply: each is one dataclass field and one ``select(count())``.

**Zeros are correct on an empty database.** The seed is not loaded yet. Every query below
returns 0 rather than raising, and the response shape is identical whether the database is
full or empty, because a dashboard that 500s before the seed lands is a dashboard nobody
can develop against.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.domain.enums import (
    CaseStatus,
    Classification,
    ConsentStatus,
    FollowupStatus,
    OpportunityStage,
    RelationshipStrength,
    SignalStatus,
)
from app.models.consular import Case
from app.models.diaspora import DiasporaProfile
from app.models.intelligence import Signal
from app.models.meetings import Meeting, MeetingFollowup
from app.models.opportunities import Opportunity
from app.models.stakeholders import Organisation, Stakeholder
from app.security.permissions import Permission
from app.security.principal import Principal, readable_classifications_for
from app.services.stakeholders import DORMANT_AFTER

__all__ = [
    "CONSULAR_OPEN_STATUSES",
    "OPPORTUNITY_OPEN_STAGES",
    "SIGNAL_WINDOW_DAYS",
    "SLA_DUE_SOON_HOURS",
    "UPCOMING_MEETING_DAYS",
    "CommandToday",
    "ConsularTile",
    "DiasporaTile",
    "IntelligenceTile",
    "MeetingTile",
    "OpportunityTile",
    "StakeholderTile",
    "build_command_today",
]

#: How far back "recent signals" reaches. Seven days is the reporting cadence a mission
#: actually works to, and it is short enough that the number moves during a demo.
SIGNAL_WINDOW_DAYS: Final[int] = 7

#: How far forward the meetings tile looks.
UPCOMING_MEETING_DAYS: Final[int] = 7

#: A case whose SLA falls due inside this window is "due soon" rather than merely open.
#: Two working days: long enough to act on, short enough that the tile is not a synonym
#: for "open".
SLA_DUE_SOON_HOURS: Final[int] = 48

#: Pipeline stages that represent live work. ``PARTNERED`` and ``CLOSED`` are the two
#: terminal stages of the opportunity machine (``app.domain.enums.OPPORTUNITY_TRANSITIONS``);
#: everything else is in flight.
OPPORTUNITY_OPEN_STAGES: Final[frozenset[OpportunityStage]] = frozenset(
    stage
    for stage in OpportunityStage
    if stage not in {OpportunityStage.PARTNERED, OpportunityStage.CLOSED}
)

#: Case statuses that still need somebody. ``CLOSED`` is the only terminal state
#: (``docs/workflows.md`` section 3); ``RESOLVED`` is excluded as well because the work is
#: done and only the paperwork remains, and counting it as open would make the tile read
#: as a backlog that nobody can clear.
CONSULAR_OPEN_STATUSES: Final[frozenset[CaseStatus]] = frozenset(
    status for status in CaseStatus if status not in {CaseStatus.CLOSED, CaseStatus.RESOLVED}
)


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpportunityTile:
    """Bilateral Opportunity Health: the trade pipeline, by stage. Requires ``read:opportunity``.

    Counts, not a composite health score (OPEN_QUESTIONS A-08): a "health" judgement needs a
    baseline and thresholds nobody has supplied, and inventing them here would put a number
    on the command centre that no officer could defend when asked what it means.

    ``pipeline_value_aud`` is the one non-count, and it is a plain sum of a stored column
    over the open stages - not a forecast. It is quoted next to the stage counts because
    "ten open" and "ten open worth A$25m" are different briefings.
    """

    total: int
    open_total: int
    by_stage: Mapping[OpportunityStage, int]
    overdue_next_action: int
    pipeline_value_aud: float
    ai_proposed: int


@dataclass(frozen=True, slots=True)
class ConsularTile:
    """Consular casework and the SLA clock. Requires ``read:consular_case``."""

    total: int
    open_total: int
    by_status: Mapping[CaseStatus, int]
    sla_breached: int
    sla_due_soon: int
    awaiting_citizen: int


@dataclass(frozen=True, slots=True)
class StakeholderTile:
    """Relationship Health: the relationship map, by strength. Requires ``read:stakeholder``.

    ``dormant`` counts contacts last spoken to more than
    :data:`app.services.stakeholders.DORMANT_AFTER` ago. Distinct from ``never_contacted``
    on purpose: a relationship that has gone quiet and one that was never started need
    different work, and a tile that merged them would hide which.
    """

    total: int
    by_relationship_strength: Mapping[RelationshipStrength, int]
    never_contacted: int
    dormant: int
    organisations: int


@dataclass(frozen=True, slots=True)
class DiasporaTile:
    """Diaspora reach, by consent. Requires ``read:diaspora_profile``."""

    total: int
    by_consent_status: Mapping[ConsentStatus, int]
    contactable: int


@dataclass(frozen=True, slots=True)
class IntelligenceTile:
    """Signal flow over the last :data:`SIGNAL_WINDOW_DAYS` days. Requires ``read:intelligence``."""

    total: int
    recent_total: int
    by_status: Mapping[SignalStatus, int]
    awaiting_triage: int


@dataclass(frozen=True, slots=True)
class MeetingTile:
    """The diary and the follow-up queue. Requires ``read:meeting``."""

    upcoming_total: int
    followups_awaiting_approval: int
    followups_drafted: int


@dataclass(frozen=True, slots=True)
class CommandToday:
    """One principal's view of the mission's day.

    ``visible_tiles`` is redundant with "which fields are not ``None``" and is returned
    anyway: it is the field a reviewer reads to see the role difference at a glance, and
    the field the web shell iterates rather than hard-coding six null checks.
    """

    generated_at: datetime
    readable_classifications: Sequence[Classification]
    visible_tiles: Sequence[str]
    opportunities: OpportunityTile | None
    consular: ConsularTile | None
    stakeholders: StakeholderTile | None
    diaspora: DiasporaTile | None
    intelligence: IntelligenceTile | None
    meetings: MeetingTile | None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    """Run a ``count()`` statement and return 0 rather than ``None`` for an empty table."""
    return session.scalar(statement) or 0


def _grouped[E: Enum](
    session: Session,
    column: InstrumentedAttribute[E],
    predicate: ColumnElement[bool],
    members: type[E],
) -> Mapping[E, int]:
    """Return ``{member: count}`` over ``column``, with every member present.

    Absent members are returned as 0 rather than omitted. A client that has to distinguish
    "no rows in this bucket" from "this bucket was filtered away" will get it wrong, and a
    bar chart with a missing bar is read as a missing stage rather than an empty one.
    """
    statement = select(column, func.count()).where(predicate).group_by(column)
    counts: dict[E, int] = dict.fromkeys(members, 0)
    for value, total in session.execute(statement):
        counts[value] = total
    return counts


# ---------------------------------------------------------------------------
# Tile builders
# ---------------------------------------------------------------------------


def _opportunity_tile(
    session: Session, zones: Sequence[Classification], now: datetime
) -> OpportunityTile:
    """Pipeline counts, every statement carrying the clearance predicate."""
    readable = Opportunity.classification.in_(zones)
    return OpportunityTile(
        total=_count(session, select(func.count()).select_from(Opportunity).where(readable)),
        open_total=_count(
            session,
            select(func.count())
            .select_from(Opportunity)
            .where(readable, Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES)),
        ),
        by_stage=_grouped(session, Opportunity.stage, readable, OpportunityStage),
        overdue_next_action=_count(
            session,
            select(func.count())
            .select_from(Opportunity)
            .where(
                readable,
                Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES),
                Opportunity.next_action_at.is_not(None),
                Opportunity.next_action_at < now,
            ),
        ),
        # coalesce so an empty pipeline is 0.0 rather than None. A tile that renders "-"
        # where a number belongs reads as a bug on stage even when it is arithmetically
        # honest.
        pipeline_value_aud=float(
            session.scalar(
                select(func.coalesce(func.sum(Opportunity.value_estimate_aud), 0)).where(
                    readable, Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES)
                )
            )
            or 0
        ),
        ai_proposed=_count(
            session,
            select(func.count())
            .select_from(Opportunity)
            .where(
                readable,
                Opportunity.stage.in_(OPPORTUNITY_OPEN_STAGES),
                Opportunity.is_proposed_by_ai.is_(True),
            ),
        ),
    )


def _consular_tile(
    session: Session, zones: Sequence[Classification], now: datetime
) -> ConsularTile:
    """Case counts and the SLA clock.

    The SLA buckets deliberately ignore ``AWAITING_CITIZEN``: that status pauses the clock
    (``docs/OPEN_QUESTIONS.md`` Q-15), so counting a paused case as breached would put a
    red number on the dashboard for a case the mission is not holding up. It is reported
    on its own instead.
    """
    readable = Case.classification.in_(zones)
    running = Case.status.in_(CONSULAR_OPEN_STATUSES - {CaseStatus.AWAITING_CITIZEN})
    due_soon_before = now + timedelta(hours=SLA_DUE_SOON_HOURS)
    return ConsularTile(
        total=_count(session, select(func.count()).select_from(Case).where(readable)),
        open_total=_count(
            session,
            select(func.count())
            .select_from(Case)
            .where(readable, Case.status.in_(CONSULAR_OPEN_STATUSES)),
        ),
        by_status=_grouped(session, Case.status, readable, CaseStatus),
        sla_breached=_count(
            session,
            select(func.count())
            .select_from(Case)
            .where(readable, running, Case.sla_due_at.is_not(None), Case.sla_due_at < now),
        ),
        sla_due_soon=_count(
            session,
            select(func.count())
            .select_from(Case)
            .where(
                readable,
                running,
                Case.sla_due_at.is_not(None),
                Case.sla_due_at >= now,
                Case.sla_due_at < due_soon_before,
            ),
        ),
        awaiting_citizen=_count(
            session,
            select(func.count())
            .select_from(Case)
            .where(readable, Case.status == CaseStatus.AWAITING_CITIZEN),
        ),
    )


def _stakeholder_tile(
    session: Session, zones: Sequence[Classification], now: datetime
) -> StakeholderTile:
    """Relationship counts by strength."""
    readable = Stakeholder.classification.in_(zones)
    return StakeholderTile(
        total=_count(session, select(func.count()).select_from(Stakeholder).where(readable)),
        by_relationship_strength=_grouped(
            session, Stakeholder.relationship_strength, readable, RelationshipStrength
        ),
        never_contacted=_count(
            session,
            select(func.count())
            .select_from(Stakeholder)
            .where(readable, Stakeholder.last_contact_at.is_(None)),
        ),
        dormant=_count(
            session,
            select(func.count())
            .select_from(Stakeholder)
            .where(
                readable,
                Stakeholder.last_contact_at.is_not(None),
                Stakeholder.last_contact_at < now - DORMANT_AFTER,
            ),
        ),
        organisations=_count(
            session,
            select(func.count())
            .select_from(Organisation)
            .where(Organisation.classification.in_(zones)),
        ),
    )


def _diaspora_tile(session: Session, zones: Sequence[Classification]) -> DiasporaTile:
    """Diaspora counts by consent.

    Consent, not classification, is what gates a diaspora profile
    (``app.domain.enums.ConsentStatus``) -- but the clearance predicate is applied as well,
    because two gates that both apply are the whole of ADR-0003 rule 3. Tombstoned profiles
    are excluded everywhere: a withdrawal that still incremented a total would be a
    withdrawal in name only.
    """
    readable = DiasporaProfile.classification.in_(zones)
    live = DiasporaProfile.is_tombstoned.is_(False)
    return DiasporaTile(
        total=_count(
            session, select(func.count()).select_from(DiasporaProfile).where(readable, live)
        ),
        by_consent_status=_grouped(
            session, DiasporaProfile.consent_status, and_(readable, live), ConsentStatus
        ),
        contactable=_count(
            session,
            select(func.count())
            .select_from(DiasporaProfile)
            .where(
                readable,
                live,
                DiasporaProfile.consent_status == ConsentStatus.GIVEN_CONTACTABLE,
            ),
        ),
    )


def _intelligence_tile(
    session: Session, zones: Sequence[Classification], now: datetime
) -> IntelligenceTile:
    """Signal flow, with a seven-day window on ``detected_at``."""
    readable = Signal.classification.in_(zones)
    since = now - timedelta(days=SIGNAL_WINDOW_DAYS)
    return IntelligenceTile(
        total=_count(session, select(func.count()).select_from(Signal).where(readable)),
        recent_total=_count(
            session,
            select(func.count()).select_from(Signal).where(readable, Signal.detected_at >= since),
        ),
        by_status=_grouped(session, Signal.status, readable, SignalStatus),
        awaiting_triage=_count(
            session,
            select(func.count())
            .select_from(Signal)
            .where(readable, Signal.status == SignalStatus.NEW),
        ),
    )


def _meeting_tile(session: Session, zones: Sequence[Classification], now: datetime) -> MeetingTile:
    """The diary, and the follow-up queue that winning moment #2 lives in.

    ``followups_awaiting_approval`` counts ``OFFICER_REVIEW``: a draft that has been
    submitted and is waiting on a human decision. It is the number that should be non-zero
    on stage when the Ambassador is asked to approve something.

    Both follow-up counts are over ``meeting_followups`` rows, not meetings: since W3.2 a
    meeting may carry several follow-ups over its life (a discarded draft is kept, and a
    re-draft is a new row). Only one can be live at a time, so the two numbers still read as
    "meetings with a draft in this state".
    """
    readable = Meeting.classification.in_(zones)
    horizon = now + timedelta(days=UPCOMING_MEETING_DAYS)
    return MeetingTile(
        upcoming_total=_count(
            session,
            select(func.count())
            .select_from(Meeting)
            .where(readable, Meeting.scheduled_start >= now, Meeting.scheduled_start < horizon),
        ),
        followups_awaiting_approval=_count(
            session, _followup_count(zones, FollowupStatus.OFFICER_REVIEW)
        ),
        followups_drafted=_count(session, _followup_count(zones, FollowupStatus.DRAFTED)),
    )


def _followup_count(zones: Sequence[Classification], status: FollowupStatus) -> Select[tuple[int]]:
    """Count follow-ups in ``status`` whose own zone AND whose meeting's zone are readable.

    Both predicates sit in the ``WHERE`` clause of one joined statement (rule 2 above). The
    meeting's zone is not decoration: ADR-0006's dominant rule makes a follow-up at least as
    sensitive as the meeting it follows up, and a meeting raised to ``CONFIDENTIAL`` after
    its draft was recorded must stop incrementing a ``TRADE_OFFICER``'s count at once --
    without anybody having to remember to reclassify the follow-up row as well.
    """
    return (
        select(func.count())
        .select_from(MeetingFollowup)
        .join(Meeting, MeetingFollowup.meeting_id == Meeting.id)
        .where(
            MeetingFollowup.classification.in_(zones),
            Meeting.classification.in_(zones),
            MeetingFollowup.status == status,
        )
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_command_today(session: Session, principal: Principal) -> CommandToday:
    """Assemble the command centre for ``principal``.

    The permission checks here are **not** a second copy of the route's gate. The route
    requires ``read:command``, which is what admits a caller to the screen at all; each
    check below decides whether one tile is computed. Putting them in the service rather
    than the handler is ``CLAUDE.md`` rule 5 -- and it is what makes "the query never ran"
    true rather than "the field was blanked on the way out".
    """
    zones = readable_classifications_for(principal)
    now = datetime.now(UTC)
    visible: list[str] = []

    opportunities: OpportunityTile | None = None
    if principal.has(Permission.READ_OPPORTUNITY):
        opportunities = _opportunity_tile(session, zones, now)
        visible.append("opportunities")

    consular: ConsularTile | None = None
    if principal.has(Permission.READ_CONSULAR_CASE):
        consular = _consular_tile(session, zones, now)
        visible.append("consular")

    stakeholders: StakeholderTile | None = None
    if principal.has(Permission.READ_STAKEHOLDER):
        stakeholders = _stakeholder_tile(session, zones, now)
        visible.append("stakeholders")

    diaspora: DiasporaTile | None = None
    if principal.has(Permission.READ_DIASPORA_PROFILE):
        diaspora = _diaspora_tile(session, zones)
        visible.append("diaspora")

    intelligence: IntelligenceTile | None = None
    if principal.has(Permission.READ_INTELLIGENCE):
        intelligence = _intelligence_tile(session, zones, now)
        visible.append("intelligence")

    meetings: MeetingTile | None = None
    if principal.has(Permission.READ_MEETING):
        meetings = _meeting_tile(session, zones, now)
        visible.append("meetings")

    return CommandToday(
        generated_at=now,
        readable_classifications=zones,
        visible_tiles=visible,
        opportunities=opportunities,
        consular=consular,
        stakeholders=stakeholders,
        diaspora=diaspora,
        intelligence=intelligence,
        meetings=meetings,
    )
