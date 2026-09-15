"""The consular service-level clock: business days, paused while the case waits on the citizen.

Pure data and arithmetic. No I/O, no SQLAlchemy -- ``app.domain`` must stay importable in
isolation -- so the case service, the dashboard, the Gateway context builder and a test all
compute the same answer from the same inputs.

Two rulings govern it (``docs/OPEN_QUESTIONS.md`` Q-15, resolved 2026-09-07):

* **The clock counts business days.** Saturday and Sunday in the mission's local calendar do
  not count. A two-day emergency travel document budget opened on a Friday afternoon is not
  breached on Sunday morning.
* **The clock pauses in ``AWAITING_CITIZEN``.** Delay attributable to the citizen must not
  count against the mission's service standard, so every interval spent waiting on the
  citizen extends the due date by exactly the business time it consumed.

**Business time is additive, and that is the whole model.** A business day is 24 hours of
weekday time in the mission's calendar; weekend time counts for nothing. Because business
time accumulates monotonically along the timeline, "the budget plus every paused interval"
is exact rather than approximate, and the due date is a single forward walk from the moment
the clock started.

**The mission calendar is a fixed UTC+10 offset (Canberra standard time), daylight saving
ignored.** Deliberately: the demo carries no timezone database dependency (``zoneinfo`` on
Windows needs the ``tzdata`` package), and an hour's drift twice a year does not move a day
boundary a consular officer would argue about. Public holidays are not modelled. Both are
stated here rather than discovered, and both are one function to change.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import Enum
from typing import Final

from app.domain.enums import CaseStatus

__all__ = [
    "BUSINESS_DAY",
    "MISSION_TIMEZONE",
    "PauseInterval",
    "SlaSnapshot",
    "SlaState",
    "add_business_days",
    "business_days_between",
    "compute_sla",
    "due_soon_threshold",
    "pauses_from_transitions",
    "subtract_business_days",
]

#: Canberra standard time. See the module docstring for why a fixed offset.
MISSION_TIMEZONE: Final[timezone] = timezone(timedelta(hours=10), "AEST")

#: One business day of elapsed weekday time.
BUSINESS_DAY: Final[timedelta] = timedelta(days=1)

#: Monday is 0 and Friday is 4 in ``datetime.weekday()``.
_LAST_WEEKDAY: Final[int] = 4

#: A case is DUE_SOON when its remaining business time falls to this share of its budget,
#: capped at two business days -- the "48 hours" the command centre speaks in. Relative,
#: because a flat two days would mark every two-day emergency case "due soon" from the
#: moment it arrived, and a signal that is always on is not a signal.
_DUE_SOON_SHARE: Final[float] = 0.25
_DUE_SOON_CAP_DAYS: Final[float] = 2.0

#: Floating-point business-day arithmetic works in seconds; this absorbs the last microsecond.
_EPSILON_SECONDS: Final[float] = 1e-6


class SlaState(str, Enum):  # noqa: UP042
    """Where a case stands against its service level. Metadata, never narrative."""

    ON_TRACK = "ON_TRACK"
    DUE_SOON = "DUE_SOON"
    BREACHED = "BREACHED"
    PAUSED = "PAUSED"
    #: The clock has stopped: the case was resolved or closed.
    STOPPED = "STOPPED"
    #: No budget applies -- an unknown case type.
    NOT_SET = "NOT_SET"


@dataclass(frozen=True, slots=True)
class PauseInterval:
    """One stretch of ``AWAITING_CITIZEN``. ``end`` is ``None`` while the case still waits."""

    start: datetime
    end: datetime | None


@dataclass(frozen=True, slots=True)
class SlaSnapshot:
    """The clock for one case at one instant, every figure in business days."""

    state: SlaState
    budget_business_days: int | None
    clock_started_at: datetime
    #: The instant the figures were measured at: now, or when the clock stopped.
    measured_at: datetime
    due_at: datetime | None
    elapsed_business_days: float
    paused_business_days: float
    remaining_business_days: float | None
    is_paused: bool
    paused_since: datetime | None
    #: For a stopped clock, whether the case finished inside its budget.
    met: bool | None


def _aware(moment: datetime) -> datetime:
    """Treat a naive datetime as UTC. Every column here is ``timestamptz``, so this is a guard."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _local(moment: datetime) -> datetime:
    return _aware(moment).astimezone(MISSION_TIMEZONE)


def _is_business(local: datetime) -> bool:
    return local.weekday() <= _LAST_WEEKDAY


