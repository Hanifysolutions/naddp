"""Automatic audit capture at the HTTP boundary.

Split the same way ``test_audit_writer.py`` is. The decision -- "given this request, this
status and what the handler said, is there a row and what is in it" -- is a pure function,
so most of this file is unit tests with no application, no event loop and no database. Only
the tests that assert a row actually lands in ``audit_events`` are marked ``integration``.

Those run against a session factory bound to an outer transaction that is always rolled
back. ``audit_events`` is append-only (ADR-0004): a row this suite committed could not be
deleted afterwards and would sit in the demo's audit timeline forever, looking exactly like
a real denial.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from app.audit.middleware import (
    ACCESS_DENIED,
    ACCESS_OBJECT_TYPE,
    ACCESS_PRIVILEGED_READ,
    DEFAULT_RULES,
    EXPORT_PERFORMED,
    MIDDLEWARE_ACTIONS,
    PRIVILEGED_CLASSIFICATIONS,
    AuditAnnotation,
    AuditKind,
    AuditMiddleware,
    AuditRule,
    _ResponseCapture,
    annotation_for,
    decide,
    mark_audited,
    record_access,
)
from app.domain.enums import Classification, PolicyResult, RoleCode
from app.models.governance import AuditEvent
from app.security.session import SESSION_COOKIE_NAME, issue_session

ME_URL: Final[str] = "/v1/session/me"
ASSUME_URL: Final[str] = "/v1/session/assume-role"
HEALTH_URL: Final[str] = "/health"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decide(
    *,
    method: str = "GET",
    path: str = "/v1/anything",
    status: int = 200,
    role: RoleCode | None = RoleCode.DEPUTY,
    rule: AuditRule | None = None,
    annotation: AuditAnnotation | None = None,
    problem: dict[str, Any] | None = None,
    route_template: str | None = None,
) -> Any:
    """Call :func:`decide` with defaults, so each test states only what it is about."""
    return decide(
        method=method,
        path=path,
        status=status,
        role=role,
        rule=rule,
        annotation=annotation if annotation is not None else AuditAnnotation(),
        problem=problem,
        route_template=route_template,
    )


def _rule_named(name: str) -> AuditRule:
    """Return one registered rule by name."""
    return next(rule for rule in DEFAULT_RULES if rule.name == name)


READ_RULE: Final[AuditRule] = _rule_named("audit.read_events")
LOGIN_RULE: Final[AuditRule] = _rule_named("session.assume_role")


def _fake_request(path: str = "/v1/anything") -> Request:
    """A minimal ASGI request, enough for the annotation helpers."""
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "state": {}})


# ---------------------------------------------------------------------------
# 1. The registry itself
# ---------------------------------------------------------------------------


def test_every_registered_action_is_in_the_declared_vocabulary() -> None:
    """A rule cannot smuggle in a verb the closed vocabulary does not know about."""
    for rule in DEFAULT_RULES:
        assert rule.action in MIDDLEWARE_ACTIONS


def test_registered_rule_names_are_unique() -> None:
    """First match wins, so two rules sharing a name would make a finding unattributable."""
    names = [rule.name for rule in DEFAULT_RULES]
    assert len(names) == len(set(names))


def test_assume_role_is_registered_as_self_audited() -> None:
    """The service writes ``session.role_assumed`` itself; the middleware must not repeat it."""
    assert LOGIN_RULE.kind is AuditKind.LOGIN
    assert LOGIN_RULE.self_audited is True


def test_the_audit_read_rule_declares_a_sub_privileged_baseline() -> None:
    """Reading a page of ordinary rows must not write a row; the handler upgrades the zone."""
    assert READ_RULE.kind is AuditKind.PRIVILEGED_READ
    assert READ_RULE.classification not in PRIVILEGED_CLASSIFICATIONS


def test_rules_match_on_method_and_full_path() -> None:
    assert READ_RULE.matches("GET", "/v1/audit/events")
    assert READ_RULE.matches("get", "/v1/audit/events/")
    # Method must match.
    assert not READ_RULE.matches("POST", "/v1/audit/events")
    # fullmatch, not search: a longer path is a different route.
    assert not READ_RULE.matches("GET", "/v1/audit/events/export")
    assert not READ_RULE.matches("GET", "/x/v1/audit/events")


def test_privileged_classifications_are_exactly_the_two_adr_0004_names() -> None:
    assert set(PRIVILEGED_CLASSIFICATIONS) == {
        Classification.CONFIDENTIAL,
        Classification.CONSULAR_SENSITIVE,
    }


# ---------------------------------------------------------------------------
# 2. Denials -- the rows that matter
# ---------------------------------------------------------------------------


def test_a_denial_on_an_unregistered_path_is_still_recorded() -> None:
    """The registry bounds volume, not denials. A refusal is always signal."""
    decision = _decide(path="/v1/consular/cases", status=403, rule=None)

    assert decision is not None
    assert decision.action == ACCESS_DENIED
    assert decision.policy_result is PolicyResult.DENY
    assert decision.object_type == ACCESS_OBJECT_TYPE


def test_a_denial_fails_closed_on_classification() -> None:
    """The zone of refused content is unknown, so the row must not be world-readable."""
    decision = _decide(status=403)

    assert decision is not None
    assert decision.classification is Classification.MISSION_INTERNAL


def test_a_denial_carries_what_was_attempted() -> None:
    """A denial that cannot say what was attempted is not evidence of anything."""
    decision = _decide(
        method="POST",
        path="/v1/opportunities/x/commit",
        status=403,
        role=RoleCode.TRADE_OFFICER,
        problem={
            "code": "permission_denied",
            "reason": "missing_permission",
            "required_permissions": ["commit:opportunity"],
            "missing_permissions": ["commit:opportunity"],
        },
    )

    assert decision is not None
    assert decision.payload["reason"] == "missing_permission"
    assert decision.payload["missing_permissions"] == ["commit:opportunity"]
    assert decision.payload["method"] == "POST"
    assert decision.payload["status"] == 403
    assert "TRADE_OFFICER" in decision.summary


def test_a_denial_without_a_readable_problem_document_is_still_recorded() -> None:
    """Losing the detail of a denial is bad; losing the denial is worse."""
    decision = _decide(status=403, problem=None)

    assert decision is not None
    assert decision.payload["reason"] == "unavailable"


def test_an_unauthenticated_denial_records_no_actor() -> None:
    decision = _decide(path=ME_URL, status=403, role=None)

    assert decision is not None
    assert "unauthenticated" in decision.summary


def test_the_deduplication_flag_cannot_suppress_a_denial() -> None:
    """An opt-out that could switch off denial logging is an opt-out worth attacking."""
    annotated = AuditAnnotation(self_audited=True)
    assert _decide(status=403, annotation=annotated) is not None
    assert _decide(status=403, rule=LOGIN_RULE, annotation=annotated) is not None


def test_a_classification_denial_is_recorded_like_a_permission_denial() -> None:
    decision = _decide(
        status=403,
        problem={"code": "classification_denied", "reason": "insufficient_clearance"},
    )

    assert decision is not None
    assert decision.action == ACCESS_DENIED
    assert decision.payload["reason"] == "insufficient_clearance"


@pytest.mark.parametrize("status", [400, 404, 409, 422, 500, 502])
def test_other_failures_are_not_policy_decisions(status: int) -> None:
    """A 404 or a 500 is not an authorisation outcome and must not be recorded as one."""
    assert _decide(status=status) is None


# ---------------------------------------------------------------------------
# 3. Silence -- what must never reach the table
# ---------------------------------------------------------------------------


def test_an_unregistered_success_writes_nothing() -> None:
    """Opt-in, not blocklist: a route nobody registered is silent."""
    assert _decide(path=HEALTH_URL, status=200, rule=None) is None


def test_a_self_audited_route_writes_nothing_on_success() -> None:
    """assume-role's own row is written in-transaction by the service; one row, not two."""
    assert _decide(method="POST", path=ASSUME_URL, status=200, rule=LOGIN_RULE) is None


