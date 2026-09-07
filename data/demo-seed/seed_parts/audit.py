"""Eight weeks of believable audit history (OPEN_QUESTIONS Q-13).

The architect's ruling: about 450 ``audit_events`` rows across a trailing **eight weeks**,
weighted toward recent, spread over all six roles, with roughly **5% carrying
``policy_result = DENY``** -- because a log with no denials looks untested, and a security
reviewer will notice.

**Every row goes through ``app.audit.writer.write_audit_event``.** Hand-inserting would
break the SHA-256 hash chain at the very first row and every later row with it, which
would turn the one table whose integrity is demonstrable into the one table that visibly
fails its own verifier. The writer takes a narrow ``occurred_at`` override added for
exactly this caller, documented in ``app/audit/writer.py``; the value is still covered by
``event_hash``, so a backdated row is no less tamper-evident than any other.

**Rows are written oldest-first** so that the chain, which is ordered by ULID primary key,
runs in the same direction as the timestamps. Writing them in a different order would
produce a chain that verifies but reads backwards.

**Re-runnable.** ``audit_events`` refuses ``DELETE`` and ``TRUNCATE``, so this block cannot
be re-applied. Every seeded row carries ``payload.seed_marker``; if any is already present
the whole block is skipped, which makes a second ``make seed`` a no-op here rather than
900 rows. ``make demo-reset`` drops the schema and starts the history again.

The vocabulary is closed (``app/audit/actions.py``): session, access and opportunity
actions only. The seed does not invent an action the API cannot also write.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import func, select

from app.audit.actions import (
    ACCESS_DENIED,
    ACCESS_PRIVILEGED_READ,
    EXPORT_PERFORMED,
    OPPORTUNITY_CLOSED,
    OPPORTUNITY_CONTACT_PLANNED,
    OPPORTUNITY_CONTACTED,
    OPPORTUNITY_DETECTED,
    OPPORTUNITY_MEETING_SCHEDULED,
    OPPORTUNITY_NEGOTIATION_OPENED,
    OPPORTUNITY_PARTNERED,
    OPPORTUNITY_QUALIFIED,
    OPPORTUNITY_REVERTED,
    OPPORTUNITY_TRANSITION_REJECTED,
    SESSION_ROLE_ASSUMED,
    is_known_action,
)
from app.audit.writer import write_audit_event
from app.domain.enums import Classification, PolicyResult, RoleCode
from app.models.consular import Case
from app.models.diaspora import DiasporaProfile
from app.models.governance import AuditEvent, User
from app.models.intelligence import Signal
from app.models.opportunities import Opportunity
from app.models.stakeholders import Stakeholder
from app.security.principal import principal_for_role
from seed_parts.context import SEED_MARKER, SeedContext, weighted_recent_offsets

__all__ = ["AUDIT_DENY_SHARE", "AUDIT_ROW_TARGET", "AUDIT_WINDOW_DAYS", "seed_audit_history"]

#: Q-13: about 450 rows.
AUDIT_ROW_TARGET: Final[int] = 450

#: Q-13: a trailing eight weeks.
AUDIT_WINDOW_DAYS: Final[int] = 56

#: Q-13: roughly one row in twenty is a denial.
AUDIT_DENY_SHARE: Final[float] = 0.05

#: Pulls the sample toward the present. See ``weighted_recent_offsets``.
_RECENCY_BIAS: Final[float] = 2.2

_ROLES: Final[tuple[RoleCode, ...]] = (
    RoleCode.TRADE_OFFICER,
    RoleCode.CONSULAR_OFFICER,
    RoleCode.DIASPORA_OFFICER,
    RoleCode.DEPUTY,
    RoleCode.AMBASSADOR,
    RoleCode.ADMIN,
)

#: How much of the log each role accounts for. Officers work the system daily; the
#: Ambassador reads it. A flat split across six roles would be the one distribution no
#: real mission has.
_ROLE_WEIGHTS: Final[tuple[int, ...]] = (30, 24, 16, 15, 9, 6)


@dataclass(frozen=True, slots=True)
class _Row:
    """One planned audit row, before it is written."""

    days_ago: float
    role: RoleCode
    action: str
    object_type: str
    object_id: uuid.UUID | None
    object_public_ref: str | None
    policy_result: PolicyResult
    classification: Classification
    summary: str
    payload: dict[str, Any]


def _already_seeded(ctx: SeedContext) -> int:
    """Count audit rows this seed wrote on a previous run."""
    ctx.session.flush()
    marker = func.jsonb_extract_path_text(AuditEvent.payload, "seed_marker")
    statement = select(func.count()).select_from(AuditEvent).where(marker == SEED_MARKER)
    return int(ctx.session.scalar(statement) or 0)


def _weighted_role(ctx: SeedContext) -> RoleCode:
    """Pick an actor role from the mission's actual activity distribution."""
    return ctx.rng.choices(_ROLES, weights=_ROLE_WEIGHTS, k=1)[0]


