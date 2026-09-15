"""The generic workflow executor: the one place a governed state actually changes.

``docs/workflows.md`` section 4 asks for **one** executor shared by all three machines,
because the ordering it specifies -- table lookup, permission, guards, state write, audit --
is the security property, and three hand-rolled copies of an ordering is three chances to
get it wrong in a way nobody notices until an auditor does.

The shape of each machine already exists as pure data in ``app.domain.enums``
(``OPPORTUNITY_TRANSITIONS``, ``FOLLOWUP_TRANSITIONS``, ``CASE_TRANSITIONS`` and their
``*_TERMINAL`` sets). This module adds the parts a table of ``(from, event) -> to`` cannot
carry -- the required permission, the audit action, whether a reason is required, the
guards and the effects -- and executes them. It never re-derives a machine's shape: a
:class:`StateMachine` is rejected at import time unless its rules cover exactly the pairs
in the domain table and target exactly the states that table names. A drifted transcription
is an ``ImportError``, not a wrong answer at runtime.

Order of operations, from ``docs/workflows.md`` section 4 as amended in W3.2::

    clearance -> a recordable reason -> state precondition -> unknown event -> terminal state
        -> event authorization, only for an event that declares one:
               permission -> clearance -> the machine's own authorize callable
        -> table lookup
               (an idempotent no-op still demands the event's permission and clearance,
                and for a section 6 control that no Gateway call is on the stack)
        -> permission -> clearance -> non-autonomous control -> reason -> guards
        -> state write -> effects -> audit_events row

Everything before the state write raises without touching the object. Everything from the
state write onwards is in the caller's transaction, so an audit failure rolls the state
change back with it (ADR-0004).

**Clearance comes first, before anything that can reveal state.** A precondition refusal
says "the object is in X", a terminal refusal names the terminal state, an illegal pair
lists the events legal from here, and every later refusal document carries ``from_state``.
An actor not cleared to read the object must learn none of that, so the very first check
is clearance, and its 403 is built without ``from_state`` or ``legal_events``. The DENY row
keeps ``from_state``: only audit readers see it. The later clearance checks stay, as defence
in depth, and are now unreachable for an uncleared actor.

**A reason that cannot be recorded is refused, never altered.** Postgres cannot store a NUL
character in a JSONB payload or a text column, and a flush that tries turns a refusal into a
500 with no DENY row. Request schemas reject it with a 422; for every other path the executor
never writes such a reason into a DENY row (``None`` is recorded in its place) and refuses the
event with 409 ``invalid_reason``. It never strips the NUL and carries on, because the stored
reason would then differ from what the actor submitted.

**Event authorizations** (W3.2; ``docs/workflows.md`` section 2, "Authorisation beyond the
matrix"). Rule 0.1 answers every pair missing from a table with 409, and for most refusals
that is the honest answer. It is the wrong answer for ``send`` on a follow-up nobody has
approved: the sender may hold ``send:meeting_followup`` -- the matrix grants it widely on
purpose -- and what is missing is an approval, which is an *authorisation* outcome (403),
not an illegal pair. So a machine may attach an authorization to an event. It runs after
the terminal check, so a terminal object still answers 409 first (nothing about a ``SENT``
follow-up needs authorising), and before the table lookup, so the refusal is the same from
every non-terminal state and says what is missing. It is opt-in and generic: a machine that
declares none -- the opportunity machine -- executes exactly as before. The refusal is
audited like every other, under the event's *own* action, so "who tried to send this" is one
query over ``action`` whatever the outcome.

**The idempotent no-op is not a way around the gates.** Rule 0.8 makes re-firing a
satisfied event a 200 with no audit row. Until W3.2 that 200 was returned before any
permission check, so a role holding no permission for the event could fire it anyway and
read the object's state from the answer. The no-op now requires the actor to hold the
permission of at least one rule for the event and to be cleared for the object -- and, when
the event is a section 6 control, that no AI Gateway call is on the stack; otherwise it is a
DENY row and a 403, like any other refusal.

**A refusal carries the id of its DENY row.** Every :class:`~app.core.errors.AppError`
raised after a denial row was written carries that row's id as ``denial_audit_event_id``
-- an attribute, never a key of the serialised ``extra``. :func:`execute_transition` clears
it when committing the row fails. A service that reports "the refusal recorded in the audit
log" uses it rather than querying for the newest matching row, which could be another
request's.

**A denial is audited.** ``docs/workflows.md`` 0.3 requires a ``policy_result = DENY`` row
for a refused transition, and rule 0.1 makes an illegal transition a first-class refusal.
An attempted illegal transition is precisely what a security reviewer wants to see logged,
so this module writes the DENY row *before* raising -- and, because the caller's request
transaction is rolled back on the way out, :func:`execute_transition` commits it. That is
safe in a way an ALLOW commit would not be: a denial has no state change to be atomic with,
so committing the row alone cannot leave the object half-transitioned.

**Two vocabularies this module does not own.**

* ``action`` strings must match ``docs/workflows.md`` character for character and will be
  centralised in ``app/audit/actions.py`` (ADR-0004, owned by another track). Each machine
  declares its own here for now; the transcription test compares them against the document.
* :data:`TRANSITION_REJECTED_SUFFIX` mints ``<prefix>.transition_rejected`` for a refusal
  that has no rule to name -- an illegal pair, a terminal state, an unknown event. That
  string must be added to the closed vocabulary when it lands.

Binding sources: ``docs/workflows.md`` sections 0 and 4, ADR-0003 (RBAC), ADR-0004
(append-only audit), ADR-0006 (classification), ``CLAUDE.md`` rules 2.3, 2.4 and 5.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.writer import write_audit_event
from app.core.errors import (
    AppError,
    ClassificationDeniedError,
    InvalidTransitionError,
    PermissionDeniedError,
)
from app.core.logging import get_logger
from app.domain.enums import Classification, PolicyResult
from app.models.governance import AuditEvent
from app.security.permissions import Permission
from app.security.principal import Principal

__all__ = [
    "AUTHORIZATION_RESERVED_EXTRA",
    "DENIAL_AUTONOMOUS_ACTOR",
    "DENIAL_GUARD_FAILED",
    "DENIAL_ILLEGAL_TRANSITION",
    "DENIAL_INSUFFICIENT_CLEARANCE",
    "DENIAL_INVALID_REASON",
    "DENIAL_MISSING_PERMISSION",
    "DENIAL_MISSING_REASON",
    "DENIAL_REASON_TOO_LONG",
    "DENIAL_STATE_PRECONDITION",
    "DENIAL_TERMINAL_STATE",
    "DENIAL_UNKNOWN_EVENT",
    "REASON_MAX_LENGTH",
    "TRANSITION_PAYLOAD_KEYS",
    "TRANSITION_REJECTED_SUFFIX",
    "AuthorizationFailure",
    "StateMachine",
    "TransitionContext",
    "TransitionOutcome",
    "TransitionRule",
    "ai_actor_scope",
    "apply_event",
    "execute_transition",
    "is_ai_actor",
    "transition_instant",
]

_logger = get_logger(__name__)

#: Cap on reason text, from ``docs/workflows.md`` 0.10. Enforced here as well as in the
#: request schema: the schema protects the HTTP path, this protects every path.
REASON_MAX_LENGTH: Final[int] = 500

#: Suffix of the audit action used when a refusal has no rule to name -- an illegal pair,
#: a terminal state, an unknown event. Rendered as ``<action_prefix>.transition_rejected``.
TRANSITION_REJECTED_SUFFIX: Final[str] = "transition_rejected"

# Denial reasons. Machine-readable, stable, and safe to disclose: the caller already knows
# which object and which event they asked for, and a refusal nobody can act on generates a
# support ticket rather than a corrected request.
DENIAL_UNKNOWN_EVENT: Final[str] = "unknown_event"
DENIAL_TERMINAL_STATE: Final[str] = "terminal_state"
DENIAL_ILLEGAL_TRANSITION: Final[str] = "illegal_transition"
DENIAL_STATE_PRECONDITION: Final[str] = "state_precondition_failed"
DENIAL_MISSING_PERMISSION: Final[str] = "missing_permission"
DENIAL_INSUFFICIENT_CLEARANCE: Final[str] = "insufficient_clearance"
DENIAL_MISSING_REASON: Final[str] = "missing_reason"
DENIAL_REASON_TOO_LONG: Final[str] = "reason_too_long"
#: The reason holds a character the database cannot store (a NUL). Refused, never altered.
DENIAL_INVALID_REASON: Final[str] = "invalid_reason"
DENIAL_GUARD_FAILED: Final[str] = "guard_failed"
DENIAL_AUTONOMOUS_ACTOR: Final[str] = "autonomous_actor"

#: Keys of the fixed ALLOW payload: the five of ``docs/workflows.md`` 0.5 plus
#: ``reason_stored_in`` (see :func:`_transition_payload`). A rule's ``audit_detail`` may add
#: keys beside these and may never replace one: a reader filtering on ``payload.to_state``
#: must be able to trust it whichever machine wrote the row.
TRANSITION_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {"from_state", "to_state", "event", "reason", "trace_id", "reason_stored_in"}
)

#: Keys the executor itself puts on the problem document of an event-authorization refusal.
#: An :class:`AuthorizationFailure` may add fields beside these and may never replace one.
AUTHORIZATION_RESERVED_EXTRA: Final[frozenset[str]] = frozenset(
    {"reason", "machine", "event", "from_state", "required_permissions"}
)

# ---------------------------------------------------------------------------
# The non-autonomy seam (BUILD_BIBLE.md section 6)
# ---------------------------------------------------------------------------

_AI_ACTOR: ContextVar[bool] = ContextVar("naddp_ai_actor", default=False)


@contextmanager
def ai_actor_scope() -> Iterator[None]:
    """Mark the current call stack as running on behalf of the AI Gateway.

    ``docs/workflows.md`` section 4 requires the four ``BUILD_BIBLE.md`` section 6 controls
    to assert that no Gateway call is on the stack. A ``ContextVar`` is how that assertion
    is made cheaply and correctly under the threadpool FastAPI runs synchronous handlers in.

    **The Gateway must enter this scope around ``generate()``.** Until it does, the check
    below is armed but never fires -- which is the right failure mode for a control whose
    two halves land in different tracks: nothing is falsely blocked, and wiring it up is one
    ``with`` statement rather than a redesign. A Gateway proposal is data, never an event
    (``docs/workflows.md`` 0.7), so anything reaching a control transition from inside this
    scope is a bug worth a 403 and an audit row.
    """
    token = _AI_ACTOR.set(True)
    try:
        yield
    finally:
        _AI_ACTOR.reset(token)


def is_ai_actor() -> bool:
    """Return whether an AI Gateway call is on the current call stack."""
    return _AI_ACTOR.get()


# ---------------------------------------------------------------------------
# Machine description
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionContext[ObjT]:
    """Everything a guard or an effect is allowed to see.

    One argument rather than four positional ones, so adding a field later (the AI trace
    that informed the event, say) does not rewrite every guard signature in the system.
    """

    obj: ObjT
    session: Session
    actor: Principal
    event: str
    reason: str | None


@dataclass(frozen=True)
class AuthorizationFailure:
    """Why an event authorization refused, in the shape the executor records and raises.

    Returned -- never raised -- by a machine's authorize callable, for the same reason a guard
    returns a sentence rather than a bool: the executor owns the ordering, the DENY row and
    the exception, so an authorization cannot forget to audit itself or pick a status of its
    own. ``error`` chooses which 403 the API renders (``ApprovalRequiredError``,
    ``SeparationOfDutiesError``). It is typed to :class:`PermissionDeniedError` subclasses
    because an authorization refusal is an authorisation outcome by definition; a refusal
    that is really about the object's content is a guard, and a guard answers 409.
    """

    #: Machine-readable reason: ``payload.denial_reason`` on the row, ``reason`` on the 403.
    denial_reason: str
    #: The sentence the caller reads, and ``payload.detail`` on the DENY row.
    detail: str
    #: Extra problem-document fields -- who could approve, say. JSON-serialisable values.
    extra: Mapping[str, Any] = field(default_factory=dict)
    error: type[PermissionDeniedError] = PermissionDeniedError

    def __post_init__(self) -> None:
        """Refuse extra fields that would overwrite what the executor reports itself."""
        clashes = sorted(set(self.extra) & AUTHORIZATION_RESERVED_EXTRA)
        if clashes:
            msg = (
                f"AuthorizationFailure.extra may not set {clashes}: the executor reports those "
                "itself, and an authorization able to rewrite them could misreport its own "
                "refusal."
            )
            raise ValueError(msg)


def _no_event_authorizations() -> Mapping[str, Any]:
    """The default for :attr:`StateMachine.event_authorizations`: none, immutably."""
    return MappingProxyType({})


@dataclass(frozen=True)
class TransitionRule[StateT: Enum, ObjT]:
    """One row of a ``docs/workflows.md`` transition table.

    ``to_state`` duplicates what the domain table in ``app.domain.enums`` already says, and
    that duplication is the point: :class:`StateMachine` refuses to build unless the two
    agree, so this rule set cannot quietly widen or redirect a machine.
    """

    from_state: StateT
    event: str
    to_state: StateT
    permission: Permission
    audit_action: str
    requires_reason: bool = False
    #: Column on the object where a required reason is persisted, e.g. ``closed_reason``.
    #: ``None`` when the object has nowhere to keep it -- see :func:`_transition_payload`.
    reason_column: str | None = None
    #: Predicates returning a human-readable failure message, or ``None`` to pass. They
    #: return a message rather than a bool so the API can explain the block (409 with a
    #: sentence) instead of merely refusing it (``docs/workflows.md`` section 4).
    guards: tuple[Callable[[TransitionContext[ObjT]], str | None], ...] = ()
    #: Side effects applied after the state write, inside the same transaction -- raising a
    #: classification on entry to NEGOTIATION, for instance.
    effects: tuple[Callable[[TransitionContext[ObjT]], None], ...] = ()
    #: True for a ``BUILD_BIBLE.md`` section 6 non-autonomous control (marked with a warning
    #: sign in ``docs/workflows.md``). Such an event is refused while :func:`is_ai_actor`.
    is_non_autonomous_control: bool = False
    #: Extra facts for the ALLOW row's payload -- ``recipient_count`` on a send, say. Called
    #: after the state write and the effects, so it sees the object as it now is, and merged
    #: after the fixed keys (:data:`TRANSITION_PAYLOAD_KEYS`), which it may never replace: a
    #: collision raises before the audit row is written, and the caller's transaction takes
    #: the state write back with it.
    audit_detail: Callable[[TransitionContext[ObjT]], Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class StateMachine[StateT: Enum, ObjT]:
    """A workflow machine: the domain's transition table plus everything to execute it.

    The accessor callables (``read_state``, ``write_state``, ``object_id_of`` ...) keep this
    module free of any dependency on a particular ORM model, which is what lets the meeting
    follow-up and consular case tracks reuse it without editing it. ``write_state`` takes
    the transition instant so a machine can update its own "when did the state last change"
    column with the database's clock rather than a second, skewed one.
    """

    name: str
    #: Bounded-context-qualified entity name for ``audit_events.object_type``.
    object_type: str
    #: First segment of every audit action for this machine, e.g. ``opportunity``.
    action_prefix: str
    #: Table the object lives in, used to reference a stored reason in an audit payload.
    table_name: str
    #: The domain table, imported from ``app.domain.enums``. Never redefined here.
    transitions: Mapping[tuple[StateT, str], StateT]
    terminal_states: frozenset[StateT]
    rules: Mapping[tuple[StateT, str], TransitionRule[StateT, ObjT]]
    read_state: Callable[[ObjT], StateT]
    write_state: Callable[[ObjT, StateT, datetime], None]
    object_id_of: Callable[[ObjT], uuid.UUID]
    classification_of: Callable[[ObjT], Classification]
    #: Events for which firing an already-satisfied event is a no-op (``docs/workflows.md``
    #: 0.8). An explicit allowlist, not a derived rule: ``revert`` also targets a state it
    #: can be fired from, and re-firing it means "step back again", never "already done".
    idempotent_events: frozenset[str] = frozenset()
    public_ref_of: Callable[[ObjT], str | None] = field(default=lambda _obj: None)
    #: Event name -> an authorization evaluated before the table lookup (module docstring).
    #: It returns ``None`` to pass or an :class:`AuthorizationFailure` to refuse. Empty for a
    #: machine whose every refusal is table-shaped.
    event_authorizations: Mapping[
        str, Callable[[TransitionContext[ObjT]], AuthorizationFailure | None]
    ] = field(default_factory=_no_event_authorizations)

    def __post_init__(self) -> None:
        """Reject a rule set that does not transcribe the domain table exactly.

        Seven failures, all of which would otherwise be a silently wrong machine: a rule for
        a pair the table does not contain (a widened machine), a missing rule (a transition
        the product cannot perform), a rule whose target disagrees with the table, a rule
        leaving a terminal state, an action outside this machine's namespace, an
        authorization for an event the machine does not have, and an authorized event whose
        rules disagree about the permission it needs -- the authorization runs before the
        table lookup, so it has no rule to ask and must be able to rely on there being one
        answer.
        """
        table_keys = set(self.transitions)
        rule_keys = set(self.rules)

        extra = sorted(f"{state.value}/{event}" for state, event in rule_keys - table_keys)
        if extra:
            msg = (
                f"{self.name}: rules define transitions the domain table does not permit: "
                f"{extra}. app.domain.enums is the source; this module is the transcription."
            )
            raise ValueError(msg)

        missing = sorted(f"{state.value}/{event}" for state, event in table_keys - rule_keys)
        if missing:
            msg = (
                f"{self.name}: the domain table has transitions with no rule: {missing}. "
                "Every legal transition needs a permission and an audit action."
            )
            raise ValueError(msg)

        for key, rule in self.rules.items():
            from_state, event = key
            if (rule.from_state, rule.event) != key:
                msg = f"{self.name}: rule keyed {key} declares ({rule.from_state}, {rule.event})."
                raise ValueError(msg)
            if rule.to_state is not self.transitions[key]:
                msg = (
                    f"{self.name}: rule {from_state.value}/{event} targets "
                    f"{rule.to_state.value}; the domain table says "
                    f"{self.transitions[key].value}."
                )
                raise ValueError(msg)
            if from_state in self.terminal_states:
                msg = (
                    f"{self.name}: {from_state.value} is terminal and accepts no events, "
                    f"but a rule fires {event} from it."
                )
                raise ValueError(msg)
            if not rule.audit_action.startswith(f"{self.action_prefix}."):
                msg = (
                    f"{self.name}: audit action {rule.audit_action!r} does not start with "
                    f"{self.action_prefix!r}; audit_events.action is a closed vocabulary."
                )
                raise ValueError(msg)

        unknown = sorted(self.idempotent_events - self.events)
        if unknown:
            msg = f"{self.name}: idempotent_events names unknown events: {unknown}."
            raise ValueError(msg)

        unauthorizable = sorted(set(self.event_authorizations) - self.events)
        if unauthorizable:
            msg = f"{self.name}: event_authorizations names unknown events: {unauthorizable}."
            raise ValueError(msg)

        for event in sorted(self.event_authorizations):
            permissions = sorted({rule.permission.value for rule in self.rules_for(event)})
            if len(permissions) != 1:
                msg = (
                    f"{self.name}: '{event}' has an event authorization, but its rules require "
                    f"{permissions}. An authorized event must require exactly one permission, "
                    "because the authorization runs before the table says which rule applies."
                )
                raise ValueError(msg)

    @property
    def events(self) -> frozenset[str]:
        """Every event name this machine accepts from at least one state."""
        return frozenset(event for _state, event in self.transitions)

    @property
    def states(self) -> frozenset[StateT]:
        """Every state this machine mentions, terminal states included."""
        reached: set[StateT] = set(self.terminal_states)
        for (from_state, _event), to_state in self.transitions.items():
            reached.add(from_state)
            reached.add(to_state)
        return frozenset(reached)

    @property
    def rejected_action(self) -> str:
        """Audit action for a refusal that has no rule to name."""
        return f"{self.action_prefix}.{TRANSITION_REJECTED_SUFFIX}"

    def rules_for(self, event: str) -> tuple[TransitionRule[StateT, ObjT], ...]:
        """Every rule firing ``event``, from any source state, in declaration order."""
        return tuple(rule for rule in self.rules.values() if rule.event == event)

    def targets_of(self, event: str) -> frozenset[StateT]:
        """Every state ``event`` can produce, from any source state."""
        return frozenset(
            to_state for (_state, name), to_state in self.transitions.items() if name == event
        )

    def audit_actions(self) -> frozenset[str]:
        """Every action string this machine can write, including the rejection action."""
        return frozenset(rule.audit_action for rule in self.rules.values()) | {self.rejected_action}


@dataclass(frozen=True)
class TransitionOutcome[StateT: Enum]:
    """What an accepted event did. Returned to the service, and rendered by the API."""

    machine: str
    object_id: uuid.UUID
    event: str
    from_state: StateT
    to_state: StateT
    audit_action: str
    audit_event_id: uuid.UUID | None
    occurred_at: datetime | None
    #: False when the event was already reflected in the current state
    #: (``docs/workflows.md`` 0.8): no state change, no audit row, still a 200.
    applied: bool = True


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def transition_instant(session: Session) -> datetime:
    """Return the database's transaction timestamp.

    The same instant the audit writer stamps on its row, for the same reason: one clock,
    the server's, so a state-change column and the audit row describing it cannot disagree
    by a host's clock skew. ``func.now()`` is ``transaction_timestamp()``, so every write in
    one transaction shares one value.
    """
    value = session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        msg = "SELECT now() did not return a timestamp; refusing to stamp a state change."
        raise RuntimeError(msg)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _transition_payload[StateT: Enum, ObjT](
    machine: StateMachine[StateT, ObjT],
    *,
    from_state: StateT,
    to_state: StateT | None,
    event: str,
    reason: str | None,
    reason_column: str | None,
    trace_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Build the fixed audit payload of ``docs/workflows.md`` 0.5.

    The five keys are exactly the ones that document fixes. One optional sixth key,
    ``reason_stored_in``, resolves a tension between 0.5 (which names ``reason`` in the
    shape) and 0.10 (which says the text is stored in the object's own column and
    *referenced*, not copied, in the log). Where a column exists the payload references it
    and carries no copy; where the object has nowhere to keep the text -- ``revert`` on an
    opportunity -- the capped text stays here, because a reason the log discards makes the
    requirement to give one meaningless. Escalated in this track's handoff.
    """
    payload: dict[str, Any] = {
        "from_state": from_state.value,
        "to_state": to_state.value if to_state is not None else None,
        "event": event,
        "reason": None if reason_column is not None else reason,
        "trace_id": str(trace_id) if trace_id is not None else None,
    }
    if reason_column is not None and reason is not None:
        payload["reason_stored_in"] = f"{machine.table_name}.{reason_column}"
    return payload


