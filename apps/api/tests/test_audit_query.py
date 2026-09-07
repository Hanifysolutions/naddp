"""The audit read service: authorisation in SQL, keyset paging, chain exposure.

The interesting assertions here are about what is *not* returned. ``ADMIN`` holds
``read:audit`` and has clearance rank 10 with no compartments, so the consular rows are
never selected for it -- not redacted afterwards, never loaded. That is the difference
between a filter and a mask, and it is the property ``CLAUDE.md`` rule 5 and ADR-0006 are
about.

Everything that touches rows is marked ``integration``: these are properties of a query
against a real table, and asserting them against a mock would assert nothing. The suite
never commits -- ``audit_events`` is append-only (ADR-0004), so a committed test row could
not be removed and would sit in the demo's timeline forever.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy.orm import Session

from app.audit.query import (
    DEFAULT_PAGE_SIZE,
    MAX_CHAIN_VERIFY_LIMIT,
    MAX_PAGE_SIZE,
    AuditFilter,
    AuditFilterError,
    describe_filter,
    list_audit_events,
    summarise_classifications,
    verify_audit_chain,
)
from app.core.errors import PermissionDeniedError
from app.domain.enums import Classification, PolicyResult, RoleCode
from app.models.governance import AuditEvent
from app.security.permissions import Permission
from app.security.principal import Principal, demo_persona, principal_for_role
from app.services.session import ensure_persona_user

TEST_ACTION: Final[str] = "test.query_probe"
OTHER_ACTION: Final[str] = "test.query_other"
TEST_OBJECT_TYPE: Final[str] = "governance.test"

DEPUTY: Final[Principal] = principal_for_role(RoleCode.DEPUTY)
ADMIN: Final[Principal] = principal_for_role(RoleCode.ADMIN)
TRADE: Final[Principal] = principal_for_role(RoleCode.TRADE_OFFICER)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose transaction is always rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _write(
    session: Session,
    *,
    actor: Principal | None = None,
    action: str = TEST_ACTION,
    classification: Classification = Classification.MISSION_INTERNAL,
    policy_result: PolicyResult = PolicyResult.ALLOW,
    object_id: uuid.UUID | None = None,
    object_public_ref: str | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    """Append one probe row, provisioning the actor's ``users`` row if needed."""
    from app.audit.writer import write_audit_event

    if actor is not None:
        ensure_persona_user(session, demo_persona(actor.role))
    return write_audit_event(
        session,
        actor=actor,
        action=action,
        object_type=TEST_OBJECT_TYPE,
        object_id=object_id,
        object_public_ref=object_public_ref,
        policy_result=policy_result,
        classification=classification,
        summary="probe",
        request_id=request_id,
    )


# ---------------------------------------------------------------------------
# 1. Permission -- checked in the service, not only on the route
# ---------------------------------------------------------------------------


def test_a_principal_without_read_audit_is_refused() -> None:
    """A service that trusts its caller to have authorised is one refactor from not being."""
    with pytest.raises(PermissionDeniedError) as caught:
        list_audit_events(Session(), TRADE)

    assert caught.value.extra["missing_permissions"] == [Permission.READ_AUDIT.value]
    assert caught.value.extra["actor_role"] == RoleCode.TRADE_OFFICER.value


def test_chain_verification_is_gated_on_the_same_permission() -> None:
    with pytest.raises(PermissionDeniedError):
        verify_audit_chain(Session(), TRADE)


@pytest.mark.parametrize("role", [RoleCode.AMBASSADOR, RoleCode.DEPUTY, RoleCode.ADMIN])
def test_exactly_the_three_audit_roles_hold_the_permission(role: RoleCode) -> None:
    """Q-02b: ADMIN reads the log but not the content it describes."""
    assert principal_for_role(role).has(Permission.READ_AUDIT)


# ---------------------------------------------------------------------------
# 2. Filter validation (pure unit)
# ---------------------------------------------------------------------------


def test_an_inverted_date_range_is_a_422_not_an_empty_page() -> None:
    """Silently answering 'no such activity' is the worst possible reply from an audit tool."""
    now = datetime.now(UTC)
    with pytest.raises(AuditFilterError) as caught:
        AuditFilter(occurred_from=now, occurred_to=now - timedelta(days=1)).validate()

    assert caught.value.status_code == 422
    assert "occurred_from" in caught.value.extra


def test_an_equal_date_range_is_legal() -> None:
    moment = datetime.now(UTC)
    AuditFilter(occurred_from=moment, occurred_to=moment).validate()


