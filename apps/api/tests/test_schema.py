"""Schema tests asserted against a **live** database, not against model metadata.

Why this file queries `information_schema` and `pg_catalog` rather than
`Base.metadata`: metadata is the thing under test. A model that was never
imported, a migration that was hand-edited after autogeneration, or a
constraint that exists in Python but was dropped from the revision would all
pass a metadata-only assertion and still ship a broken database. Everything
here therefore reads the catalog of the database the application will actually
connect to, and every "this is rejected" test issues the offending statement
and requires the *database* to refuse it.

The whole module is marked `integration` and skips -- never fails -- when no
database answers, so the unit suite stays DB-independent:

    uv run pytest -m "not integration"    # no database needed
    uv run pytest                          # runs these too, if a DB is up

Every test runs inside a transaction that is rolled back, so a run leaves no
rows behind. That matters more than usual here: `audit_events` is append-only
(ADR-0004), so a committed test row could not be deleted afterwards.

Covers:
  * all 28 tables exist (the 26 from PROMPT_W1 Phase 2, document_chunks from W2.1 and
    meeting_followups from W3.2);
  * the three embedding columns are `vector(1536)` with HNSW indexes;
  * `audit_events` and `case_events` reject UPDATE and DELETE (ADR-0004);
  * the BUILD_BIBLE section 6 non-autonomy CHECK constraints bite -- for meeting
    follow-ups, the whole W3.2 set: no dispatch without a named approval, a submission
    before any approval and times in order, no approval left on an unapproved row,
    separation of duties, discard reasons, recipient labels, one live follow-up per
    meeting, and the update guard (no delete, terminal rows immutable, provenance fixed,
    only the machine's status pairs, content changed only while DRAFTED, an approval never
    rewritten) -- each refusal paired with the legitimate statement it must still allow;
  * `cases.public_ref` is UNIQUE, NOT NULL, not the primary key, and shares no
    entropy with `id` (ADR-0007).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import (
    CaseStatus,
    Classification,
    FollowupStatus,
    KnowledgeStatus,
    MeetingType,
    PolicyResult,
    Priority,
)
from app.models.consular import Case
from app.models.governance import AuditEvent, User
from app.models.knowledge import KnowledgeArticle
from app.models.meetings import Meeting, MeetingFollowup

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Expectations
# ---------------------------------------------------------------------------
#
# Deliberately hard-coded rather than derived from ``Base.metadata``. If a model
# module stopped being imported, metadata would shrink and a derived list would
# shrink with it -- the test would pass while the table vanished. A literal list
# is the only version of this assertion that can fail for the right reason.

EXPECTED_TABLES: Final[frozenset[str]] = frozenset(
    {
        # governance
        "users",
        "roles",
        "permissions",
        "role_permissions",
        "user_roles",
        "audit_events",
        # intelligence
        "sources",
        "documents",
        "signals",
        "briefs",
        "brief_items",
        # stakeholders
        "organisations",
        "stakeholders",
        "interactions",
        # opportunities
        "opportunities",
        # meetings
        "meetings",
        "meeting_followups",
        "meeting_attendees",
        "actions",
        # consular
        "cases",
        "case_events",
        "case_evidence",
        # knowledge
        "knowledge_articles",
        # diaspora
        "diaspora_profiles",
        "expertise_tags",
        "diaspora_expertise",
        "document_chunks",
        # ai
        "ai_traces",
    }
)

#: Alembic's bookkeeping table is real but is not one of the 26.
NON_DOMAIN_TABLES: Final[frozenset[str]] = frozenset({"alembic_version"})

EMBEDDING_DIMENSIONS: Final[int] = 1536

#: Week 1 seeds no vectors; Week 2 populates them. The columns and their HNSW
#: indexes must nevertheless exist now, because adding an index to a populated
#: table later is the expensive version of this decision.
EMBEDDING_TABLES: Final[tuple[str, ...]] = (
    "documents",
    "knowledge_articles",
    "diaspora_profiles",
)

#: Tables ADR-0004 makes append-only at the database level.
APPEND_ONLY_TABLES: Final[tuple[str, ...]] = ("audit_events", "case_events")

#: The two CHECKs that refuse a follow-up recorded as dispatched without a named approval.
#: Postgres tests CHECK constraints in alphabetical order by name and reports the first
#: that fails, so a SENT row with no approver at all is refused by
#: ``approved_states_name_approver`` before ``sent_requires_approval`` is reached. Either
#: refusing it is the guarantee; which one speaks is an ordering detail.
SEND_WITHOUT_APPROVAL_CONSTRAINTS: Final[tuple[str, ...]] = (
    "ck_meeting_followups_approved_states_name_approver",
    "ck_meeting_followups_sent_requires_approval",
)

#: What ``naddp_guard_followup_update`` says, by rule, so each refusal is pinned to the rule
#: that made it rather than to "some error happened".
NOT_A_TRANSITION: Final[str] = "is not a transition of the follow-up machine"
CONTENT_LOCKED: Final[str] = "locked outside DRAFTED"
APPROVAL_FROZEN: Final[str] = "the approval is frozen once given"

#: The W3.2 follow-up triggers, by name.
FOLLOWUP_TRIGGERS: Final[frozenset[str]] = frozenset(
    {
        "trg_meeting_followups_no_delete",
        "trg_meeting_followups_no_truncate",
        "trg_meeting_followups_guard_update",
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose transaction is always rolled back.

    Skips rather than fails when no database is reachable: unit CI runs with no
    Postgres and must stay green.
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


def _unique_suffix() -> str:
    """A short random suffix, so repeated runs cannot collide on unique columns."""
    return uuid.uuid4().hex[:12]


def _make_user(db: Session) -> User:
    """Insert and flush a user, for the FK columns the non-autonomy checks require."""
    user = User(
        email=f"schema-test-{_unique_suffix()}@naddp.test",
        full_name="Schema Test Officer",
        mission="Canberra",
        is_demo_persona=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _make_case(**overrides: object) -> Case:
    """A minimally valid consular case. ``public_ref`` comes from the column default."""
    fields: dict[str, object] = {
        "case_type_code": "PASSPORT_RENEWAL",
        "status": CaseStatus.NEW,
        "priority": Priority.NORMAL,
        "subject_name": "Schema Test Subject",
        "country": "AU",
        "channel": "WEB",
        "summary": "synthetic row created by tests/test_schema.py",
        "requires_human_determination": True,
        "classification": Classification.CONSULAR_SENSITIVE,
    }
    fields.update(overrides)
    return Case(**fields)


# ---------------------------------------------------------------------------
# 1. All 28 tables exist
# ---------------------------------------------------------------------------


def _live_tables(db: Session) -> frozenset[str]:
    rows = db.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    ).scalars()
    return frozenset(rows)


def test_expected_table_count_is_twenty_eight() -> None:
    """Guards the expectation itself: a typo that drops a name must not pass silently."""
    assert len(EXPECTED_TABLES) == 28


def test_all_expected_tables_exist(db: Session) -> None:
    missing = EXPECTED_TABLES - _live_tables(db)
    assert not missing, f"tables declared by PROMPT_W1 Phase 2 but absent: {sorted(missing)}"


def test_no_unexpected_tables_exist(db: Session) -> None:
    """A table nobody declared is either a leftover or a silent schema change."""
    unexpected = _live_tables(db) - EXPECTED_TABLES - NON_DOMAIN_TABLES
    assert not unexpected, f"undeclared tables present: {sorted(unexpected)}"


def test_every_domain_table_has_a_uuid_primary_key(db: Session) -> None:
    """ADR-0007: every primary key column is `uuid`, including composite join keys."""
    rows = db.execute(
        text(
            """
            SELECT c.relname AS tbl, a.attname AS col, format_type(a.atttypid, a.atttypmod) AS typ
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN LATERAL unnest(con.conkey) AS k(attnum) ON TRUE
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
            WHERE con.contype = 'p' AND con.connamespace = 'public'::regnamespace
            """
        )
    ).all()

    non_uuid = [
        (tbl, col, typ) for tbl, col, typ in rows if tbl in EXPECTED_TABLES and typ != "uuid"
    ]
    assert not non_uuid, f"non-uuid primary key columns: {non_uuid}"


# ---------------------------------------------------------------------------
# 2. pgvector columns and indexes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_embedding_column_is_vector_1536(db: Session, table: str) -> None:
    """`atttypmod` carries the dimension; a bare `vector` would silently accept any width."""
    declared = db.execute(
        text(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND c.relname = :table
              AND a.attname = 'embedding'
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        ),
        {"table": table},
    ).scalar_one_or_none()

    assert declared == f"vector({EMBEDDING_DIMENSIONS})", (
        f"{table}.embedding is {declared!r}, expected vector({EMBEDDING_DIMENSIONS})"
    )


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_embedding_column_is_nullable(db: Session, table: str) -> None:
    """Week 1 seeds no vectors, so a NOT NULL embedding would make the seed impossible."""
    is_nullable = db.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = 'embedding'"
        ),
        {"table": table},
    ).scalar_one()
    assert is_nullable == "YES"


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_embedding_has_an_hnsw_index(db: Session, table: str) -> None:
    definitions = db.execute(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = :table"),
        {"table": table},
    ).scalars()
    hnsw = [d for d in definitions if "USING hnsw" in d and "embedding" in d]
    assert hnsw, f"{table}.embedding has no HNSW index"


# ---------------------------------------------------------------------------
# 3. Append-only enforcement (ADR-0004)
# ---------------------------------------------------------------------------


def _make_audit_event() -> AuditEvent:
    return AuditEvent(
        action="schema_test.probe",
        object_type="case",
        policy_result=PolicyResult.ALLOW,
        request_id=f"req-{_unique_suffix()}",
        summary="synthetic row created by tests/test_schema.py",
        payload={},
        event_hash=uuid.uuid4().hex,
        # An unclaimed link. The chain refuses a second genesis row (a NULL link) and a second
        # claim on any predecessor, so a probe row must name a predecessor nobody holds.
        prev_event_hash=uuid.uuid4().hex,
        classification=Classification.MISSION_INTERNAL,
    )


# These two issue raw SQL rather than mutating the mapped object, and that is the point.
# ADR-0004 has three enforcement layers, and this file tests the one that actually holds:
# the database trigger, which fires for every statement from every client. Going through
# the ORM would instead trip the application-level `before_flush` guard in
# `app/audit/writer.py` -- a useful layer, tested in `tests/test_audit_writer.py` -- and
# this file would then never reach the database at all, silently losing the coverage it
# exists for.


def test_audit_events_rejects_update(db: Session) -> None:
    """ADR-0004: an audit log that can be edited is not evidence."""
    event = _make_audit_event()
    db.add(event)
    db.flush()

    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.execute(
            text("UPDATE audit_events SET action = 'schema_test.tampered' WHERE id = :id"),
            {"id": event.id},
        )

    assert "append-only" in str(excinfo.value)
    assert "UPDATE" in str(excinfo.value)


def test_audit_events_rejects_delete(db: Session) -> None:
    event = _make_audit_event()
    db.add(event)
    db.flush()

    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": event.id})

    assert "append-only" in str(excinfo.value)
    assert "DELETE" in str(excinfo.value)


def test_audit_events_row_survives_a_rejected_mutation(db: Session) -> None:
    """The guard must abort the statement, not silently swallow it."""
    event = _make_audit_event()
    db.add(event)
    db.flush()
    event_id = event.id

    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"),
            {"id": event_id},
        )

    surviving = db.execute(
        text("SELECT action FROM audit_events WHERE id = :id"), {"id": event_id}
    ).scalar_one()
    assert surviving == "schema_test.probe"


def test_case_events_rejects_update_and_delete(db: Session) -> None:
    """The consular timeline carries the same promise as the audit log."""
    case = _make_case()
    db.add(case)
    db.flush()

    db.execute(
        text(
            "INSERT INTO case_events (id, case_id, event_type, is_system, note, classification) "
            "VALUES (:id, :case_id, 'CREATED', TRUE, 'probe', 'CONSULAR_SENSITIVE')"
        ),
        {"id": uuid.uuid4(), "case_id": case.id},
    )

    with pytest.raises(IntegrityError) as update_error, db.begin_nested():
        db.execute(
            text("UPDATE case_events SET note = 'tampered' WHERE case_id = :c"), {"c": case.id}
        )
    assert "append-only" in str(update_error.value)

    with pytest.raises(IntegrityError) as delete_error, db.begin_nested():
        db.execute(text("DELETE FROM case_events WHERE case_id = :c"), {"c": case.id})
    assert "append-only" in str(delete_error.value)


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_append_only_triggers_are_installed(db: Session, table: str) -> None:
    """Behaviour is proven above; this pins the mechanism so it cannot quietly move."""
    operations = db.execute(
        text(
            "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t "
            "WHERE t.tgrelid = CAST(:table AS regclass) AND NOT t.tgisinternal"
        ),
        {"table": table},
    ).scalars()
    combined = " ".join(operations)
    assert "UPDATE" in combined
    assert "DELETE" in combined
    assert "TRUNCATE" in combined


def test_append_only_guard_is_scoped_not_global(db: Session) -> None:
    """A guard that blocked every UPDATE would pass the tests above and break the app."""
    case = _make_case()
    db.add(case)
    db.flush()

    case.summary = "ordinary mutable row, updated normally"
    db.flush()

    assert db.get(Case, case.id) is not None


# ---------------------------------------------------------------------------
# 4. Non-autonomy CHECK constraints (BUILD_BIBLE section 6)
# ---------------------------------------------------------------------------


def _make_meeting(db: Session) -> Meeting:
    """Insert and flush a meeting for a follow-up to hang off."""
    meeting = Meeting(
        title="Follow-up constraint probe",
        meeting_type=MeetingType.BILATERAL,
        scheduled_start=datetime.now(UTC),
        scheduled_end=datetime.now(UTC),
        agenda="probe",
        classification=Classification.MISSION_INTERNAL,
    )
    db.add(meeting)
    db.flush()
    return meeting


def _followup(meeting: Meeting, drafter: User, **overrides: object) -> MeetingFollowup:
    """A minimally valid DRAFTED follow-up, with ``overrides`` applied."""
    fields: dict[str, object] = {
        "meeting_id": meeting.id,
        "status": FollowupStatus.DRAFTED,
        "subject": "Follow-up constraint probe",
        "recipients": ["Probe Organisation -- External Affairs"],
        "body": "synthetic row created by tests/test_schema.py",
        "drafted_by_user_id": drafter.id,
        "drafted_at": datetime.now(UTC),
        "classification": Classification.MISSION_INTERNAL,
    }
    fields.update(overrides)
    return MeetingFollowup(**fields)


#: A fixed, ordered timeline for the moments the ordering CHECKs compare: submitted, then
#: approved, then sent. Fixed and in the past rather than ``datetime.now()``: two
#: ``datetime.now()`` calls in one expression can land out of order, and a later statement
#: stamping the database's ``now()`` must come after every moment inserted here.
_TIMELINE_START: Final[datetime] = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return _TIMELINE_START + timedelta(minutes=minutes)


def _submitted(submitter: User) -> dict[str, object]:
    """The fields a submitted follow-up carries."""
    return {"submitted_by_user_id": submitter.id, "submitted_at": _at(10)}


def _approved(approver: User) -> dict[str, object]:
    """The fields a properly approved follow-up carries (after :func:`_submitted`)."""
    return {"approved_by_user_id": approver.id, "approved_at": _at(20)}


def _sent(sender: User) -> dict[str, object]:
    """The fields a dispatched follow-up carries (after :func:`_approved`)."""
    return {"sent_by_user_id": sender.id, "sent_at": _at(30)}


def _insert(db: Session, followup: MeetingFollowup) -> MeetingFollowup:
    db.add(followup)
    db.flush()
    return followup


def _refused_insert(db: Session, followup: MeetingFollowup) -> str:
    """Insert ``followup``, require the database to refuse it, and return its message."""
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.add(followup)
        db.flush()
    return str(excinfo.value)


def _refused_statement(
    db: Session, statement: str, followup_id: uuid.UUID, **params: object
) -> str:
    """Execute raw SQL against one follow-up, require a refusal, and return its message.

    Raw SQL on purpose: these are the writes that bypass every service, which is exactly
    what the constraints and triggers exist to stop. ``:id`` is bound to ``followup_id``;
    ``params`` binds the rest.
    """
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.execute(text(statement), {"id": followup_id, **params})
    return str(excinfo.value)


def _accepted_statement(
    db: Session, statement: str, followup_id: uuid.UUID, **params: object
) -> None:
    """Execute raw SQL against one follow-up and require the database to accept it."""
    with db.begin_nested():
        db.execute(text(statement), {"id": followup_id, **params})


def _stored(db: Session, followup_id: uuid.UUID) -> tuple[str, str, uuid.UUID | None, bool]:
    """``(status, body, approved_by_user_id, approved_at IS NOT NULL)`` as the database holds it."""
    row = db.execute(
        text(
            "SELECT CAST(status AS text), body, approved_by_user_id, approved_at IS NOT NULL "
            "FROM meeting_followups WHERE id = :id"
        ),
        {"id": followup_id},
    ).one()
    return str(row[0]), str(row[1]), row[2], bool(row[3])


def _status_of(db: Session, followup_id: uuid.UUID) -> str:
    status = db.execute(
        text("SELECT CAST(status AS text) FROM meeting_followups WHERE id = :id"),
        {"id": followup_id},
    ).scalar_one()
    return str(status)


def test_a_sent_followup_with_no_approver_is_refused(db: Session) -> None:
    """Winning moment 2: an outbound communication cannot be recorded as sent unapproved."""
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    message = _refused_insert(
        db, _followup(meeting, drafter, status=FollowupStatus.SENT, sent_at=datetime.now(UTC))
    )
    assert any(name in message for name in SEND_WITHOUT_APPROVAL_CONSTRAINTS), message


@pytest.mark.parametrize(
    "shape",
    ["dispatch_time_with_no_approver", "approver_named_but_never_approved"],
)
def test_sent_requires_approval_is_the_constraint_that_refuses_it(db: Session, shape: str) -> None:
    """``ck_meeting_followups_sent_requires_approval`` by name: the winning-moment constraint.

    Two shapes in which it is the first CHECK to fail, so the name is asserted exactly: a
    dispatch time with no approver on a row not (yet) claiming ``SENT``, and a ``SENT`` row
    naming an approver who never recorded an approval time.
    """
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    if shape == "dispatch_time_with_no_approver":
        followup = _followup(
            meeting, drafter, status=FollowupStatus.OFFICER_REVIEW, sent_at=datetime.now(UTC)
        )
    else:
        approver = _make_user(db)
        followup = _followup(
            meeting,
            drafter,
            status=FollowupStatus.SENT,
            **_submitted(drafter),
            **_sent(drafter),
            approved_by_user_id=approver.id,
            approved_at=None,
        )
    message = _refused_insert(db, followup)
    assert "ck_meeting_followups_sent_requires_approval" in message, message


def test_a_status_only_update_from_review_to_sent_is_refused(db: Session) -> None:
    """Writing ``SENT`` and a dispatch time, and nothing else, onto a row under review.

    What this proves is narrow: that shape is refused, and the row is still waiting for its
    human afterwards. It names no approver, so the CHECKs would refuse it as well; the
    guard's transition rule speaks first because a BEFORE trigger runs before constraints.
    It is not, on its own, proof that the path is enforced -- a statement that also names
    an approver is, and the tests below issue exactly that.
    """
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    followup = _insert(
        db, _followup(meeting, drafter, status=FollowupStatus.OFFICER_REVIEW, **_submitted(drafter))
    )
    message = _refused_statement(
        db,
        "UPDATE meeting_followups SET status = 'SENT', sent_at = now() WHERE id = :id",
        followup.id,
    )
    assert NOT_A_TRANSITION in message, message
    assert _status_of(db, followup.id) == FollowupStatus.OFFICER_REVIEW.value


#: The forged-approval statement, and the same statement rewriting the body on the way.
_FORGED_SEND: Final[str] = (
    "UPDATE meeting_followups SET status = 'SENT', submitted_by_user_id = :drafter, "
    "submitted_at = now(), approved_by_user_id = :approver, approved_at = now(), "
    "sent_by_user_id = :drafter, sent_at = now() WHERE id = :id"
)
_FORGED_SEND_WITH_NEW_BODY: Final[str] = (
    "UPDATE meeting_followups SET status = 'SENT', submitted_by_user_id = :drafter, "
    "submitted_at = now(), approved_by_user_id = :approver, approved_at = now(), "
    "sent_by_user_id = :drafter, sent_at = now(), body = 'ALTERED AFTER NOBODY APPROVED' "
    "WHERE id = :id"
)


@pytest.mark.parametrize(
    "statement",
    [_FORGED_SEND, _FORGED_SEND_WITH_NEW_BODY],
    ids=["as_drafted", "body_rewritten"],
)
def test_one_update_cannot_take_a_draft_straight_to_sent(db: Session, statement: str) -> None:
    """The forged approval: every column a sent row needs, in one statement, from DRAFTED.

    The statement names a real approver who is not the drafter, a submission, an approval
    time and a dispatch time -- all in order -- so it satisfies every CHECK. Nothing about
    it passed through review or approval, and in the second shape it rewrites the body on
    the way. The guard refuses both because DRAFTED to SENT is not a pair of the machine.
    """
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    followup = _insert(db, _followup(meeting, drafter))
    message = _refused_statement(
        db, statement, followup.id, drafter=drafter.id, approver=approver.id
    )
    assert NOT_A_TRANSITION in message, message
    assert _stored(db, followup.id) == (
        FollowupStatus.DRAFTED.value,
        "synthetic row created by tests/test_schema.py",
        None,
        False,
    )


def test_one_update_cannot_take_a_followup_under_review_straight_to_sent(db: Session) -> None:
    """Skipping APPROVED with the approver columns filled in is refused, not just the bare shape."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    followup = _insert(
        db, _followup(meeting, drafter, status=FollowupStatus.OFFICER_REVIEW, **_submitted(drafter))
    )
    message = _refused_statement(
        db,
        "UPDATE meeting_followups SET status = 'SENT', approved_by_user_id = :approver, "
        "approved_at = now(), sent_by_user_id = :drafter, sent_at = now() WHERE id = :id",
        followup.id,
        drafter=drafter.id,
        approver=approver.id,
    )
    assert NOT_A_TRANSITION in message, message
    status, _, approved_by, approved = _stored(db, followup.id)
    assert (status, approved_by, approved) == (FollowupStatus.OFFICER_REVIEW.value, None, False)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE meeting_followups SET approved_by_user_id = :other WHERE id = :id",
        "UPDATE meeting_followups SET status = 'SENT', approved_by_user_id = :other, "
        "sent_by_user_id = :other, sent_at = now() WHERE id = :id",
        "UPDATE meeting_followups SET status = 'SENT', approved_at = now(), "
        "sent_by_user_id = :other, sent_at = now() WHERE id = :id",
    ],
    ids=[
        "approver_swapped_on_approved_row",
        "approver_swapped_on_send",
        "approval_restamped_on_send",
    ],
)
def test_an_approval_once_given_is_never_rewritten(db: Session, statement: str) -> None:
    """Who approved it, and when, is history: not reassignable, not even by the send itself."""
    meeting = _make_meeting(db)
    drafter, approver, other = _make_user(db), _make_user(db), _make_user(db)
    followup = _insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.APPROVED,
            **_submitted(drafter),
            **_approved(approver),
        ),
    )
    message = _refused_statement(db, statement, followup.id, other=other.id)
    assert APPROVAL_FROZEN in message, message
    status, _, approved_by, approved = _stored(db, followup.id)
    assert (status, approved_by, approved) == (FollowupStatus.APPROVED.value, approver.id, True)


