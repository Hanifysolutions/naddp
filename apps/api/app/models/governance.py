"""Governance: who exists, what they may do, and what they actually did.

Six tables, one bounded context (``BUILD_BIBLE.md`` section 8):

``users``
    Mission staff. Identity is faked by the role picker (ADR-0003); the people, the roles
    and the audit trail are not.
``roles`` / ``permissions`` / ``role_permissions``
    The deny-by-default RBAC matrix, persisted rather than hard-coded so that navigation
    can be *derived* from a principal's permissions instead of branching on their role
    (ADR-0003 rule 5).
``user_roles``
    Which persona holds which role, and who granted it.
``audit_events``
    The append-only record of every consequential decision (ADR-0004). This is the most
    important table in the repository.

**Why the five configuration tables carry no ``classification`` column.** ADR-0006
classifies *content*; a role definition and a permission code are configuration, and the
permission set is deliberately readable by its holder so that navigation can be generated
from it. ``audit_events`` is the exception and does carry a zone, because an audit row
describes content and can therefore be as sensitive as the thing it describes.

Cross-module reference declared here by table-name string only, with no ``relationship()``
and no import of the owning module: ``audit_events.trace_id -> ai_traces.id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import PolicyResult, RoleCode
from app.models.base import Base, pg_enum
from app.models.mixins import ClassifiedMixin, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "POLICY_RESULT_ENUM",
    "ROLE_CODE_ENUM",
    "USERS_EMAIL_LOWER_INDEX",
    "AuditEvent",
    "Permission",
    "Role",
    "RolePermission",
    "User",
    "UserRole",
]

# ---------------------------------------------------------------------------
# Shared native enum types
# ---------------------------------------------------------------------------
#
# Declared once at module level for the same reason ``mixins.CLASSIFICATION_ENUM`` is: a
# Postgres enum type is global to the schema, so two ``pg_enum(RoleCode, "role_code")``
# calls would be two SQLAlchemy objects both claiming to emit ``CREATE TYPE role_code``,
# and Alembic would have to be told which one owns the DDL. ``role_code`` is used twice in
# this module alone -- ``roles.code`` and ``audit_events.actor_role`` -- so this is not
# hypothetical. A model elsewhere needing either type imports the instance from here
# rather than minting a rival.

#: The six demo personas as a native Postgres enum (``role_code``).
ROLE_CODE_ENUM: Final[SAEnum] = pg_enum(RoleCode, "role_code")

#: ``ALLOW`` / ``DENY`` as a native Postgres enum (``policy_result``).
POLICY_RESULT_ENUM: Final[SAEnum] = pg_enum(PolicyResult, "policy_result")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A member of mission staff -- the human actor behind every audited event.

    In the demo a row here is a *persona* the presenter assumes from the role picker, for
    example "Senior Trade Commissioner, Nigerian High Commission, Canberra". Identity is
    the only faked part of the system (ADR-0003): the roles this user holds, the
    permissions those roles carry, and the audit rows their actions write are all real,
    and are the same code paths a pilot would run.

    **There is deliberately no password, no hash, no salt, no API key and no token column
    on this table, and there must never be one.** Sign-in is
    ``POST /v1/session/assume-role``, which mints a signed demo cookie; nothing in this
    system verifies a credential. A credential column would therefore be an attractive
    nuisance -- storage that looks authoritative, is never actually checked, and is the
    first thing a security reviewer (or an attacker) reaches for. When real authentication
    arrives it belongs to the identity provider, and this table gains a subject
    identifier, not a secret.

    Invariants a reader must know:

    * ``email`` is unique **case-insensitively**; see :data:`USERS_EMAIL_LOWER_INDEX`.
    * ``is_active`` is the deactivation switch. A departed officer is deactivated, never
      deleted, because ``audit_events.actor_user_id`` references this row under
      ``ON DELETE RESTRICT`` -- the log must always be able to name who acted.
    """

    __tablename__ = "users"
    __table_args__ = (
        {
            "comment": (
                "Mission staff personas. Contains NO credential of any kind: identity is "
                "faked by the role picker (ADR-0003) while authorisation is real."
            )
        },
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        comment=(
            "Work address, unique case-insensitively via uq_users_email_lower. 320 is the "
            "RFC 5321 maximum (64-char local part, '@', 255-char domain), so no real "
            "address is ever truncated."
        ),
    )
    full_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Display name as it appears in the role picker and on audit timelines.",
    )
    title: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        comment=(
            "Job title, e.g. 'Senior Trade Commissioner'. Nullable: a newly provisioned "
            "account may not have one yet, and an empty string would be a worse lie."
        ),
    )
    mission: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        comment=(
            "Posting, e.g. 'Nigerian High Commission, Canberra'. A plain string rather "
            "than a foreign key: the demo has one mission, and a missions table holding a "
            "single row would be structure without information."
        ),
    )
    is_demo_persona: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment=(
            "True for every seeded persona. Drives the DEMO / SYNTHETIC badge required on "
            "every screen (BUILD_BIBLE 11). Defaults to True so a row created by a path "
            "nobody thought about is badged rather than silently presented as real."
        ),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment=(
            "False deactivates the persona and hides it from the role picker. "
            "Deactivation, never deletion: audit_events references users ON DELETE "
            "RESTRICT so the log can always name its actor."
        ),
    )

    # Both booleans above take a Python-side default with no server_default, matching
    # ClassifiedMixin. alembic/env.py sets compare_server_default=True, and a server
    # default on a column the ORM always populates buys nothing while risking a spurious
    # autogenerate diff on every run. It also means a raw INSERT that bypasses the ORM
    # fails loudly on NOT NULL rather than quietly acquiring a value nobody chose.

    user_roles: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        foreign_keys="UserRole.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
        # No `secondary="user_roles"` shortcut to Role is offered here. user_roles carries
        # grant provenance (granted_at, granted_by), and a plain many-to-many collection
        # would discard exactly the columns that make a grant auditable.
    )
    granted_user_roles: Mapped[list[UserRole]] = relationship(
        back_populates="granted_by_user",
        foreign_keys="UserRole.granted_by",
        passive_deletes="all",
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="actor",
        foreign_keys="AuditEvent.actor_user_id",
        passive_deletes="all",
        # `passive_deletes="all"` is load-bearing, not a micro-optimisation. Without it,
        # deleting a User makes the ORM emit `UPDATE audit_events SET actor_user_id=NULL`
        # to detach the children -- an UPDATE the append-only trigger rejects, turning a
        # clean foreign-key RESTRICT violation into a confusing trigger exception. With
        # it, the ORM emits nothing and the foreign key refuses the delete, which is both
        # the intended behaviour and the intended error message.
    )


