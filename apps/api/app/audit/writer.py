"""The append-only ``audit_events`` writer (ADR-0004).

``audit_events`` is the most important table in the repository, and this module is the
**only supported way to write to it**. Everything else -- services, route handlers, the
seed loader -- calls :func:`write_audit_event` inside the same transaction as the state
change it describes. If the transition commits, the audit row commits; if the audit write
fails, the transition rolls back. An audit log written on a best-effort basis after commit
is exactly as reliable as the code path that was supposed to write it.

Three things live here.

**1. The writer.** :func:`write_audit_event` inserts one row and returns it. It never
updates and never deletes, and it exposes no API that could.

**2. The hash chain.** Every row carries ``event_hash = sha256(canonical || prev_hash)``,
so the rows form a chain in ULID order. :func:`verify_chain` walks it and reports the
first broken link.

    *Be honest about what this buys.* The chain **detects** tampering; it does not
    **prevent** it. Prevention is the other two layers of ADR-0004: table grants
    (``REVOKE UPDATE, DELETE, TRUNCATE``) and the ``BEFORE UPDATE OR DELETE`` trigger. The
    chain is what still says something useful when an attacker had enough privilege to
    bypass both -- they must then also recompute every subsequent hash, and if the head of
    the chain has been copied anywhere off-box, even that fails. Nothing here defends
    against an attacker who rewrites the whole table *and* the verifier.

    *Concurrency: the chain cannot fork.* ``uq_audit_events_prev_event_hash`` is a UNIQUE
    index on ``prev_event_hash``, ``NULLS NOT DISTINCT`` so there is exactly one genesis row.
    The database refuses a second row claiming a predecessor that is already claimed, even
    when the two writers never saw each other's rows. :func:`write_audit_event` reads the
    head, links to it and inserts inside a SAVEPOINT; a writer that lost the race gets a
    unique violation, rolls back to the savepoint only -- the caller's business transaction
    is untouched -- re-reads the head and re-links (:func:`_append_linked`). Every write
    still lands, in one chain with no fork and no gap.

    This closes ``docs/W1_STATUS.md`` section 6 item 1. The demo is NOT single-writer -- the
    audit middleware writes a row per denied request, and browsers issue requests
    concurrently -- and the earlier best-effort advisory lock wrote *unlocked* once its
    budget ran out, so a burst could fork the chain and make the verifier report tampering
    that never happened. A verification a viewer is invited to run on stage has to be one
    that cannot show a false break.

    Waiting is bounded, never indefinite. A writer queued behind another transaction's
    uncommitted claim waits at most :data:`_LINK_LOCK_TIMEOUT_MS` per attempt and
    :data:`_APPEND_BUDGET_SECONDS` in all, then raises :class:`AuditChainBusyError`: the action
    is refused, and rolls back with its audit row, rather than being recorded out of order.
    A fork would be a false finding and a hung request would freeze the demo; a clear refusal
    is neither.

**3. The ORM immutability guard.** A SQLAlchemy ``before_flush`` listener refuses to flush
a session in which an :class:`~app.models.governance.AuditEvent` has been modified or
deleted. ADR-0004 lists this as the third enforcement layer. It protects exactly one
process and is bypassed by ``session.execute(update(...))``, by ``psql`` and by a
migration -- the database trigger is the layer that actually holds. What this buys is a
*clear Python error at the point of the bug*, naming the object and the offending
operation, instead of an opaque ``IntegrityError`` from a trigger at flush time.

What must never be written here: request bodies, document contents, consular case
narrative, or personal identifiers beyond internal IDs (ADR-0004). ``payload`` carries
references, not payloads. The audit log must not become a second, less protected copy of
the sensitive data it describes.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Final

from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.ids import new_id
from app.core.logging import get_logger, get_request_id
from app.domain.enums import Classification, PolicyResult
from app.models.governance import AuditEvent
from app.security.principal import Principal

__all__ = [
    "AUDIT_HASH_DOMAIN",
    "LINK_INDEX_NAME",
    "AuditChainBusyError",
    "AuditContext",
    "AuditImmutabilityError",
    "ChainVerification",
    "audit_context",
    "canonical_json",
    "compute_event_hash",
    "current_audit_context",
    "install_audit_immutability_guard",
    "verify_chain",
    "write_audit_event",
]

_logger = get_logger(__name__)

#: Domain-separation prefix mixed into every digest.
#:
#: Versioned so that a future change to the canonical field set is a *new* chain rather
#: than a silent reinterpretation of the old one: rows written under v1 verify under v1,
#: and a verifier that only knows v2 reports a break instead of a false pass.
AUDIT_HASH_DOMAIN: Final[str] = "naddp.audit.v1"

#: What a genesis row's ``prev_event_hash`` contributes to the digest. The column itself
#: stays NULL -- a sentinel string in an evidentiary column would be fabricated data.
_GENESIS_PREV: Final[str] = ""

#: Correlation-id prefix for a write with no HTTP request behind it (seed loader,
#: scheduled job, migration). ``audit_events.request_id`` is NOT NULL by design: a
#: non-HTTP actor mints its own id rather than leaving the chain of correlation broken.
SYSTEM_REQUEST_ID_PREFIX: Final[str] = "system"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuditImmutabilityError(RuntimeError):
    """An ``AuditEvent`` was modified or deleted through the ORM.

    Deliberately **not** an :class:`~app.core.errors.AppError`. ``AppError`` models a
    deliberate, client-visible failure; this is a programming error in our own code, so it
    surfaces as an opaque 500 with a logged traceback, which is the correct outcome. A
    caller cannot fix it and must not be told how to.
    """


class AuditChainBusyError(AppError):
    """An audit row could not be linked into the chain within its budget.

    Raised instead of writing an unlinked or out-of-order row. It propagates through the
    caller's transaction, so the action the row would have recorded does not happen either:
    ADR-0004's rule that an action and its audit row commit together holds under contention
    too. A 503, because it is transient -- another transaction held the head of the chain for
    longer than the budget -- and a retry normally succeeds.
    """

    code = "audit_chain_busy"
    status_code = 503
    title = "Audit Log Busy"
    default_detail = (
        "The action could not be recorded in the audit log in time, so it was not carried out. "
        "Try again."
    )


# ---------------------------------------------------------------------------
# Canonical serialisation
# ---------------------------------------------------------------------------


def _canonical_scalar(value: object) -> object:
    """Render one value into a form that serialises identically on every run."""
    if isinstance(value, Enum):
        # `.value`, never `str(member)`: the (str, Enum) shape used across the domain
        # renders as "RoleCode.ADMIN" under str(), and that string is not the wire value.
        return _canonical_scalar(value.value)
    if isinstance(value, datetime):
        # Normalise to UTC before formatting. psycopg returns timestamps in the session
        # time zone, so the same instant read back in a different session would otherwise
        # produce a different string and a false chain break.
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        # str(), not float(): binary floating point cannot represent 0.10, and a hash
        # that depends on a rounding artefact is not reproducible.
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_scalar(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical_scalar(item) for item in value]
    return value


def canonical_json(fields: Mapping[str, Any]) -> bytes:
    """Serialise ``fields`` deterministically: sorted keys, no whitespace, UTF-8.

    Two rows with the same content must produce byte-identical output whatever order the
    dictionaries were built in, on any machine, in any Python version -- otherwise the
    chain would break on a re-verify for no reason at all. ``sort_keys`` handles ordering,
    ``separators`` removes the incidental whitespace ``json.dumps`` inserts by default, and
    ``ensure_ascii=False`` means a name with a diacritic hashes as itself rather than as an
    escape sequence.

    Raises:
        TypeError: if any value is not JSON-serialisable after normalisation. Loud by
            design: an unhashable payload must fail at the write, not produce a row whose
            hash cannot be recomputed.
    """
    normalised = {str(key): _canonical_scalar(value) for key, value in fields.items()}
    try:
        return json.dumps(
            normalised,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except TypeError as exc:
        msg = (
            "audit payload is not JSON-serialisable, so its event_hash could not be "
            f"computed: {exc}. audit_events.payload carries references, never objects."
        )
        raise TypeError(msg) from exc


def _hashable_fields(row: AuditEvent) -> dict[str, Any]:
    """The exact field set covered by the chain, for one row.

    Every column an auditor would care about is here. ``prev_event_hash`` is deliberately
    absent: it is appended separately by :func:`compute_event_hash`, matching the
    ADR-0004 formula ``sha256(canonical || prev)``. ``event_hash`` itself is absent for
    the obvious reason.
    """
    return {
        "action": row.action,
        "actor_role": row.actor_role,
        "actor_user_id": row.actor_user_id,
        "classification": row.classification,
        "id": row.id,
        "ip_address": row.ip_address,
        "object_id": row.object_id,
        "object_public_ref": row.object_public_ref,
        "object_type": row.object_type,
        "occurred_at": row.occurred_at,
        "payload": row.payload,
        "policy_result": row.policy_result,
        "request_id": row.request_id,
        "summary": row.summary,
        "trace_id": row.trace_id,
        "user_agent": row.user_agent,
    }


def compute_event_hash(row: AuditEvent, prev_event_hash: str | None) -> str:
    """Return the SHA-256 hex digest binding ``row`` to its predecessor.

    ``sha256(domain || canonical_json(fields) || prev_event_hash)``. Editing or removing
    any earlier row changes its digest, which breaks the link every later row asserts, so
    a single tampered row invalidates the entire tail of the chain rather than just itself.
    """
    digest = hashlib.sha256()
    digest.update(AUDIT_HASH_DOMAIN.encode("utf-8"))
    digest.update(b"\n")
    digest.update(canonical_json(_hashable_fields(row)))
    digest.update(b"\n")
    digest.update((prev_event_hash or _GENESIS_PREV).encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Ambient context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Correlation fields that every audit row in one unit of work shares.

    Exists so a caller cannot forget ``request_id``. Without it, the correlation column
    would be the argument every service forgets under deadline, and a log whose rows
    cannot be joined back to the request that produced them answers far fewer questions.
    """

    request_id: str
    ip_address: str | None = None
    user_agent: str | None = None
    trace_id: uuid.UUID | None = None


