"""The consular SLA clock (``app.domain.sla``): business days, paused on the citizen.

Pure tests, no database. Every instant is built in the mission calendar (UTC+10) so a
reader can check the weekday by eye; 14 September 2026 is a Monday.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import CaseStatus
from app.domain.sla import (
    MISSION_TIMEZONE,
    PauseInterval,
    SlaState,
    add_business_days,
    business_days_between,
    compute_sla,
    due_soon_threshold,
    pauses_from_transitions,
    subtract_business_days,
)


def _at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """An instant in September 2026, mission-local."""
    return datetime(2026, 9, day, hour, minute, tzinfo=MISSION_TIMEZONE)


MONDAY = 14
FRIDAY = 18
SATURDAY = 19
SUNDAY = 20
NEXT_MONDAY = 21


def test_a_weekday_counts_in_full_and_a_weekend_not_at_all() -> None:
    assert business_days_between(_at(MONDAY), _at(MONDAY + 1)) == pytest.approx(1.0)
    assert business_days_between(_at(SATURDAY), _at(NEXT_MONDAY)) == pytest.approx(0.0)
    assert business_days_between(_at(FRIDAY, 12), _at(NEXT_MONDAY, 12)) == pytest.approx(1.0)


def test_business_days_between_is_antisymmetric() -> None:
    forward = business_days_between(_at(MONDAY, 9), _at(FRIDAY, 17))
    assert business_days_between(_at(FRIDAY, 17), _at(MONDAY, 9)) == pytest.approx(-forward)


def test_adding_business_days_skips_the_weekend() -> None:
    """A two-day budget opened on Friday afternoon falls due on Tuesday afternoon."""
    due = add_business_days(_at(FRIDAY, 15), 2)
    assert due == _at(NEXT_MONDAY + 1, 15).astimezone(UTC)


def test_adding_from_a_weekend_starts_on_monday() -> None:
    assert add_business_days(_at(SATURDAY, 10), 0.5) == _at(NEXT_MONDAY, 12).astimezone(UTC)


@pytest.mark.parametrize(
    ("start", "days"),
    [
        (_at(MONDAY, 9), 1.0),
        (_at(FRIDAY, 15), 2.0),
        (_at(MONDAY, 23, 30), 21.0),
        (_at(FRIDAY), 0.25),
    ],
)
def test_subtracting_inverts_adding(start: datetime, days: float) -> None:
    due = add_business_days(start, days)
    assert subtract_business_days(due, days) == start.astimezone(UTC)
    assert business_days_between(start, due) == pytest.approx(days)


def test_subtracting_across_a_weekend_lands_on_friday() -> None:
    assert subtract_business_days(_at(NEXT_MONDAY, 6), 0.5) == _at(FRIDAY, 18).astimezone(UTC)


def test_a_running_case_inside_its_budget_is_on_track() -> None:
    snapshot = compute_sla(
        clock_started_at=_at(MONDAY, 9),
        budget_business_days=21,
        pauses=(),
        now=_at(FRIDAY, 9),
    )
    assert snapshot.state is SlaState.ON_TRACK
    assert snapshot.elapsed_business_days == pytest.approx(4.0)
    assert snapshot.remaining_business_days == pytest.approx(17.0)
    assert snapshot.due_at is not None
    assert business_days_between(_at(MONDAY, 9), snapshot.due_at) == pytest.approx(21.0)


def test_near_the_end_of_its_budget_a_case_is_due_soon() -> None:
    """An emergency travel document: 2 days, measured with a few hours left."""
    snapshot = compute_sla(
        clock_started_at=_at(MONDAY, 9),
        budget_business_days=2,
        pauses=(),
        now=_at(MONDAY + 2, 3),
    )
    assert snapshot.state is SlaState.DUE_SOON
    assert 0 < (snapshot.remaining_business_days or 0) <= due_soon_threshold(2)


def test_the_due_soon_threshold_is_relative_and_capped() -> None:
    assert due_soon_threshold(2) == pytest.approx(0.5)
    assert due_soon_threshold(21) == pytest.approx(2.0)


def test_past_its_budget_a_case_is_breached() -> None:
    snapshot = compute_sla(
        clock_started_at=_at(MONDAY, 9),
        budget_business_days=2,
        pauses=(),
        now=_at(FRIDAY, 9),
    )
    assert snapshot.state is SlaState.BREACHED
    assert (snapshot.remaining_business_days or 0) < 0


def test_the_clock_pauses_while_the_case_waits_on_the_citizen() -> None:
    """Q-15: paused business time neither counts as elapsed nor shortens the budget."""
    pauses = (PauseInterval(start=_at(MONDAY + 1, 9), end=None),)
    snapshot = compute_sla(
        clock_started_at=_at(MONDAY, 9),
        budget_business_days=2,
        pauses=pauses,
        now=_at(FRIDAY, 9),
    )
    assert snapshot.state is SlaState.PAUSED
    assert snapshot.is_paused is True
    assert snapshot.paused_since == _at(MONDAY + 1, 9).astimezone(UTC)
    assert snapshot.elapsed_business_days == pytest.approx(1.0)
    assert snapshot.paused_business_days == pytest.approx(3.0)


def test_a_finished_pause_extends_the_due_date_by_the_business_time_it_used() -> None:
    running = compute_sla(
        clock_started_at=_at(MONDAY, 9),
        budget_business_days=2,
        pauses=(),
        now=_at(MONDAY, 10),
    )
    paused = compute_sla(
        clock_started_at=_at(MONDAY, 9),
        budget_business_days=2,
        pauses=(PauseInterval(start=_at(MONDAY, 12), end=_at(MONDAY + 1, 12)),),
        now=_at(MONDAY + 1, 13),
    )
    assert running.due_at is not None and paused.due_at is not None
    assert business_days_between(running.due_at, paused.due_at) == pytest.approx(1.0)
    assert paused.state is not SlaState.PAUSED


def test_a_stopped_clock_reports_whether_the_budget_was_met() -> None:
    snapshot = compute_sla(
        clock_started_at=_at(MONDAY, 9),
        budget_business_days=21,
        pauses=(),
        now=_at(NEXT_MONDAY, 9),
        stopped_at=_at(FRIDAY, 9),
    )
    assert snapshot.state is SlaState.STOPPED
    assert snapshot.met is True
    assert snapshot.elapsed_business_days == pytest.approx(4.0)


def test_no_budget_is_not_set_rather_than_invented() -> None:
    snapshot = compute_sla(
        clock_started_at=_at(MONDAY, 9), budget_business_days=None, pauses=(), now=_at(FRIDAY)
    )
    assert snapshot.state is SlaState.NOT_SET
    assert snapshot.due_at is None
    assert snapshot.remaining_business_days is None


def test_pauses_are_paired_from_the_timeline_in_any_order() -> None:
    enter = _at(MONDAY, 9)
    leave = _at(MONDAY + 1, 9)
    again = _at(MONDAY + 2, 9)
    transitions = [
        (again, CaseStatus.IN_REVIEW, CaseStatus.AWAITING_CITIZEN),
        (leave, CaseStatus.AWAITING_CITIZEN, CaseStatus.IN_REVIEW),
        (enter, CaseStatus.ASSIGNED, CaseStatus.AWAITING_CITIZEN),
        (enter + timedelta(minutes=5), None, None),
    ]
    pauses = pauses_from_transitions(transitions)
    assert pauses == (
        PauseInterval(start=enter.astimezone(UTC), end=leave.astimezone(UTC)),
        PauseInterval(start=again.astimezone(UTC), end=None),
    )