def test_a_return_to_draft_cannot_keep_its_approval(db: Session) -> None:
    """A status-only APPROVED to DRAFTED would leave an approval of content about to unlock.

    Refused by CHECK: the transition is legal, the approval columns are unchanged, and a
    DRAFTED row may not carry them. Without this, the content could be rewritten under
    DRAFTED and then sent under the old approval.
    """
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    followup = _insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.APPROVED,
            **_submitted(drafter),
            **_approved(approver),
        ),
    )
    message = _refused_statement(
        db, "UPDATE meeting_followups SET status = 'DRAFTED' WHERE id = :id", followup.id
    )
    assert "ck_meeting_followups_unapproved_states_carry_no_approval" in message, message
    assert _status_of(db, followup.id) == FollowupStatus.APPROVED.value


@pytest.mark.parametrize(
    ("from_status", "statement"),
    [
        (
            FollowupStatus.DRAFTED,
            "UPDATE meeting_followups SET status = 'OFFICER_REVIEW', "
            "submitted_by_user_id = :drafter, submitted_at = now(), "
            "body = 'rewritten on the way into review' WHERE id = :id",
        ),
        (
            FollowupStatus.OFFICER_REVIEW,
            "UPDATE meeting_followups SET status = 'APPROVED', approved_by_user_id = :approver, "
            "approved_at = now(), body = 'rewritten while approving' WHERE id = :id",
        ),
        (
            FollowupStatus.APPROVED,
            "UPDATE meeting_followups SET body = 'rewritten after approval' WHERE id = :id",
        ),
        (
            FollowupStatus.APPROVED,
            "UPDATE meeting_followups SET status = 'SENT', sent_by_user_id = :drafter, "
            "sent_at = now(), body = 'rewritten while sending' WHERE id = :id",
        ),
    ],
    ids=["leaving_drafted", "in_the_approving_update", "after_approval", "in_the_send"],
)
def test_content_never_changes_in_or_after_the_update_that_leaves_drafted(
    db: Session, from_status: FollowupStatus, statement: str
) -> None:
    """The artefact an approver sees is the artefact that gets sent -- from submission on."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    fields: dict[str, object] = {}
    if from_status is not FollowupStatus.DRAFTED:
        fields.update(_submitted(drafter))
    if from_status is FollowupStatus.APPROVED:
        fields.update(_approved(approver))
    followup = _insert(db, _followup(meeting, drafter, status=from_status, **fields))
    message = _refused_statement(
        db, statement, followup.id, drafter=drafter.id, approver=approver.id
    )
    assert CONTENT_LOCKED in message, message
    status, body, _, _ = _stored(db, followup.id)
    assert (status, body) == (from_status.value, "synthetic row created by tests/test_schema.py")


def test_the_service_shaped_revoke_is_accepted(db: Session) -> None:
    """The control for the freeze: returning to DRAFTED and clearing the approval is allowed.

    The shape ``revoke_approval`` flushes (``_write_status`` clears submission and approval
    together), followed by the edit it exists to permit.
    """
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    followup = _insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.APPROVED,
            **_submitted(drafter),
            **_approved(approver),
        ),
    )
    _accepted_statement(
        db,
        "UPDATE meeting_followups SET status = 'DRAFTED', submitted_by_user_id = NULL, "
        "submitted_at = NULL, approved_by_user_id = NULL, approved_at = NULL WHERE id = :id",
        followup.id,
    )
    _accepted_statement(
        db,
        "UPDATE meeting_followups SET body = 'edited after revocation' WHERE id = :id",
        followup.id,
    )
    assert _stored(db, followup.id) == (
        FollowupStatus.DRAFTED.value,
        "edited after revocation",
        None,
        False,
    )


def test_a_real_submit_approve_send_sequence_is_accepted(db: Session) -> None:
    """The control for the whole guard: the path the service walks, one flush per event.

    Each UPDATE carries what the executor writes for that event -- the status and its moment
    from ``_write_status``, the actor from the rule's effect -- through the ORM, as the
    service does. A guard that refused this would have broken the feature to block the bypass.
    """
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    followup = _insert(db, _followup(meeting, drafter))

    followup.status = FollowupStatus.OFFICER_REVIEW
    followup.submitted_at = _at(10)
    followup.submitted_by_user_id = drafter.id
    db.flush()

    followup.status = FollowupStatus.APPROVED
    followup.approved_at = _at(20)
    followup.approved_by_user_id = approver.id
    db.flush()

    followup.status = FollowupStatus.SENT
    followup.sent_at = _at(30)
    followup.sent_by_user_id = drafter.id
    db.flush()

    assert _stored(db, followup.id) == (
        FollowupStatus.SENT.value,
        "synthetic row created by tests/test_schema.py",
        approver.id,
        True,
    )


def test_an_approved_followup_must_have_been_submitted(db: Session) -> None:
    """An approval of something nobody submitted for review is not an approval."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    message = _refused_insert(
        db, _followup(meeting, drafter, status=FollowupStatus.APPROVED, **_approved(approver))
    )
    assert "ck_meeting_followups_approved_states_were_submitted" in message, message