def test_a_handler_can_suppress_its_own_allow_row() -> None:
    annotation = AuditAnnotation(self_audited=True, classification=Classification.CONFIDENTIAL)
    assert _decide(path="/v1/audit/events", status=200, rule=READ_RULE, annotation=annotation) is (
        None
    )


@pytest.mark.parametrize("zone", [Classification.PUBLIC, Classification.MISSION_INTERNAL])
def test_an_ordinary_read_is_not_a_privileged_read(zone: Classification) -> None:
    """Auditing every page view would bury the ten rows that matter."""
    annotation = AuditAnnotation(classification=zone)
    assert _decide(path="/v1/audit/events", status=200, rule=READ_RULE, annotation=annotation) is (
        None
    )


# ---------------------------------------------------------------------------
# 4. Privileged reads and exports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zone", [Classification.CONFIDENTIAL, Classification.CONSULAR_SENSITIVE])
def test_a_privileged_read_is_recorded(zone: Classification) -> None:
    decision = _decide(
        path="/v1/audit/events",
        status=200,
        rule=READ_RULE,
        annotation=AuditAnnotation(classification=zone),
    )

    assert decision is not None
    assert decision.action == ACCESS_PRIVILEGED_READ
    assert decision.policy_result is PolicyResult.ALLOW
    assert decision.classification is zone
    assert decision.object_type == "governance.audit_event"