def _next_midnight(local: datetime) -> datetime:
    return (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _midnight(local: datetime) -> datetime:
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def business_days_between(start: datetime, end: datetime) -> float:
    """Business days of elapsed weekday time from ``start`` to ``end``; negative if reversed."""
    if _aware(end) < _aware(start):
        return -business_days_between(end, start)
    cursor = _local(start)
    stop = _local(end)
    seconds = 0.0
    while cursor < stop:
        boundary = min(_next_midnight(cursor), stop)
        if _is_business(cursor):
            seconds += (boundary - cursor).total_seconds()
        cursor = boundary
    return seconds / BUSINESS_DAY.total_seconds()


def add_business_days(start: datetime, days: float) -> datetime:
    """The instant ``days`` of weekday time after ``start``; a negative ``days`` walks back."""
    if days < 0:
        return subtract_business_days(start, -days)
    remaining = days * BUSINESS_DAY.total_seconds()
    cursor = _local(start)
    while True:
        if not _is_business(cursor):
            cursor = _next_midnight(cursor)
            continue
        available = (_next_midnight(cursor) - cursor).total_seconds()
        if remaining <= available + _EPSILON_SECONDS:
            return (cursor + timedelta(seconds=remaining)).astimezone(UTC)
        remaining -= available
        cursor = _next_midnight(cursor)


def subtract_business_days(end: datetime, days: float) -> datetime:
    """The instant ``days`` of weekday time before ``end``. The inverse of the above."""
    if days < 0:
        return add_business_days(end, -days)
    remaining = days * BUSINESS_DAY.total_seconds()
    cursor = _local(end)
    while True:
        day_start = _midnight(cursor)
        if cursor == day_start:
            # Standing on a midnight, the business time before us is all of the previous day.
            previous_start = day_start - BUSINESS_DAY
            if _is_business(previous_start):
                available = BUSINESS_DAY.total_seconds()
                if remaining <= available + _EPSILON_SECONDS:
                    return (day_start - timedelta(seconds=remaining)).astimezone(UTC)
                remaining -= available
            cursor = previous_start
            continue
        if _is_business(cursor):
            available = (cursor - day_start).total_seconds()
            if remaining <= available + _EPSILON_SECONDS:
                return (cursor - timedelta(seconds=remaining)).astimezone(UTC)
            remaining -= available
        cursor = day_start


def due_soon_threshold(budget_business_days: int) -> float:
    """Remaining business days at or below which a running case is DUE_SOON."""
    return min(_DUE_SOON_CAP_DAYS, budget_business_days * _DUE_SOON_SHARE)


def pauses_from_transitions(
    transitions: Iterable[tuple[datetime, CaseStatus | None, CaseStatus | None]],
) -> tuple[PauseInterval, ...]:
    """Pair each entry into ``AWAITING_CITIZEN`` with the next exit from it.

    ``transitions`` are ``(occurred_at, from_status, to_status)`` triples from the case
    timeline, in any order; entries that are not transitions (a note, an SLA marker) carry
    ``None`` and are ignored. An entry with no matching exit is the pause still running.
    """
    ordered = sorted(
        ((_aware(at), before, after) for at, before, after in transitions if after is not None),
        key=lambda item: item[0],
    )
    pauses: list[PauseInterval] = []
    started: datetime | None = None
    for at, before, after in ordered:
        if after is CaseStatus.AWAITING_CITIZEN and started is None:
            started = at
        elif before is CaseStatus.AWAITING_CITIZEN and started is not None:
            pauses.append(PauseInterval(start=started, end=at))
            started = None
    if started is not None:
        pauses.append(PauseInterval(start=started, end=None))
    return tuple(pauses)


def compute_sla(
    *,
    clock_started_at: datetime,
    budget_business_days: int | None,
    pauses: Sequence[PauseInterval],
    now: datetime,
    stopped_at: datetime | None = None,
) -> SlaSnapshot:
    """Measure one case's clock.

    Args:
        clock_started_at: When the clock started: intake, or the most recent reopen (a reopen
            restarts the clock with a fresh budget, ``docs/workflows.md`` section 3 row 20).
        budget_business_days: The case type's ``default_sla_days``, read as business days.
        pauses: Every ``AWAITING_CITIZEN`` interval. Portions before the clock started or
            after it stopped are ignored.
        now: The instant to measure at.
        stopped_at: When the case was resolved or closed, if it has been.
    """
    start = _aware(clock_started_at)
    measured = _aware(stopped_at) if stopped_at is not None else _aware(now)
    if measured < start:
        measured = start

    paused = 0.0
    paused_since: datetime | None = None
    for pause in pauses:
        pause_start = max(_aware(pause.start), start)
        pause_end = measured if pause.end is None else min(_aware(pause.end), measured)
        if pause.end is None and stopped_at is None:
            paused_since = _aware(pause.start)
        if pause_end > pause_start:
            paused += business_days_between(pause_start, pause_end)

    elapsed = max(business_days_between(start, measured) - paused, 0.0)
    is_paused = paused_since is not None

    if budget_business_days is None:
        return SlaSnapshot(
            state=SlaState.NOT_SET,
            budget_business_days=None,
            clock_started_at=start,
            measured_at=measured,
            due_at=None,
            elapsed_business_days=elapsed,
            paused_business_days=paused,
            remaining_business_days=None,
            is_paused=is_paused,
            paused_since=paused_since,
            met=None,
        )

    remaining = budget_business_days - elapsed
    due_at = add_business_days(start, budget_business_days + paused)

    if stopped_at is not None:
        state = SlaState.STOPPED
    elif is_paused:
        state = SlaState.PAUSED
    elif remaining < 0:
        state = SlaState.BREACHED
    elif remaining <= due_soon_threshold(budget_business_days):
        state = SlaState.DUE_SOON
    else:
        state = SlaState.ON_TRACK

    return SlaSnapshot(
        state=state,
        budget_business_days=budget_business_days,
        clock_started_at=start,
        measured_at=measured,
        due_at=due_at,
        elapsed_business_days=elapsed,
        paused_business_days=paused,
        remaining_business_days=remaining,
        is_paused=is_paused,
        paused_since=paused_since,
        met=(remaining >= 0) if stopped_at is not None else None,
    )