@pytest.mark.parametrize("status", [FollowupStatus.DRAFTED, FollowupStatus.OFFICER_REVIEW])
def test_an_unapproved_followup_carries_no_approval(db: Session, status: FollowupStatus) -> None:
    """An approval cannot be parked on a row that is not approved, waiting to be sent under."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    message = _refused_insert(
        db,
        _followup(meeting, drafter, status=status, **_submitted(drafter), **_approved(approver)),
    )
    assert "ck_meeting_followups_unapproved_states_carry_no_approval" in message, message


@pytest.mark.parametrize(
    ("minutes", "constraint"),
    [
        ((20, 10, 30), "ck_meeting_followups_approval_follows_submission"),
        ((10, 30, 20), "ck_meeting_followups_dispatch_follows_approval"),
    ],
    ids=["approved_before_submitted", "sent_before_approved"],
)
def test_submission_approval_and_dispatch_are_in_order(
    db: Session, minutes: tuple[int, int, int], constraint: str
) -> None:
    """``submitted_at <= approved_at <= sent_at``: nothing is approved before it was submitted."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    submitted, approved, sent = minutes
    message = _refused_insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.SENT,
            submitted_by_user_id=drafter.id,
            submitted_at=_at(submitted),
            approved_by_user_id=approver.id,
            approved_at=_at(approved),
            sent_by_user_id=drafter.id,
            sent_at=_at(sent),
        ),
    )
    assert constraint in message, message