def test_a_login_route_that_does_not_audit_itself_is_recorded_by_the_middleware() -> None:
    """The fallback the ``self_audited`` flag exists to switch off.

    Nothing in Week 1 reaches this branch, because ``assume-role`` writes its own row. It
    is asserted anyway: the day a login route lands that does *not*, the middleware must be
    what keeps the authentication event in the log rather than nothing at all.
    """
    rule = AuditRule(
        name="test.login",
        methods=frozenset({"POST"}),
        path=re.compile(r"/v1/test/login"),
        kind=AuditKind.LOGIN,
        action="session.role_assumed",
        object_type="governance.user",
        classification=Classification.MISSION_INTERNAL,
        self_audited=False,
    )
    decision = _decide(method="POST", path="/v1/test/login", status=200, rule=rule)

    assert decision is not None
    assert decision.action == "session.role_assumed"
    assert decision.policy_result is PolicyResult.ALLOW
    assert "assumed a role" in decision.summary


def test_the_route_template_is_recorded_beside_the_concrete_path() -> None:
    """So a reader can group by route without the object id exploding the cardinality."""
    decision = _decide(
        path="/v1/consular/cases/018f0000-0000-7000-8000-000000000001",
        status=403,
        route_template="/v1/consular/cases/{case_id}",
    )

    assert decision is not None
    assert decision.payload["route"] == "/v1/consular/cases/{case_id}"
    assert decision.payload["path"].startswith("/v1/consular/cases/018f")


def test_a_handler_annotation_reaches_the_row() -> None:
    object_id = uuid.uuid4()
    decision = _decide(
        path="/v1/audit/events",
        status=200,
        rule=READ_RULE,
        annotation=AuditAnnotation(
            classification=Classification.CONFIDENTIAL,
            object_id=object_id,
            object_public_ref="CS-2026-0001",
            payload={"returned": 3},
        ),
    )

    assert decision is not None
    assert decision.object_id == object_id
    assert decision.object_public_ref == "CS-2026-0001"
    assert decision.payload["returned"] == 3
    # Rule provenance is on the row, so a surprising entry can be traced to its rule.
    assert decision.payload["rule"] == READ_RULE.name


def test_an_export_path_segment_is_audited_with_no_rule_at_all() -> None:
    """PROMPT Phase 3: 'any route carrying export:bulk or a path segment /export'."""
    decision = _decide(path="/v1/opportunities/export", status=200, rule=None)

    assert decision is not None
    assert decision.action == EXPORT_PERFORMED
    assert decision.policy_result is PolicyResult.ALLOW


def test_an_export_is_recorded_even_when_the_content_is_public() -> None:
    """Reading one record and extracting the table are different risks (ADR-0003 rule 4)."""
    decision = _decide(
        path="/v1/diaspora/export",
        status=200,
        rule=None,
        annotation=AuditAnnotation(classification=Classification.PUBLIC),
    )

    assert decision is not None
    assert decision.action == EXPORT_PERFORMED
    assert decision.classification is Classification.PUBLIC


def test_export_matches_a_whole_segment_only() -> None:
    """``/v1/exports`` and ``/v1/exporters`` are not the export segment."""
    assert _decide(path="/v1/exports", status=200) is None
    assert _decide(path="/v1/knowledge/exporters", status=200) is None


def test_every_action_decide_can_emit_is_in_the_vocabulary() -> None:
    emitted = [
        _decide(status=403),
        _decide(path="/v1/x/export", status=200),
        _decide(
            path="/v1/audit/events",
            status=200,
            rule=READ_RULE,
            annotation=AuditAnnotation(classification=Classification.CONFIDENTIAL),
        ),
    ]
    for decision in emitted:
        assert decision is not None
        assert decision.action in MIDDLEWARE_ACTIONS


# ---------------------------------------------------------------------------
# 5. Untrusted input in an evidentiary column
# ---------------------------------------------------------------------------


def test_a_path_with_control_characters_cannot_forge_a_log_line() -> None:
    decision = _decide(path="/v1/a\nINJECTED\rb", status=403)

    assert decision is not None
    assert "\n" not in decision.summary
    assert "\r" not in decision.payload["path"]


def test_a_very_long_path_is_truncated() -> None:
    decision = _decide(path="/v1/" + "a" * 5000, status=403)

    assert decision is not None
    assert len(decision.payload["path"]) < 300
    assert decision.payload["path"].endswith("...")