def test_describe_filter_omits_unset_fields_and_renders_json_safe_values() -> None:
    identifier = uuid.uuid4()
    described = describe_filter(
        AuditFilter(
            actor_user_id=identifier,
            actor_role=RoleCode.DEPUTY,
            policy_result=PolicyResult.DENY,
            occurred_from=datetime(2026, 9, 7, 12, tzinfo=UTC),
        )
    )

    assert described == {
        "actor_user_id": str(identifier),
        "actor_role": "DEPUTY",
        "policy_result": "DENY",
        "occurred_from": "2026-09-07T12:00:00+00:00",
    }


def test_summarise_classifications_returns_dominance_order() -> None:
    rows = [
        AuditEvent(classification=Classification.CONFIDENTIAL),
        AuditEvent(classification=Classification.PUBLIC),
        AuditEvent(classification=Classification.PUBLIC),
    ]
    assert summarise_classifications(rows) == (
        Classification.PUBLIC,
        Classification.CONFIDENTIAL,
    )


def test_summarise_classifications_of_nothing_is_empty() -> None:
    assert summarise_classifications([]) == ()


# ---------------------------------------------------------------------------
# 3. Authorisation applied in SQL
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_a_reader_never_loads_a_row_above_their_clearance(db: Session) -> None:
    """ADMIN reads the log and cannot read a consular case -- separation of duties."""
    request_id = f"test-{uuid.uuid4()}"
    _write(db, classification=Classification.MISSION_INTERNAL, request_id=request_id)
    _write(db, classification=Classification.CONSULAR_SENSITIVE, request_id=request_id)
    _write(db, classification=Classification.CONFIDENTIAL, request_id=request_id)

    filters = AuditFilter(request_id=request_id)
    deputy_page = list_audit_events(db, DEPUTY, filters=filters)
    admin_page = list_audit_events(db, ADMIN, filters=filters)

    assert {row.classification for row in deputy_page.events} == {
        Classification.MISSION_INTERNAL,
        Classification.CONSULAR_SENSITIVE,
        Classification.CONFIDENTIAL,
    }
    assert {row.classification for row in admin_page.events} == {Classification.MISSION_INTERNAL}


@pytest.mark.integration
def test_the_total_cannot_leak_the_rows_it_excluded(db: Session) -> None:
    """A total of 3 beside 1 rendered row would tell ADMIN exactly what it is missing."""
    request_id = f"test-{uuid.uuid4()}"
    _write(db, classification=Classification.MISSION_INTERNAL, request_id=request_id)
    _write(db, classification=Classification.CONSULAR_SENSITIVE, request_id=request_id)
    _write(db, classification=Classification.CONFIDENTIAL, request_id=request_id)

    page = list_audit_events(db, ADMIN, filters=AuditFilter(request_id=request_id))

    assert page.total_matching == 1
    assert len(page.events) == 1


@pytest.mark.integration
def test_the_applied_zones_are_reported_back(db: Session) -> None:
    """So a short page reads as 'your view of the log' rather than as 'the log'."""
    page = list_audit_events(db, ADMIN, limit=1)

    assert page.applied_classifications == (
        Classification.PUBLIC,
        Classification.MISSION_INTERNAL,
    )


# ---------------------------------------------------------------------------
# 4. Filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_every_filter_narrows_the_result(db: Session) -> None:
    request_id = f"test-{uuid.uuid4()}"
    object_id = uuid.uuid4()
    wanted = _write(
        db,
        actor=DEPUTY,
        action=TEST_ACTION,
        policy_result=PolicyResult.DENY,
        object_id=object_id,
        object_public_ref="OP-2026-0001",
        request_id=request_id,
    )
    _write(db, action=OTHER_ACTION, request_id=request_id)

    cases = [
        AuditFilter(request_id=request_id, action=TEST_ACTION),
        AuditFilter(request_id=request_id, actor_user_id=DEPUTY.user_id),
        AuditFilter(request_id=request_id, actor_role=RoleCode.DEPUTY),
        AuditFilter(request_id=request_id, object_type=TEST_OBJECT_TYPE, object_id=object_id),
        AuditFilter(request_id=request_id, object_public_ref="OP-2026-0001"),
        AuditFilter(request_id=request_id, policy_result=PolicyResult.DENY),
    ]
    for filters in cases:
        page = list_audit_events(db, DEPUTY, filters=filters)
        assert [row.id for row in page.events] == [wanted.id], filters