def _read_targets(
    role: RoleCode,
    opportunities: Sequence[Opportunity],
    signals: Sequence[Signal],
    stakeholders: Sequence[Stakeholder],
    cases: Sequence[Case],
    profiles: Sequence[DiasporaProfile],
) -> list[tuple[str, Sequence[Any], Classification]]:
    """What a role legitimately reads, as (object_type, candidates, zone) triples.

    Deny-by-default made concrete: the ``ADMIN`` list holds no content at all, because
    ADMIN is not a content super-user (ADR-0003), and only the compartment holders can
    read a case.
    """
    trade: list[tuple[str, Sequence[Any], Classification]] = [
        ("opportunities.opportunity", opportunities, Classification.MISSION_INTERNAL),
        ("intelligence.signal", signals, Classification.PUBLIC),
        ("stakeholders.stakeholder", stakeholders, Classification.MISSION_INTERNAL),
    ]
    consular: list[tuple[str, Sequence[Any], Classification]] = [
        ("consular.case", cases, Classification.CONSULAR_SENSITIVE),
    ]
    diaspora: list[tuple[str, Sequence[Any], Classification]] = [
        ("diaspora.profile", profiles, Classification.MISSION_INTERNAL),
    ]
    if role is RoleCode.TRADE_OFFICER:
        return trade
    if role is RoleCode.CONSULAR_OFFICER:
        return consular
    if role is RoleCode.DIASPORA_OFFICER:
        return [*diaspora, *trade[1:]]
    if role in {RoleCode.DEPUTY, RoleCode.AMBASSADOR}:
        return [*trade, *consular, *diaspora]
    return [("governance.audit_event", (), Classification.MISSION_INTERNAL)]


#: The refusals. Each is a real rule from the matrix, not a decorative failure:
#: (attempting role, object type, zone of the thing refused, why).
_DENIALS: Final[tuple[tuple[RoleCode, str, Classification, str], ...]] = (
    (
        RoleCode.TRADE_OFFICER,
        "consular.case",
        Classification.CONSULAR_SENSITIVE,
        "holds no consular compartment and no read:consular_case grant",
    ),
    (
        RoleCode.DIASPORA_OFFICER,
        "consular.case",
        Classification.CONSULAR_SENSITIVE,
        "holds no consular compartment",
    ),
    (
        RoleCode.ADMIN,
        "opportunities.opportunity",
        Classification.MISSION_INTERNAL,
        "is not a content super-user: no read:opportunity grant and clearance rank 10",
    ),
    (
        RoleCode.ADMIN,
        "consular.case",
        Classification.CONSULAR_SENSITIVE,
        "is not a content super-user and holds no consular compartment",
    ),
    (
        RoleCode.CONSULAR_OFFICER,
        "opportunities.opportunity",
        Classification.MISSION_INTERNAL,
        "holds no read:opportunity grant: consular need-to-know is deep, not broad",
    ),
    (
        RoleCode.TRADE_OFFICER,
        "governance.export",
        Classification.MISSION_INTERNAL,
        "holds no export:bulk grant -- the narrowest grant in the matrix",
    ),
    (
        RoleCode.DIASPORA_OFFICER,
        "governance.export",
        Classification.MISSION_INTERNAL,
        "holds no export:bulk grant",
    ),
)

_STAGE_ACTIONS: Final[tuple[str, ...]] = (
    OPPORTUNITY_DETECTED,
    OPPORTUNITY_QUALIFIED,
    OPPORTUNITY_CONTACT_PLANNED,
    OPPORTUNITY_CONTACTED,
    OPPORTUNITY_MEETING_SCHEDULED,
    OPPORTUNITY_NEGOTIATION_OPENED,
    OPPORTUNITY_CLOSED,
    OPPORTUNITY_REVERTED,
)


