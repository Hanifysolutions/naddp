"""Automatic audit capture at the HTTP boundary (ADR-0004, PROMPT Phase 3).

``app/audit/writer.py`` is the *explicit* path: a service writes a row inside the same
transaction as the state change it describes. This module is the *automatic* path. ADR-0004
names four things middleware must record without a service being asked to remember:

1. **logins** -- ``POST /v1/session/assume-role``,
2. **data access on privileged objects** -- a read that served ``CONFIDENTIAL`` or
   ``CONSULAR_SENSITIVE`` content,
3. **exports** -- any bulk extraction,
4. **every denial** -- a ``PermissionDeniedError`` or ``ClassificationDeniedError``, as a
   row with ``policy_result = DENY``.

The fourth is the one that matters most. A security log that records only what succeeded is
the classic mistake: it can show that the system was used, never that it was defended. The
refusals are the evidence.

Two mechanisms, and they answer two different failure modes
-----------------------------------------------------------

**The route registry (:data:`DEFAULT_RULES`) bounds the volume.** The middleware audits
*nothing* unless a rule matches the request, so ``/health``, ``/health/ready``, ``/docs``,
``/openapi.json`` and every route nobody has registered produce no rows at all. This is
opt-in rather than a blocklist on purpose: a blocklist has to be extended every time
somebody adds a polling endpoint, and the day it is forgotten the audit table fills with
health checks and the real signal is gone. A registry fails the other way -- a new route is
silent until somebody registers it -- which is a visible gap rather than a flood, and the
route-coverage test is where it gets caught.

**The request-state flag (:func:`mark_audited`) bounds the duplication.** A route whose
service already wrote its own row calls it, and the middleware then writes no second row
for that request. ``POST /v1/session/assume-role`` is exactly this case: it is registered
as a login rule so the registry documents *why* the middleware is quiet there, and the rule
carries ``self_audited=True`` because ``app.services.session.assume_role`` writes the
``session.role_assumed`` row itself, in the same transaction as the persona insert -- which
is strictly better than a middleware row written afterwards.

The flag suppresses **``ALLOW`` rows only**. A denial is always recorded, whatever the
annotation says. An opt-out that could switch off denial logging is an opt-out worth
attacking, and the route that would have written the row usually never ran anyway: the
denial came from a dependency, before the handler body.

Availability over completeness -- a deliberate demo trade-off
-------------------------------------------------------------

If the audit write fails, this middleware logs the failure loudly at ``error`` level and
**lets the request through with its original response unchanged**. It never converts an
audit failure into a 500 the caller can see, and it never swallows or rewrites the
response.

That is the opposite of the rule the writer follows. A service's audit write is *inside*
the business transaction precisely so that a state change we cannot record does not happen.
Here there is no state change to roll back: the response has already been sent by the time
the row is attempted, and the alternative -- failing the request afterwards -- would break
the demo without recovering the missing row.

**A production system would very likely choose the opposite**, at least for exports and for
consular reads: refuse to serve content whose access cannot be recorded, or queue the row
to a durable outbox and fail closed if the outbox is full. That is a real decision with a
real cost, and it is recorded here rather than left implicit, because the difference between
the two systems is one ``raise`` and nobody would notice it missing.

Known limitations, stated rather than discovered
------------------------------------------------

* The row is written **after** the response, in its own transaction, on a session this
  middleware opens. It is therefore not atomic with anything -- a process killed between
  the response and the commit loses the row. Accepted for the same reason as above.
* Concurrent requests can fork the hash chain, exactly as ``app/audit/writer.py`` documents
  for concurrent writers generally. The demo is single-writer; a real deployment needs the
  chain serialised.
* An unhandled exception (a 500) is not audited. It is a bug rather than a policy decision,
  ``structlog`` already carries its traceback and ``request_id``, and inventing an
  ``action`` for "our code broke" would put noise into a closed vocabulary.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.audit.writer import write_audit_event
from app.core.logging import get_logger
from app.domain.enums import Classification, PolicyResult, RoleCode, dominant
from app.security.principal import Principal, demo_persona, principal_for_role
from app.security.session import SESSION_COOKIE_NAME, read_session
from app.services.session import (
    SESSION_OBJECT_TYPE,
    SESSION_ROLE_ASSUMED,
    ensure_persona_user,
)

__all__ = [
    "ACCESS_DENIED",
    "ACCESS_OBJECT_TYPE",
    "ACCESS_PRIVILEGED_READ",
    "DEFAULT_RULES",
    "DENIAL_STATUS",
    "EXPORT_PATH_SEGMENT",
    "EXPORT_PERFORMED",
    "MIDDLEWARE_ACTIONS",
    "PRIVILEGED_CLASSIFICATIONS",
    "AuditAnnotation",
    "AuditDecision",
    "AuditKind",
    "AuditMiddleware",
    "AuditRule",
    "annotation_for",
    "decide",
    "mark_audited",
    "record_access",
]

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: A refused request. ``policy_result = DENY``; ``payload.reason`` distinguishes
#: ``no_session`` / ``missing_permission`` / ``insufficient_clearance``.
#:
#: One action rather than three, because the interesting axis for a reader is "somebody was
#: refused something", and the reason is a filterable field rather than a separate verb.
ACCESS_DENIED: Final[str] = "access.denied"

#: A completed read whose content was ``CONFIDENTIAL`` or ``CONSULAR_SENSITIVE``.
ACCESS_PRIVILEGED_READ: Final[str] = "access.privileged_read"

#: A bulk extraction. Named verbatim in ADR-0004's action table.
EXPORT_PERFORMED: Final[str] = "export.performed"

#: Object type for an event about a request rather than about a domain row -- a denial on
#: an unregistered path, principally. Bounded-context qualified like every other value in
#: this column: refusing access is a governance act.
ACCESS_OBJECT_TYPE: Final[str] = "governance.access_attempt"

#: Every ``action`` this module can emit.
#:
#: Exposed so that ``app/audit/actions.py`` -- the closed vocabulary module, owned by the
#: workflow track -- can assert this set is a subset of it, and so a test in this track can
#: assert no rule smuggles in an unlisted verb. The strings are declared here rather than
#: imported because that module does not exist yet; when it lands it must carry these four
#: character for character (``docs/workflows.md`` rule 0.4).
MIDDLEWARE_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        ACCESS_DENIED,
        ACCESS_PRIVILEGED_READ,
        EXPORT_PERFORMED,
        SESSION_ROLE_ASSUMED,
    }
)

#: The zones ADR-0004 calls privileged: reading one is itself an auditable event.
#:
#: ``CONSULAR_SENSITIVE`` is here for whose interests are at stake rather than for how
#: damaging disclosure would be -- a private individual who did not choose to be in the
#: system. ``MISSION_INTERNAL`` is deliberately absent: auditing every ordinary read would
#: produce a row per page view and bury the ten rows that matter.
PRIVILEGED_CLASSIFICATIONS: Final[frozenset[Classification]] = frozenset(
    {Classification.CONFIDENTIAL, Classification.CONSULAR_SENSITIVE}
)

#: Both security gates render as 403 (``app.core.errors``), and nothing else in this API
#: does. Status alone is therefore a sufficient denial signal, and the problem document is
#: read only to enrich the row -- so a change to the body shape degrades the detail of a
#: denial row rather than losing the row.
DENIAL_STATUS: Final[int] = 403

#: The RFC 9457 ``code`` values an authorisation refusal can carry.
#:
#: The two gates, plus the two refinements of the permission gate a workflow raises when the
#: actor holds the permission but may not use it on this artefact: ``approval_required``
#: (a follow-up sent without a named approval) and ``separation_of_duties`` (a drafter
#: approving their own). All four render as 403, so the status test above already records
#: them; listing the codes keeps a refusal a denial even if a later change gives one of them
#: a different status line.
_DENIAL_CODES: Final[frozenset[str]] = frozenset(
    {
        "permission_denied",
        "classification_denied",
        "approval_required",
        "separation_of_duties",
    }
)

#: A path segment that makes a route an export regardless of the registry.
#:
#: PROMPT Phase 3 names "a route carrying the ``export:bulk`` permission or a path segment
#: ``/export``". The permission half cannot be read from here: ``require(...)`` returns a
#: closure, and reaching into its cell to recover the permission would be a brittle trick
#: that fails silently the day the helper is refactored. So the *path* is the automatic
#: half, and an export route that does not sit under ``/export`` must register a rule --
#: which is checked by the route-coverage test rather than hoped for.
EXPORT_PATH_SEGMENT: Final[str] = "export"

#: ``audit_events.user_agent`` is ``String(256)``; ``ip_address`` is ``String(45)``.
_USER_AGENT_MAX_LENGTH: Final[int] = 256
_IP_MAX_LENGTH: Final[int] = 45

#: Paths are attacker-influenced text that ends up in a log line and a summary sentence.
#: Truncated and stripped of control characters before either.
_PATH_MAX_LENGTH: Final[int] = 200

#: Only a problem document is ever buffered, and only up to this. The middleware must not
#: become a way to make the API hold a large response in memory twice.
_PROBLEM_BODY_MAX_BYTES: Final[int] = 16 * 1024

#: Key under which the per-request annotation lives in the ASGI ``scope["state"]``.
_ANNOTATION_KEY: Final[str] = "naddp_audit_annotation"


# ---------------------------------------------------------------------------
# The route registry
# ---------------------------------------------------------------------------


class AuditKind(str, Enum):  # noqa: UP042
    """What a registered route is, from the audit log's point of view.

    ``(str, Enum)`` for the same reason as every other enum in the codebase: the shape is
    locked across the domain, and a value is ``member.value``.
    """

    LOGIN = "LOGIN"
    PRIVILEGED_READ = "PRIVILEGED_READ"
    EXPORT = "EXPORT"


@dataclass(frozen=True, slots=True)
class AuditRule:
    """One registered route, and what the middleware should record about it.

    Matched on the concrete request path with a regular expression rather than on FastAPI's
    route template, so the registry is pure data: it can be written, reviewed and tested
    without building an application, and a rule cannot silently stop matching because a
    router prefix moved.
    """

    name: str
    methods: frozenset[str]
    path: re.Pattern[str]
    kind: AuditKind
    action: str
    object_type: str
    classification: Classification
    self_audited: bool = False

    def matches(self, method: str, path: str) -> bool:
        """Return whether this rule governs ``method path``."""
        return method.upper() in self.methods and self.path.fullmatch(path) is not None


def _rule(
    name: str,
    methods: tuple[str, ...],
    pattern: str,
    kind: AuditKind,
    action: str,
    object_type: str,
    classification: Classification,
    *,
    self_audited: bool = False,
) -> AuditRule:
    """Build one rule, compiling its pattern once at import."""
    return AuditRule(
        name=name,
        methods=frozenset(method.upper() for method in methods),
        path=re.compile(pattern),
        kind=kind,
        action=action,
        object_type=object_type,
        classification=classification,
        self_audited=self_audited,
    )


#: The Week 1 registry.
#:
#: Short because Week 1 has two routers. Every later bounded-context track adds its own
#: rules here: a consular list read, a diaspora search, an opportunity export. The pattern
#: to follow is the audit rule below -- register the route, declare the *maximum* zone it
#: can serve, and have the handler call :func:`record_access` with the zone it actually
#: served so that a routine page view writes nothing.
DEFAULT_RULES: Final[tuple[AuditRule, ...]] = (
    _rule(
        "session.assume_role",
        ("POST",),
        r"/v1/session/assume-role/?",
        AuditKind.LOGIN,
        SESSION_ROLE_ASSUMED,
        SESSION_OBJECT_TYPE,
        Classification.MISSION_INTERNAL,
        # app.services.session.assume_role writes this row itself, inside the same
        # transaction as the persona insert. Registered anyway so the registry states why
        # the middleware is silent here instead of leaving it to be inferred from absence.
        self_audited=True,
    ),
    _rule(
        "audit.read_events",
        ("GET",),
        r"/v1/audit/events/?",
        AuditKind.PRIVILEGED_READ,
        ACCESS_PRIVILEGED_READ,
        "governance.audit_event",
        # The baseline is deliberately below the privileged threshold: reading a page of
        # ordinary MISSION_INTERNAL audit rows writes nothing. The handler calls
        # record_access() with the dominant zone of the rows it actually served, so a page
        # that contained a consular row -- and only such a page -- is itself recorded.
        Classification.MISSION_INTERNAL,
    ),
    _rule(
        "ai.read_trace",
        ("GET",),
        r"/v1/ai/traces/[^/]+/?",
        AuditKind.PRIVILEGED_READ,
        ACCESS_PRIVILEGED_READ,
        "ai.trace",
        # Same shape as audit.read_events, and the worked example of the extension pattern
        # for later tracks: the baseline sits below the privileged threshold so opening the
        # trace drawer on a routine answer is silent, and the handler reports the dominant
        # of the trace's data_class and result_class so a drawer opened on confidential
        # material -- and only that -- appends a row.
        Classification.MISSION_INTERNAL,
    ),
    # The Meetings context (W3.2). Three GET rules, in this order because first match wins:
    # the index, then the approval queue, then one meeting -- whose pattern would otherwise
    # also match /v1/meetings/approvals and file a queue read as a meeting read. Each handler
    # reports the dominant zone it actually served, so an ordinary diary read is silent and a
    # CONFIDENTIAL meeting read by the Ambassador or Deputy is recorded. The POST routes match
    # none of these: their services write their own rows.
    _rule(
        "meetings.list_meetings",
        ("GET",),
        r"/v1/meetings/?",
        AuditKind.PRIVILEGED_READ,
        ACCESS_PRIVILEGED_READ,
        "meetings.meeting",
        Classification.MISSION_INTERNAL,
    ),
    _rule(
        "meetings.read_approval_queue",
        ("GET",),
        r"/v1/meetings/approvals/?",
        AuditKind.PRIVILEGED_READ,
        ACCESS_PRIVILEGED_READ,
        "meetings.followup",
        Classification.MISSION_INTERNAL,
    ),
    _rule(
        "meetings.read_meeting",
        ("GET",),
        r"/v1/meetings/[^/]+/?",
        AuditKind.PRIVILEGED_READ,
        ACCESS_PRIVILEGED_READ,
        "meetings.meeting",
        Classification.MISSION_INTERNAL,
    ),
)


# ---------------------------------------------------------------------------
# What a route tells the middleware
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AuditAnnotation:
    """What a handler declares about the request it just served.

    Mutable and request-scoped, unlike everything else in the audit layer. It is a message
    from the handler to the middleware and has no life beyond one request.

    Only the handler knows two things the middleware cannot infer: whether it already wrote
    its own row, and what the content it served was actually classified. Guessing either
    from the URL would be exactly the kind of inference that is right until it is not.
    """

    self_audited: bool = False
    action: str | None = None
    object_type: str | None = None
    object_id: uuid.UUID | None = None
    object_public_ref: str | None = None
    classification: Classification | None = None
    summary: str | None = None
    trace_id: uuid.UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def annotation_for(request: Request) -> AuditAnnotation:
    """Return this request's annotation, creating it on first use.

    Stored in ``scope["state"]``, which every middleware and handler in the chain shares,
    so the value survives the hop between a route handler and this middleware.
    """
    state = request.scope.setdefault("state", {})
    existing = state.get(_ANNOTATION_KEY)
    if isinstance(existing, AuditAnnotation):
        return existing
    created = AuditAnnotation()
    state[_ANNOTATION_KEY] = created
    return created


def _annotation_in(scope: Scope) -> AuditAnnotation:
    """Read the annotation straight from a scope, without building a ``Request``."""
    state = scope.get("state") or {}
    existing = state.get(_ANNOTATION_KEY)
    return existing if isinstance(existing, AuditAnnotation) else AuditAnnotation()


def mark_audited(request: Request) -> None:
    """Declare that this request's audit row is written by the route itself.

    Call it from a handler whose service already appended a row, so the middleware does not
    write a second one describing the same act.

    Suppresses ``ALLOW`` rows only -- see the module docstring. A denial is recorded
    regardless, and deliberately cannot be turned off from inside a handler.
    """
    annotation_for(request).self_audited = True


def record_access(
    request: Request,
    *,
    classification: Classification,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    object_public_ref: str | None = None,
    summary: str | None = None,
    trace_id: uuid.UUID | None = None,
    payload: Mapping[str, Any] | None = None,
) -> None:
    """Declare what this request actually touched, and at what classification.

    The handler is the only place that knows. For a list endpoint pass
    ``dominant(*zones)`` over the rows actually served (``app.domain.enums.dominant``): the
    aggregate takes the highest zone among its parts, so a page containing one consular row
    is a consular read even if the other forty rows are routine.

    Whether a row results is then the middleware's decision, not the handler's: a
    ``PRIVILEGED_READ`` rule records only when the zone is in
    :data:`PRIVILEGED_CLASSIFICATIONS`, and an ``EXPORT`` records always.
    """
    annotation = annotation_for(request)
    annotation.classification = classification
    if object_type is not None:
        annotation.object_type = object_type
    if object_id is not None:
        annotation.object_id = object_id
    if object_public_ref is not None:
        annotation.object_public_ref = object_public_ref
    if summary is not None:
        annotation.summary = summary
    if trace_id is not None:
        annotation.trace_id = trace_id
    if payload:
        annotation.payload.update(payload)


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditDecision:
    """Everything :func:`app.audit.writer.write_audit_event` needs for one row.

    Separated from the ASGI plumbing on purpose. The interesting logic of this module is
    "given a request, a response status and what the handler said, is there a row and what
    is in it" -- a pure function over plain values, exercised exhaustively by unit tests
    with no application, no event loop and no database. What is left in the middleware is
    transport.
    """

    action: str
    object_type: str
    policy_result: PolicyResult
    classification: Classification
    summary: str
    payload: dict[str, Any]
    object_id: uuid.UUID | None = None
    object_public_ref: str | None = None
    trace_id: uuid.UUID | None = None
    rule_name: str | None = None


def _safe_path(path: str) -> str:
    """Truncate a path and strip control characters before it reaches a log or a summary.

    A request path is attacker-influenced text. Unfiltered, a newline in it forges a log
    line, and an unbounded one bloats an evidentiary column with something nobody sent
    deliberately.
    """
    cleaned = "".join(character for character in path if character.isprintable())
    if len(cleaned) <= _PATH_MAX_LENGTH:
        return cleaned
    return f"{cleaned[:_PATH_MAX_LENGTH]}..."


def _actor_label(role: RoleCode | None) -> str:
    """How the summary sentence names the actor."""
    return role.value if role is not None else "An unauthenticated caller"


def _denial_details(problem: Mapping[str, Any] | None) -> dict[str, Any]:
    """Lift the useful fields out of an RFC 9457 problem document.

    Best effort by design. ``app.security.deps.require`` puts ``required_permissions``,
    ``missing_permissions`` and ``actor_role`` on the denial, and that is the only place
    that information exists -- but if the document is missing or reshaped, the row is still
    written with whatever survived. Losing the detail of a denial is bad; losing the denial
    is worse.
    """
    if not problem:
        return {"reason": "unavailable"}

    details: dict[str, Any] = {}
    for key in ("code", "reason", "required_permissions", "missing_permissions"):
        value = problem.get(key)
        if value is not None:
            details[key] = value
    details.setdefault("reason", "unspecified")
    return details


def decide(
    *,
    method: str,
    path: str,
    status: int,
    role: RoleCode | None,
    rule: AuditRule | None,
    annotation: AuditAnnotation,
    problem: Mapping[str, Any] | None = None,
    route_template: str | None = None,
) -> AuditDecision | None:
    """Return the row this request should produce, or ``None`` for silence.

    Pure. The whole policy of this module lives here, and nothing in it touches a database,
    a socket or a clock.

    Order of the tests matters:

    1. **A denial is always a row.** Checked first, before the registry and before the
       deduplication flag, so an unregistered path and an opted-out handler both still
       leave the refusal in the log.
    2. **Anything else that failed is not a policy decision.** A 404, a 422 or a 500 is not
       an authorisation outcome, and recording it as one would make ``policy_result``
       mean two different things.
    3. **Then the registry**, which is what keeps ``/health`` out of the table.
    4. **Then the flag**, which is what keeps ``assume-role`` from being logged twice.
    """
    safe_path = _safe_path(path)
    base_payload: dict[str, Any] = {
        "method": method.upper(),
        "path": safe_path,
        "status": status,
    }
    if route_template is not None:
        base_payload["route"] = route_template

    is_export_path = EXPORT_PATH_SEGMENT in path.strip("/").split("/")

    # 1. Denials -----------------------------------------------------------------
    if status == DENIAL_STATUS or (problem is not None and problem.get("code") in _DENIAL_CODES):
        payload = {**base_payload, **_denial_details(problem)}
        if rule is not None:
            payload["rule"] = rule.name
        return AuditDecision(
            action=ACCESS_DENIED,
            object_type=rule.object_type if rule is not None else ACCESS_OBJECT_TYPE,
            policy_result=PolicyResult.DENY,
            # The zone of the content that was refused is unknown -- the request never
            # reached it. dominant() with no parts answers MISSION_INTERNAL rather than
            # PUBLIC for exactly this situation (ADR-0006 point 6: unknown fails closed),
            # and the same default is used here so a denial row is never world-readable.
            classification=rule.classification if rule is not None else dominant(),
            summary=(
                f"{_actor_label(role)} was refused {method.upper()} {safe_path} "
                f"({payload.get('reason')})."
            ),
            payload=payload,
            rule_name=rule.name if rule is not None else None,
        )

    # 2. Everything else that failed ---------------------------------------------
    if status >= 400:
        return None

    # 3. The registry --------------------------------------------------------------
    if rule is None and not is_export_path:
        return None

    kind = rule.kind if rule is not None else AuditKind.EXPORT

    # 4. Deduplication --------------------------------------------------------------
    if annotation.self_audited or (rule is not None and rule.self_audited):
        return None

    classification = annotation.classification or (
        rule.classification if rule is not None else dominant()
    )
    object_type = annotation.object_type or (
        rule.object_type if rule is not None else ACCESS_OBJECT_TYPE
    )
    payload = {**base_payload}
    if rule is not None:
        payload["rule"] = rule.name
    payload.update(annotation.payload)

    if kind is AuditKind.PRIVILEGED_READ:
        if classification not in PRIVILEGED_CLASSIFICATIONS:
            # An ordinary read of ordinary material. Recording it would add a row per page
            # view, and ADR-0004 asks for privileged reads specifically.
            return None
        summary = (
            f"{_actor_label(role)} read {object_type} classified {classification.value} "
            f"via {method.upper()} {safe_path}."
        )
        action = annotation.action or (rule.action if rule is not None else ACCESS_PRIVILEGED_READ)
    elif kind is AuditKind.EXPORT:
        summary = (
            f"{_actor_label(role)} exported {object_type} classified "
            f"{classification.value} via {method.upper()} {safe_path}."
        )
        action = annotation.action or EXPORT_PERFORMED
    else:  # AuditKind.LOGIN, reached only when the route did not audit itself.
        summary = f"{_actor_label(role)} assumed a role via {method.upper()} {safe_path}."
        action = annotation.action or (rule.action if rule is not None else SESSION_ROLE_ASSUMED)

    return AuditDecision(
        action=action,
        object_type=object_type,
        policy_result=PolicyResult.ALLOW,
        classification=classification,
        summary=annotation.summary or summary,
        payload=payload,
        object_id=annotation.object_id,
        object_public_ref=annotation.object_public_ref,
        trace_id=annotation.trace_id,
        rule_name=rule.name if rule is not None else None,
    )


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class _ResponseCapture:
    """Records the response status, and the body of a problem document only.

    A general response-buffering middleware would double the memory cost of every
    response and break streaming. This one starts buffering only after seeing a 403 status
    line, stops at :data:`_PROBLEM_BODY_MAX_BYTES`, and passes every message straight
    through untouched -- the client's bytes are never delayed, reordered or rewritten.
    """

    __slots__ = ("body", "started", "status", "truncated")

    def __init__(self) -> None:
        self.status: int | None = None
        self.started = False
        self.body = bytearray()
        self.truncated = False

    def observe(self, message: Message) -> None:
        """Note whatever this response message reveals."""
        kind = message.get("type")
        if kind == "http.response.start":
            self.started = True
            status = message.get("status")
            self.status = status if isinstance(status, int) else None
            return
        if kind != "http.response.body" or self.status != DENIAL_STATUS:
            return
        chunk = message.get("body") or b""
        room = _PROBLEM_BODY_MAX_BYTES - len(self.body)
        if room <= 0:
            self.truncated = True
            return
        self.body.extend(chunk[:room])
        self.truncated = self.truncated or len(chunk) > room

    def problem(self) -> Mapping[str, Any] | None:
        """Parse the captured problem document, or ``None`` if there is not one."""
        if self.status != DENIAL_STATUS or not self.body or self.truncated:
            return None
        try:
            document: object = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # A 403 whose body is not the problem document we expect is still a denial;
            # only the detail is lost. Nothing is raised and nothing is logged as an error.
            return None
        return document if isinstance(document, dict) else None


class AuditMiddleware:
    """ASGI middleware appending the ADR-0004 automatic rows.

    Raw ASGI rather than ``BaseHTTPMiddleware``: this needs the response *status* and, for
    a 403 only, the response *body*, and ``BaseHTTPMiddleware`` would require consuming and
    rebuilding the body stream of every response to get them. Passing ``send`` through a
    thin wrapper reads both without touching what the client receives.

    Position in the stack does not matter for correctness. Every 403 in this API is
    produced by ``ExceptionMiddleware``, which is innermost, so the response is visible from
    anywhere outside it; and ``request_id`` is read from ``scope["state"]``, which
    ``RequestContextMiddleware`` populates on the shared scope rather than through a
    context variable that would have been reset by the time control returns here.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        rules: Sequence[AuditRule] = DEFAULT_RULES,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        """Wrap ``app``.

        Args:
            app: The next ASGI application in the stack.
            rules: The route registry. Overridable so a test can drive the middleware with
                a rule of its own instead of asserting against whatever Week 1 happens to
                register.
            session_factory: Where the middleware's own session comes from. Defaults to the
                process-wide factory, resolved lazily so importing this module never builds
                an engine. Injectable because ``audit_events`` is append-only: a test must
                be able to hand in a factory bound to a transaction it will roll back, or
                its rows would sit in the demo's timeline forever.
        """
        self.app = app
        self.rules = tuple(rules)
        self._session_factory = session_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve the request, then append its audit row if it earned one."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})
        capture = _ResponseCapture()

        async def send_wrapper(message: Message) -> None:
            capture.observe(message)
            await send(message)

        await self.app(scope, receive, send_wrapper)
        await self._record(scope, capture)

    # -- internals ---------------------------------------------------------------

    def _match(self, method: str, path: str) -> AuditRule | None:
        """Return the first rule governing ``method path``, or ``None``.

        First match wins, so a specific rule must be registered before a general one. With
        two rules that is not yet a hazard; the route-coverage test is where a shadowed
        rule would be caught once there are twenty.
        """
        return next((rule for rule in self.rules if rule.matches(method, path)), None)

    async def _record(self, scope: Scope, capture: _ResponseCapture) -> None:
        """Decide, then write. Never raises, and never alters the response.

        The response is already on the wire when this runs. Anything that goes wrong from
        here is logged at ``error`` with the request id attached and nothing else happens --
        the availability-over-completeness trade documented at the top of this module.
        """
        try:
            if capture.status is None:
                # The response never started -- a client disconnect or a cancelled task.
                # There is no outcome to record.
                return

            request = Request(scope)
            role = read_session(request.cookies.get(SESSION_COOKIE_NAME))
            method = scope.get("method", "GET")
            path = scope.get("path", "")
            route = scope.get("route")
            route_template = getattr(route, "path", None)

            decision = decide(
                method=method,
                path=path,
                status=capture.status,
                role=role,
                rule=self._match(method, path),
                annotation=_annotation_in(scope),
                problem=capture.problem(),
                route_template=route_template if isinstance(route_template, str) else None,
            )
            if decision is None:
                return

            await run_in_threadpool(
                self._write,
                decision,
                role,
                self._request_id(scope),
                _client_ip(scope),
                _user_agent(scope),
            )
        except Exception as exc:  # an audit failure must never reach the caller
            _logger.error(
                "audit.middleware_write_failed",
                path=_safe_path(str(scope.get("path", ""))),
                method=str(scope.get("method", "")),
                status=capture.status,
                error_type=type(exc).__name__,
                hint=(
                    "The response was already served. This row is LOST -- treat a "
                    "recurrence as a gap in the audit trail, not as noise."
                ),
                exc_info=exc,
            )

    @staticmethod
    def _request_id(scope: Scope) -> str | None:
        """Read the correlation id ``RequestContextMiddleware`` put on the scope."""
        state = scope.get("state") or {}
        value = state.get("request_id")
        return value if isinstance(value, str) and value else None

    def _write(
        self,
        decision: AuditDecision,
        role: RoleCode | None,
        request_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        """Append the row in its own transaction. Synchronous; runs in the threadpool.

        SQLAlchemy is synchronous here by a locked decision (ADR-0005), so this must not run
        on the event loop -- a blocked loop during a live demo is a stalled UI for every
        other request in flight.

        This is the one audit write that is *not* inside a business transaction, and that is
        correct rather than a compromise: the thing being recorded is the HTTP request
        itself, which finished before this ran. There is no state change here to keep it
        atomic with.
        """
        factory = self._session_factory
        session = factory() if factory is not None else _default_session_factory()()
        try:
            actor: Principal | None = None
            if role is not None:
                actor = principal_for_role(role)
                # audit_events.actor_user_id is a FK ON DELETE RESTRICT. A signed cookie
                # proves a role, not that anybody provisioned its persona -- after a
                # demo-reset the row is gone while the cookie is still valid. Idempotent
                # and cheap; without it the insert would fail and the denial would be lost.
                ensure_persona_user(session, demo_persona(role))

            write_audit_event(
                session,
                actor=actor,
                action=decision.action,
                object_type=decision.object_type,
                object_id=decision.object_id,
                object_public_ref=decision.object_public_ref,
                policy_result=decision.policy_result,
                classification=decision.classification,
                summary=decision.summary,
                payload=decision.payload,
                request_id=request_id,
                trace_id=decision.trace_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _default_session_factory() -> Callable[[], Session]:
    """Resolve the process-wide session factory lazily.

    Imported inside the function so that importing this module never constructs an engine:
    the pure decision tests, the seed loader and ``--help`` must all be able to import it
    with no database configured.
    """
    from app.core.db import get_session_factory

    return get_session_factory()


def _client_ip(scope: Scope) -> str | None:
    """Best-effort client address from the ASGI scope.

    Reads ``scope["client"]``, never ``X-Forwarded-For``: a forwarded header is
    caller-controlled, and a caller-controlled value in an evidentiary column is worse than
    a NULL. Behind a real proxy this is configured at the ASGI layer (uvicorn
    ``--proxy-headers`` with trusted hosts), which rewrites ``scope["client"]`` from a
    header the proxy is trusted to have set.
    """
    client = scope.get("client")
    if not client:
        return None
    host = client[0]
    return str(host)[:_IP_MAX_LENGTH] if host else None


def _user_agent(scope: Scope) -> str | None:
    """Client user agent from the raw ASGI headers, truncated to the column width."""
    for name, value in scope.get("headers") or ():
        if name == b"user-agent":
            decoded = value.decode("latin-1", errors="replace").strip()
            return decoded[:_USER_AGENT_MAX_LENGTH] or None
    return None
