"""The audit chain cannot fork: concurrent appends stay one linear chain, and every write lands.

Closes ``docs/W1_STATUS.md`` section 6 item 1. The Governance page invites a viewer to verify the
chain on stage, and a verification that could report a false break under load is worse than none.
These tests are what make the button trustworthy.

What is proven:

* the live table carries the unique link index, and refuses a second claim on a claimed link and
  a second genesis row outright;
* a burst of concurrent appends from many connections lands every row, in one chain with no fork
  (no link claimed twice), no gap (every link names a row that exists), one genesis and one head,
  and ``verify_chain`` reports it intact;
* a writer holding a stale head -- deterministically interleaved, with an id minted before the
  winner's -- re-links behind the winner instead of forking, and its id still sorts after it;
* two writers racing for an empty chain cannot both become the genesis row;
* a writer blocked past its budget refuses loudly, writes nothing and leaves its caller's
  transaction usable -- it never writes out of order.

**Isolation.** A fork only exists between COMMITTED transactions, so these tests commit -- and
``audit_events`` is append-only, so committed probe rows could never be removed from the demo's
log. Each test therefore builds a scratch copy of the live table in a throwaway schema with
``CREATE TABLE ... (LIKE public.audit_events INCLUDING ALL)``, which copies its columns, defaults,
CHECKs and indexes -- the unique link index among them -- but none of its rows, and routes the
production writer at it through SQLAlchemy's ``schema_translate_map``. The code under test is the
real writer; the index under test is copied from the live table, so a migration that failed to
create it fails here too. The schema is dropped afterwards.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final

import pytest
from sqlalchemy import Connection, Engine, Row, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import writer
from app.audit.writer import (
    LINK_INDEX_NAME,
    AuditChainBusyError,
    verify_chain,
    write_audit_event,
)
from app.core.ids import new_id
from app.domain.enums import Classification, PolicyResult
from app.models.governance import AuditEvent

pytestmark = pytest.mark.integration

PROBE_ACTION: Final[str] = "test.chain_probe"
PROBE_OBJECT: Final[str] = "governance.test"

#: Concurrent connections, and appends per connection, in the burst test.
WORKERS: Final[int] = 6
APPENDS_PER_WORKER: Final[int] = 8


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def probe(database_available: bool) -> Iterator[Engine]:
    """An engine whose ``audit_events`` is an empty scratch copy of the live table."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine

    engine = get_engine()
    schema = f"audit_chain_probe_{uuid.uuid4().hex[:10]}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(
            text(f'CREATE TABLE "{schema}".audit_events (LIKE public.audit_events INCLUDING ALL)')
        )
        _mirror_index_names(connection, schema)
    try:
        yield engine.execution_options(schema_translate_map={None: schema})
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


_INDEX_NAME: Final[re.Pattern[str]] = re.compile(r"INDEX \S+ ON \S+ ")


def _index_definitions(connection: Connection, schema: str) -> dict[str, str]:
    """``{definition without its name and schema: index name}`` for one schema's audit table."""
    rows = connection.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = :schema AND tablename = 'audit_events'"
        ),
        {"schema": schema},
    )
    return {
        _INDEX_NAME.sub("INDEX ON audit_events ", definition): name for name, definition in rows
    }


def _mirror_index_names(connection: Connection, schema: str) -> None:
    """Give the copy's indexes the live table's names, matched by definition.

    ``LIKE ... INCLUDING ALL`` copies every index but names them itself
    (``audit_events_prev_event_hash_idx``, ``audit_events_pkey``). The writer recognises losing
    the race by the violated constraint's name, so the copy must carry the live names -- and
    matching by definition doubles as a check that the copy holds exactly the live indexes.
    """
    live = _index_definitions(connection, "public")
    copied = _index_definitions(connection, schema)
    assert set(copied) == set(live), "the scratch copy does not carry the live table's indexes"
    for definition, copied_name in copied.items():
        connection.execute(
            text(f'ALTER INDEX "{schema}"."{copied_name}" RENAME TO "{live[definition]}"')
        )


