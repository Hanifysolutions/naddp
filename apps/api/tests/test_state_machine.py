"""The generic workflow executor, as pure data.

No database and no FastAPI here. Everything in this module is a property of the *tables* --
that a machine cannot be built unless it transcribes its domain table exactly, that terminal
states have no outgoing rule, that the cartesian product of states and events contains
nothing legal beyond the table. Those are the properties ``docs/workflows.md`` section 4
lists as tests 1, 2 and 5, and they are worth asserting without a database because a machine
whose shape is wrong is wrong before any row exists.

The behaviour that needs a session -- audit rows, denials, guards that query -- is in
``tests/test_opportunities.py``, marked ``integration``.

The toy ``Lamp`` machine below exists so the constructor's rejections can be provoked
deliberately. Provoking them on the real opportunity machine would mean writing a
deliberately broken transcription of a binding document, which is exactly the thing a reader
should never find in this repository.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Final

import pytest

from app.domain.enums import (
    OPPORTUNITY_TERMINAL,
    OPPORTUNITY_TRANSITIONS,
    Classification,
    OpportunityStage,
)
from app.security.permissions import Permission
from app.services.opportunities import OPPORTUNITY_ACTIONS, OPPORTUNITY_MACHINE
from app.services.state_machine import (
    TRANSITION_REJECTED_SUFFIX,
    StateMachine,
    TransitionRule,
    ai_actor_scope,
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


LAMP_TABLE: Final[dict[tuple[Colour, str], Colour]] = {
    (Colour.RED, "go"): Colour.GREEN,
    (Colour.GREEN, "stop"): Colour.RED,
    (Colour.RED, "scrap"): Colour.SCRAPPED,
}
LAMP_TERMINAL: Final[frozenset[Colour]] = frozenset({Colour.SCRAPPED})


def _lamp_rule(
    from_state: Colour,
    event: str,
    to_state: Colour,
    action: str = "lamp.changed",
) -> TransitionRule[Colour, Lamp]:
    return TransitionRule(
        from_state=from_state,
        event=event,
        to_state=to_state,
        permission=Permission.READ_COMMAND,
        audit_action=action,
    )


def _write_colour(lamp: Lamp, colour: Colour, _moment: datetime) -> None:
    lamp.colour = colour


def _lamp_machine(
    rules: dict[tuple[Colour, str], TransitionRule[Colour, Lamp]],
    *,
    transitions: dict[tuple[Colour, str], Colour] | None = None,
    idempotent_events: frozenset[str] = frozenset(),
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
    )


def _complete_lamp_rules() -> dict[tuple[Colour, str], TransitionRule[Colour, Lamp]]:
    return {key: _lamp_rule(key[0], key[1], to_state) for key, to_state in LAMP_TABLE.items()}


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


def test_write_state_receives_the_transition_instant() -> None:
    """The accessor contract the opportunity machine relies on to stamp its ageing clock."""
    machine = _lamp_machine(_complete_lamp_rules())
    lamp = Lamp(id=uuid.uuid4(), colour=Colour.RED)
    machine.write_state(lamp, Colour.GREEN, datetime.now(UTC))
    assert lamp.colour is Colour.GREEN


# ---------------------------------------------------------------------------
# 2. The non-autonomy seam (BUILD_BIBLE.md section 6)
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
# 3. The opportunity machine's shape (docs/workflows.md section 4, tests 1, 2 and 5)
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