def test_a_send_stamped_before_its_approval_is_refused(db: Session) -> None:
    """The ordering CHECK binds the legal APPROVED to SENT update too, not only inserts."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    followup = _insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.APPROVED,
            **_submitted(drafter),
            **_approved(approver),
        ),
    )
    message = _refused_statement(
        db,
        "UPDATE meeting_followups SET status = 'SENT', sent_by_user_id = :drafter, "
        "sent_at = :sent_at WHERE id = :id",
        followup.id,
        drafter=drafter.id,
        sent_at=_at(15),
    )
    assert "ck_meeting_followups_dispatch_follows_approval" in message, message
    assert _status_of(db, followup.id) == FollowupStatus.APPROVED.value


def test_a_sent_status_without_a_dispatch_time_is_refused(db: Session) -> None:
    """The status alone cannot claim SENT: that is what makes the approval check airtight."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    message = _refused_insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.SENT,
            **_submitted(drafter),
            **_approved(approver),
        ),
    )
    assert "ck_meeting_followups_sent_status_iff_timestamp" in message, message


def test_the_drafter_cannot_be_the_approver(db: Session) -> None:
    """Separation of duties is structural now that the drafter is on the row."""
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    message = _refused_insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.APPROVED,
            **_submitted(drafter),
            **_approved(drafter),
        ),
    )
    assert "ck_meeting_followups_approver_is_not_drafter" in message, message