# ---------------------------------------------------------------------------
# 6. Response capture
# ---------------------------------------------------------------------------


def _capture(status: int, body: bytes) -> _ResponseCapture:
    capture = _ResponseCapture()
    capture.observe({"type": "http.response.start", "status": status, "headers": []})
    capture.observe({"type": "http.response.body", "body": body})
    return capture


def test_capture_buffers_nothing_for_a_normal_response() -> None:
    """A general response buffer would double the memory cost of every response."""
    capture = _capture(200, b'{"large": "payload"}')

    assert capture.status == 200
    assert capture.body == b""
    assert capture.problem() is None


def test_capture_parses_a_problem_document() -> None:
    capture = _capture(403, json.dumps({"code": "permission_denied"}).encode())

    assert capture.problem() == {"code": "permission_denied"}


def test_capture_gives_up_rather_than_guessing_at_a_truncated_body() -> None:
    capture = _capture(403, b"x" * (17 * 1024))

    assert capture.truncated is True
    assert capture.problem() is None


def test_capture_survives_a_body_that_is_not_json() -> None:
    assert _capture(403, b"<html>nope</html>").problem() is None


def test_capture_survives_a_json_body_that_is_not_an_object() -> None:
    assert _capture(403, b"[1, 2, 3]").problem() is None


# ---------------------------------------------------------------------------
# 7. Annotation helpers
# ---------------------------------------------------------------------------


def test_annotation_is_created_once_per_request_and_shared() -> None:
    request = _fake_request()
    assert annotation_for(request) is annotation_for(request)


def test_mark_audited_sets_the_flag() -> None:
    request = _fake_request()
    mark_audited(request)
    assert annotation_for(request).self_audited is True


def test_record_access_merges_without_clobbering() -> None:
    request = _fake_request()
    record_access(request, classification=Classification.CONFIDENTIAL, payload={"a": 1})
    record_access(request, classification=Classification.CONSULAR_SENSITIVE, payload={"b": 2})

    annotation = annotation_for(request)
    assert annotation.classification is Classification.CONSULAR_SENSITIVE
    assert annotation.payload == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# 8. The availability-over-completeness trade, without a database
# ---------------------------------------------------------------------------


def _no_session() -> Session:
    """A factory that must never be called. Raises rather than failing the test directly,
    so that the middleware's own ``except Exception`` is what is being exercised."""
    msg = "the middleware opened a session it should not have"
    raise RuntimeError(msg)


