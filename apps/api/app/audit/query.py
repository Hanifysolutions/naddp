"""Reading the audit log: filters, pagination and chain verification.

``app/audit/writer.py`` appends; this module reads. It is the only supported read path, and
it is a **service**, not a route handler helper (``CLAUDE.md`` rule 5): the API layer parses
query strings and renders JSON, and every rule about who may see which row lives here.

Two gates, both applied before any row is loaded
------------------------------------------------

1. **Permission.** ``read:audit``, held by ``AMBASSADOR``, ``DEPUTY`` and ``ADMIN``
   (``docs/OPEN_QUESTIONS.md`` Q-02b). Checked here as well as on the route, because a
   service that trusts its callers to have authorised is a service that is one refactor
   away from being called from somewhere that did not.

2. **Classification, in SQL.** ``WHERE classification IN (...)`` is built from
   :func:`app.security.principal.readable_classifications_for` and put into the statement
   before it runs (``CLAUDE.md`` rule 5, ADR-0006). Never afterwards in Python. Filtering
   after the query returns the right rows and the wrong counts, and the counts are what
   leak: a total of 41 with 38 rows rendered tells the reader exactly how many records they
   are not cleared for. ``total_matching`` is computed under the *identical* predicate for
   the same reason, so it can never disagree with the page.

   This is where separation of duties becomes visible rather than asserted. ``ADMIN``
   holds ``read:audit`` and has clearance rank 10 with no compartments, so ``ADMIN`` reads
   the ``PUBLIC`` and ``MISSION_INTERNAL`` rows and the ``CONSULAR_SENSITIVE`` rows are not
   merely redacted for them -- they are never selected. The platform administrator can
   prove who opened a case file without being able to read one.

Pagination is keyset, not offset
--------------------------------

``ORDER BY id DESC`` with ``WHERE id < cursor``. ``id`` is a ULID rendered into a UUID
(ADR-0007), so its byte order is time order and this is both a stable sort and a
chronological one without trusting the clock column. Offset pagination on an append-only
table that is being written to *while it is read* -- which is exactly what happens here,
since reading a privileged page writes a row -- silently skips and repeats records as the
offsets shift underneath. A keyset cursor cannot.

Chain verification is deliberately not classification-filtered
--------------------------------------------------------------

:func:`verify_audit_chain` walks every row, including rows the caller may not read. It has
to: the chain links each row to its immediate predecessor, so a filtered walk would report
a break at every row the filter removed and would be worthless as evidence. What it
discloses is a boolean, a count, and -- only on failure -- one row id and its position. No
content, no actor, no action. That is the right trade, and it is stated here rather than
left for a reader to notice.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Final

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from app.audit.writer import ChainVerification, verify_chain
from app.core.errors import AppError, PermissionDeniedError
from app.domain.enums import CLASSIFICATION_RANK, Classification, PolicyResult, RoleCode
from app.models.governance import AuditEvent
from app.security.permissions import Permission
from app.security.principal import Principal, readable_classifications_for

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_CHAIN_VERIFY_LIMIT",
    "MAX_PAGE_SIZE",
    "AuditFilter",
    "AuditFilterError",
    "AuditPage",
    "describe_filter",
    "list_audit_events",
    "summarise_classifications",
    "verify_audit_chain",
]

#: Page size when the caller does not ask for one. Large enough that the demo's audit
#: timeline fills a screen in one request; small enough that a page is not a bulk export by
#: another name (``export:bulk`` is a separate permission and this endpoint is not it).
DEFAULT_PAGE_SIZE: Final[int] = 50

#: Hard ceiling on a page. ``export:bulk`` exists precisely so that "read a record" and
#: "extract the table" are different grants; letting ``limit`` grow without bound would
#: quietly merge them.
MAX_PAGE_SIZE: Final[int] = 200

#: Ceiling on a bounded chain verification. Verifying the whole table stays available by
#: passing ``limit=None``, which is what an auditor wants and what the demo does at its
#: current volume; the ceiling exists so a caller cannot ask for an arbitrary large-but-
#: bounded walk that is slower than the full one and harder to reason about.
MAX_CHAIN_VERIFY_LIMIT: Final[int] = 10_000


class AuditFilterError(AppError):
    """The requested audit filter is self-contradictory.

    An :class:`~app.core.errors.AppError` rather than a bare ``ValueError``: the caller
    asked for something impossible and can fix it, so it renders as a 422 problem document
    naming what was wrong instead of an opaque 500.

    Declared here rather than in ``app.core.errors`` because it is meaningful only to this
    service. The handler registered for ``AppError`` renders every subclass, wherever it
    lives.
    """

    code = "invalid_audit_filter"
    status_code = 422
    title = "Invalid Audit Filter"
    default_detail = "The audit query filter is not valid."


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """What the caller is looking for. Every field is optional and ANDed with the rest.

    Frozen: a filter is a question already asked. Nothing downstream may widen it after the
    authorisation predicate has been attached, which is the mistake that turns a scoped
    query into an unscoped one.
    """

    actor_user_id: uuid.UUID | None = None
    actor_role: RoleCode | None = None
    action: str | None = None
    object_type: str | None = None
    object_id: uuid.UUID | None = None
    object_public_ref: str | None = None
    policy_result: PolicyResult | None = None
    request_id: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None

    def validate(self) -> None:
        """Raise :class:`AuditFilterError` if the filter can match nothing.

        An inverted date range is a typo, not a query. Returning an empty page would let it
        read as "there is no such activity", which in an audit tool is the most dangerous
        possible answer to give silently.
        """
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from > self.occurred_to
        ):
            raise AuditFilterError(
                "occurred_from is later than occurred_to, so no event can match.",
                extra={
                    "occurred_from": self.occurred_from.isoformat(),
                    "occurred_to": self.occurred_to.isoformat(),
                },
            )


@dataclass(frozen=True, slots=True)
class AuditPage:
    """One page of audit rows, plus what it took to produce it.

    ``applied_classifications`` is returned deliberately. The reader is told which zones
    their clearance admitted, so a short page reads as "this is your view of the log"
    rather than as "this is the log" -- the difference between a tool that is trusted and
    one that is quietly misleading. It discloses nothing new: the same list is already on
    ``GET /v1/session/me``.
    """

    events: tuple[AuditEvent, ...]
    total_matching: int
    next_cursor: uuid.UUID | None
    has_more: bool
    applied_classifications: tuple[Classification, ...]


def _require_audit_reader(principal: Principal) -> None:
    """Refuse a principal without ``read:audit``.

    Deliberately duplicates the route's ``require(...)`` dependency. The route gate is what
    produces a good HTTP answer; this one is what makes the rule true for every caller,
    including a future scheduled report or CLI that never goes through a router.
    """
    if not principal.has(Permission.READ_AUDIT):
        raise PermissionDeniedError(
            extra={
                "reason": "missing_permission",
                "required_permissions": [Permission.READ_AUDIT.value],
                "missing_permissions": [Permission.READ_AUDIT.value],
                "actor_role": principal.role.value,
            },
        )


def _predicates(principal: Principal, filters: AuditFilter) -> list[ColumnElement[bool]]:
    """Build every ``WHERE`` clause, authorisation first.

    The classification predicate is prepended rather than appended purely so that a printed
    statement reads with the security clause first; SQL cares about neither. What matters is
    that it is in this list at all, and that the list is the only way a statement is built
    in this module -- there is no code path that assembles a query without it.
    """
    clauses: list[ColumnElement[bool]] = [
        AuditEvent.classification.in_(readable_classifications_for(principal))
    ]

    if filters.actor_user_id is not None:
        clauses.append(AuditEvent.actor_user_id == filters.actor_user_id)
    if filters.actor_role is not None:
        clauses.append(AuditEvent.actor_role == filters.actor_role)
    if filters.action is not None:
        clauses.append(AuditEvent.action == filters.action)
    if filters.object_type is not None:
        clauses.append(AuditEvent.object_type == filters.object_type)
    if filters.object_id is not None:
        clauses.append(AuditEvent.object_id == filters.object_id)
    if filters.object_public_ref is not None:
        clauses.append(AuditEvent.object_public_ref == filters.object_public_ref)
    if filters.policy_result is not None:
        clauses.append(AuditEvent.policy_result == filters.policy_result)
    if filters.request_id is not None:
        clauses.append(AuditEvent.request_id == filters.request_id)
    if filters.occurred_from is not None:
        clauses.append(AuditEvent.occurred_at >= filters.occurred_from)
    if filters.occurred_to is not None:
        clauses.append(AuditEvent.occurred_at <= filters.occurred_to)

    return clauses


def _clamp_limit(limit: int | None) -> int:
    """Coerce a requested page size into ``1 .. MAX_PAGE_SIZE``."""
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))


def list_audit_events(
    session: Session,
    principal: Principal,
    *,
    filters: AuditFilter | None = None,
    limit: int | None = None,
    cursor: uuid.UUID | None = None,
) -> AuditPage:
    """Return one page of audit rows the principal is cleared to read, newest first.

    Args:
        session: Any session with ``SELECT`` on ``audit_events``.
        principal: The reader. Must hold ``read:audit``; their clearance decides which
            zones are selected.
        filters: The narrowing clauses. ``None`` means "everything you may read".
        cursor: The ``id`` of the last row of the previous page. Rows strictly *before* it
            in ULID order are returned, so a row appended between the two calls -- and one
            will be, if the previous page was privileged -- neither shifts the window nor
            appears twice.
        limit: Page size, clamped to ``1 .. MAX_PAGE_SIZE``.

    Returns:
        An :class:`AuditPage`. ``total_matching`` counts every row satisfying the same
        filters and the same authorisation predicate, ignoring the cursor, so a UI can show
        "50 of 812" without a second query that might disagree with the first.

    Raises:
        PermissionDeniedError: the principal does not hold ``read:audit``.
        AuditFilterError: the filter can match nothing (an inverted date range).
    """
    _require_audit_reader(principal)
    active = filters if filters is not None else AuditFilter()
    active.validate()

    page_size = _clamp_limit(limit)
    clauses = _predicates(principal, active)

    total = session.scalar(select(func.count()).select_from(AuditEvent).where(*clauses)) or 0

    windowed = list(clauses)
    if cursor is not None:
        windowed.append(AuditEvent.id < cursor)

    # limit + 1: one extra row is the cheapest possible "is there another page", and it
    # cannot disagree with the page the way a second COUNT query can.
    statement: Select[tuple[AuditEvent]] = (
        select(AuditEvent).where(*windowed).order_by(AuditEvent.id.desc()).limit(page_size + 1)
    )
    rows = list(session.scalars(statement))

    has_more = len(rows) > page_size
    events = tuple(rows[:page_size])
    next_cursor = events[-1].id if has_more and events else None

    return AuditPage(
        events=events,
        total_matching=int(total),
        next_cursor=next_cursor,
        has_more=has_more,
        applied_classifications=tuple(readable_classifications_for(principal)),
    )


def verify_audit_chain(
    session: Session,
    principal: Principal,
    *,
    limit: int | None = None,
) -> ChainVerification:
    """Walk the hash chain and report whether the log is intact.

    Exposed to the UI so that "the audit log has not been tampered with" is something a
    viewer can check rather than something the vendor asserts. That is the entire value of
    the chain: a claim nobody can verify is a claim.

    Be precise about what a pass means. It says every row's stored digest still matches its
    own contents and its predecessor's digest, under the ``naddp.audit.v1`` domain. It does
    not defend against an attacker who rewrote the whole table *and* recomputed the chain --
    for that, the head digest has to be copied somewhere off-box and compared, which is
    pilot work noted in ADR-0004. Concurrent writers cannot fork the chain -- the unique link
    index refuses a second claim on any predecessor -- so a break here is a real finding, never
    an artefact of load.

    Args:
        session: Any session with ``SELECT`` on ``audit_events``.
        principal: The caller. Must hold ``read:audit``. Their *clearance* is deliberately
            not applied -- see the module docstring.
        limit: Verify only the most recent ``limit`` rows, clamped to
            ``1 .. MAX_CHAIN_VERIFY_LIMIT``. ``None`` verifies the whole table.

    Raises:
        PermissionDeniedError: the principal does not hold ``read:audit``.
    """
    _require_audit_reader(principal)
    bounded = None if limit is None else max(1, min(limit, MAX_CHAIN_VERIFY_LIMIT))
    return verify_chain(session, limit=bounded)


def summarise_classifications(events: Sequence[AuditEvent]) -> tuple[Classification, ...]:
    """Return the distinct zones present in ``events``, in dominance order.

    Used by the API layer to tell the audit middleware what a page actually contained, so
    that a page holding a ``CONSULAR_SENSITIVE`` row is recorded as a privileged read and a
    page of routine rows is not recorded at all.
    """
    present = {event.classification for event in events}
    return tuple(sorted(present, key=lambda zone: CLASSIFICATION_RANK[zone]))


def describe_filter(filters: AuditFilter) -> dict[str, Any]:
    """Render the active filter as JSON-safe values, for an audit row's ``payload``.

    Only the fields actually set appear, and every value is a bounded identifier, an enum
    or a timestamp -- never free text a caller supplied about a person. ``payload`` carries
    references, not payloads (ADR-0004).
    """
    described: dict[str, Any] = {}
    for spec in fields(filters):
        value = getattr(filters, spec.name)
        if value is None:
            continue
        if isinstance(value, datetime):
            described[spec.name] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            described[spec.name] = str(value)
        elif isinstance(value, PolicyResult | RoleCode):
            described[spec.name] = value.value
        else:
            described[spec.name] = value
    return described