@pytest.mark.parametrize(
    "reason",
    [None, "   ", "\n\t", "\t\r\n"],
    ids=["none", "spaces", "newline_tab", "tab_return_newline"],
)
def test_a_discard_requires_a_reason(db: Session, reason: str | None) -> None:
    """Discarding a drafted communication is a consequential act; it says why (Q-05).

    Whitespace of any kind is not a reason. One-argument ``btrim`` strips only spaces, so
    the CHECK asks for a non-whitespace character (``~ '[^[:space:]]'``) instead.
    """
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    message = _refused_insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.DISCARDED,
            discarded_by_user_id=drafter.id,
            discarded_at=datetime.now(UTC),
            discard_reason=reason,
        ),
    )
    assert "ck_meeting_followups_discarded_requires_reason" in message, message


def test_discard_fields_exist_only_on_a_discarded_row(db: Session) -> None:
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    message = _refused_insert(db, _followup(meeting, drafter, discard_reason="not discarded"))
    assert "ck_meeting_followups_discard_fields_only_when_discarded" in message, message


@pytest.mark.parametrize(
    "recipients",
    [
        ["counterpart@example.org"],
        ["Covalent Lithium -- External Affairs", "someone@example.org"],
        [],
        [f"Recipient label {index}" for index in range(9)],
        {"to": "Covalent Lithium"},
    ],
    ids=["address", "address_among_labels", "empty", "nine_labels", "not_an_array"],
)
def test_recipients_are_one_to_eight_labels_never_addresses(
    db: Session, recipients: object
) -> None:
    """A drafted email with a real address in it is one careless click from a real email."""
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    message = _refused_insert(db, _followup(meeting, drafter, recipients=recipients))
    assert "ck_meeting_followups_recipients_are_labels" in message, message