def _write(session: Session, label: str) -> AuditEvent:
    return write_audit_event(
        session,
        actor=None,
        action=PROBE_ACTION,
        object_type=PROBE_OBJECT,
        policy_result=PolicyResult.ALLOW,
        classification=Classification.MISSION_INTERNAL,
        summary=label,
        request_id=f"probe-{uuid.uuid4().hex[:12]}",
    )


def _append(engine: Engine, label: str) -> uuid.UUID:
    """One append in its own transaction, committed."""
    with Session(bind=engine) as session:
        row_id = _write(session, label).id
        session.commit()
        return row_id


def _chain(engine: Engine) -> list[Row[Any]]:
    with Session(bind=engine) as session:
        return list(
            session.execute(
                select(
                    AuditEvent.id,
                    AuditEvent.prev_event_hash,
                    AuditEvent.event_hash,
                    AuditEvent.summary,
                ).order_by(AuditEvent.id)
            )
        )


def _assert_linear(engine: Engine, expected: int) -> list[Row[Any]]:
    """One chain: every row lands, no link is claimed twice, none names a missing row."""
    rows = _chain(engine)
    with Session(bind=engine) as session:
        verification = verify_chain(session)

    assert len(rows) == expected, f"{len(rows)} rows landed, {expected} were written"
    links = [row.prev_event_hash for row in rows]
    digests = {row.event_hash for row in rows}

    assert links[0] is None and links.count(None) == 1, "exactly one genesis, first in id order"
    assert len(set(links)) == expected, "no fork: no predecessor is claimed twice"
    assert {link for link in links if link is not None} <= digests, "no gap: every link exists"
    assert len(digests - set(links)) == 1, "exactly one head"
    assert verification.is_intact, verification
    assert verification.checked == expected
    return rows


def _run_threads(named: dict[str, Callable[[], None]], timeout: float = 30) -> None:
    errors: dict[str, BaseException] = {}

    def guard(name: str, target: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                target()
            except BaseException as exc:
                errors[name] = exc

        return run

    threads = [threading.Thread(target=guard(name, fn), name=name) for name, fn in named.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
        assert not thread.is_alive(), f"{thread.name} did not finish: a writer hung"
    if errors:
        name, error = next(iter(errors.items()))
        raise AssertionError(f"{name} failed: {error!r}") from error


# ---------------------------------------------------------------------------
# The live table
# ---------------------------------------------------------------------------


def test_the_live_table_carries_the_unique_link_index(database_available: bool) -> None:
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")
    from app.core.db import get_session_factory

    with get_session_factory()() as session:
        definition = session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                "AND tablename = 'audit_events' AND indexname = :name"
            ),
            {"name": LINK_INDEX_NAME},
        )
    assert definition is not None, f"{LINK_INDEX_NAME} is missing; run `make migrate`"
    assert "UNIQUE" in definition
    assert "(prev_event_hash)" in definition
    assert "NULLS NOT DISTINCT" in definition