@pytest.mark.integration
def test_a_date_range_bounds_the_result(db: Session) -> None:
    request_id = f"test-{uuid.uuid4()}"
    row = _write(db, request_id=request_id)
    moment = row.occurred_at

    inside = list_audit_events(
        db,
        DEPUTY,
        filters=AuditFilter(
            request_id=request_id,
            occurred_from=moment - timedelta(minutes=1),
            occurred_to=moment + timedelta(minutes=1),
        ),
    )
    outside = list_audit_events(
        db,
        DEPUTY,
        filters=AuditFilter(request_id=request_id, occurred_from=moment + timedelta(minutes=1)),
    )

    assert [event.id for event in inside.events] == [row.id]
    assert outside.events == ()


@pytest.mark.integration
def test_no_filter_means_everything_you_may_read(db: Session) -> None:
    _write(db)
    page = list_audit_events(db, DEPUTY, limit=5)

    assert page.events
    assert page.total_matching >= len(page.events)


# ---------------------------------------------------------------------------
# 5. Paging
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rows_come_back_newest_first(db: Session) -> None:
    request_id = f"test-{uuid.uuid4()}"
    written = [_write(db, request_id=request_id) for _ in range(4)]

    page = list_audit_events(db, DEPUTY, filters=AuditFilter(request_id=request_id))

    assert [row.id for row in page.events] == [row.id for row in reversed(written)]


@pytest.mark.integration
def test_a_keyset_cursor_walks_the_whole_result_without_repeating(db: Session) -> None:
    """Offset paging on a table that grows while it is read skips and repeats rows."""
    request_id = f"test-{uuid.uuid4()}"
    written = [_write(db, request_id=request_id) for _ in range(5)]
    filters = AuditFilter(request_id=request_id)

    seen: list[uuid.UUID] = []
    cursor: uuid.UUID | None = None
    for _ in range(5):
        page = list_audit_events(db, DEPUTY, filters=filters, limit=2, cursor=cursor)
        seen.extend(row.id for row in page.events)
        assert page.total_matching == 5
        if not page.has_more:
            assert page.next_cursor is None
            break
        assert page.next_cursor is not None
        cursor = page.next_cursor

    assert seen == [row.id for row in reversed(written)]
    assert len(seen) == len(set(seen))


@pytest.mark.integration
def test_the_last_page_reports_no_more(db: Session) -> None:
    request_id = f"test-{uuid.uuid4()}"
    _write(db, request_id=request_id)

    page = list_audit_events(db, DEPUTY, filters=AuditFilter(request_id=request_id), limit=10)

    assert page.has_more is False
    assert page.next_cursor is None


@pytest.mark.integration
def test_the_page_size_is_clamped_rather_than_trusted(db: Session) -> None:
    """``export:bulk`` exists so that reading a record and extracting the table differ."""
    _write(db)
    assert len(list_audit_events(db, DEPUTY, limit=10_000).events) <= MAX_PAGE_SIZE
    assert len(list_audit_events(db, DEPUTY, limit=0).events) <= 1
    assert len(list_audit_events(db, DEPUTY, limit=None).events) <= DEFAULT_PAGE_SIZE


# ---------------------------------------------------------------------------
# 6. Chain verification
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_chain_reports_intact_over_freshly_written_rows(db: Session) -> None:
    _write(db)
    _write(db)

    result = verify_audit_chain(db, DEPUTY, limit=5)

    assert result.is_intact, result.reason
    assert result.checked > 0
    assert result.broken_at_id is None


@pytest.mark.integration
def test_the_chain_is_not_narrowed_by_the_readers_clearance(db: Session) -> None:
    """A filtered walk would report a break at every removed row and prove nothing."""
    _write(db, classification=Classification.CONSULAR_SENSITIVE)
    _write(db, classification=Classification.CONFIDENTIAL)

    as_deputy = verify_audit_chain(db, DEPUTY, limit=4)
    as_admin = verify_audit_chain(db, ADMIN, limit=4)

    assert as_admin.checked == as_deputy.checked
    assert as_admin.is_intact == as_deputy.is_intact


@pytest.mark.integration
def test_the_chain_limit_is_clamped(db: Session) -> None:
    _write(db)
    result = verify_audit_chain(db, DEPUTY, limit=MAX_CHAIN_VERIFY_LIMIT * 10)
    assert result.is_intact, result.reason