_AUDIT_CONTEXT: ContextVar[AuditContext | None] = ContextVar("naddp_audit_context", default=None)


def _mint_system_request_id() -> str:
    """Correlation id for a write with no HTTP request behind it."""
    return f"{SYSTEM_REQUEST_ID_PREFIX}-{new_id()}"


def current_audit_context() -> AuditContext:
    """Return the ambient context, deriving one if none has been bound.

    Resolution order: an :func:`audit_context` block, then the ``request_id`` bound by
    ``RequestContextMiddleware``, then a freshly minted ``system-…`` id. The last case is
    the seed loader and the scheduled job -- they get a real, unique correlation id rather
    than a NULL or a shared constant, so their rows are still groupable per run.
    """
    bound = _AUDIT_CONTEXT.get()
    if bound is not None:
        return bound
    return AuditContext(request_id=get_request_id() or _mint_system_request_id())


@contextmanager
def audit_context(
    *,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> Iterator[AuditContext]:
    """Bind correlation fields for every :func:`write_audit_event` call in this block.

    Used by a route handler to attach the client address and user agent once, and by a
    service that has just made an AI call to attach its ``trace_id`` to whatever the
    resulting decision writes::

        with audit_context(ip_address=client_ip, user_agent=agent):
            service.approve(session, followup, actor=principal)

    ``ContextVar``-based, so it is safe under the threadpool FastAPI runs synchronous
    handlers in, and it unbinds itself even when the block raises.
    """
    context = AuditContext(
        request_id=request_id or get_request_id() or _mint_system_request_id(),
        ip_address=ip_address,
        user_agent=user_agent,
        trace_id=trace_id,
    )
    token = _AUDIT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _AUDIT_CONTEXT.reset(token)


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


def _database_now(session: Session) -> datetime:
    """Read the current transaction timestamp from the database server.

    ``occurred_at`` has a ``server_default`` of ``now()``, so the ORM would normally leave
    the value to the database and never learn it. The hash chain has to cover the
    timestamp -- a log whose times can be altered without breaking the chain is only half
    an evidentiary record -- so the value is fetched explicitly and written by the writer.

    It is still the **database's** clock, not the caller's, which is the property the
    column comment insists on: ``func.now()`` is ``transaction_timestamp()``, so every row
    written in one transaction shares one instant, exactly as the server default would
    have produced.
    """
    value = session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        msg = (
            "SELECT now() did not return a timestamp; refusing to write an audit row "
            "with a caller-supplied time."
        )
        raise RuntimeError(msg)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


#: The UNIQUE index that makes a fork impossible (migration ``e7c41a9d2b58``).
LINK_INDEX_NAME: Final[str] = "uq_audit_events_prev_event_hash"

#: The longest one attempt waits on another transaction's uncommitted claim to the same
#: predecessor. A unique-index conflict with an in-flight row is a lock wait on that
#: transaction, so ``lock_timeout`` bounds it. An append costs a few round trips, so two
#: seconds is far beyond ordinary contention and short enough to retry within budget.
_LINK_LOCK_TIMEOUT_MS: Final[int] = 2_000

#: The total time one append may spend re-linking before it refuses. A burst far larger than
#: anything a demo produces drains well inside it; see ``tests/test_audit_chain_concurrency.py``.
_APPEND_BUDGET_SECONDS: Final[float] = 10.0

#: SQLSTATE of a unique violation: the link (or, after an id nudge, the id) was claimed first.
_UNIQUE_VIOLATION: Final[str] = "23505"

#: SQLSTATEs of the other ways losing the race surfaces: a lock wait past ``lock_timeout``,
#: and being chosen as a deadlock victim while waiting on the head.
_CONTENTION_STATES: Final[frozenset[str]] = frozenset({"55P03", "40P01"})

#: The constraints whose violation means "another writer got there first".
_RACE_CONSTRAINTS: Final[frozenset[str]] = frozenset(
    {"uq_audit_events_prev_event_hash", "pk_audit_events"}
)


@dataclass(frozen=True, slots=True)
class _ChainHead:
    """The most recent row: what the next row links to, and what its id must sort after."""

    id: uuid.UUID
    event_hash: str


def _chain_head(session: Session) -> _ChainHead | None:
    """Return the most recent row's id and digest, or ``None`` when the chain is empty.

    Ordered by ``id``, not by ``occurred_at``. Primary keys are ULIDs rendered into UUIDs
    (ADR-0007), whose leading 48 bits are a millisecond timestamp, and Postgres compares
    ``uuid`` byte-wise -- so ``ORDER BY id DESC`` is a chronological order that does not
    depend on trusting a separate clock column.

    The value read here can be stale by the time the row is inserted: another transaction
    may be linking to the same head. That is safe because of the unique link index, not
    because of anything this function does (:func:`_append_linked`).
    """
    row = session.execute(
        select(AuditEvent.id, AuditEvent.event_hash).order_by(AuditEvent.id.desc()).limit(1)
    ).first()
    return None if row is None else _ChainHead(id=row.id, event_hash=row.event_hash)


def _id_after(head: _ChainHead | None) -> uuid.UUID:
    """A fresh ULID that sorts after the head, so ULID order stays chain order.

    :func:`verify_chain` walks rows in ``id`` order and expects each to name the one before
    it. A writer that minted its id, lost the race and re-linked behind a row with a later id
    would break that on its own. So the id is minted per attempt, after the head is read, and
    nudged past the head in the rare case the ULID clock does not already put it there
    (another process, within the same millisecond).
    """
    candidate = new_id()
    if head is None or candidate > head.id:
        return candidate
    return uuid.UUID(int=head.id.int + 1 + secrets.randbelow(1 << 32))


def _sqlstate(exc: DBAPIError) -> str | None:
    state = getattr(exc.orig, "sqlstate", None)
    return state if isinstance(state, str) else None


def _lost_the_race(exc: DBAPIError) -> bool:
    """Whether ``exc`` means another writer claimed the head first, rather than a real fault."""
    state = _sqlstate(exc)
    if state in _CONTENTION_STATES:
        return True
    if state != _UNIQUE_VIOLATION:
        return False
    diagnostics = getattr(exc.orig, "diag", None)
    return getattr(diagnostics, "constraint_name", None) in _RACE_CONSTRAINTS


def _append_linked(session: Session, fields: Mapping[str, Any]) -> AuditEvent:
    """Link one row to the head of the chain and insert it, re-linking if it lost the race.

    Each attempt reads the head, mints an id that sorts after it, computes the digest and
    inserts -- inside a SAVEPOINT, with ``lock_timeout`` bounding any wait on another
    transaction's uncommitted claim. Losing the race (the unique link index refused the row,
    the wait timed out, or Postgres picked this statement as a deadlock victim) rolls back to
    the savepoint only: the caller's business transaction and everything it has written stay
    intact, and the next attempt links behind the winner. A new ``AuditEvent`` is built per
    attempt, because an object flushed inside a rolled-back savepoint is not reusable.

    Raises:
        AuditChainBusyError: the budget ran out before the row could be linked.
        DBAPIError: any other database fault, unchanged.
    """
    postgres = session.get_bind().dialect.name == "postgresql"
    deadline = time.monotonic() + _APPEND_BUDGET_SECONDS
    attempt = 0
    while True:
        attempt += 1
        head = _chain_head(session)
        row = AuditEvent(id=_id_after(head), **fields)
        row.prev_event_hash = head.event_hash if head is not None else None
        row.event_hash = compute_event_hash(row, row.prev_event_hash)
        try:
            with session.begin_nested():
                if postgres:
                    # set_config(..., is_local => true) inside the savepoint: a rolled-back
                    # attempt reverts it with the savepoint, and a successful one restores the
                    # caller's value explicitly before the savepoint is released.
                    previous_timeout = session.scalar(select(func.current_setting("lock_timeout")))
                    session.execute(
                        select(func.set_config("lock_timeout", f"{_LINK_LOCK_TIMEOUT_MS}ms", True))
                    )
                session.add(row)
                session.flush()
                if postgres:
                    session.execute(select(func.set_config("lock_timeout", previous_timeout, True)))
        except DBAPIError as exc:
            if not _lost_the_race(exc):
                raise
            if time.monotonic() >= deadline:
                _logger.error(
                    "audit.chain_busy",
                    attempts=attempt,
                    budget_seconds=_APPEND_BUDGET_SECONDS,
                    consequence="refused rather than written out of order; the action rolls back",
                )
                raise AuditChainBusyError(extra={"attempts": attempt}) from exc
            _logger.info("audit.chain_relinked", attempt=attempt, sqlstate=_sqlstate(exc))
            continue
        return row


def write_audit_event(
    session: Session,
    *,
    actor: Principal | None,
    action: str,
    object_type: str,
    object_id: uuid.UUID | None = None,
    object_public_ref: str | None = None,
    policy_result: PolicyResult,
    classification: Classification,
    summary: str,
    payload: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    trace_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    occurred_at: datetime | None = None,
) -> AuditEvent:
    """Append one row to ``audit_events`` and return it.

    The row is inserted with ``session.flush()`` but **not committed**: committing belongs
    to the caller's transaction, which is the entire point (ADR-0004). A service calls this
    between its state write and its commit.

    Args:
        session: The session carrying the business transaction. Must be the same one the
            state change is on, or the two can diverge.
        actor: The principal who acted, or ``None`` for a system action -- the seed loader,
            a scheduled job, a migration. ``None`` is written as a NULL actor rather than
            borrowed from a human, because "the system did it" is a genuinely different
            fact. Note that ``actor.user_id`` must exist in ``users``: the foreign key is
            ``ON DELETE RESTRICT`` and will refuse a principal nobody provisioned.
        action: A member of the closed vocabulary (``app/audit/actions.py``, and the
            ``Audit action`` column of ``docs/workflows.md``). Not validated here: the
            vocabulary module is owned by the workflow track, and a writer that imported it
            would make this module unusable until that lands. The closed-vocabulary test is
            the enforcement point.
        object_type: Bounded-context-qualified entity name, e.g. ``consular.case``.
        object_id: The affected row's id, where the event has a single object.
        object_public_ref: The citizen-facing reference where one exists (ADR-0007), so an
            auditor can search by the reference a citizen quoted over the phone.
        policy_result: ``ALLOW`` or ``DENY``. Denials are recorded, not only successes.
        classification: The zone of the content this row *describes*. An audit row about a
            consular case is itself ``CONSULAR_SENSITIVE``.
        summary: One human-readable sentence for the audit timeline and trace drawer.
            References, never content.
        payload: Structured, bounded, non-sensitive context. For a transition, exactly
            ``{from_state, to_state, event, reason, trace_id}``.
        request_id: Correlation id. Defaults to the ambient :func:`audit_context`, then to
            the middleware's request id, then to a minted ``system-…`` id.
        trace_id: The ``ai_traces`` row that informed this event, where one did.
        ip_address: Client address; ``None`` for a system action.
        user_agent: Client user agent; ``None`` for a system action.
        occurred_at: **Backdating override, for the seed loader only.** Leave it ``None``
            and the timestamp comes from the database clock, which is what every
            application path must do -- a caller-supplied time in an evidentiary table is a
            caller-controlled fact, and that is the whole reason ``_database_now`` exists.

            The single sanctioned exception is ``data/demo-seed/seed.py``, which has to
            lay down about 450 rows of *historical* audit trail over a trailing eight
            weeks (``docs/OPEN_QUESTIONS.md`` Q-13). The alternative was hand-inserting
            those rows, which would bypass the hash chain and break it at the first row --
            so the narrow parameter is strictly safer than the workaround it replaces.
            The value is still covered by ``event_hash`` (it is in
            :func:`_hashable_fields`), so a backdated row is no less tamper-evident than
            any other; what it is not is *independently attested*, which is why nothing
            but the seed may pass it.

            Must be timezone-aware. A naive value is rejected rather than assumed to be
            UTC: a silent assumption here would put a row hours away from where the caller
            meant it, in the one table where the time is the evidence.

    Raises:
        ValueError: if ``occurred_at`` is naive.
        AuditChainBusyError: the row could not be linked into the chain within its budget.
            Nothing was written, and the caller's transaction must not commit the action.

    Returns:
        The persisted :class:`~app.models.governance.AuditEvent`, with its ``id``,
        ``occurred_at``, ``event_hash`` and ``prev_event_hash`` populated.
    """
    context = current_audit_context()

    if occurred_at is not None and occurred_at.tzinfo is None:
        msg = (
            "write_audit_event(occurred_at=...) requires a timezone-aware datetime; "
            "a naive value would be silently reinterpreted as UTC in the one table "
            "whose timestamps are the evidence."
        )
        raise ValueError(msg)

    # Flush first, outside the savepoint, so that any audit row written earlier in this
    # transaction is visible to the head read, and so that a rolled-back link attempt can
    # never take the caller's pending changes with it.
    session.flush()

    row = _append_linked(
        session,
        {
            "occurred_at": occurred_at if occurred_at is not None else _database_now(session),
            "actor_user_id": actor.user_id if actor is not None else None,
            "actor_role": actor.role if actor is not None else None,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "object_public_ref": object_public_ref,
            "policy_result": policy_result,
            "classification": classification,
            "request_id": request_id or context.request_id,
            "trace_id": trace_id if trace_id is not None else context.trace_id,
            "summary": summary,
            "payload": dict(payload or {}),
            "ip_address": ip_address if ip_address is not None else context.ip_address,
            "user_agent": user_agent if user_agent is not None else context.user_agent,
        },
    )

    _logger.info(
        "audit.event_written",
        action=action,
        object_type=object_type,
        policy_result=policy_result.value,
        classification=classification.value,
        actor_role=actor.role.value if actor is not None else None,
        audit_request_id=row.request_id,
    )
    return row


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """The result of walking the hash chain.

    ``is_intact`` is the answer; the rest exists so that "no" is actionable. A verifier
    that only returns a boolean tells an auditor that something is wrong and nothing about
    where, which in a table of hundreds of thousands of rows is barely better than silence.
    """

    is_intact: bool
    checked: int
    broken_at_id: uuid.UUID | None = None
    broken_at_index: int | None = None
    reason: str | None = None


def verify_chain(session: Session, limit: int | None = None) -> ChainVerification:
    """Walk the hash chain in ULID order and report the first broken link.

    Args:
        session: Any session with SELECT on ``audit_events``.
        limit: Verify only the most recent ``limit`` rows. One extra row is fetched as an
            anchor so the first checked row's ``prev_event_hash`` is still verified against
            a real predecessor rather than skipped. ``None`` verifies the whole table.

    Returns:
        A :class:`ChainVerification`. ``checked`` counts rows whose link *and* digest were
        both recomputed; the anchor row is not counted, because its own digest is taken on
        trust in a bounded verification.

    What a break means: the row named either had a field altered after it was written, or
    its predecessor was altered or removed. It does **not** on its own identify which --
    the verifier reports the first position at which the recomputation stops agreeing, and
    a human reads the log from there. Concurrent writers cannot produce a break: the unique
    link index refuses a second claim on any predecessor and the writer re-links (module
    docstring), so a break is a finding, never an artefact of load.
    """
    if limit is not None and limit <= 0:
        return ChainVerification(is_intact=True, checked=0)

    if limit is None:
        rows = list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
        anchor: AuditEvent | None = None
    else:
        newest_first = list(
            session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit + 1))
        )
        rows = list(reversed(newest_first))
        # More rows exist than we asked for, so rows[0] is the predecessor of the window
        # rather than the genesis row: use it as a trusted anchor and verify from rows[1].
        anchor = rows.pop(0) if len(rows) > limit else None

    expected_prev: str | None = anchor.event_hash if anchor is not None else None

    for index, row in enumerate(rows):
        if row.prev_event_hash != expected_prev:
            return ChainVerification(
                is_intact=False,
                checked=index,
                broken_at_id=row.id,
                broken_at_index=index,
                reason=(
                    "prev_event_hash does not match the preceding row: expected "
                    f"{expected_prev!r}, found {row.prev_event_hash!r}. A row was altered "
                    "or removed at or before this position."
                ),
            )
        recomputed = compute_event_hash(row, row.prev_event_hash)
        if recomputed != row.event_hash:
            return ChainVerification(
                is_intact=False,
                checked=index,
                broken_at_id=row.id,
                broken_at_index=index,
                reason=(
                    "event_hash does not match this row's own contents: stored "
                    f"{row.event_hash!r}, recomputed {recomputed!r}. This row was altered "
                    "after it was written."
                ),
            )
        expected_prev = row.event_hash

    return ChainVerification(is_intact=True, checked=len(rows))


