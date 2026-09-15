"""``app/audit/actions.py`` -- the closed vocabulary, checked against the code it governs.

This module is the whole point of writing the vocabulary out as literals instead of
importing it from the modules that emit it. A derived vocabulary agrees with its sources by
construction and can never catch a drift; these assertions can, and they fail the build
rather than leaving a query silently incomplete.

Three directions are checked, and all three matter:

* **Nothing emits an action outside the vocabulary.** A row nobody can filter for is a row
  nobody will find in an investigation.
* **The vocabulary contains nothing that cannot be emitted.** A dead verb in a closed set
  is a reader being told a category exists when no row will ever carry it.
* **The shape rule holds** (``docs/workflows.md`` 0.4): ``<context>.<past_participle>``,
  lower ``snake_case`` on both halves.
"""

from __future__ import annotations

import re
from itertools import combinations
from typing import Final

import pytest

from app.audit.actions import (
    ACCESS_ACTIONS,
    AUDIT_ACTIONS,
    CONSULAR_CASE_ACTIONS,
    CONSULAR_CASE_TRANSITION_REJECTED,
    MEETING_FOLLOWUP_ACTIONS,
    MEETING_FOLLOWUP_DRAFTED,
    MEETING_FOLLOWUP_SENT,
    MEETING_FOLLOWUP_TRANSITION_REJECTED,
    OPPORTUNITY_ACTIONS,
    SESSION_ACTIONS,
    TRANSITION_REJECTED_SUFFIX,
    is_known_action,
    transition_rejected_action,
)
from app.audit.middleware import DEFAULT_RULES, MIDDLEWARE_ACTIONS
from app.services.cases import CASE_ACTIONS as CASE_EVENT_ACTIONS
from app.services.cases import CASE_MACHINE
from app.services.followups import FOLLOWUP_ACTIONS as FOLLOWUP_EVENT_ACTIONS
from app.services.followups import FOLLOWUP_MACHINE
from app.services.opportunities import OPPORTUNITY_ACTIONS as OPPORTUNITY_EVENT_ACTIONS
from app.services.opportunities import OPPORTUNITY_MACHINE
from app.services.session import SESSION_ROLE_ASSUMED

#: ``<context>.<past_participle>``, lower snake_case on both halves.
ACTION_SHAPE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")


# ---------------------------------------------------------------------------
# 1. Nothing emits an action outside the vocabulary
# ---------------------------------------------------------------------------


def test_the_middleware_emits_only_known_actions() -> None:
    """``MIDDLEWARE_ACTIONS`` names the four strings that module can write."""
    assert MIDDLEWARE_ACTIONS <= AUDIT_ACTIONS


def test_every_registered_rule_names_a_known_action() -> None:
    """A rule that named an unlisted verb would produce unfindable rows."""
    for rule in DEFAULT_RULES:
        assert is_known_action(rule.action), f"rule {rule.name!r} names {rule.action!r}"


def test_the_session_service_emits_a_known_action() -> None:
    """``session.role_assumed``, character for character (docs/workflows.md rule 0.4)."""
    assert SESSION_ROLE_ASSUMED in SESSION_ACTIONS
    assert SESSION_ROLE_ASSUMED in AUDIT_ACTIONS


def test_the_opportunity_machine_emits_only_known_actions() -> None:
    """Every action the machine can write, including its refusal action."""
    assert OPPORTUNITY_MACHINE.audit_actions() <= AUDIT_ACTIONS


def test_every_opportunity_event_action_is_in_the_vocabulary() -> None:
    """The event-to-action map in the service, checked value by value."""
    for event, action in OPPORTUNITY_EVENT_ACTIONS.items():
        assert is_known_action(action), f"event {event!r} writes unknown action {action!r}"


def test_the_followup_machine_emits_only_known_actions() -> None:
    """Every action the follow-up machine can write, including its refusal action."""
    assert FOLLOWUP_MACHINE.audit_actions() <= MEETING_FOLLOWUP_ACTIONS
    assert FOLLOWUP_MACHINE.audit_actions() <= AUDIT_ACTIONS


def test_every_followup_event_action_is_in_the_vocabulary() -> None:
    """The service's event map, value by value -- the creation event ``draft`` included."""
    for event, action in FOLLOWUP_EVENT_ACTIONS.items():
        assert action in MEETING_FOLLOWUP_ACTIONS, f"event {event!r} writes {action!r}"
    assert FOLLOWUP_EVENT_ACTIONS["draft"] == MEETING_FOLLOWUP_DRAFTED
    assert FOLLOWUP_EVENT_ACTIONS["send"] == MEETING_FOLLOWUP_SENT