def test_the_live_table_refuses_a_second_claim_and_a_second_genesis(
    database_available: bool,
) -> None:
    """Structural, not procedural: a forged fork is refused by the database itself."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")
    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        claimed = session.scalar(
            select(AuditEvent.prev_event_hash)
            .where(AuditEvent.prev_event_hash.is_not(None))
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        if claimed is None:
            pytest.skip("the audit log has fewer than two rows; run `make demo-reset`")

        for link in (claimed, None):
            with pytest.raises(IntegrityError) as refused, session.begin_nested():
                session.execute(
                    insert(AuditEvent).values(
                        id=new_id(),
                        action=PROBE_ACTION,
                        object_type=PROBE_OBJECT,
                        policy_result=PolicyResult.ALLOW,
                        classification=Classification.MISSION_INTERNAL,
                        request_id="probe-forged-fork",
                        summary="a second claim on a link that is already claimed",
                        payload={},
                        event_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                        prev_event_hash=link,
                    )
                )
            assert LINK_INDEX_NAME in str(refused.value)
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# Concurrent appends, against a scratch copy of the table
# ---------------------------------------------------------------------------


def test_a_concurrent_burst_lands_every_write_in_one_linear_chain(probe: Engine) -> None:
    barrier = threading.Barrier(WORKERS, timeout=10)

    def worker(index: int) -> list[str]:
        barrier.wait()
        labels = [f"worker {index} write {n}" for n in range(APPENDS_PER_WORKER)]
        for label in labels:
            _append(probe, label)
        return labels

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        written = [label for labels in pool.map(worker, range(WORKERS)) for label in labels]

    rows = _assert_linear(probe, WORKERS * APPENDS_PER_WORKER)
    assert sorted(row.summary for row in rows) == sorted(written), "every write landed once"


def test_a_writer_holding_a_stale_head_relinks_behind_the_winner(
    probe: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic interleaving of the exact fork the unique index exists to refuse."""
    _append(probe, "genesis")
    # Minted before the winner exists: a loser that kept this id would sort BEFORE the row it
    # has to follow, which verify_chain would report as a break.
    early_id = new_id()

    read_stale_head = threading.Event()
    winner_committed = threading.Event()
    loser_heads: list[writer._ChainHead | None] = []
    real_head = writer._chain_head
    real_new_id = writer.new_id

    def pausing_head(session: Session) -> writer._ChainHead | None:
        head = real_head(session)
        if threading.current_thread().name == "loser":
            loser_heads.append(head)
            if len(loser_heads) == 1:
                read_stale_head.set()
                assert winner_committed.wait(10), "the winner never committed"
        return head

    def early_id_for_the_loser() -> uuid.UUID:
        return early_id if threading.current_thread().name == "loser" else real_new_id()

    monkeypatch.setattr(writer, "_chain_head", pausing_head)
    monkeypatch.setattr(writer, "new_id", early_id_for_the_loser)

    def winner() -> None:
        assert read_stale_head.wait(10), "the loser never read the head"
        _append(probe, "winner")
        winner_committed.set()

    _run_threads({"loser": lambda: _append(probe, "loser"), "winner": winner})

    rows = _assert_linear(probe, 3)
    assert [row.summary for row in rows] == ["genesis", "winner", "loser"]
    assert rows[2].prev_event_hash == rows[1].event_hash, "the loser linked behind the winner"
    assert len(loser_heads) == 2, "the loser lost once, re-read the head once"
    assert loser_heads[0] is not None and loser_heads[0].id == rows[0].id
    assert early_id < rows[1].id < rows[2].id, "the loser's id was moved past the winner's"


def test_two_writers_racing_for_an_empty_chain_cannot_both_be_genesis(
    probe: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier = threading.Barrier(2, timeout=10)
    heads: dict[str, list[writer._ChainHead | None]] = {"first": [], "second": []}
    real_head = writer._chain_head

    def racing_head(session: Session) -> writer._ChainHead | None:
        head = real_head(session)
        name = threading.current_thread().name
        if name in heads:
            heads[name].append(head)
            if len(heads[name]) == 1:
                barrier.wait()
        return head

    monkeypatch.setattr(writer, "_chain_head", racing_head)
    _run_threads(
        {
            "first": lambda: _append(probe, "first"),
            "second": lambda: _append(probe, "second"),
        }
    )

    assert heads["first"][0] is None and heads["second"][0] is None, "both saw an empty chain"
    assert sorted(len(seen) for seen in heads.values()) == [1, 2], "exactly one re-linked"
    _assert_linear(probe, 2)


def test_a_writer_blocked_past_its_budget_refuses_rather_than_forks(
    probe: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _append(probe, "genesis")
    monkeypatch.setattr(writer, "_LINK_LOCK_TIMEOUT_MS", 150)
    monkeypatch.setattr(writer, "_APPEND_BUDGET_SECONDS", 0.6)

    with Session(bind=probe) as holder, Session(bind=probe) as blocked:
        _write(holder, "holds the head and does not commit")
        with pytest.raises(AuditChainBusyError) as refused:
            _write(blocked, "queued behind an uncommitted claim")
        assert refused.value.status_code == 503
        assert refused.value.extra["attempts"] >= 2, "it retried before refusing"
        assert blocked.scalar(select(1)) == 1, "the caller's transaction is still usable"
        blocked.rollback()
        holder.rollback()

    _assert_linear(probe, 1)
