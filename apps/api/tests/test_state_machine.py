"""The generic workflow executor: its constructor, its W3.2 extensions, and the machines' shape.

Mostly pure. Everything in sections 1 to 4 is a property of the *tables* or of the executor's
contract -- that a machine cannot be built unless it transcribes its domain table exactly,
that an event authorization is validated at construction, that terminal states have no
outgoing rule, that the cartesian product of states and events contains nothing legal beyond
the table. Those are the properties ``docs/workflows.md`` section 4 lists as tests 1, 2 and 5,
and they are worth asserting without a database because a machine whose shape is wrong is
wrong before any row exists.

Section 5 needs Postgres and is marked ``integration``: it is where the executor's ordering is
proven by the DENY rows it writes -- an event authorization refusing before the table lookup,
a terminal state still answering first, and an idempotent no-op no longer handed to a role
without the permission. Behaviour specific to a real machine lives with that machine, in
``tests/test_opportunities.py`` and ``tests/test_followups.py``.

The toy ``Lamp`` machine below exists so the constructor's rejections and the executor's
ordering can be provoked deliberately. Provoking them on a real machine would mean writing a
deliberately broken transcription of a binding document, which is exactly the thing a reader
should never find in this repository.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    ApprovalRequiredError,
    ClassificationDeniedError,
    InvalidTransitionError,
    PermissionDeniedError,
)
from app.domain.enums import (
    OPPORTUNITY_TERMINAL,
    OPPORTUNITY_TRANSITIONS,
    Classification,
    OpportunityStage,
    PolicyResult,
    RoleCode,
)
from app.models.governance import AuditEvent
from app.security.permissions import Permission
from app.security.principal import Principal, demo_persona, principal_for_role
from app.services.opportunities import OPPORTUNITY_ACTIONS, OPPORTUNITY_MACHINE
from app.services.session import ensure_persona_user
from app.services.state_machine import (
    AUTHORIZATION_RESERVED_EXTRA,
    DENIAL_ILLEGAL_TRANSITION,
    DENIAL_INSUFFICIENT_CLEARANCE,
    DENIAL_MISSING_PERMISSION,
    DENIAL_TERMINAL_STATE,
    TRANSITION_PAYLOAD_KEYS,
    TRANSITION_REJECTED_SUFFIX,
    AuthorizationFailure,
    StateMachine,
    TransitionContext,
    TransitionRule,
    ai_actor_scope,
    apply_event,
    execute_transition,
    is_ai_actor,
)


class Colour(Enum):
    """A toy state enum.

    A plain ``Enum``, not the ``(str, Enum)`` shape the domain uses: the executor is generic
    over ``Enum`` and touches only ``.value``, and using the narrower type here proves that.
    """

    RED = "RED"
    GREEN = "GREEN"
    SCRAPPED = "SCRAPPED"


@dataclass
class Lamp:
    id: uuid.UUID
    colour: Colour
    classification: Classification = Classification.PUBLIC
    locked: bool = False


LAMP_TABLE: Final[dict[tuple[Colour, str], Colour]] = {
    (Colour.RED, "go"): Colour.GREEN,
    (Colour.GREEN, "stop"): Colour.RED,
    (Colour.RED, "scrap"): Colour.SCRAPPED,
}
LAMP_TERMINAL: Final[frozenset[Colour]] = frozenset({Colour.SCRAPPED})

_LampAuthorization = Callable[[TransitionContext[Lamp]], AuthorizationFailure | None]


def _lamp_rule(
    from_state: Colour,
    event: str,
    to_state: Colour,
    action: str = "lamp.changed",
    *,
    permission: Permission = Permission.READ_COMMAND,
    audit_detail: Callable[[TransitionContext[Lamp]], Mapping[str, Any]] | None = None,
) -> TransitionRule[Colour, Lamp]:
    return TransitionRule(
        from_state=from_state,
        event=event,
        to_state=to_state,
        permission=permission,
        audit_action=action,
        audit_detail=audit_detail,
    )


def _write_colour(lamp: Lamp, colour: Colour, _moment: datetime) -> None:
    lamp.colour = colour


def _lamp_machine(
    rules: dict[tuple[Colour, str], TransitionRule[Colour, Lamp]],
    *,
    transitions: dict[tuple[Colour, str], Colour] | None = None,
    idempotent_events: frozenset[str] = frozenset(),
    event_authorizations: dict[str, _LampAuthorization] | None = None,
) -> StateMachine[Colour, Lamp]:
    return StateMachine(
        name="lamp",
        object_type="test.lamp",
        action_prefix="lamp",
        table_name="lamps",
        transitions=LAMP_TABLE if transitions is None else transitions,
        terminal_states=LAMP_TERMINAL,
        rules=rules,
        read_state=lambda lamp: lamp.colour,
        write_state=_write_colour,
        object_id_of=lambda lamp: lamp.id,
        classification_of=lambda lamp: lamp.classification,
        idempotent_events=idempotent_events,
        event_authorizations=MappingProxyType(dict(event_authorizations or {})),
    )


def _complete_lamp_rules() -> dict[tuple[Colour, str], TransitionRule[Colour, Lamp]]:
    return {key: _lamp_rule(key[0], key[1], to_state) for key, to_state in LAMP_TABLE.items()}


def _never_refuses(_context: TransitionContext[Lamp]) -> AuthorizationFailure | None:
    return None


# ---------------------------------------------------------------------------
# 1. A machine is a transcription, and the constructor proves it
# ---------------------------------------------------------------------------


def test_a_complete_transcription_builds() -> None:
    machine = _lamp_machine(_complete_lamp_rules())
    assert machine.events == {"go", "stop", "scrap"}
    assert machine.states == {Colour.RED, Colour.GREEN, Colour.SCRAPPED}


def test_a_rule_the_domain_table_does_not_permit_is_refused() -> None:
    """The failure that would silently widen a machine past its binding document."""
    rules = _complete_lamp_rules()
    rules[(Colour.GREEN, "scrap")] = _lamp_rule(Colour.GREEN, "scrap", Colour.SCRAPPED)
    with pytest.raises(ValueError, match="does not permit"):
        _lamp_machine(rules)


def test_a_missing_rule_is_refused() -> None:
    """A transition with no permission and no audit action is a transition nobody can fire."""
    rules = _complete_lamp_rules()
    del rules[(Colour.RED, "go")]
    with pytest.raises(ValueError, match="no rule"):
        _lamp_machine(rules)


def test_a_rule_that_disagrees_with_the_table_about_its_target_is_refused() -> None:
    rules = _complete_lamp_rules()
    rules[(Colour.RED, "go")] = _lamp_rule(Colour.RED, "go", Colour.SCRAPPED)
    with pytest.raises(ValueError, match="the domain table says"):
        _lamp_machine(rules)


def test_a_transition_out_of_a_terminal_state_is_refused() -> None:
    """A terminal state accepts nothing, and the constructor will not let one pretend to.

    This needs a doctored *table*, not merely a doctored rule: a rule the table does not
    contain is already refused by the check above, so the only way a machine could ever
    leave a terminal state is a domain table that disagreed with its own terminal set.
    """
    table = dict(LAMP_TABLE)
    table[(Colour.SCRAPPED, "go")] = Colour.GREEN
    rules = _complete_lamp_rules()
    rules[(Colour.SCRAPPED, "go")] = _lamp_rule(Colour.SCRAPPED, "go", Colour.GREEN)
    with pytest.raises(ValueError, match="terminal and accepts no events"):
        _lamp_machine(rules, transitions=table)


def test_an_action_outside_the_machines_namespace_is_refused() -> None:
    """``audit_events.action`` is a closed vocabulary; a stray prefix is a row nobody finds."""
    rules = _complete_lamp_rules()
    rules[(Colour.RED, "go")] = _lamp_rule(Colour.RED, "go", Colour.GREEN, action="bulb.lit")
    with pytest.raises(ValueError, match="closed vocabulary"):
        _lamp_machine(rules)


def test_an_idempotent_event_the_machine_does_not_have_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown events"):
        _lamp_machine(_complete_lamp_rules(), idempotent_events=frozenset({"flicker"}))


def test_targets_of_and_rejected_action() -> None:
    machine = _lamp_machine(_complete_lamp_rules())
    assert machine.targets_of("go") == {Colour.GREEN}
    assert machine.targets_of("nonsense") == frozenset()
    assert machine.rejected_action == f"lamp.{TRANSITION_REJECTED_SUFFIX}"
    assert machine.rejected_action in machine.audit_actions()


def test_rules_for_returns_every_rule_of_one_event() -> None:
    machine = _lamp_machine(_complete_lamp_rules())
    assert [rule.from_state for rule in machine.rules_for("go")] == [Colour.RED]
    assert machine.rules_for("nonsense") == ()


def test_write_state_receives_the_transition_instant() -> None:
    """The accessor contract the opportunity machine relies on to stamp its ageing clock."""
    machine = _lamp_machine(_complete_lamp_rules())
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.RED)
    machine.write_state(lamp, Colour.GREEN, datetime.now(UTC))
    assert lamp.colour is Colour.GREEN


# ---------------------------------------------------------------------------
# 2. Event authorizations and audit detail are validated where they can be
# ---------------------------------------------------------------------------

#: A table in which one event, ``scrap``, is legal from two states -- the shape needed to
#: provoke the "one permission per authorized event" rule.
TWO_WAY_SCRAP_TABLE: Final[dict[tuple[Colour, str], Colour]] = {
    **LAMP_TABLE,
    (Colour.GREEN, "scrap"): Colour.SCRAPPED,
}


def _two_way_scrap_rules(
    green_scrap_permission: Permission,
) -> dict[tuple[Colour, str], TransitionRule[Colour, Lamp]]:
    rules = {
        key: _lamp_rule(key[0], key[1], to_state) for key, to_state in TWO_WAY_SCRAP_TABLE.items()
    }
    rules[(Colour.GREEN, "scrap")] = _lamp_rule(
        Colour.GREEN, "scrap", Colour.SCRAPPED, permission=green_scrap_permission
    )
    return rules


def test_an_authorization_for_an_event_the_machine_does_not_have_is_refused() -> None:
    with pytest.raises(ValueError, match="event_authorizations names unknown events"):
        _lamp_machine(_complete_lamp_rules(), event_authorizations={"flicker": _never_refuses})


def test_an_authorized_event_whose_rules_disagree_about_permission_is_refused() -> None:
    """The authorization runs before the table lookup, so it needs one permission to check."""
    rules = _two_way_scrap_rules(Permission.COMMIT_OPPORTUNITY)
    with pytest.raises(ValueError, match="exactly one permission"):
        _lamp_machine(
            rules, transitions=TWO_WAY_SCRAP_TABLE, event_authorizations={"scrap": _never_refuses}
        )


def test_an_authorized_event_whose_rules_agree_builds() -> None:
    rules = _two_way_scrap_rules(Permission.READ_COMMAND)
    machine = _lamp_machine(
        rules, transitions=TWO_WAY_SCRAP_TABLE, event_authorizations={"scrap": _never_refuses}
    )
    assert set(machine.event_authorizations) == {"scrap"}


def test_the_same_disagreement_is_fine_on_an_unauthorized_event() -> None:
    """The rule is opt-in: an event with no authorization may require different permissions."""
    machine = _lamp_machine(
        _two_way_scrap_rules(Permission.COMMIT_OPPORTUNITY), transitions=TWO_WAY_SCRAP_TABLE
    )
    assert len(machine.rules_for("scrap")) == 2


def test_the_opportunity_machine_declares_no_event_authorizations() -> None:
    """Opt-in: the machine that predates the extension runs exactly the order it always did."""
    assert dict(OPPORTUNITY_MACHINE.event_authorizations) == {}
    assert isinstance(OPPORTUNITY_MACHINE.event_authorizations, MappingProxyType)


@pytest.mark.parametrize("key", sorted(AUTHORIZATION_RESERVED_EXTRA))
def test_an_authorization_failure_may_not_overwrite_what_the_executor_reports(key: str) -> None:
    with pytest.raises(ValueError, match="may not set"):
        AuthorizationFailure(denial_reason="locked", detail="Locked.", extra={key: "forged"})


def test_an_authorization_failure_defaults_to_a_plain_permission_denial() -> None:
    failure = AuthorizationFailure(denial_reason="locked", detail="Locked.")
    assert failure.error is PermissionDeniedError
    assert dict(failure.extra) == {}


class _ClockOnlySession:
    """Answers the executor's ``SELECT now()`` and nothing else.

    Enough to drive :func:`apply_event` to the point where it merges a rule's audit detail,
    which is before it writes anything. If a future change reorders that, this stand-in has
    no ``add`` or ``flush`` and the test fails loudly rather than passing by accident.
    """

    def scalar(self, statement: object) -> datetime:
        del statement
        return datetime.now(UTC)


@pytest.mark.parametrize("key", sorted(TRANSITION_PAYLOAD_KEYS))
def test_audit_detail_may_not_replace_a_fixed_payload_key(key: str) -> None:
    """``docs/workflows.md`` 0.5 fixes the transition payload; a rule may add, never rewrite."""
    rules = _complete_lamp_rules()
    rules[(Colour.RED, "go")] = _lamp_rule(
        Colour.RED, "go", Colour.GREEN, audit_detail=lambda _context: {key: "forged"}
    )
    machine = _lamp_machine(rules)
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.RED)

    with pytest.raises(ValueError, match="fixed transition payload owns"):
        apply_event(
            cast(Session, _ClockOnlySession()),
            machine,
            lamp,
            event="go",
            actor=principal_for_role(RoleCode.DEPUTY),
        )


# ---------------------------------------------------------------------------
# 3. The non-autonomy seam (BUILD_BIBLE.md section 6)
# ---------------------------------------------------------------------------


def test_ai_actor_scope_is_off_by_default_and_restores_itself() -> None:
    assert is_ai_actor() is False
    with ai_actor_scope():
        assert is_ai_actor() is True
    assert is_ai_actor() is False


def test_ai_actor_scope_restores_itself_when_the_block_raises() -> None:
    """A control that stays armed after an exception would block every later transition."""
    with pytest.raises(RuntimeError), ai_actor_scope():
        raise RuntimeError("gateway blew up")
    assert is_ai_actor() is False


# ---------------------------------------------------------------------------
# 4. The opportunity machine's shape (docs/workflows.md section 4, tests 1, 2 and 5)
# ---------------------------------------------------------------------------


def test_the_opportunity_machine_uses_the_domain_table_itself() -> None:
    """Not a copy of it. A copy is a thing that can drift; this cannot."""
    assert OPPORTUNITY_MACHINE.transitions is OPPORTUNITY_TRANSITIONS
    assert OPPORTUNITY_MACHINE.terminal_states is OPPORTUNITY_TERMINAL


def test_every_documented_pair_produces_the_documented_target() -> None:
    for (from_stage, event), to_stage in OPPORTUNITY_TRANSITIONS.items():
        rule = OPPORTUNITY_MACHINE.rules[(from_stage, event)]
        assert rule.to_state is to_stage, f"{from_stage.value}/{event}"


def test_no_pair_outside_the_table_is_legal() -> None:
    """The cartesian product of stages and events, asserted exhaustively.

    ``docs/workflows.md`` section 4, test 2. 8 stages x 9 events = 72 pairs, of which 17 are
    legal; this asserts the other 55 have no rule and therefore no permission, no audit
    action and no path to a state write.
    """
    legal = set(OPPORTUNITY_TRANSITIONS)
    checked = 0
    for stage in OpportunityStage:
        for event in OPPORTUNITY_MACHINE.events:
            checked += 1
            if (stage, event) in legal:
                continue
            assert OPPORTUNITY_MACHINE.rules.get((stage, event)) is None
    assert checked == len(OpportunityStage) * len(OPPORTUNITY_MACHINE.events)


def test_terminal_stages_have_no_outgoing_rule() -> None:
    """``docs/workflows.md`` 0.6: neither PARTNERED nor CLOSED is reopenable."""
    assert set(OPPORTUNITY_TERMINAL) == {OpportunityStage.PARTNERED, OpportunityStage.CLOSED}
    for stage in OPPORTUNITY_TERMINAL:
        for event in OPPORTUNITY_MACHINE.events:
            assert (stage, event) not in OPPORTUNITY_MACHINE.rules


def test_partnered_is_reachable_only_by_the_commitment_control() -> None:
    """The opportunity machine's counterpart of the SENT invariant.

    A partnership is a commitment (``BUILD_BIBLE.md`` section 6). Exactly one rule reaches
    ``PARTNERED``, it requires ``commit:opportunity``, and it is marked non-autonomous.
    """
    reaching = [
        rule
        for rule in OPPORTUNITY_MACHINE.rules.values()
        if rule.to_state is OpportunityStage.PARTNERED
    ]
    assert len(reaching) == 1
    only = reaching[0]
    assert only.from_state is OpportunityStage.NEGOTIATION
    assert only.event == "partner"
    assert only.permission is Permission.COMMIT_OPPORTUNITY
    assert only.is_non_autonomous_control is True


def test_every_route_to_closed_demands_a_reason() -> None:
    """``docs/workflows.md`` section 1: CLOSED always carries a reason, and stores it."""
    for rule in OPPORTUNITY_MACHINE.rules.values():
        if rule.to_state is OpportunityStage.CLOSED:
            assert rule.requires_reason is True, rule.event
            assert rule.reason_column == "closed_reason", rule.event


def test_revert_is_never_treated_as_idempotent() -> None:
    """A second revert means 'step back again', never 'already done'.

    ``revert`` targets states it can also be fired from, so a *derived* idempotency rule
    would swallow the second correction silently. The allowlist is why it does not.
    """
    assert "revert" not in OPPORTUNITY_MACHINE.idempotent_events
    assert OPPORTUNITY_MACHINE.idempotent_events <= OPPORTUNITY_MACHINE.events


def test_every_event_has_an_action_and_every_action_is_namespaced() -> None:
    for event in OPPORTUNITY_MACHINE.events:
        assert event in OPPORTUNITY_ACTIONS
    for action in OPPORTUNITY_MACHINE.audit_actions():
        assert action.startswith("opportunity.")


# ---------------------------------------------------------------------------
# 5. The executor's ordering, proven by the rows it writes (integration)
# ---------------------------------------------------------------------------

LOCKED_DETAIL: Final[str] = "The lamp is locked; somebody else must unlock it first."


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose enclosing transaction is always rolled back.

    ``audit_events`` is append-only (ADR-0004): a DENY row committed for real by this suite
    could never be removed. ``join_transaction_mode="create_savepoint"`` lets the executor
    commit its refusals while this fixture still discards everything at the end.
    """
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _actor(session: Session, role: RoleCode) -> Principal:
    """A principal whose ``users`` row exists, as the audit foreign key demands."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    return principal_for_role(role)


def _lamp_rows(session: Session, lamp: Lamp) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.object_id == lamp.id)))


def _locked_lamps_refuse(calls: list[str]) -> _LampAuthorization:
    """An authorization that refuses a locked lamp, and records that it was asked."""

    def authorize(context: TransitionContext[Lamp]) -> AuthorizationFailure | None:
        calls.append(context.event)
        if not context.obj.locked:
            return None
        return AuthorizationFailure(
            denial_reason="lamp_locked",
            detail=LOCKED_DETAIL,
            extra={"lock": "engaged"},
            error=ApprovalRequiredError,
        )

    return authorize


def _authorized_lamp_machine(
    calls: list[str], *, go_permission: Permission = Permission.COMMIT_OPPORTUNITY
) -> StateMachine[Colour, Lamp]:
    """``go`` needs ``go_permission`` (AMBASSADOR and DEPUTY by default) and an unlocked lamp."""
    rules = _complete_lamp_rules()
    rules[(Colour.RED, "go")] = _lamp_rule(
        Colour.RED, "go", Colour.GREEN, "lamp.lit", permission=go_permission
    )
    return _lamp_machine(rules, event_authorizations={"go": _locked_lamps_refuse(calls)})


@pytest.mark.integration
def test_an_event_authorization_refuses_before_the_table_with_the_events_own_action(
    db: Session,
) -> None:
    """``go`` is not legal from GREEN, yet a locked lamp answers 403 -- not 409 -- first.

    That ordering is the whole extension: the refusal says what is missing (the unlock), and
    the DENY row is filed under the event's own action rather than the rejection action.
    """
    deputy = _actor(db, RoleCode.DEPUTY)
    calls: list[str] = []
    machine = _authorized_lamp_machine(calls)
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.GREEN, locked=True)

    with pytest.raises(ApprovalRequiredError) as raised:
        execute_transition(db, machine, lamp, event="go", actor=deputy)

    error = raised.value
    assert error.status_code == 403
    assert error.detail == LOCKED_DETAIL
    assert error.extra["reason"] == "lamp_locked"
    assert error.extra["lock"] == "engaged"
    assert error.extra["from_state"] == Colour.GREEN.value
    assert error.extra["required_permissions"] == [Permission.COMMIT_OPPORTUNITY.value]
    assert calls == ["go"]
    assert lamp.colour is Colour.GREEN

    rows = _lamp_rows(db, lamp)
    assert len(rows) == 1
    row = rows[0]
    assert row.policy_result is PolicyResult.DENY
    assert row.action == "lamp.lit"
    assert row.object_type == "test.lamp"
    assert row.payload["denial_reason"] == "lamp_locked"
    assert row.payload["detail"] == LOCKED_DETAIL
    assert row.payload["required_permission"] == Permission.COMMIT_OPPORTUNITY.value


@pytest.mark.integration
def test_a_passing_authorization_leaves_the_decision_to_the_table(db: Session) -> None:
    """Unlocked, ``go`` from GREEN is simply illegal again: 409 and the rejection action."""
    deputy = _actor(db, RoleCode.DEPUTY)
    calls: list[str] = []
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.GREEN)

    with pytest.raises(InvalidTransitionError) as raised:
        execute_transition(db, _authorized_lamp_machine(calls), lamp, event="go", actor=deputy)

    assert raised.value.extra["reason"] == DENIAL_ILLEGAL_TRANSITION
    assert calls == ["go"]
    assert [row.action for row in _lamp_rows(db, lamp)] == ["lamp.transition_rejected"]


@pytest.mark.integration
def test_an_authorized_event_still_demands_its_permission_before_asking(db: Session) -> None:
    """A caller who may not fire the event learns nothing from the authorization's answer."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    calls: list[str] = []
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.RED, locked=True)

    with pytest.raises(PermissionDeniedError) as raised:
        execute_transition(db, _authorized_lamp_machine(calls), lamp, event="go", actor=trade)

    assert type(raised.value) is PermissionDeniedError
    assert raised.value.extra["reason"] == DENIAL_MISSING_PERMISSION
    assert calls == []
    row = _lamp_rows(db, lamp)[0]
    assert row.action == "lamp.lit"
    assert row.payload["denial_reason"] == DENIAL_MISSING_PERMISSION