def test_the_case_machine_emits_only_known_actions() -> None:
    """Every action the consular case machine can write, including its refusal action."""
    assert CASE_MACHINE.audit_actions() <= CONSULAR_CASE_ACTIONS
    assert CASE_MACHINE.audit_actions() <= AUDIT_ACTIONS


def test_every_case_event_action_is_in_the_vocabulary() -> None:
    """The service's event map, value by value -- ``intake`` included."""
    for event, action in CASE_EVENT_ACTIONS.items():
        assert action in CONSULAR_CASE_ACTIONS, f"event {event!r} writes {action!r}"


# ---------------------------------------------------------------------------
# 2. The vocabulary contains nothing dead
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_exactly_what_this_build_can_emit() -> None:
    """No dead verbs. Every string in the closed set has an emitter in this build.

    ``ACCESS_ACTIONS`` is the one group where a member -- ``export.performed`` -- has no
    registered route yet: the middleware writes it for any path with an ``/export``
    segment, which is a capability rather than a route, so it is emittable without being
    registered. ``meeting_followup.drafted`` is not a machine transition -- a follow-up that
    does not exist has no state to leave -- so it is emitted by the service's own event map.
    """
    emittable = (
        set(MIDDLEWARE_ACTIONS)
        | set(OPPORTUNITY_MACHINE.audit_actions())
        | set(OPPORTUNITY_EVENT_ACTIONS.values())
        | set(FOLLOWUP_MACHINE.audit_actions())
        | set(FOLLOWUP_EVENT_ACTIONS.values())
        | set(CASE_MACHINE.audit_actions())
        | set(CASE_EVENT_ACTIONS.values())
        | {SESSION_ROLE_ASSUMED}
    )
    assert emittable == AUDIT_ACTIONS


def test_the_groups_partition_the_vocabulary() -> None:
    """The five groups are pairwise disjoint and together are the whole set."""
    groups = (
        SESSION_ACTIONS,
        ACCESS_ACTIONS,
        OPPORTUNITY_ACTIONS,
        MEETING_FOLLOWUP_ACTIONS,
        CONSULAR_CASE_ACTIONS,
    )
    assert frozenset().union(*groups) == AUDIT_ACTIONS
    for left, right in combinations(groups, 2):
        assert not left & right


def test_close_and_dismiss_share_one_action() -> None:
    """Two events, one terminal stage, one verb. The event is in ``payload.event``."""
    assert OPPORTUNITY_EVENT_ACTIONS["close"] == OPPORTUNITY_EVENT_ACTIONS["dismiss"]
    assert len(set(OPPORTUNITY_EVENT_ACTIONS.values())) == len(OPPORTUNITY_ACTIONS) - 1


def test_every_followup_event_has_its_own_verb() -> None:
    """Eight events, eight verbs, plus the refusal action -- no synonyms in this machine."""
    verbs = set(FOLLOWUP_EVENT_ACTIONS.values())
    assert len(verbs) == len(FOLLOWUP_EVENT_ACTIONS) == 8
    assert verbs | {MEETING_FOLLOWUP_TRANSITION_REJECTED} == MEETING_FOLLOWUP_ACTIONS


# ---------------------------------------------------------------------------
# 3. Shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", sorted(AUDIT_ACTIONS))
def test_every_action_is_dotted_snake_case(action: str) -> None:
    assert ACTION_SHAPE.fullmatch(action), action


def test_the_rejection_action_helper_agrees_with_the_state_machines() -> None:
    """One concept, one spelling. The machines and the vocabulary must not diverge."""
    assert transition_rejected_action("opportunity") == OPPORTUNITY_MACHINE.rejected_action
    assert OPPORTUNITY_MACHINE.rejected_action.endswith(TRANSITION_REJECTED_SUFFIX)
    assert transition_rejected_action("meeting_followup") == FOLLOWUP_MACHINE.rejected_action
    assert FOLLOWUP_MACHINE.rejected_action == MEETING_FOLLOWUP_TRANSITION_REJECTED
    assert transition_rejected_action("case") == CASE_MACHINE.rejected_action
    assert CASE_MACHINE.rejected_action == CONSULAR_CASE_TRANSITION_REJECTED


def test_an_unknown_action_is_not_known() -> None:
    """The negative case, so the predicate is not vacuously true."""
    assert not is_known_action("opportunity.moved")
    assert not is_known_action("meeting_followup.deleted")
    assert not is_known_action("")
