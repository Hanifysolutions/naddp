"""Role assumption: the demo's stand-in for signing in.

Domain logic for ``POST /v1/session/assume-role``, kept out of the route handler
(``CLAUDE.md`` rule 5). Two things happen, in one transaction:

1. The demo persona's ``users`` row is materialised if it does not exist yet.
2. A ``session.role_assumed`` audit row is appended.

**Why the endpoint provisions the user row.** ``audit_events.actor_user_id`` is a foreign
key with ``ON DELETE RESTRICT``: the log must always be able to name who acted, so the row
has to exist before the log can point at it. Persona ids are deterministic
(``app.security.principal.DEMO_PERSONA_NAMESPACE``), so this insert and the seed loader's
produce the same six rows and neither depends on running first. Assuming a role in a demo
*is* the provisioning act for that persona; making the endpoint self-sufficient means the
identity story cannot break because the seed has not been run yet.

**Why it is audited.** ADR-0004 lists ``session.role_assumed`` in the closed action
vocabulary and requires authentication events to be recorded automatically. A believable,
lived-in audit timeline that shows who was in the system and when is part of the demo, and
it is the row that makes every later row attributable.

What this module deliberately does *not* do: create ``roles``, ``permissions`` or
``user_roles`` rows. Authorisation is answered by ``app.security.matrix``, which is the
single source of truth (``docs/OPEN_QUESTIONS.md`` Q-02b); the tables are the seed track's
mirror of it for provenance and for the admin screens, and two writers competing over
reference data is how the mirror ends up disagreeing with the original.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.writer import write_audit_event
from app.core.logging import get_logger
from app.domain.enums import Classification, PolicyResult, RoleCode
from app.models.governance import User
from app.security.principal import (
    DemoPersona,
    Principal,
    demo_persona,
    principal_for_role,
)

__all__ = [
    "SESSION_OBJECT_TYPE",
    "SESSION_ROLE_ASSUMED",
    "assume_role",
    "ensure_persona_user",
]

_logger = get_logger(__name__)

#: The audit action for a role assumption, named verbatim in ADR-0004.
#:
#: Declared here rather than imported because ``app/audit/actions.py`` -- the closed
#: vocabulary module -- is owned by the workflow track and does not exist yet. When it
#: lands it must carry this exact string: ``docs/workflows.md`` rule 0.4 requires the
#: action values to match character for character, and a mismatch is a row nobody queries.
SESSION_ROLE_ASSUMED: Final[str] = "session.role_assumed"

#: Bounded-context-qualified object type for the persona the event is about.
SESSION_OBJECT_TYPE: Final[str] = "governance.user"


def ensure_persona_user(session: Session, persona: DemoPersona) -> User:
    """Return the ``users`` row for ``persona``, creating it on first use.

    Idempotent and safe to race. The insert runs inside a SAVEPOINT so that a concurrent
    caller losing the primary-key race rolls back only its own insert, not the surrounding
    business transaction, and then re-reads the row the winner committed. Handling this is
    not premature: the role picker is the one endpoint a presenter can double-click.
    """
    existing = session.get(User, persona.user_id)
    if existing is not None:
        return existing

    user = User(
        id=persona.user_id,
        email=persona.email,
        full_name=persona.full_name,
        title=persona.title,
        mission=persona.mission,
        is_demo_persona=True,
        is_active=True,
    )
    try:
        with session.begin_nested():
            session.add(user)
    except IntegrityError:
        session.expunge(user)
        raced = session.get(User, persona.user_id)
        if raced is None:
            # The insert failed for a reason other than losing the race -- a duplicate
            # email under a different id, say. Re-raising is correct: silently continuing
            # would write an audit row naming a user that does not exist.
            raise
        _logger.info("session.persona_insert_raced", role=persona.role.value)
        return raced

    _logger.info("session.persona_provisioned", role=persona.role.value, user_id=str(user.id))
    return user


def assume_role(
    session: Session,
    *,
    role: RoleCode,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> Principal:
    """Provision the persona, record the assumption, and return the resulting principal.

    Commits. The audit row and the persona insert are in the same transaction, so a failure
    to record the assumption leaves no half-assumed session behind.

    Args:
        session: Request-scoped session.
        role: The role being assumed.
        ip_address: Client address, recorded on the audit row.
        user_agent: Client user agent, recorded on the audit row.

    Returns:
        The :class:`~app.security.principal.Principal` the caller now acts as. The cookie
        that carries it is the route handler's business, not this module's.
    """
    persona = demo_persona(role)
    principal = principal_for_role(role)
    ensure_persona_user(session, persona)

    write_audit_event(
        session,
        actor=principal,
        action=SESSION_ROLE_ASSUMED,
        object_type=SESSION_OBJECT_TYPE,
        object_id=principal.user_id,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        summary=(
            f"{principal.full_name} ({persona.title}) assumed the {role.value} role "
            "via the demo role picker."
        ),
        payload={
            # References and counts, never the permission list itself: the log records
            # that a role was assumed, and the matrix records what that role could do.
            "role": role.value,
            "clearance_rank": principal.clearance_rank,
            "permission_count": len(principal.permissions),
            "compartments": sorted(principal.compartments),
            "identity_source": "demo_role_picker",
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.commit()

    _logger.info("session.role_assumed", role=role.value, user_id=str(principal.user_id))
    return principal