@pytest.mark.integration
def test_an_authorized_event_still_demands_clearance_before_asking(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    calls: list[str] = []
    machine = _authorized_lamp_machine(calls, go_permission=Permission.ADVANCE_OPPORTUNITY)
    lamp = Lamp(
        id=uuid.uuid4(), colour=Colour.RED, locked=True, classification=Classification.CONFIDENTIAL
    )

    with pytest.raises(ClassificationDeniedError):
        execute_transition(db, machine, lamp, event="go", actor=trade)

    assert calls == []
    assert _lamp_rows(db, lamp)[0].payload["denial_reason"] == DENIAL_INSUFFICIENT_CLEARANCE


@pytest.mark.integration
def test_a_terminal_state_answers_before_any_authorization(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    calls: list[str] = []
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.SCRAPPED, locked=True)

    with pytest.raises(InvalidTransitionError) as raised:
        execute_transition(db, _authorized_lamp_machine(calls), lamp, event="go", actor=deputy)

    assert raised.value.extra["reason"] == DENIAL_TERMINAL_STATE
    assert calls == []


def _idempotent_lamp_machine(permission: Permission) -> StateMachine[Colour, Lamp]:
    rules = _complete_lamp_rules()
    rules[(Colour.RED, "go")] = _lamp_rule(
        Colour.RED, "go", Colour.GREEN, "lamp.lit", permission=permission
    )
    return _lamp_machine(rules, idempotent_events=frozenset({"go"}))


@pytest.mark.integration
def test_an_idempotent_no_op_is_refused_to_an_actor_without_the_permission(db: Session) -> None:
    """The W3.2 hardening. Before it, the TRADE_OFFICER here got a 200 and the lamp's state."""
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    deputy = _actor(db, RoleCode.DEPUTY)
    machine = _idempotent_lamp_machine(Permission.COMMIT_OPPORTUNITY)
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.GREEN)

    with pytest.raises(PermissionDeniedError) as raised:
        execute_transition(db, machine, lamp, event="go", actor=trade)

    assert raised.value.extra["reason"] == DENIAL_MISSING_PERMISSION
    assert raised.value.extra["required_permissions"] == [Permission.COMMIT_OPPORTUNITY.value]
    rows = _lamp_rows(db, lamp)
    assert len(rows) == 1
    assert rows[0].policy_result is PolicyResult.DENY
    assert rows[0].action == "lamp.lit"

    outcome = execute_transition(db, machine, lamp, event="go", actor=deputy)
    assert outcome.applied is False
    assert outcome.audit_event_id is None
    assert len(_lamp_rows(db, lamp)) == 1


