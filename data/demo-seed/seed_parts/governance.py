"""Users, roles, permissions and grants.

Every row here is **derived**, never retyped. ``users`` comes from
``app.security.principal.DEMO_PERSONAS`` (whose ids are deterministic ``uuid5`` values, so
the API's own persona provisioning and this loader agree rather than race); ``permissions``
comes from ``app.security.permissions.PERMISSION_SPECS``; ``role_permissions`` comes from
``app.security.matrix.ROLE_PERMISSIONS``; and clearance ranks and compartments come from
``ROLE_CLEARANCE_RANK`` / ``ROLE_COMPARTMENTS``, which themselves read
``data/taxonomy/classifications.json``.

That is the point. A hand-written copy of the matrix in the seed would drift from the one
the API enforces, and the drift would present as "the demo works but the RBAC screen
disagrees with it" -- which is worse than either being wrong on its own.
"""

from __future__ import annotations

from typing import Final

from app.domain.enums import RoleCode
from app.models.governance import Permission, Role, RolePermission, User, UserRole
from app.security.matrix import ROLE_PERMISSIONS
from app.security.permissions import PERMISSION_SPECS
from app.security.principal import DEMO_PERSONAS, ROLE_CLEARANCE_RANK, ROLE_COMPARTMENTS
from seed_parts.context import SeedContext

__all__ = ["ROLE_LABELS", "seed_governance"]

#: Display names for the role picker. The persona *titles* live on ``users`` and are the
#: job; these are the role.
ROLE_LABELS: Final[dict[RoleCode, str]] = {
    RoleCode.AMBASSADOR: "High Commissioner",
    RoleCode.DEPUTY: "Deputy Head of Mission",
    RoleCode.TRADE_OFFICER: "Trade Officer",
    RoleCode.CONSULAR_OFFICER: "Consular Officer",
    RoleCode.DIASPORA_OFFICER: "Diaspora Engagement Officer",
    RoleCode.ADMIN: "Platform Administrator",
}

#: What each role is accountable for, shown under its name in the role picker. Transcribed
#: from ``data/taxonomy/classifications.json`` ``role_ranks[].description`` so that the
#: sentence a user reads is the same sentence that justifies the clearance rank.
ROLE_DESCRIPTIONS: Final[dict[RoleCode, str]] = {
    RoleCode.AMBASSADOR: (
        "Head of mission; accountable for everything the mission does. Holds the consular "
        "compartment and the two commitment controls -- concluding a partnership and "
        "approving an outbound diplomatic communication -- but does not work consular "
        "cases day to day."
    ),
    RoleCode.DEPUTY: (
        "Acts for the Ambassador and needs equivalent reach: the full opportunity "
        "pipeline, follow-up approval, and the consular casework grants, plus the audit "
        "log and bulk export."
    ),
    RoleCode.TRADE_OFFICER: (
        "Full working access to trade and intelligence material and no consular "
        "need-to-know. Runs the pipeline day to day, drafts and sends follow-ups, and "
        "cannot approve one -- separation of duties, enforced server-side."
    ),
    RoleCode.CONSULAR_OFFICER: (
        "Working tier plus the consular compartment: creates, triages, works, resolves and "
        "closes cases. Deliberately not rank 30 -- a consular officer's need-to-know is "
        "deep, not broad -- so the trade pipeline is not visible from here."
    ),
    RoleCode.DIASPORA_OFFICER: (
        "Working tier for diaspora capability and stakeholder engagement. Diaspora "
        "profiles are governed by recorded consent rather than by the consular "
        "compartment, which is why this role holds no compartment at all."
    ),
    RoleCode.ADMIN: (
        "Platform administration and audit reading. Explicitly NOT a content super-user "
        "(ADR-0003): holds no read permission over intelligence, opportunities, "
        "stakeholders, cases or diaspora, and clearance rank 10 would not open them if it "
        "did."
    ),
}


def seed_governance(ctx: SeedContext) -> dict[RoleCode, User]:
    """Load users, roles, permissions and both grant tables. Returns the persona users."""
    permissions = _seed_permissions(ctx)
    roles = _seed_roles(ctx)
    _seed_role_permissions(ctx, roles, permissions)
    return _seed_users(ctx, roles)


def _seed_permissions(ctx: SeedContext) -> dict[str, Permission]:
    """One row per member of ``PERMISSION_SPECS`` -- all 40, deny-by-default vocabulary."""
    rows: dict[str, Permission] = {}
    for code, spec in PERMISSION_SPECS.items():
        rows[code.value] = ctx.upsert(
            Permission,
            ctx.register("permission", f"perm-{code.value}"),
            code=code.value,
            label=spec.label,
            description=spec.description,
            is_sensitive=spec.is_sensitive,
            bounded_context=spec.bounded_context,
        )
    return rows


def _seed_roles(ctx: SeedContext) -> dict[RoleCode, Role]:
    """The six roles, with the clearance ranks and compartments the taxonomy declares."""
    rows: dict[RoleCode, Role] = {}
    for role in RoleCode:
        rows[role] = ctx.upsert(
            Role,
            ctx.register("role", f"role-{role.value.lower()}"),
            code=role,
            label=ROLE_LABELS[role],
            description=ROLE_DESCRIPTIONS[role],
            clearance_rank=ROLE_CLEARANCE_RANK[role],
            compartments=sorted(ROLE_COMPARTMENTS[role]),
        )
    return rows


def _seed_role_permissions(
    ctx: SeedContext,
    roles: dict[RoleCode, Role],
    permissions: dict[str, Permission],
) -> None:
    """Materialise ``ROLE_PERMISSIONS`` -- the authoritative matrix -- as grant rows."""
    ctx.session.flush()
    for role, granted in ROLE_PERMISSIONS.items():
        for permission in sorted(granted, key=lambda item: item.value):
            ctx.upsert(
                RolePermission,
                {
                    "role_id": roles[role].id,
                    "permission_id": permissions[permission.value].id,
                },
            )


def _seed_users(ctx: SeedContext, roles: dict[RoleCode, Role]) -> dict[RoleCode, User]:
    """The six demo personas, at the ids ``DEMO_PERSONAS`` already fixes.

    The ids are **not** minted by :func:`~seed_parts.context.mint_id`: they are the
    ``uuid5`` values ``app.security.principal`` derives, because ``app.services.session``
    provisions the same rows on first role assumption. Two different id schemes would give
    the demo two Ngozi Ezes, one of whom owns the pipeline and neither of whom is the one
    the role picker signs you in as.
    """
    rows: dict[RoleCode, User] = {}
    for role, persona in DEMO_PERSONAS.items():
        ctx.manifest.setdefault("user", {})[f"user-{role.value.lower()}"] = str(persona.user_id)
        rows[role] = ctx.upsert(
            User,
            persona.user_id,
            email=persona.email,
            full_name=persona.full_name,
            title=persona.title,
            mission=persona.mission,
            is_demo_persona=True,
            is_active=True,
        )
    ctx.session.flush()
    for role, user in rows.items():
        ctx.upsert(
            UserRole,
            {"user_id": user.id, "role_id": roles[role].id},
            granted_by=None,
        )
    return rows