def _write_denial[StateT: Enum, ObjT](
    session: Session,
    machine: StateMachine[StateT, ObjT],
    obj: ObjT,
    *,
    actor: Principal,
    event: str,
    from_state: StateT,
    rule: TransitionRule[StateT, ObjT] | None,
    denial_reason: str,
    detail: str,
    reason: str | None,
    trace_id: uuid.UUID | None,
) -> AuditEvent:
    """Append the ``policy_result = DENY`` row for a refused transition.

    The action is the rule's own action where one exists, so "who tried to partner this
    opportunity" is a single indexed query over ``action`` regardless of outcome. Where no
    rule exists -- an illegal pair, a terminal state, an unknown event -- there is no action
    to borrow and the machine's rejection action is used instead.
    """
    object_id = machine.object_id_of(obj)
    payload = _transition_payload(
        machine,
        from_state=from_state,
        to_state=rule.to_state if rule is not None else None,
        event=event,
        reason=reason,
        reason_column=None,
        trace_id=trace_id,
    )
    payload["denial_reason"] = denial_reason
    payload["detail"] = detail
    payload["actor_role"] = actor.role.value
    if rule is not None:
        payload["required_permission"] = rule.permission.value

    row = write_audit_event(
        session,
        actor=actor,
        action=rule.audit_action if rule is not None else machine.rejected_action,
        object_type=machine.object_type,
        object_id=object_id,
        object_public_ref=machine.public_ref_of(obj),
        policy_result=PolicyResult.DENY,
        classification=machine.classification_of(obj),
        summary=(
            f"{actor.full_name} ({actor.role.value}) was refused '{event}' on "
            f"{machine.object_type} {object_id} in {from_state.value}: {detail}"
        ),
        payload=payload,
        trace_id=trace_id,
    )
    _logger.info(
        "workflow.transition_denied",
        machine=machine.name,
        machine_event=event,
        from_state=from_state.value,
        denial_reason=denial_reason,
        actor_role=actor.role.value,
        object_id=str(object_id),
    )
    return row


