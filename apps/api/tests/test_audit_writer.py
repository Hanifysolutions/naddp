"""The append-only audit writer: canonical hashing, the chain, and the ORM guard.

Split deliberately. The hashing and context tests are pure units and always run. Everything
that asserts a *property of the table* -- the chain, the immutability guard, the
same-transaction rule -- is marked ``integration`` and skips without a database, because
those properties belong to the database and asserting them against a mock would assert
nothing.

Chain tests are scoped with ``verify_chain(limit=...)``. The audit table is append-only and
shared, so a test must never assume it starts empty; a bounded verification anchors on the
row preceding the window and checks only what this test wrote.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.audit.writer import (
    AUDIT_HASH_DOMAIN,
    AuditImmutabilityError,
    _reject_audit_mutation,
    audit_context,
    canonical_json,
    compute_event_hash,
    current_audit_context,
    install_audit_immutability_guard,
    verify_chain,
    write_audit_event,
)
from app.domain.enums import Classification, PolicyResult, RoleCode
from app.models.governance import AuditEvent
from app.security.principal import demo_persona, principal_for_role
from app.services.session import ensure_persona_user

TEST_ACTION: Final[str] = "test.audit_probe"
TEST_OBJECT_TYPE: Final[str] = "governance.test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose transaction is always rolled back.

    Nothing this module writes may commit: ``audit_events`` is append-only, so a committed
    test row could not be removed afterwards and would sit in the demo's audit timeline
    forever.
    """
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _write(session: Session, summary: str = "probe", **overrides: object) -> AuditEvent:
    """Write one probe event with sane defaults."""
    fields: dict[str, object] = {
        "actor": None,
        "action": TEST_ACTION,
        "object_type": TEST_OBJECT_TYPE,
        "policy_result": PolicyResult.ALLOW,
        "classification": Classification.MISSION_INTERNAL,
        "summary": summary,
    }
    fields.update(overrides)
    return write_audit_event(session, **fields)  # type: ignore[arg-type]


def _detached_row() -> AuditEvent:
    """An unsaved ``AuditEvent`` with every hashable field populated, for unit hashing."""
    return AuditEvent(
        id=uuid.UUID("018f0000-0000-7000-8000-000000000001"),
        occurred_at=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        actor_user_id=None,
        actor_role=RoleCode.DEPUTY,
        action=TEST_ACTION,
        object_type=TEST_OBJECT_TYPE,
        object_id=None,
        object_public_ref=None,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        request_id="req-1",
        trace_id=None,
        summary="probe",
        payload={"b": 2, "a": 1},
        ip_address=None,
        user_agent=None,
    )


# ---------------------------------------------------------------------------
# 1. Canonical serialisation (pure unit)
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_keys_and_strips_whitespace() -> None:
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonical_json_is_independent_of_insertion_order() -> None:
    """Two rows with the same content must hash identically however they were built."""
    assert canonical_json({"a": 1, "b": {"y": 2, "x": 3}}) == canonical_json(
        {"b": {"x": 3, "y": 2}, "a": 1}
    )


def test_canonical_json_normalises_the_types_a_row_actually_holds() -> None:
    encoded = canonical_json(
        {
            "zone": Classification.CONFIDENTIAL,
            "when": datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
            "who": uuid.UUID("00000000-0000-0000-0000-000000000001"),
            "amount": Decimal("0.10"),
        }
    ).decode()
    # Enum by value, never str(member) -- "Classification.CONFIDENTIAL" is not the wire value.
    assert '"zone":"CONFIDENTIAL"' in encoded
    assert '"when":"2026-09-07T12:00:00+00:00"' in encoded
    assert '"who":"00000000-0000-0000-0000-000000000001"' in encoded
    # Decimal as a string: a hash that depended on a binary rounding artefact would not
    # be reproducible.
    assert '"amount":"0.10"' in encoded


def test_canonical_json_is_timezone_stable() -> None:
    """The same instant read back in a different session time zone must hash the same."""
    from datetime import timedelta, timezone

    utc = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    lagos = utc.astimezone(timezone(timedelta(hours=1)))
    assert canonical_json({"t": utc}) == canonical_json({"t": lagos})


def test_canonical_json_keeps_non_ascii_intact() -> None:
    assert canonical_json({"name": "Adéyemi"}) == '{"name":"Adéyemi"}'.encode()


def test_canonical_json_refuses_an_unserialisable_payload() -> None:
    """Loud at the write, rather than a row whose hash cannot be recomputed."""
    with pytest.raises(TypeError, match="not JSON-serialisable"):
        canonical_json({"connection": object()})


# ---------------------------------------------------------------------------
# 2. The digest (pure unit)
# ---------------------------------------------------------------------------


def test_hash_is_deterministic() -> None:
    row = _detached_row()
    assert compute_event_hash(row, None) == compute_event_hash(row, None)
    assert len(compute_event_hash(row, None)) == 64