# ---------------------------------------------------------------------------
# ORM immutability guard (ADR-0004 enforcement layer 3)
# ---------------------------------------------------------------------------


def _mutated_audit_rows(session: Session) -> list[AuditEvent]:
    """Return every ``AuditEvent`` this flush would UPDATE or DELETE.

    ``session.dirty`` is documented as *optimistic*: it can list an object whose net
    changes are empty, for instance one whose attribute was reassigned to the value it
    already had. ``session.is_modified`` is re-checked so the guard fires on a real change
    only -- a guard with false positives would be turned off, and a guard that is turned
    off is not a control.
    """
    offenders = [obj for obj in session.deleted if isinstance(obj, AuditEvent)]
    offenders.extend(
        obj
        for obj in session.dirty
        if isinstance(obj, AuditEvent) and session.is_modified(obj, include_collections=False)
    )
    return offenders


def _reject_audit_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    """``before_flush`` listener: refuse to flush a mutated or deleted audit row."""
    offenders = _mutated_audit_rows(session)
    if not offenders:
        return

    identifiers = ", ".join(str(row.id) for row in offenders)
    _logger.error("audit.mutation_attempt", audit_event_ids=identifiers, count=len(offenders))
    msg = (
        "audit_events is append-only (ADR-0004): refusing to flush a session that would "
        f"UPDATE or DELETE {len(offenders)} audit row(s) [{identifiers}]. Corrections are "
        "APPENDED as a compensating event whose payload references the earlier row's id -- "
        "never applied. If you reached this from a migration or a data fix, the answer is "
        "still a new row."
    )
    raise AuditImmutabilityError(msg)


def install_audit_immutability_guard() -> None:
    """Register the ``before_flush`` guard on the ``Session`` class. Idempotent.

    Registered on the class rather than on one session, so it covers every session in the
    process including those a script or a test opens directly -- there is no way to obtain
    an unguarded session by accident.

    Called at import of this module, so importing the writer is sufficient to arm it, and
    exposed as a function so ``create_app()`` can arm it explicitly at startup for a
    process that has not yet written an audit row.
    """
    if event.contains(Session, "before_flush", _reject_audit_mutation):
        return
    event.listen(Session, "before_flush", _reject_audit_mutation)


install_audit_immutability_guard()