def apply_event[StateT: Enum, ObjT](
    session: Session,
    machine: StateMachine[StateT, ObjT],
    obj: ObjT,
    *,
    event: str,
    actor: Principal,
    reason: str | None = None,
    expected_state: StateT | None = None,
    trace_id: uuid.UUID | None = None,
) -> TransitionOutcome[StateT]:
    """Fire ``event`` against ``obj``, or refuse it and record the refusal.

    Does **not** commit. The state write and the audit row are left pending in the caller's
    transaction so they commit or roll back together (ADR-0004). A refusal leaves a DENY row
    pending in the same way -- which the caller must commit, because the exception will
    otherwise roll it away. :func:`execute_transition` is the wrapper that gets that right;
    prefer it unless you are composing several transitions into one unit of work.

    Args:
        session: The session carrying the business transaction.
        machine: The machine to execute.
        obj: The object whose state is being changed. Already loaded, and ideally locked.
        event: The event name the client sent. A client never proposes a target state
            (``docs/workflows.md`` 0.2); the target is computed from the table.
        actor: The authenticated human firing the event. No transition is autonomous.
        reason: Justification, required by the events marked with a pencil in
            ``docs/workflows.md`` and capped at :data:`REASON_MAX_LENGTH`.
        expected_state: Optional precondition. When given and not equal to the object's
            current state, the request is refused without changing anything -- the
            optimistic-concurrency rule of ``docs/workflows.md`` 0.9, in the form the
            current schema supports. See the note in ``app/services/opportunities.py``.
        trace_id: The ``ai_traces`` row that informed this event, where one did.

    Returns:
        A :class:`TransitionOutcome`. ``applied`` is False for the idempotent no-op case.

    Raises:
        InvalidTransitionError: 409. Unknown event, terminal state, illegal pair, failed
            precondition, a reason that cannot be recorded, a missing or over-long reason,
            or a failed guard.
        PermissionDeniedError: 403. The actor lacks the event's permission, a
            non-autonomous control was reached from inside an AI Gateway call, or an event
            authorization refused -- raised as the subclass the authorization names, such
            as ``ApprovalRequiredError``.
        ClassificationDeniedError: 403. The actor is not cleared to read the object it is
            trying to change (ADR-0006 -- both gates must pass). Checked first, so this is
            the answer whatever else about the request would have been refused.

    Every refusal above is raised after its DENY row was written, and carries that row's id
    as ``denial_audit_event_id`` (see the module docstring).
    """
    denials: list[AuditEvent] = []
    try:
        return _apply_event_in_order(
            session,
            machine,
            obj,
            event=event,
            actor=actor,
            reason=reason,
            expected_state=expected_state,
            trace_id=trace_id,
            denials=denials,
        )
    except AppError as refused:
        if denials:
            refused.denial_audit_event_id = denials[-1].id
        raise