def test_hash_changes_when_any_hashed_field_changes() -> None:
    baseline = compute_event_hash(_detached_row(), None)
    for attribute, value in (
        ("action", "test.other"),
        ("summary", "tampered"),
        ("policy_result", PolicyResult.DENY),
        ("classification", Classification.CONFIDENTIAL),
        ("request_id", "req-2"),
        ("actor_role", RoleCode.ADMIN),
        ("occurred_at", datetime(2026, 9, 7, 12, 0, 1, tzinfo=UTC)),
        ("payload", {"a": 1, "b": 3}),
    ):
        row = _detached_row()
        setattr(row, attribute, value)
        assert compute_event_hash(row, None) != baseline, attribute


def test_hash_changes_when_the_predecessor_changes() -> None:
    """This is what makes it a chain rather than a per-row checksum."""
    row = _detached_row()
    assert compute_event_hash(row, "a" * 64) != compute_event_hash(row, "b" * 64)
    assert compute_event_hash(row, None) != compute_event_hash(row, "a" * 64)


def test_the_digest_is_domain_separated_and_versioned() -> None:
    """Rows written under v1 verify under v1; a v2 verifier reports a break, not a pass."""
    assert AUDIT_HASH_DOMAIN == "naddp.audit.v1"


# ---------------------------------------------------------------------------
# 3. Ambient context (pure unit)
# ---------------------------------------------------------------------------


def test_a_context_free_write_still_gets_a_correlation_id() -> None:
    """``request_id`` is NOT NULL: a non-HTTP actor mints its own rather than leaving it."""
    context = current_audit_context()
    assert context.request_id.startswith("system-")


def test_audit_context_binds_and_unbinds() -> None:
    trace = uuid.uuid4()
    with audit_context(request_id="req-abc", ip_address="10.0.0.1", trace_id=trace) as bound:
        assert bound.request_id == "req-abc"
        assert current_audit_context().ip_address == "10.0.0.1"
        assert current_audit_context().trace_id == trace
    assert current_audit_context().request_id != "req-abc"


def test_audit_context_unbinds_even_when_the_block_raises() -> None:
    with pytest.raises(RuntimeError), audit_context(request_id="req-boom"):
        raise RuntimeError("boom")
    assert current_audit_context().request_id != "req-boom"


# ---------------------------------------------------------------------------
# 4. The ORM immutability guard is armed (pure unit)
# ---------------------------------------------------------------------------


def test_the_guard_is_registered_on_the_session_class() -> None:
    """Registered on the class, so no code path can obtain an unguarded session."""
    assert event.contains(Session, "before_flush", _reject_audit_mutation)


def test_installing_the_guard_twice_is_a_no_op() -> None:
    install_audit_immutability_guard()
    install_audit_immutability_guard()
    assert event.contains(Session, "before_flush", _reject_audit_mutation)


# ---------------------------------------------------------------------------
# 5. Writing (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_a_write_populates_every_column_the_chain_needs(db: Session) -> None:
    with audit_context(request_id="req-write", ip_address="203.0.113.4", user_agent="pytest"):
        row = _write(db, "a system action")

    assert row.id is not None
    assert row.occurred_at.tzinfo is not None
    assert row.request_id == "req-write"
    assert row.ip_address == "203.0.113.4"
    assert row.user_agent == "pytest"
    assert len(row.event_hash) == 64
    assert row.event_hash == compute_event_hash(row, row.prev_event_hash)


@pytest.mark.integration
def test_occurred_at_comes_from_the_database_clock(db: Session) -> None:
    """A caller-supplied timestamp in an evidentiary table is a caller-controlled fact."""
    server_now = db.scalar(text("SELECT now()"))
    row = _write(db)
    assert isinstance(server_now, datetime)
    # func.now() is transaction_timestamp(), so both readings are the same instant.
    assert row.occurred_at == server_now


@pytest.mark.integration
def test_a_system_action_has_a_null_actor(db: Session) -> None:
    """'The system did it' is a genuinely different fact from a borrowed human actor."""
    row = _write(db, actor=None)
    assert row.actor_user_id is None
    assert row.actor_role is None


@pytest.mark.integration
def test_a_human_action_names_its_actor_and_the_role_held_at_the_time(db: Session) -> None:
    principal = principal_for_role(RoleCode.DEPUTY)
    ensure_persona_user(db, demo_persona(RoleCode.DEPUTY))
    row = _write(db, actor=principal)
    assert row.actor_user_id == principal.user_id
    assert row.actor_role is RoleCode.DEPUTY


@pytest.mark.integration
def test_a_denial_is_recorded_as_a_row(db: Session) -> None:
    """ADR-0003 rule 6: a posture that logs only successes cannot show that it works."""
    row = _write(db, policy_result=PolicyResult.DENY, summary="refused")
    assert row.policy_result is PolicyResult.DENY