def _plan(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    opportunities: Sequence[Opportunity],
    signals: Sequence[Signal],
    stakeholders: Sequence[Stakeholder],
    cases: Sequence[Case],
    profiles: Sequence[DiasporaProfile],
) -> list[_Row]:
    """Plan the whole history, then sort it into chronological order."""
    offsets = weighted_recent_offsets(
        ctx.rng, AUDIT_ROW_TARGET, AUDIT_WINDOW_DAYS, bias=_RECENCY_BIAS
    )
    deny_count = round(AUDIT_ROW_TARGET * AUDIT_DENY_SHARE)
    # Which positions in the sample are denials. Chosen up front so the share is exact
    # rather than approximately right after 450 independent coin flips.
    deny_positions = set(ctx.rng.sample(range(AUDIT_ROW_TARGET), deny_count))

    rows: list[_Row] = []
    for index, days_ago in enumerate(offsets):
        if index in deny_positions:
            rows.append(_denial_row(ctx, days_ago, opportunities, cases))
            continue
        role = _weighted_role(ctx)
        draw = ctx.rng.random()
        if draw < 0.27:
            rows.append(_session_row(ctx, days_ago, role, users))
        elif draw < 0.82:
            rows.append(
                _read_row(
                    ctx, days_ago, role, opportunities, signals, stakeholders, cases, profiles
                )
            )
        elif draw < 0.86 and role in {RoleCode.AMBASSADOR, RoleCode.DEPUTY}:
            rows.append(_export_row(ctx, days_ago, role, opportunities))
        else:
            rows.append(_transition_row(ctx, days_ago, role, opportunities))
    rows.sort(key=lambda row: -row.days_ago)
    return rows


def _session_row(
    ctx: SeedContext,
    days_ago: float,
    role: RoleCode,
    users: dict[RoleCode, User],
) -> _Row:
    principal = principal_for_role(role)
    return _Row(
        days_ago=days_ago,
        role=role,
        action=SESSION_ROLE_ASSUMED,
        object_type="governance.user",
        object_id=users[role].id,
        object_public_ref=None,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        summary=(f"{principal.full_name} assumed the {role.value} role via the demo role picker."),
        payload={
            "role": role.value,
            "clearance_rank": principal.clearance_rank,
            "permission_count": len(principal.permissions),
            "compartments": sorted(principal.compartments),
            "identity_source": "demo_role_picker",
        },
    )


def _read_row(
    ctx: SeedContext,
    days_ago: float,
    role: RoleCode,
    opportunities: Sequence[Opportunity],
    signals: Sequence[Signal],
    stakeholders: Sequence[Stakeholder],
    cases: Sequence[Case],
    profiles: Sequence[DiasporaProfile],
) -> _Row:
    targets = _read_targets(role, opportunities, signals, stakeholders, cases, profiles)
    object_type, candidates, zone = ctx.pick(targets)
    if not candidates:
        # ADMIN reads the log, which is the one privileged read it holds.
        return _Row(
            days_ago=days_ago,
            role=role,
            action=ACCESS_PRIVILEGED_READ,
            object_type="governance.audit_event",
            object_id=None,
            object_public_ref=None,
            policy_result=PolicyResult.ALLOW,
            classification=Classification.MISSION_INTERNAL,
            summary="Platform administrator queried the audit log.",
            payload={"filter": "recent", "limit": 50, "grant": "read:audit"},
        )
    target = ctx.pick(candidates)
    public_ref = getattr(target, "public_ref", None)
    label = getattr(target, "title", None) or getattr(target, "full_name", None) or object_type
    return _Row(
        days_ago=days_ago,
        role=role,
        action=ACCESS_PRIVILEGED_READ,
        object_type=object_type,
        object_id=target.id,
        object_public_ref=public_ref,
        policy_result=PolicyResult.ALLOW,
        classification=zone,
        summary=(
            f"{role.value} opened {object_type.split('.')[-1]} '{str(label)[:60]}'."
            if object_type != "consular.case"
            # Never put a case subject's name in the log: the log is a second, less
            # protected copy of whatever it quotes (ADR-0004).
            else f"{role.value} opened consular case {public_ref}."
        ),
        payload={"grant": f"read:{object_type.split('.')[-1]}", "surface": "detail"},
    )


def _export_row(
    ctx: SeedContext,
    days_ago: float,
    role: RoleCode,
    opportunities: Sequence[Opportunity],
) -> _Row:
    rows = 12 + ctx.rng.randrange(40)
    return _Row(
        days_ago=days_ago,
        role=role,
        action=EXPORT_PERFORMED,
        object_type="opportunities.opportunity",
        object_id=None,
        object_public_ref=None,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        summary=f"{role.value} exported {rows} pipeline rows.",
        payload={
            "row_count": rows,
            "filter": {"stage": "open", "sector_code": "CRITICAL_MINERALS"},
            "grant": "export:bulk",
            "format": "csv",
        },
    )