# The order of the checks below IS the specification (docs/workflows.md section 4), so this
# function is deliberately long and deliberately linear. Splitting it into helpers would let
# a future edit reorder them, and the ordering is the security property.
def _apply_event_in_order[StateT: Enum, ObjT](
    session: Session,
    machine: StateMachine[StateT, ObjT],
    obj: ObjT,
    *,
    event: str,
    actor: Principal,
    reason: str | None,
    expected_state: StateT | None,
    trace_id: uuid.UUID | None,
    denials: list[AuditEvent],
) -> TransitionOutcome[StateT]:
    """The body of :func:`apply_event`. Every DENY row it writes is appended to ``denials``."""
    from_state = machine.read_state(obj)
    stripped = reason.strip() if reason is not None else ""
    normalised_reason = stripped or None
    # A NUL cannot be stored (see the module docstring), so no DENY row ever carries one: the
    # refusal records None in its place, and step 0b refuses the event.
    reason_is_unrecordable = normalised_reason is not None and "\x00" in normalised_reason
    recorded_reason = None if reason_is_unrecordable else normalised_reason

    def deny(
        denial_reason: str,
        detail: str,
        *,
        rule: TransitionRule[StateT, ObjT] | None = None,
    ) -> None:
        denials.append(
            _write_denial(
                session,
                machine,
                obj,
                actor=actor,
                event=event,
                from_state=from_state,
                rule=rule,
                denial_reason=denial_reason,
                detail=detail,
                reason=recorded_reason,
                trace_id=trace_id,
            )
        )

    def denial_extra(denial_reason: str, **fields: object) -> dict[str, Any]:
        return {
            "reason": denial_reason,
            "machine": machine.name,
            "event": event,
            "from_state": from_state.value,
            **fields,
        }

    # 0a. Clearance, before anything that can reveal state (module docstring). The problem
    #     document is built WITHOUT denial_extra: no from_state, no legal_events, and a detail
    #     that names no state. The DENY row is filed under the event's own action whenever the
    #     event names one permission, so "who tried to send this" stays one query over action.
    object_zone = machine.classification_of(obj)
    if not actor.may_read(object_zone):
        rules_of_event = machine.rules_for(event)
        uniform_rule = (
            rules_of_event[0]
            if len({candidate.permission for candidate in rules_of_event}) == 1
            else None
        )
        deny(
            DENIAL_INSUFFICIENT_CLEARANCE,
            f"not cleared for {object_zone.value}",
            rule=machine.rules.get((from_state, event)) or uniform_rule,
        )
        raise ClassificationDeniedError(
            extra={
                "reason": DENIAL_INSUFFICIENT_CLEARANCE,
                "machine": machine.name,
                "event": event,
                "classification": object_zone.value,
                "actor_role": actor.role.value,
            },
        )

    # 0b. A reason the database cannot store is refused, never altered (module docstring).
    if reason_is_unrecordable:
        detail = "the reason contains a NUL character, which cannot be recorded"
        deny(DENIAL_INVALID_REASON, detail, rule=machine.rules.get((from_state, event)))
        raise InvalidTransitionError(
            f"{detail}. Remove it and try again.",
            extra=denial_extra(DENIAL_INVALID_REASON),
        )

    # 0c. Precondition (docs/workflows.md 0.9). Before anything but clearance: a caller acting
    #     on a stale read must not be told whether their event would otherwise have been legal.
    if expected_state is not None and expected_state is not from_state:
        detail = (
            f"the object is in {from_state.value}, the request expected "
            f"{expected_state.value}; somebody else moved it first"
        )
        deny(DENIAL_STATE_PRECONDITION, detail)
        raise InvalidTransitionError(
            f"This {machine.name} has moved on: {detail}. Re-read it and try again.",
            extra=denial_extra(DENIAL_STATE_PRECONDITION, expected_state=expected_state.value),
        )

    # 1. Unknown event. Deny-by-default over a closed vocabulary of event names.
    if event not in machine.events:
        detail = f"'{event}' is not an event of the {machine.name} machine"
        deny(DENIAL_UNKNOWN_EVENT, detail)
        raise InvalidTransitionError(
            f"{detail}. Valid events: {', '.join(sorted(machine.events))}.",
            extra=denial_extra(DENIAL_UNKNOWN_EVENT, valid_events=sorted(machine.events)),
        )

    # 2. Terminal states accept nothing (docs/workflows.md 0.6). Checked BEFORE the
    #    idempotency shortcut, so `close` on a CLOSED object is a refusal rather than a
    #    quiet 200 -- recovery from a terminal state is a new object, never a re-entry.
    if from_state in machine.terminal_states:
        detail = f"{from_state.value} is terminal and accepts no events"
        deny(DENIAL_TERMINAL_STATE, detail)
        raise InvalidTransitionError(
            f"{detail}. Recovery from a terminal state means creating a new "
            f"{machine.name} that references this one.",
            extra=denial_extra(DENIAL_TERMINAL_STATE),
        )

    # 2b. Event authorization (docs/workflows.md section 2, "Authorisation beyond the
    #     matrix"), only for an event that declares one. Before the table lookup, so an
    #     event legal from some state but not authorised on THIS object is a 403 saying what
    #     is missing rather than a 409 illegal pair; after the terminal check, so a terminal
    #     object still answers 409 first. Permission and clearance come first, in the order
    #     of steps 4 and 5, so a caller who may not fire the event at all learns nothing from
    #     the authorization's answer.
    authorize = machine.event_authorizations.get(event)
    if authorize is not None:
        # The rule for this pair where one exists, else the event's first rule. Either way
        # the DENY row carries the event's own action (`meeting_followup.sent`), never the
        # machine's rejection action, and __post_init__ guarantees every rule for an
        # authorized event requires the same permission.
        representative = machine.rules.get((from_state, event)) or machine.rules_for(event)[0]
        authorized_permission = representative.permission
        if not actor.has(authorized_permission):
            deny(
                DENIAL_MISSING_PERMISSION,
                f"missing {authorized_permission.value}",
                rule=representative,
            )
            raise PermissionDeniedError(
                extra=denial_extra(
                    DENIAL_MISSING_PERMISSION,
                    required_permissions=[authorized_permission.value],
                    missing_permissions=[authorized_permission.value],
                    actor_role=actor.role.value,
                ),
            )
        authorization_zone = machine.classification_of(obj)
        if not actor.may_read(authorization_zone):
            deny(
                DENIAL_INSUFFICIENT_CLEARANCE,
                f"not cleared for {authorization_zone.value}",
                rule=representative,
            )
            raise ClassificationDeniedError(
                extra=denial_extra(
                    DENIAL_INSUFFICIENT_CLEARANCE,
                    classification=authorization_zone.value,
                    actor_role=actor.role.value,
                ),
            )
        refusal = authorize(
            TransitionContext(
                obj=obj, session=session, actor=actor, event=event, reason=normalised_reason
            )
        )
        if refusal is not None:
            deny(refusal.denial_reason, refusal.detail, rule=representative)
            raise refusal.error(
                refusal.detail,
                extra=denial_extra(
                    refusal.denial_reason,
                    required_permissions=[authorized_permission.value],
                    **refusal.extra,
                ),
            )

    # 3. Table lookup. The client sent an event; the table computes the target.
    rule = machine.rules.get((from_state, event))
    if rule is None:
        # 3a. Idempotency (docs/workflows.md 0.8): the event is already reflected in the
        #     current state. A no-op 200 with no second audit row -- not an error, and not
        #     a duplicate transition.
        if event in machine.idempotent_events and from_state in machine.targets_of(event):
            # The no-op still passes both gates. A 200 returned before them would let a role
            # with no permission for the event, or no clearance for the object, fire it and
            # read the object's state from the answer. Holding any one rule's permission
            # suffices: the event is already satisfied, so no single rule applies.
            event_rules = machine.rules_for(event)
            held = [candidate for candidate in event_rules if actor.has(candidate.permission)]
            if not held:
                required = sorted({candidate.permission.value for candidate in event_rules})
                deny(
                    DENIAL_MISSING_PERMISSION,
                    f"missing {' or '.join(required)}",
                    rule=event_rules[0],
                )
                raise PermissionDeniedError(
                    extra=denial_extra(
                        DENIAL_MISSING_PERMISSION,
                        required_permissions=required,
                        missing_permissions=required,
                        actor_role=actor.role.value,
                    ),
                )
            noop_zone = machine.classification_of(obj)
            if not actor.may_read(noop_zone):
                deny(
                    DENIAL_INSUFFICIENT_CLEARANCE,
                    f"not cleared for {noop_zone.value}",
                    rule=held[0],
                )
                raise ClassificationDeniedError(
                    extra=denial_extra(
                        DENIAL_INSUFFICIENT_CLEARANCE,
                        classification=noop_zone.value,
                        actor_role=actor.role.value,
                    ),
                )
            # Nor is the no-op a way around step 6: a section 6 control re-fired from inside
            # a Gateway call is refused and recorded, exactly as its first firing would be.
            control_rules = [
                candidate for candidate in event_rules if candidate.is_non_autonomous_control
            ]
            if control_rules and is_ai_actor():
                detail = "a section 6 control cannot be fired from inside an AI Gateway call"
                deny(DENIAL_AUTONOMOUS_ACTOR, detail, rule=control_rules[0])
                raise PermissionDeniedError(
                    f"{detail}. The Gateway may propose; only a human may decide.",
                    extra=denial_extra(
                        DENIAL_AUTONOMOUS_ACTOR,
                        required_permissions=sorted(
                            {candidate.permission.value for candidate in control_rules}
                        ),
                    ),
                )
            _logger.info(
                "workflow.transition_noop",
                machine=machine.name,
                machine_event=event,
                state=from_state.value,
                object_id=str(machine.object_id_of(obj)),
            )
            return TransitionOutcome(
                machine=machine.name,
                object_id=machine.object_id_of(obj),
                event=event,
                from_state=from_state,
                to_state=from_state,
                audit_action="",
                audit_event_id=None,
                occurred_at=None,
                applied=False,
            )
        detail = f"'{event}' is not legal from {from_state.value}"
        deny(DENIAL_ILLEGAL_TRANSITION, detail)
        raise InvalidTransitionError(
            f"{detail}.",
            extra=denial_extra(
                DENIAL_ILLEGAL_TRANSITION,
                legal_events=sorted(
                    name for state, name in machine.transitions if state is from_state
                ),
            ),
        )

    # 4. Permission (gate 1). Checked before the guards so a caller who may not fire the
    #    event learns nothing about whether the object would have satisfied them.
    if not actor.has(rule.permission):
        deny(DENIAL_MISSING_PERMISSION, f"missing {rule.permission.value}", rule=rule)
        raise PermissionDeniedError(
            extra=denial_extra(
                DENIAL_MISSING_PERMISSION,
                required_permissions=[rule.permission.value],
                missing_permissions=[rule.permission.value],
                actor_role=actor.role.value,
            ),
        )

    # 5. Clearance (gate 2, ADR-0006). Neither gate implies the other, and changing a row
    #    you may not read is not a lesser act than reading it.
    zone = machine.classification_of(obj)
    if not actor.may_read(zone):
        deny(DENIAL_INSUFFICIENT_CLEARANCE, f"not cleared for {zone.value}", rule=rule)
        raise ClassificationDeniedError(
            extra=denial_extra(
                DENIAL_INSUFFICIENT_CLEARANCE,
                classification=zone.value,
                actor_role=actor.role.value,
            ),
        )

    # 6. Non-autonomous control (BUILD_BIBLE.md section 6, docs/workflows.md section 4).
    if rule.is_non_autonomous_control and is_ai_actor():
        detail = "a section 6 control cannot be fired from inside an AI Gateway call"
        deny(DENIAL_AUTONOMOUS_ACTOR, detail, rule=rule)
        raise PermissionDeniedError(
            f"{detail}. The Gateway may propose; only a human may decide.",
            extra=denial_extra(
                DENIAL_AUTONOMOUS_ACTOR, required_permissions=[rule.permission.value]
            ),
        )

    # 7. Reason (docs/workflows.md 0.10).
    if rule.requires_reason and normalised_reason is None:
        detail = f"'{event}' requires a reason"
        deny(DENIAL_MISSING_REASON, detail, rule=rule)
        raise InvalidTransitionError(f"{detail}.", extra=denial_extra(DENIAL_MISSING_REASON))
    if normalised_reason is not None and len(normalised_reason) > REASON_MAX_LENGTH:
        detail = f"reason exceeds {REASON_MAX_LENGTH} characters"
        deny(DENIAL_REASON_TOO_LONG, detail, rule=rule)
        raise InvalidTransitionError(f"{detail}.", extra=denial_extra(DENIAL_REASON_TOO_LONG))

    # 8. Guards -- pure predicates over the object, returning why they refused.
    context = TransitionContext(
        obj=obj, session=session, actor=actor, event=event, reason=normalised_reason
    )
    for guard in rule.guards:
        failure = guard(context)
        if failure is not None:
            deny(DENIAL_GUARD_FAILED, failure, rule=rule)
            raise InvalidTransitionError(
                failure, extra=denial_extra(DENIAL_GUARD_FAILED, guard_failure=failure)
            )

    # 9. State write, then effects, then the rule's audit detail, then the audit row -- one
    #    transaction.
    occurred_at = transition_instant(session)
    machine.write_state(obj, rule.to_state, occurred_at)
    if rule.reason_column is not None and normalised_reason is not None:
        setattr(obj, rule.reason_column, normalised_reason)
    for effect in rule.effects:
        effect(context)

    object_id = machine.object_id_of(obj)
    payload = _transition_payload(
        machine,
        from_state=from_state,
        to_state=rule.to_state,
        event=event,
        reason=normalised_reason,
        reason_column=rule.reason_column,
        trace_id=trace_id,
    )
    if rule.audit_detail is not None:
        detail_fields = rule.audit_detail(context)
        clashes = sorted(set(detail_fields) & TRANSITION_PAYLOAD_KEYS)
        if clashes:
            msg = (
                f"{machine.name}: the audit_detail of {from_state.value}/{event} returned "
                f"{clashes}, which the fixed transition payload owns (docs/workflows.md 0.5). "
                "Name the extra facts something else."
            )
            raise ValueError(msg)
        payload.update(detail_fields)

    audit_row = write_audit_event(
        session,
        actor=actor,
        action=rule.audit_action,
        object_type=machine.object_type,
        object_id=object_id,
        object_public_ref=machine.public_ref_of(obj),
        policy_result=PolicyResult.ALLOW,
        classification=machine.classification_of(obj),
        summary=(
            f"{actor.full_name} ({actor.role.value}) fired '{event}' on "
            f"{machine.object_type} {object_id}: {from_state.value} -> {rule.to_state.value}."
        ),
        payload=payload,
        trace_id=trace_id,
    )

    _logger.info(
        "workflow.transition_applied",
        machine=machine.name,
        machine_event=event,
        from_state=from_state.value,
        to_state=rule.to_state.value,
        action=rule.audit_action,
        actor_role=actor.role.value,
        object_id=str(object_id),
    )
    return TransitionOutcome(
        machine=machine.name,
        object_id=object_id,
        event=event,
        from_state=from_state,
        to_state=rule.to_state,
        audit_action=rule.audit_action,
        audit_event_id=audit_row.id,
        occurred_at=audit_row.occurred_at,
    )