#: Case-insensitive uniqueness for ``users.email``.
#:
#: A functional index on ``lower(email)`` rather than a plain ``UNIQUE (email)``. Addresses
#: are typed by humans and arrive from a role picker, a seed file and (later) an identity
#: provider with inconsistent capitalisation. ``A.Okonkwo@...`` and ``a.okonkwo@...`` are
#: one person, and a plain unique constraint would happily store both, after which the
#: resolver picks whichever row it finds first -- two principals, one human, no error. The
#: index also backs the case-insensitive lookup the resolver actually runs.
#:
#: Declared at module scope rather than in ``__table_args__`` because the expression needs
#: the mapped attribute ``User.email``, which exists only once the class body has finished.
#: Constructing the Index registers it on the ``users`` table automatically. Its name is
#: explicit because NAMING_CONVENTION cannot look inside an expression to derive one.
USERS_EMAIL_LOWER_INDEX: Final[Index] = Index(
    "uq_users_email_lower",
    func.lower(User.email),
    unique=True,
)


class Role(UUIDPrimaryKeyMixin, Base):
    """One of the six demo roles, with its clearance rank and its compartments.

    ``clearance_rank`` and ``compartments`` are transcribed from
    ``data/taxonomy/classifications.json`` (``role_ranks``) and must not be invented:
    AMBASSADOR 40, DEPUTY 30, CONSULAR_OFFICER 20 with ``["consular"]``, TRADE_OFFICER 20,
    DIASPORA_OFFICER 20, ADMIN 10. Two of those are worth stating out loud, because they
    are the interesting cases and a reviewer will probe them:

    * ``CONSULAR_OFFICER`` is rank 20, deliberately **not** 30. A consular officer's
      need-to-know is deep, not broad, so rank alone must not open a negotiating position.
    * ``ADMIN`` is rank 10 with **no** compartments. It administers the platform and reads
      the audit log; it is explicitly not a content super-user (ADR-0003), which is the
      failure mode of most demo RBAC.

    Rank plus compartment answers "may this principal read content in this zone"
    (ADR-0006). Permissions answer the separate question "may this role perform this verb
    on this object type". Both gates must pass, and neither implies the other.

    Only ``created_at`` is carried, not the full :class:`TimestampMixin`. A role definition
    is reference data seeded once; an ``updated_at`` would imply an editing workflow that
    does not exist, and changing a role's clearance is a migration plus an audit row, not
    a silent UPDATE.
    """

    __tablename__ = "roles"
    __table_args__ = (
        {
            "comment": (
                "The six demo roles. clearance_rank and compartments are transcribed from "
                "data/taxonomy/classifications.json role_ranks (ADR-0006)."
            )
        },
    )

    code: Mapped[RoleCode] = mapped_column(
        ROLE_CODE_ENUM,
        nullable=False,
        unique=True,
        comment=(
            "Stable identifier, and the value the role picker and the audit log speak. A "
            "native enum, so the database refuses a seventh role whichever client wrote it."
        ),
    )
    label: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Human-readable name for the UI, e.g. 'Deputy Head of Mission'.",
    )
    description: Mapped[str] = mapped_column(
        nullable=False,
        comment="What this role is accountable for. Shown in the role picker.",
    )
    clearance_rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=(
            "ADR-0006 clearance rank: 40 AMBASSADOR, 30 DEPUTY, 20 CONSULAR_OFFICER / "
            "TRADE_OFFICER / DIASPORA_OFFICER, 10 ADMIN. Compared against a zone's "
            "min_role_rank_to_read. Higher rank alone never satisfies a compartment."
        ),
    )
    compartments: Mapped[list[str]] = mapped_column(
        nullable=False,
        default=list,
        comment=(
            "Need-to-know compartments held, e.g. ['consular']. JSONB because the access "
            "predicate reads the list whole and never joins against it. Empty for "
            "TRADE_OFFICER, DIASPORA_OFFICER and ADMIN, which is precisely why none of "
            "them can open a consular case file."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Database clock, so seed, API and migration rows share one time source.",
    )

    role_permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions",
        viewonly=True,
        # Read-only convenience for the security matrix loader and the permission-derived
        # navigation payload, which want the permission set and nothing else.
        # `viewonly=True` is required rather than tidy: role_permissions is also mapped as
        # an association object above, and two writable paths onto the same rows race.
        # Grants are *written* through `role_permissions` and *read* through here. Unlike
        # user_roles, role_permissions carries no provenance columns, so this shortcut
        # discards nothing.
    )