def test_a_meeting_holds_at_most_one_live_followup(db: Session) -> None:
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    _insert(db, _followup(meeting, drafter))
    message = _refused_insert(
        db,
        _followup(meeting, drafter, status=FollowupStatus.OFFICER_REVIEW, **_submitted(drafter)),
    )
    assert "uq_meeting_followups_one_live_per_meeting" in message, message


def test_a_new_draft_is_accepted_beside_terminal_followups(db: Session) -> None:
    """The control for the index: sent and discarded follow-ups do not block a re-draft."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    discarded = _insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.DISCARDED,
            discarded_by_user_id=drafter.id,
            discarded_at=datetime.now(UTC),
            discard_reason="Superseded by a written note.",
        ),
    )
    _insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.SENT,
            **_submitted(drafter),
            **_approved(approver),
            **_sent(drafter),
        ),
    )
    redraft = _insert(db, _followup(meeting, drafter, supersedes_followup_id=discarded.id))
    assert redraft.id is not None


def test_a_properly_approved_sent_followup_is_accepted(db: Session) -> None:
    """The positive control: the constraints block the unapproved case, not the feature."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    followup = _insert(
        db,
        _followup(
            meeting,
            drafter,
            status=FollowupStatus.SENT,
            **_submitted(drafter),
            **_approved(approver),
            **_sent(drafter),
        ),
    )
    assert _status_of(db, followup.id) == FollowupStatus.SENT.value


