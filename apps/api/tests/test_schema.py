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
  * all 26 tables exist (BUILD_BIBLE / PROMPT_W1 Phase 2 table list);
  * the three embedding columns are `vector(1536)` with HNSW indexes;
  * `audit_events` and `case_events` reject UPDATE and DELETE (ADR-0004);
  * the three BUILD_BIBLE section 6 non-autonomy CHECK constraints bite;
  * `cases.public_ref` is UNIQUE, NOT NULL, not the primary key, and shares no
    entropy with `id` (ADR-0007).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import (
    CaseStatus,
    Classification,
    KnowledgeStatus,
    MeetingType,
    PolicyResult,
    Priority,
)
from app.models.consular import Case
from app.models.governance import AuditEvent, User
from app.models.knowledge import KnowledgeArticle
from app.models.meetings import Meeting

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
# 1. All 26 tables exist
# ---------------------------------------------------------------------------


def _live_tables(db: Session) -> frozenset[str]:
    rows = db.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    ).scalars()
    return frozenset(rows)


def test_expected_table_count_is_twenty_six() -> None:
    """Guards the expectation itself: a typo that drops a name must not pass silently."""
    assert len(EXPECTED_TABLES) == 26


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


def test_meeting_followup_cannot_be_sent_without_an_approver(db: Session) -> None:
    """Winning moment 2: external outreach blocks on a named human."""
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.add(
            Meeting(
                title="Non-autonomy probe",
                meeting_type=MeetingType.BILATERAL,
                scheduled_start=datetime.now(UTC),
                scheduled_end=datetime.now(UTC),
                agenda="probe",
                classification=Classification.MISSION_INTERNAL,
                followup_sent_at=datetime.now(UTC),
                followup_approved_by_user_id=None,
            )
        )
        db.flush()

    assert "ck_meetings_followup_sent_requires_approval" in str(excinfo.value)


def test_meeting_followup_is_accepted_with_an_approver(db: Session) -> None:
    """The constraint must block the unapproved case only -- not the whole feature."""
    approver = _make_user(db)
    meeting = Meeting(
        title="Non-autonomy control",
        meeting_type=MeetingType.BILATERAL,
        scheduled_start=datetime.now(UTC),
        scheduled_end=datetime.now(UTC),
        agenda="control",
        classification=Classification.MISSION_INTERNAL,
        followup_sent_at=datetime.now(UTC),
        followup_approved_by_user_id=approver.id,
    )
    db.add(meeting)
    db.flush()

    assert meeting.id is not None


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