async def _denying_app(scope: Scope, receive: Receive, send: Send) -> None:
    """A bare ASGI app that always refuses, in the shape ``app.core.errors`` produces."""
    body = json.dumps({"code": "permission_denied", "reason": "missing_permission"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/problem+json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def test_an_audit_failure_never_reaches_the_caller() -> None:
    """Deliberate: the response is already sent, so failing the request recovers nothing."""
    attempts: list[str] = []

    def exploding_factory() -> Session:
        attempts.append("called")
        msg = "the audit database is on fire"
        raise RuntimeError(msg)

    client = TestClient(AuditMiddleware(_denying_app, session_factory=exploding_factory))
    response = client.get("/v1/anything")

    assert response.status_code == 403
    assert response.json() == {"code": "permission_denied", "reason": "missing_permission"}
    assert attempts == ["called"], "the middleware must have tried, and swallowed the failure"


def test_the_response_body_is_passed_through_byte_for_byte() -> None:
    """The middleware observes; it never rewrites what the client receives."""
    client = TestClient(AuditMiddleware(_denying_app, session_factory=_no_session))
    assert (
        client.get("/v1/anything").content
        == json.dumps({"code": "permission_denied", "reason": "missing_permission"}).encode()
    )


def test_a_non_http_scope_is_passed_straight_through() -> None:
    """Lifespan and websocket traffic have no audit outcome and must not be inspected."""
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    async def drive() -> None:
        async def receive() -> Any:
            return {"type": "lifespan.startup"}

        async def send(_message: Any) -> None:
            return None

        middleware = AuditMiddleware(inner, session_factory=_no_session)
        await middleware({"type": "lifespan"}, receive, send)

    asyncio.run(drive())
    assert [scope["type"] for scope in seen] == ["lifespan"]


# ---------------------------------------------------------------------------
# 9. Integration -- rows actually landing
# ---------------------------------------------------------------------------


@pytest.fixture
def audited_client(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """An app with the audit middleware installed, on a transaction that is rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine, get_session
    from app.main import create_app

    connection = get_engine().connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    request_session = factory()

    # create_app() installs AuditMiddleware itself now, so the factory is injected
    # rather than a second instance stacked on top -- two instances would write every
    # row twice, and the second copy would be committed for real.
    app = create_app(audit_session_factory=factory)
    app.dependency_overrides[get_session] = lambda: request_session

    try:
        with TestClient(app) as client:
            client.cookies.clear()
            yield client, request_session
    finally:
        app.dependency_overrides.clear()
        request_session.close()
        transaction.rollback()
        connection.close()


def _rows(session: Session, action: str, request_id: str) -> list[AuditEvent]:
    """Every row this request produced with ``action``, oldest first."""
    session.expire_all()
    statement = (
        select(AuditEvent)
        .where(AuditEvent.request_id == request_id, AuditEvent.action == action)
        .order_by(AuditEvent.id)
    )
    return list(session.scalars(statement))


@pytest.mark.integration
def test_a_denial_writes_one_deny_row(audited_client: tuple[TestClient, Session]) -> None:
    client, session = audited_client
    response = client.get(ME_URL)
    assert response.status_code == 403

    request_id = response.headers["X-Request-ID"]
    rows = _rows(session, ACCESS_DENIED, request_id)

    assert len(rows) == 1
    row = rows[0]
    assert row.policy_result is PolicyResult.DENY
    assert row.actor_user_id is None
    assert row.payload["reason"] == "no_session"
    assert row.payload["path"] == ME_URL
    assert row.request_id == request_id


@pytest.mark.integration
def test_the_row_carries_the_client_and_the_clock(
    audited_client: tuple[TestClient, Session],
) -> None:
    """PROMPT Phase 3 names actor, action, object, result, request id, time, ip, agent."""
    client, session = audited_client
    response = client.get(ME_URL, headers={"user-agent": "naddp-test/1.0"})
    assert response.status_code == 403

    row = _rows(session, ACCESS_DENIED, response.headers["X-Request-ID"])[0]

    assert row.user_agent == "naddp-test/1.0"
    assert row.ip_address
    assert row.occurred_at is not None
    assert row.object_type == ACCESS_OBJECT_TYPE
    # The timestamp is the database's, and it is inside the hash chain.
    assert row.event_hash


@pytest.mark.integration
def test_a_denial_by_a_known_role_names_the_actor(
    audited_client: tuple[TestClient, Session],
) -> None:
    """TRADE_OFFICER holds no read:audit, so /v1/audit/events refuses it -- with a name."""
    from app.api.v1.audit import router as audit_router

    client, session = audited_client
    client.app.include_router(audit_router, prefix="/v1")  # type: ignore[attr-defined]
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(RoleCode.TRADE_OFFICER))

    response = client.get("/v1/audit/events")
    assert response.status_code == 403

    rows = _rows(session, ACCESS_DENIED, response.headers["X-Request-ID"])
    assert len(rows) == 1
    assert rows[0].actor_role is RoleCode.TRADE_OFFICER
    assert rows[0].payload["missing_permissions"] == ["read:audit"]
    # The FK is ON DELETE RESTRICT, so the middleware must have provisioned the persona.
    assert rows[0].actor_user_id is not None


@pytest.mark.integration
def test_a_health_poll_writes_nothing(audited_client: tuple[TestClient, Session]) -> None:
    """A row per health check would drown the real signal."""
    client, session = audited_client
    response = client.get(HEALTH_URL)
    assert response.status_code == 200

    request_id = response.headers["X-Request-ID"]
    session.expire_all()
    found = session.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id)).all()
    assert found == []


@pytest.mark.integration
def test_assume_role_writes_exactly_one_row(
    audited_client: tuple[TestClient, Session],
) -> None:
    """The service's in-transaction row, and no duplicate from the middleware."""
    client, session = audited_client
    response = client.post(ASSUME_URL, json={"role": RoleCode.DEPUTY.value})
    assert response.status_code == 200

    request_id = response.headers["X-Request-ID"]
    session.expire_all()
    found = list(session.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id)))
    assert len(found) == 1
    assert found[0].action == "session.role_assumed"


@pytest.mark.integration
def test_the_middleware_row_joins_the_hash_chain(
    audited_client: tuple[TestClient, Session],
) -> None:
    """A row written outside a business transaction is still a link, not an orphan."""
    from app.audit.writer import verify_chain

    client, session = audited_client
    assert client.get(ME_URL).status_code == 403
    assert client.get(ME_URL).status_code == 403

    session.expire_all()
    result = verify_chain(session, limit=5)
    assert result.is_intact, result.reason


def test_rule_patterns_are_precompiled() -> None:
    """Compiled at import, so a malformed pattern is an ImportError rather than a miss."""
    for rule in DEFAULT_RULES:
        assert isinstance(rule.path, re.Pattern)