def test_a_followup_cannot_be_deleted(db: Session) -> None:
    """Q-05: never delete a drafted diplomatic communication. Discard it, with a reason."""
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    followup = _insert(db, _followup(meeting, drafter))
    message = _refused_statement(db, "DELETE FROM meeting_followups WHERE id = :id", followup.id)
    assert "never deleted" in message, message
    assert _status_of(db, followup.id) == FollowupStatus.DRAFTED.value


def test_a_meeting_with_a_followup_cannot_be_deleted(db: Session) -> None:
    """``ON DELETE RESTRICT``: deleting the meeting must not take its follow-up with it."""
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    followup = _insert(db, _followup(meeting, drafter))
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.execute(text("DELETE FROM meetings WHERE id = :id"), {"id": meeting.id})
    assert "fk_meeting_followups_meeting_id_meetings" in str(excinfo.value)
    assert _status_of(db, followup.id) == FollowupStatus.DRAFTED.value


def test_content_is_locked_outside_drafted(db: Session) -> None:
    """The artefact an approver sees is the artefact that gets sent (workflows section 2)."""
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    followup = _insert(
        db, _followup(meeting, drafter, status=FollowupStatus.OFFICER_REVIEW, **_submitted(drafter))
    )
    message = _refused_statement(
        db, "UPDATE meeting_followups SET body = 'changed under review' WHERE id = :id", followup.id
    )
    assert CONTENT_LOCKED in message, message


def test_content_is_editable_while_drafted(db: Session) -> None:
    """The control for the lock: a guard that froze every edit would break drafting."""
    meeting = _make_meeting(db)
    drafter = _make_user(db)
    followup = _insert(db, _followup(meeting, drafter))
    db.execute(
        text("UPDATE meeting_followups SET body = 'edited while drafted' WHERE id = :id"),
        {"id": followup.id},
    )
    body = db.execute(
        text("SELECT body FROM meeting_followups WHERE id = :id"), {"id": followup.id}
    ).scalar_one()
    assert body == "edited while drafted"


@pytest.mark.parametrize("terminal", [FollowupStatus.SENT, FollowupStatus.DISCARDED])
def test_a_terminal_followup_is_immutable(db: Session, terminal: FollowupStatus) -> None:
    """Nothing about a sent or discarded follow-up changes -- not even its timestamps."""
    meeting = _make_meeting(db)
    drafter, approver = _make_user(db), _make_user(db)
    terminal_fields: dict[str, object] = (
        {**_submitted(drafter), **_approved(approver), **_sent(drafter)}
        if terminal is FollowupStatus.SENT
        else {
            "discarded_by_user_id": drafter.id,
            "discarded_at": datetime.now(UTC),
            "discard_reason": "Superseded by a written note.",
        }
    )
    followup = _insert(db, _followup(meeting, drafter, status=terminal, **terminal_fields))
    message = _refused_statement(
        db, "UPDATE meeting_followups SET updated_at = now() WHERE id = :id", followup.id
    )
    assert "terminal" in message, message


def test_provenance_never_changes(db: Session) -> None:
    """Who drafted it, when, for which meeting and from which trace is history, not state."""
    meeting = _make_meeting(db)
    drafter, somebody_else = _make_user(db), _make_user(db)
    followup = _insert(db, _followup(meeting, drafter))
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.execute(
            text("UPDATE meeting_followups SET drafted_by_user_id = :other WHERE id = :id"),
            {"other": somebody_else.id, "id": followup.id},
        )
    assert "provenance" in str(excinfo.value)