def execute_transition[StateT: Enum, ObjT](
    session: Session,
    machine: StateMachine[StateT, ObjT],
    obj: ObjT,
    *,
    event: str,
    actor: Principal,
    reason: str | None = None,
    expected_state: StateT | None = None,
    trace_id: uuid.UUID | None = None,
) -> TransitionOutcome[StateT]:
    """Run :func:`apply_event` and commit, on the accept path **and the refuse path**.

    The refuse path is the interesting half. ``get_session`` rolls the request transaction
    back when a handler raises, so a DENY row left pending would vanish with the request
    that earned it -- and a log recording only the transitions that succeeded is not
    evidence of a control, it is evidence of a happy path. Committing the row alone is safe
    precisely because a refusal made no state change for it to be atomic with.

    A failure to commit the denial is logged and swallowed: the caller's action was refused
    either way, and turning their 403 into a 500 would tell them less, not more. The refusal's
    ``denial_audit_event_id`` is cleared in that case, because the row it named was rolled
    back and no longer exists.
    """
    try:
        outcome = apply_event(
            session,
            machine,
            obj,
            event=event,
            actor=actor,
            reason=reason,
            expected_state=expected_state,
            trace_id=trace_id,
        )
    except AppError as refused:
        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            refused.denial_audit_event_id = None
            _logger.error(
                "workflow.denial_audit_commit_failed",
                machine=machine.name,
                machine_event=event,
                exc_info=True,
            )
        raise
    session.commit()
    return outcome