@pytest.mark.integration
def test_an_idempotent_no_op_is_refused_to_an_actor_without_clearance(db: Session) -> None:
    trade = _actor(db, RoleCode.TRADE_OFFICER)
    machine = _idempotent_lamp_machine(Permission.READ_COMMAND)
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.GREEN, classification=Classification.CONFIDENTIAL)

    with pytest.raises(ClassificationDeniedError):
        execute_transition(db, machine, lamp, event="go", actor=trade)

    assert _lamp_rows(db, lamp)[0].payload["denial_reason"] == DENIAL_INSUFFICIENT_CLEARANCE


@pytest.mark.integration
def test_audit_detail_is_merged_after_the_fixed_keys_and_sees_the_new_state(db: Session) -> None:
    deputy = _actor(db, RoleCode.DEPUTY)
    rules = _complete_lamp_rules()
    rules[(Colour.RED, "go")] = _lamp_rule(
        Colour.RED,
        "go",
        Colour.GREEN,
        "lamp.lit",
        audit_detail=lambda context: {"colour_now": context.obj.colour.value, "bulbs": 2},
    )
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.RED)

    outcome = execute_transition(db, _lamp_machine(rules), lamp, event="go", actor=deputy)

    row = _lamp_rows(db, lamp)[0]
    assert row.id == outcome.audit_event_id
    assert row.payload["colour_now"] == Colour.GREEN.value
    assert row.payload["bulbs"] == 2
    assert row.payload["from_state"] == Colour.RED.value
    assert row.payload["to_state"] == Colour.GREEN.value
    assert TRANSITION_PAYLOAD_KEYS - {"reason_stored_in"} <= set(row.payload)