def _transition_row(
    ctx: SeedContext,
    days_ago: float,
    role: RoleCode,
    opportunities: Sequence[Opportunity],
) -> _Row:
    # Only the three roles that hold pipeline grants ever appear as the actor on a
    # transition; anyone else drawn here reads instead.
    if role not in {RoleCode.TRADE_OFFICER, RoleCode.DEPUTY, RoleCode.AMBASSADOR}:
        role = RoleCode.TRADE_OFFICER
    opportunity = ctx.pick(opportunities)
    action = (
        OPPORTUNITY_PARTNERED
        if role in {RoleCode.AMBASSADOR, RoleCode.DEPUTY} and ctx.rng.random() < 0.08
        else ctx.pick(_STAGE_ACTIONS)
    )
    event = action.split(".", 1)[1]
    return _Row(
        days_ago=days_ago,
        role=role,
        action=action,
        object_type="opportunities.opportunity",
        object_id=opportunity.id,
        object_public_ref=None,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        summary=f"{role.value} recorded '{event}' on '{opportunity.title[:60]}'.",
        payload={
            "event": event,
            "to_state": opportunity.stage.value,
            "reason": None,
            "trace_id": None,
        },
    )


def _denial_row(
    ctx: SeedContext,
    days_ago: float,
    opportunities: Sequence[Opportunity],
    cases: Sequence[Case],
) -> _Row:
    role, object_type, zone, reason = ctx.pick(_DENIALS)
    if object_type == "governance.export":
        return _Row(
            days_ago=days_ago,
            role=role,
            action=ACCESS_DENIED,
            object_type=object_type,
            object_id=None,
            object_public_ref=None,
            policy_result=PolicyResult.DENY,
            classification=zone,
            summary=f"{role.value} was refused a bulk export: {reason}.",
            payload={"required": ["export:bulk"], "held": False, "reason": reason},
        )
    target = ctx.pick(cases) if object_type == "consular.case" else ctx.pick(opportunities)
    return _Row(
        days_ago=days_ago,
        role=role,
        action=ACCESS_DENIED,
        object_type=object_type,
        object_id=target.id,
        object_public_ref=getattr(target, "public_ref", None),
        policy_result=PolicyResult.DENY,
        classification=zone,
        summary=f"{role.value} was refused access to a {object_type.split('.')[-1]}: {reason}.",
        payload={
            "required": [f"read:{object_type.split('.')[-1]}"],
            "zone": zone.value,
            "reason": reason,
        },
    )


def _rejected_transition_row(ctx: SeedContext, days_ago: float, opportunity: Opportunity) -> _Row:
    """A refused state transition -- the control from BUILD_BIBLE section 6, in the log."""
    return _Row(
        days_ago=days_ago,
        role=RoleCode.TRADE_OFFICER,
        action=OPPORTUNITY_TRANSITION_REJECTED,
        object_type="opportunities.opportunity",
        object_id=opportunity.id,
        object_public_ref=None,
        policy_result=PolicyResult.DENY,
        classification=Classification.MISSION_INTERNAL,
        summary=(
            "TRADE_OFFICER attempted to conclude a partnership and was refused: "
            "commit:opportunity is held by AMBASSADOR and DEPUTY only."
        ),
        payload={
            "event": "partner",
            "required": ["commit:opportunity"],
            "reason": "concluding a partnership is a commitment and is never delegated",
        },
    )


def seed_audit_history(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    opportunities: Sequence[Opportunity],
    signals: Sequence[Signal],
    stakeholders: Sequence[Stakeholder],
    cases: Sequence[Case],
    profiles: Sequence[DiasporaProfile],
) -> int:
    """Write the history through the audit writer. Returns the number of rows written."""
    existing = _already_seeded(ctx)
    if existing:
        return 0

    rows = _plan(ctx, users, opportunities, signals, stakeholders, cases, profiles)

    # Swap two of the plain denials for refused state transitions, so the log shows a
    # control refusing an action and not only a reader being turned away at the door.
    rejected_positions = [
        index for index, row in enumerate(rows) if row.policy_result is PolicyResult.DENY
    ][:2]
    for position in rejected_positions:
        rows[position] = _rejected_transition_row(
            ctx, rows[position].days_ago, ctx.pick(opportunities)
        )

    for row in rows:
        if not is_known_action(row.action):
            msg = (
                f"seeded audit action {row.action!r} is outside the closed vocabulary in "
                "app/audit/actions.py. The seed must not invent an action the API cannot "
                "also write."
            )
            raise ValueError(msg)
        payload = dict(row.payload)
        payload["seed_marker"] = SEED_MARKER
        write_audit_event(
            ctx.session,
            actor=principal_for_role(row.role),
            action=row.action,
            object_type=row.object_type,
            object_id=row.object_id,
            object_public_ref=row.object_public_ref,
            policy_result=row.policy_result,
            classification=row.classification,
            summary=row.summary,
            payload=payload,
            request_id=f"seed-{SEED_MARKER}",
            occurred_at=ctx.days_ago(row.days_ago),
        )
    ctx.session.flush()
    return len(rows)
