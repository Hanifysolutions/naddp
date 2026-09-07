"""Stage 9 against a real database: one ``ai_traces`` row per call, refusals included.

Marked ``integration``. Everything runs inside an outer transaction that is always rolled
back, so a demo run's trace timeline is never polluted by the suite.

``ai_traces`` is where the claim "you can audit how this answer was produced" is either
true or merely stated, and its ``fallback = (fallback_reason IS NOT NULL)`` check
constraint is a real gate that only a database can enforce -- which is exactly why these
tests are not satisfied by asserting on the in-memory :class:`~app.ai.gateway.TraceRecord`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Final

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gateway import generate_traced
from app.ai.schemas import GatewayContext
from app.domain.enums import AiPurpose, ApprovalStatus, Classification, RoleCode
from app.models.ai import FALLBACK_REASONS, AiTrace
from app.security.principal import demo_persona, principal_for_role

pytestmark = pytest.mark.integration


@pytest.fixture
def transactional_session(database_available: bool) -> Iterator[Session]:
    """A session on a transaction this fixture rolls back, with the personas provisioned."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine
    from app.services.session import ensure_persona_user

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        for role in RoleCode:
            ensure_persona_user(session, demo_persona(role))
        session.flush()
        # Rows that were already there. These tests assert "exactly one trace row", and
        # ai_traces is NOT empty in a working environment: `make seed` writes traces, and so
        # does anyone who clicked the demo before running the suite. Counting the whole
        # table made the suite pass only on a virgin database, which is the worst kind of
        # green -- it holds right up until somebody uses the product.
        session.info[BASELINE_KEY] = set(session.scalars(select(AiTrace.id)))
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


#: Key under which the fixture stashes the pre-existing trace ids on ``Session.info``.
BASELINE_KEY: Final[str] = "naddp_baseline_trace_ids"


def _traces(session: Session) -> list[AiTrace]:
    """Trace rows written **by this test**, oldest first.

    Filtered against the baseline the fixture captured rather than against the whole table,
    and ordered by id -- which is a ULID, so id order is write order and ``[0]`` means "the
    first row this test produced" instead of "whatever the database returned first".
    """
    baseline: set[uuid.UUID] = session.info.get(BASELINE_KEY, set())
    rows = session.execute(select(AiTrace).order_by(AiTrace.id)).scalars()
    return [row for row in rows if row.id not in baseline]


def test_a_served_answer_writes_exactly_one_trace_row(transactional_session: Session) -> None:
    outcome = generate_traced(
        AiPurpose.MORNING_BRIEF,
        Classification.MISSION_INTERNAL,
        GatewayContext(),
        principal_for_role(RoleCode.AMBASSADOR),
        session=transactional_session,
    )
    transactional_session.flush()

    rows = _traces(transactional_session)
    assert len(rows) == 1
    row = rows[0]

    assert str(row.id) == outcome.envelope.trace_id
    assert row.purpose is AiPurpose.MORNING_BRIEF
    assert row.scenario == "ambassador"
    assert row.actor_role is RoleCode.AMBASSADOR
    assert row.user_id == principal_for_role(RoleCode.AMBASSADOR).user_id
    assert row.model_route == "external-noret"
    assert row.route_reason
    assert row.fallback is True
    assert row.fallback_reason in FALLBACK_REASONS
    assert row.evidence_ids
    assert row.citation_check_passed is True
    assert row.approval_status is ApprovalStatus.NOT_REQUIRED
    assert row.request_id
    assert row.latency_ms is not None and row.latency_ms >= 0
    stage_names = [stage["stage"] for stage in row.stages]
    assert stage_names[0] == "purpose_allowlist"
    # Nine, including the stage that describes this very write. A persisted trace whose
    # own stage list stopped at eight would read as a pipeline that halted.
    assert stage_names[-1] == "trace_write"
    assert len(stage_names) == 9


def test_the_trace_holds_no_prompt_text(transactional_session: Session) -> None:
    """ADR-0004: a digest, never the assembled prompt, which embeds retrieved evidence."""
    generate_traced(
        AiPurpose.MEETING_PREP,
        Classification.MISSION_INTERNAL,
        GatewayContext(subject_ref="covalent-lithium-bilateral"),
        principal_for_role(RoleCode.TRADE_OFFICER),
        session=transactional_session,
    )
    transactional_session.flush()

    row = _traces(transactional_session)[0]
    assert row.prompt_hash is not None
    assert len(row.prompt_hash) == 64
    assert row.retrieval_filter["applied"] == "before_selection"


def test_a_refusal_also_writes_a_trace(transactional_session: Session) -> None:
    """A trace table that records only successes tells an auditor nothing."""
    outcome = generate_traced(
        AiPurpose.CONSULAR_TRIAGE,
        Classification.MISSION_INTERNAL,
        GatewayContext(facts={"case_narrative": "The applicant reported that ..."}),
        principal_for_role(RoleCode.CONSULAR_OFFICER),
        session=transactional_session,
    )
    transactional_session.flush()

    assert outcome.envelope.approval_status is ApprovalStatus.BLOCKED
    row = _traces(transactional_session)[0]
    assert row.approval_status is ApprovalStatus.BLOCKED
    assert row.live is False
    assert row.fallback is False
    assert row.fallback_reason is None, "the check constraint requires NULL when not fallen back"
    assert row.error is not None and "case_narrative" in row.error
    assert row.citation_check_passed is None, "the check never ran; NULL is not False"


def test_two_calls_in_one_transaction_write_two_rows(transactional_session: Session) -> None:
    for purpose in (AiPurpose.MORNING_BRIEF, AiPurpose.DIASPORA_MATCH):
        generate_traced(
            purpose,
            Classification.MISSION_INTERNAL,
            GatewayContext(),
            principal_for_role(RoleCode.AMBASSADOR),
            session=transactional_session,
        )
    transactional_session.flush()
    assert len(_traces(transactional_session)) == 2


def test_the_gateway_does_not_commit(transactional_session: Session) -> None:
    """The caller owns the transaction: the trace belongs with the change it describes."""
    generate_traced(
        AiPurpose.MORNING_BRIEF,
        Classification.PUBLIC,
        GatewayContext(),
        principal_for_role(RoleCode.AMBASSADOR),
        session=transactional_session,
    )
    assert transactional_session.in_transaction()
    transactional_session.rollback()
    assert _traces(transactional_session) == []