@pytest.mark.integration
def test_a_rolled_back_transaction_leaves_no_audit_row(db: Session) -> None:
    """The same-transaction property, asserted from the failing direction."""
    row = _write(db, "will be rolled back")
    row_id = row.id
    db.rollback()
    assert db.scalar(select(AuditEvent).where(AuditEvent.id == row_id)) is None


@pytest.mark.integration
def test_an_unserialisable_payload_fails_the_write(db: Session) -> None:
    with pytest.raises(TypeError):
        _write(db, payload={"session": object()})


# ---------------------------------------------------------------------------
# 6. The chain (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_consecutive_writes_link_to_one_another(db: Session) -> None:
    first = _write(db, "one")
    second = _write(db, "two")
    third = _write(db, "three")

    assert second.prev_event_hash == first.event_hash
    assert third.prev_event_hash == second.event_hash
    assert len({first.event_hash, second.event_hash, third.event_hash}) == 3


@pytest.mark.integration
def test_verify_chain_reports_an_intact_chain(db: Session) -> None:
    for index in range(4):
        _write(db, f"row {index}")
    result = verify_chain(db, limit=4)
    assert result.is_intact
    assert result.checked == 4
    assert result.broken_at_id is None


@pytest.mark.integration
def test_verify_chain_detects_a_forged_link(db: Session) -> None:
    """A row inserted with a predecessor it does not actually follow."""
    _write(db, "genuine")
    forged = AuditEvent(
        action=TEST_ACTION,
        object_type=TEST_OBJECT_TYPE,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        request_id="req-forged",
        summary="inserted with a broken link",
        payload={},
        prev_event_hash="0" * 64,
        event_hash="1" * 64,
    )
    db.add(forged)
    db.flush()

    result = verify_chain(db, limit=2)
    assert not result.is_intact
    assert result.broken_at_id == forged.id
    assert result.reason is not None
    assert "prev_event_hash" in result.reason


@pytest.mark.integration
def test_verify_chain_detects_a_row_whose_contents_no_longer_match_its_hash(
    db: Session,
) -> None:
    """The case the chain exists for: a row altered by someone who bypassed the trigger.

    The ORM guard and the database trigger both refuse an UPDATE, so the tampering is
    simulated by inserting a row whose stored ``event_hash`` does not describe its own
    contents -- which is exactly the state a successful tamper would leave behind.
    """
    previous = _write(db, "genuine")
    tampered = AuditEvent(
        action=TEST_ACTION,
        object_type=TEST_OBJECT_TYPE,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        request_id="req-tampered",
        summary="contents do not match the digest",
        payload={},
        prev_event_hash=previous.event_hash,
        event_hash="f" * 64,
    )
    db.add(tampered)
    db.flush()

    result = verify_chain(db, limit=2)
    assert not result.is_intact
    assert result.broken_at_id == tampered.id
    assert result.reason is not None
    assert "event_hash" in result.reason


@pytest.mark.integration
def test_verify_chain_with_a_window_anchors_on_the_preceding_row(db: Session) -> None:
    """A bounded verification still checks the first row's link, rather than skipping it."""
    for index in range(5):
        _write(db, f"row {index}")
    assert verify_chain(db, limit=2).checked == 2
    assert verify_chain(db, limit=2).is_intact


@pytest.mark.integration
def test_verify_chain_with_a_non_positive_limit_checks_nothing(db: Session) -> None:
    _write(db, "row")
    result = verify_chain(db, limit=0)
    assert result.is_intact
    assert result.checked == 0


# ---------------------------------------------------------------------------
# 7. The ORM immutability guard, against a real session (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_the_guard_refuses_to_flush_a_modified_audit_row(db: Session) -> None:
    """ADR-0004 layer 3: a clear Python error at the point of the bug."""
    row = _write(db, "immutable")
    row.summary = "edited after the fact"
    with pytest.raises(AuditImmutabilityError, match="append-only"):
        db.flush()


@pytest.mark.integration
def test_the_guard_refuses_to_flush_a_deleted_audit_row(db: Session) -> None:
    row = _write(db, "immutable")
    db.delete(row)
    with pytest.raises(AuditImmutabilityError, match="append-only"):
        db.flush()


@pytest.mark.integration
def test_the_guard_names_the_row_and_points_at_the_correction_path(db: Session) -> None:
    row = _write(db, "immutable")
    row.action = "test.tampered"
    with pytest.raises(AuditImmutabilityError) as excinfo:
        db.flush()
    message = str(excinfo.value)
    assert str(row.id) in message
    assert "compensating event" in message


@pytest.mark.integration
def test_the_guard_does_not_interfere_with_ordinary_writes(db: Session) -> None:
    """A guard that produced false positives would be turned off, and then it is not a control."""
    persona = demo_persona(RoleCode.TRADE_OFFICER)
    user = ensure_persona_user(db, persona)
    user.title = "Acting Senior Trade Commissioner"
    db.flush()
    _write(db, "still writable")
    assert verify_chain(db, limit=1).is_intact