class Permission(UUIDPrimaryKeyMixin, Base):
    """One capability in the deny-by-default RBAC matrix.

    Codes are ``verb:object`` -- ``read:consular_case``, ``approve:meeting_followup``,
    ``close:opportunity``, ``export:bulk``. The order is settled and it is not cosmetic:
    the verb is the part that varies by role and the part a human scans a grant table for,
    and every permission in ``docs/workflows.md`` is written this way. ``object.verb`` is
    wrong here.

    Deny-by-default means the *absence* of a grant row is a denial. There is no implicit
    "authenticated users may read" tier, and an endpoint with no ``require(...)``
    dependency is a bug rather than a public endpoint.

    ``is_sensitive`` marks the permissions that gate a consequential action or a bulk
    extraction. It is a presentation and review aid -- the UI badges them, the audit reader
    highlights them -- and never an authorisation input on its own. Authorisation is the
    matrix plus the ADR-0006 classification check; a third, implicit gate hiding in a
    boolean would make the real rule harder to reason about, not safer.

    ``export:bulk`` deserves its own sentence, because it is the narrowest grant in the
    matrix (AMBASSADOR and DEPUTY only) and is never implied by any read permission
    (ADR-0003 rule 4). Reading one record and extracting the whole table are different
    risks, and the second is the shape of virtually every real data-loss incident.

    ``created_at`` only, for the same reason as :class:`Role`: this is seeded reference
    data, and a permission code that changed meaning after grants were issued would
    silently rewrite what those grants allow.
    """

    __tablename__ = "permissions"
    __table_args__ = (
        {
            "comment": (
                "The RBAC capability vocabulary, verb:object. Deny-by-default: no grant "
                "row means denied (ADR-0003)."
            )
        },
    )

    code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        comment=(
            "verb:object, e.g. 'approve:meeting_followup'. Matches app/security/matrix.py "
            "and docs/workflows.md character for character; a mismatch is a silent denial."
        ),
    )
    label: Mapped[str] = mapped_column(
        String(96),
        nullable=False,
        comment="Human-readable name, e.g. 'Approve a meeting follow-up'.",
    )
    description: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "What holding this permission lets a principal do, and -- as importantly -- "
            "what it does not."
        ),
    )
    is_sensitive: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment=(
            "True for a BUILD_BIBLE section 6 non-autonomous control "
            "(commit:opportunity, approve/send:meeting_followup, "
            "triage/resolve/close:consular_case) or a bulk extraction (export:bulk). "
            "Advisory for UI and audit review only; never an authorisation input."
        ),
    )
    bounded_context: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment=(
            "Owning module: intelligence, opportunities, stakeholders, meetings, "
            "consular, diaspora, knowledge or governance (BUILD_BIBLE 8). Groups the "
            "permission-derived navigation, so a role holding nothing in a context sees "
            "no section for it."
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Database clock, so seed, API and migration rows share one time source.",
    )

    role_permissions: Mapped[list[RolePermission]] = relationship(
        back_populates="permission",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RolePermission(Base):
    """A grant: this role holds this permission. The RBAC matrix, one row per tick.

    Mapped as an association object rather than a bare ``Table`` so that both sides can
    declare a typed ``relationship()``, and so that a future column (a grant expiry, a
    scope qualifier) does not require restructuring the mapping.

    The primary key is the pair ``(role_id, permission_id)``, which makes the invariant
    structural: a role cannot hold the same permission twice, so "does this role hold X"
    can never return an ambiguous two rows. There is no surrogate ``id``, because a
    surrogate on a pure join table adds a column, an index and a unique constraint that
    must then be maintained separately in order to say the same thing.

    Both foreign keys are ``ON DELETE CASCADE``: a grant is meaningless without both ends,
    and an orphaned grant row would be a permission nobody can see and nobody revoked.
    Deleting a role or a permission is itself an audited administrative act; the cascade
    removes the grants and the audit row records that it happened.
    """

    __tablename__ = "role_permissions"
    __table_args__ = (
        {
            "comment": (
                "Role-to-permission grants. Composite PK (role_id, permission_id), so a "
                "grant cannot be duplicated."
            )
        },
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        comment="The role receiving the capability.",
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        comment="The capability granted.",
    )

    role: Mapped[Role] = relationship(back_populates="role_permissions")
    permission: Mapped[Permission] = relationship(back_populates="role_permissions")


class UserRole(Base):
    """An assignment: this user holds this role, granted at this time by this person.

    Composite primary key ``(user_id, role_id)`` -- a user holds a role or does not, and
    holding it twice is not a state the schema should be able to represent.

    ``granted_at`` and ``granted_by`` are the reason this is an association object rather
    than a plain many-to-many table. "Who gave this officer consular access, and when" is
    a question an auditor asks, and a bare join table cannot answer it. The definitive
    history remains the ``role.granted`` / ``role.revoked`` rows in ``audit_events``; these
    two columns are the denormalised *current* answer, so the common question does not
    require scanning the log.

    ``user_id`` cascades on delete -- an assignment to a deleted user is meaningless --
    while ``granted_by`` deliberately does not; see the column comment.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        {
            "comment": (
                "Role assignments with grant provenance. Composite PK (user_id, role_id). "
                "The authoritative history is audit_events; these columns are the current "
                "answer."
            )
        },
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="The persona holding the role.",
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
        comment="The role held.",
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the assignment was made. Database clock, not the caller's.",
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        comment=(
            "Who issued the grant. NULL means the seed or a system bootstrap made it, "
            "which is a genuinely different fact from 'a human granted it'. ON DELETE "
            "RESTRICT rather than SET NULL so the two cannot be conflated: deleting a "
            "user who issued grants requires dealing with those grants first, instead of "
            "quietly rewriting them into system grants."
        ),
    )

    user: Mapped[User] = relationship(
        back_populates="user_roles",
        foreign_keys=[user_id],
    )
    role: Mapped[Role] = relationship()
    granted_by_user: Mapped[User | None] = relationship(
        back_populates="granted_user_roles",
        foreign_keys=[granted_by],
        # `foreign_keys` is mandatory on both User-facing relationships here: this table
        # has two foreign keys into `users`, and without it SQLAlchemy cannot tell which
        # one to join on.
    )


class AuditEvent(UUIDPrimaryKeyMixin, ClassifiedMixin, Base):
    """The append-only record of every consequential decision (ADR-0004).

    One row per event: a workflow transition, an approval, a denial, a privileged read, an
    export, an assumed role, an AI generation that produced a persisted artefact. Written
    **inside the business transaction** that made the change, so the log cannot drift from
    application state -- if the transition commits the audit row commits, and if the audit
    write fails the transition rolls back. An audit log written on a best-effort basis
    after commit is exactly as reliable as the code path that was supposed to write it.

    Denials are recorded, not only successes (``policy_result``). A security posture that
    logs only what worked cannot demonstrate to anybody that it works.

    **UPDATE and DELETE on this table are blocked by a database trigger created in the
    migration that creates the table** (``trg_audit_events_append_only`` and
    ``trg_audit_events_no_truncate``), alongside
    ``REVOKE UPDATE, DELETE, TRUNCATE`` on the application role. Never rely on the ORM as
    the only guard: a SQLAlchemy ``before_flush`` listener gives a clear Python-level error
    at the point of the bug, which is worth having, but it protects exactly one process and
    is bypassed by ``session.execute(update(...))``, by ``psql``, by a migration, and by
    any other client. The trigger is the layer that actually holds, and it is the one that
    fires in local development and CI. If you find yourself wanting to update an audit row,
    the answer is to **append a compensating event** (``*.corrected``) whose ``payload``
    references the earlier row's ``id``: the reader presents the corrected timeline and the
    storage layer stays dumb and immutable.

    There is deliberately **no ``updated_at``**, which is why this class does not use
    :class:`TimestampMixin`. An append-only row is never updated, and a column implying
    otherwise would eventually attract code that tries.

    What must never be written here: request bodies, document contents, consular case
    narrative, or personal identifiers beyond internal IDs. ``payload`` carries references,
    not payloads -- the audit log must not become a second, less protected copy of the
    sensitive data it describes.

    Ordering: ``id`` is a ULID, so ``ORDER BY id`` is a valid chronological total order that
    does not depend on trusting ``occurred_at`` (ADR-0007). ``occurred_at`` remains the
    semantic timestamp and is indexed for range queries.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        # Index(None, ...) lets NAMING_CONVENTION derive the name, which is what keeps a
        # composite index nameable -- and therefore droppable -- in a later migration.
        # (object_type, object_id) answers "show me this object's history"; the pair is
        # needed because object_id is not unique across the 26 tables it can point at.
        Index(None, "object_type", "object_id"),
        # (actor_user_id, occurred_at) answers "what did this person do, most recent
        # first" -- the question an auditor asks, and the one the trace drawer runs.
        Index(None, "actor_user_id", "occurred_at"),
        {
            "comment": (
                "APPEND-ONLY (ADR-0004). INSERT and SELECT only; UPDATE and DELETE are "
                "refused by the trg_audit_events_append_only and "
                "trg_audit_events_no_truncate triggers, and by REVOKE. "
                "Corrections are appended, "
                "never applied."
            )
        },
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
        comment=(
            "When the event happened, on the database clock rather than the caller's. A "
            "caller-supplied timestamp in an evidentiary table is a caller-controlled fact."
        ),
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        comment=(
            "Who acted. NULL for system actions -- scheduled jobs, the seed loader, "
            "startup migrations -- which have no human actor and must not borrow one. ON "
            "DELETE RESTRICT: an audited user can never be deleted out from under the "
            "log, so 'who did this' stays answerable months later."
        ),
    )
    actor_role: Mapped[RoleCode | None] = mapped_column(
        ROLE_CODE_ENUM,
        nullable=True,
        comment=(
            "The role the actor was acting in AT THE TIME. Denormalised on purpose: role "
            "assignments change, and a log that resolved the role by joining user_roles "
            "would silently rewrite history the moment somebody was promoted. NULL "
            "alongside a NULL actor for system actions."
        ),
    )
    action: Mapped[str] = mapped_column(
        String(96),
        nullable=False,
        index=True,
        comment=(
            "Closed vocabulary from app/audit/actions.py, dotted and snake_case: "
            "'opportunity.qualified', 'meeting_followup.sent', 'case.closed', "
            "'export.performed', 'session.role_assumed'. Must match docs/workflows.md "
            "character for character."
        ),
    )
    object_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment=(
            "Bounded-context-qualified entity name, e.g. 'opportunities.opportunity', "
            "'consular.case'. Qualified so two contexts may both own a 'case' without the "
            "log becoming ambiguous."
        ),
    )
    object_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        comment=(
            "The affected row's ULID. Deliberately NOT a foreign key: the log outlives "
            "what it describes, points at 26 different tables, and must stay readable "
            "after demo-reset drops them. NULL for an event with no single object, such "
            "as a session or a failed login."
        ),
    )
    object_public_ref: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment=(
            "The object's citizen-facing reference where it has one (cases.public_ref, "
            "ADR-0007). Stored so an auditor can search the log by the reference a "
            "citizen quoted over the phone, without first resolving it to an internal ID."
        ),
    )
    policy_result: Mapped[PolicyResult] = mapped_column(
        POLICY_RESULT_ENUM,
        nullable=False,
        comment=(
            "ALLOW or DENY. Denials are recorded, which is what makes the RBAC story "
            "demonstrable rather than merely assertable (ADR-0003)."
        ),
    )
    request_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment=(
            "Correlates every row emitted by one HTTP request, and joins the structlog "
            "request log and the ai_traces row. NOT NULL: a non-HTTP actor (seed loader, "
            "scheduled job) mints its own correlation id rather than leaving the chain "
            "broken."
        ),
    )
    # The column type is stated explicitly here, and only here. SQLAlchemy skips the
    # `type_annotation_map` for any column carrying a ForeignKey and resolves the type from
    # the referenced column instead -- which works for the same-module keys above, but
    # leaves this one as NullType whenever `app.models.ai` has not been imported.
    # `app.models.base` always imports it, so DDL is never actually affected; stating the
    # type anyway means this table can be inspected on its own without a silent NullType,
    # and it costs nothing because ADR-0007 makes every primary key a `uuid`.
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ai_traces.id", ondelete="RESTRICT"),
        nullable=True,
        comment=(
            "The AI Gateway trace that informed this event, where one did. NULL for a "
            "purely human action -- and the fact that it is usually NULL is itself the "
            "point: it shows at a glance which decisions involved AI at all."
        ),
    )
    summary: Mapped[str] = mapped_column(
        nullable=False,
        comment=(
            "One human-readable sentence, rendered directly in the trace drawer and the "
            "audit timeline, e.g. 'Deputy approved the Covalent follow-up email'. "
            "References, never content: no case narrative, no document text."
        ),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        nullable=False,
        default=dict,
        comment=(
            "Structured, bounded, non-sensitive context. For a transition, exactly "
            "{from_state, to_state, event, reason, trace_id} (docs/workflows.md 0.5); for "
            "an export, the row count and the filter used. JSONB because the trace drawer "
            "queries it. References, not payloads: never a request body or document text."
        ),
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        comment=(
            "Client address; 45 characters fits an IPv4-mapped IPv6 literal. NULL for "
            "system actions, which have no client. A sentinel such as 'system' would be "
            "fabricated data in an evidentiary table, so NULL is the honest value."
        ),
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment=(
            "Client user agent, truncated at 256 characters. NULL for system actions, for "
            "the same reason as ip_address."
        ),
    )
    event_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment=(
            "SHA-256 hex digest over this row's canonical serialisation together with "
            "prev_event_hash, forming a tamper-evident chain. Editing or removing any row "
            "breaks every subsequent link, so a verifier detects tampering even where the "
            "attacker held enough privilege to bypass both the trigger and the grants."
        ),
    )
    prev_event_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "event_hash of the immediately preceding row in ULID order. NULL for the "
            "genesis row only; a NULL anywhere else is itself evidence of a break."
        ),
    )

    actor: Mapped[User | None] = relationship(
        back_populates="audit_events",
        foreign_keys=[actor_user_id],
    )

    # No relationship() to ai_traces: it lives in app/models/ai.py, and a model module
    # must never import a sibling (import cycles between model modules are forbidden). The
    # foreign key is declared by table-name string above, which is all the database needs;
    # the AI module owns the other side if it ever wants one.