def test_followup_triggers_are_installed(db: Session) -> None:
    """Behaviour is proven above; this pins the mechanism, including TRUNCATE.

    TRUNCATE is pinned in the catalog rather than exercised, because issuing it would take
    an ACCESS EXCLUSIVE lock on a table other test sessions may be reading.
    """
    rows = db.execute(
        text(
            "SELECT t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
            "WHERE t.tgrelid = CAST('meeting_followups' AS regclass) AND NOT t.tgisinternal"
        )
    ).all()
    definitions = {str(name): str(definition) for name, definition in rows}
    assert set(definitions) == FOLLOWUP_TRIGGERS
    assert "BEFORE DELETE" in definitions["trg_meeting_followups_no_delete"]
    assert "BEFORE TRUNCATE" in definitions["trg_meeting_followups_no_truncate"]
    assert "BEFORE UPDATE" in definitions["trg_meeting_followups_guard_update"]


def test_meetings_no_longer_carry_followup_columns(db: Session) -> None:
    """One source of truth: every follow-up reader moved to ``meeting_followups`` (W3.2)."""
    rows = db.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'meetings'"
        )
    ).all()
    columns: dict[str, str] = {str(name): str(data_type) for name, data_type in rows}
    assert not [name for name in columns if name.startswith("followup_")]
    assert columns.get("pre_read_result") == "jsonb"


def test_case_determination_requires_a_named_human(db: Session) -> None:
    """A consular determination may never be autonomous."""
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.add(
            _make_case(
                status=CaseStatus.RESOLVED,
                determination="Renewal approved",
                determined_by_user_id=None,
            )
        )
        db.flush()

    assert "ck_cases_determination_requires_human" in str(excinfo.value)


def test_case_determination_is_accepted_with_a_named_human(db: Session) -> None:
    officer = _make_user(db)
    case = _make_case(
        status=CaseStatus.RESOLVED,
        determination="Renewal approved",
        determined_by_user_id=officer.id,
    )
    db.add(case)
    db.flush()

    assert case.id is not None


def _make_article(**overrides: object) -> KnowledgeArticle:
    fields: dict[str, object] = {
        "slug": f"schema-test-{_unique_suffix()}",
        "title": "Schema test article",
        "summary": "synthetic",
        "body": "synthetic",
        "category": "test",
        "status": KnowledgeStatus.APPROVED,
        "classification": Classification.PUBLIC,
    }
    fields.update(overrides)
    return KnowledgeArticle(**fields)


def test_knowledge_article_cannot_be_approved_without_an_approver(db: Session) -> None:
    """Published guidance carries the mission's name; a human has to put it there."""
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.add(_make_article(approved_by_user_id=None))
        db.flush()

    assert "ck_knowledge_articles_approved_requires_human" in str(excinfo.value)


def test_knowledge_article_is_accepted_with_an_approver(db: Session) -> None:
    approver = _make_user(db)
    article = _make_article(approved_by_user_id=approver.id)
    db.add(article)
    db.flush()

    assert article.id is not None


def test_draft_knowledge_article_needs_no_approver(db: Session) -> None:
    """The constraint keys on APPROVED, so drafting must stay unencumbered."""
    article = _make_article(status=KnowledgeStatus.DRAFT, approved_by_user_id=None)
    db.add(article)
    db.flush()

    assert article.id is not None


# ---------------------------------------------------------------------------
# 5. cases.public_ref (ADR-0007)
# ---------------------------------------------------------------------------


def test_public_ref_is_not_null_and_not_the_primary_key(db: Session) -> None:
    is_nullable = db.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'cases' AND column_name = 'public_ref'"
        )
    ).scalar_one()
    assert is_nullable == "NO"

    primary_key_columns = db.execute(
        text(
            """
            SELECT a.attname
            FROM pg_constraint con
            JOIN LATERAL unnest(con.conkey) AS k(attnum) ON TRUE
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            WHERE con.contype = 'p' AND con.conrelid = 'cases'::regclass
            """
        )
    ).scalars()
    assert set(primary_key_columns) == {"id"}


def test_public_ref_has_a_unique_index(db: Session) -> None:
    unique_indexes = db.execute(
        text(
            """
            SELECT i.relname
            FROM pg_index x
            JOIN pg_class i ON i.oid = x.indexrelid
            JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = ANY (x.indkey)
            WHERE x.indrelid = 'cases'::regclass AND x.indisunique AND a.attname = 'public_ref'
            """
        )
    ).scalars()
    assert list(unique_indexes), "cases.public_ref has no unique index"


def test_duplicate_public_ref_is_rejected(db: Session) -> None:
    first = _make_case()
    db.add(first)
    db.flush()

    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.add(_make_case(public_ref=first.public_ref))
        db.flush()

    assert "public_ref" in str(excinfo.value)


def test_public_ref_is_independent_of_the_primary_key(db: Session) -> None:
    """ADR-0007's whole point: two columns, because one cannot do both jobs.

    The ids are ULIDs minted microseconds apart, so they share a long prefix.
    If `public_ref` were derived from `id` in any way -- a truncation, an
    encoding, a hash of a shared seed -- the references would share structure
    too. They must not.
    """
    first, second = _make_case(), _make_case()
    db.add_all([first, second])
    db.flush()

    assert first.public_ref != second.public_ref
    assert first.public_ref != str(first.id)
    assert second.public_ref != str(second.id)

    def token(case: Case) -> str:
        return case.public_ref.removeprefix("NADDP-").replace("-", "")

    for case in (first, second):
        id_hex = case.id.hex.upper()
        assert token(case) not in id_hex
        # No shared prefix of any interesting length, in either direction.
        assert not any(id_hex.startswith(token(case)[:n]) for n in range(4, 13))

    # The ids really are adjacent, which is what makes the assertion above mean
    # something: same ULID timestamp prefix, entirely unrelated references.
    assert first.id.hex[:8] == second.id.hex[:8]
    assert token(first)[:4] != token(second)[:4]
