"""Unified Outcomes board: one governed picture, each figure under its own authorisation.

The board's claim is "the picture is unified; the permissions are not". Three halves prove it.

**Part 1 is structural and pure.** Each context module under ``app.services.outcomes`` imports
models from its own bounded context only, and the composer imports no model and issues no
statement, so no figure can be pooled from another context's rows. The thread definition parses.

**Part 2 needs Postgres** and builds the board inside a transaction that is always rolled back:

* AMBASSADOR, CONSULAR_OFFICER and DIASPORA_OFFICER get differently composed boards, and ADMIN
  gets every domain withheld;
* a withheld domain's tables are never queried -- captured SQL shows it -- and it reports no
  figure at all, never a zero;
* no statement touches two domains' tables;
* removing one permission from an otherwise all-seeing principal withholds exactly one domain
  and leaves every other figure unchanged -- the authorisations are separate, not one gate;
* figures equal the domain's own counts within the caller's clearance;
* the hero thread runs from the corridor opportunity through the stakeholder and the meeting to
  the diaspora experts and the consular case, and an anchor outside clearance reads as not found.

**Part 3 is HTTP**: the composed payloads per role, nothing withheld leaking into the JSON, and a
caller without a session refused.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import (
    CaseStatus,
    Classification,
    ConsentStatus,
    FollowupStatus,
    OpportunityStage,
    RoleCode,
)
from app.models.consular import Case
from app.models.diaspora import DiasporaProfile
from app.models.meetings import Meeting, MeetingFollowup
from app.models.opportunities import Opportunity
from app.security.permissions import Permission
from app.security.principal import (
    Principal,
    demo_persona,
    principal_for_role,
    readable_classifications_for,
)
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.outcomes import OutcomesBoard, build_outcomes_board
from app.services.outcomes.anchors import hero_thread, thread_spec
from app.services.outcomes.common import (
    NOT_FOUND_NOTE,
    authorise,
    days_ago,
    humanise,
    section_tone,
)
from app.services.session import ensure_persona_user

OUTCOMES_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "app" / "services" / "outcomes"

SECTION_KEYS: Final[tuple[str, ...]] = (
    "bilateral",
    "citizen_service",
    "diaspora",
    "relationships",
    "meetings",
)

#: The one model module each context may import.
CONTEXT_MODELS: Final[dict[str, set[str]]] = {
    "bilateral": {"app.models.opportunities"},
    "citizen_service": {"app.models.consular"},
    "diaspora": {"app.models.diaspora"},
    "relationships": {"app.models.stakeholders"},
    "meetings": {"app.models.meetings"},
}

#: The only other-service modules each context may lean on: its own context's service.
CONTEXT_SERVICES: Final[dict[str, set[str]]] = {
    "bilateral": set(),
    "citizen_service": {"app.services.cases"},
    "diaspora": {"app.services.diaspora"},
    "relationships": {"app.services.stakeholders"},
    "meetings": set(),
}

#: The permission whose removal withholds each section.
SECTION_PERMISSION: Final[dict[str, Permission]] = {
    "bilateral": Permission.READ_OPPORTUNITY,
    "citizen_service": Permission.READ_CONSULAR_CASE,
    "diaspora": Permission.READ_DIASPORA_PROFILE,
    "relationships": Permission.READ_STAKEHOLDER,
    "meetings": Permission.READ_MEETING,
}

EXPECTED_SECTIONS: Final[dict[RoleCode, frozenset[str]]] = {
    RoleCode.AMBASSADOR: frozenset(SECTION_KEYS),
    RoleCode.DEPUTY: frozenset(SECTION_KEYS),
    RoleCode.TRADE_OFFICER: frozenset({"bilateral", "diaspora", "relationships", "meetings"}),
    RoleCode.CONSULAR_OFFICER: frozenset({"citizen_service", "meetings"}),
    RoleCode.DIASPORA_OFFICER: frozenset({"bilateral", "diaspora", "relationships", "meetings"}),
    RoleCode.ADMIN: frozenset(),
}

#: Which domain each table belongs to, for reading captured SQL.
TABLE_DOMAIN: Final[dict[str, str]] = {
    "opportunities": "bilateral",
    "cases": "citizen_service",
    "case_events": "citizen_service",
    "diaspora_profiles": "diaspora",
    "diaspora_expertise": "diaspora",
    "expertise_tags": "diaspora",
    "stakeholders": "relationships",
    "organisations": "relationships",
    "interactions": "relationships",
    "meetings": "meetings",
    "meeting_followups": "meetings",
}

HERO_ENGINEER: Final[str] = "Ijeoma Nwachukwu"
HERO_ACADEMIC: Final[str] = "Femi Balogun-Wright"
HERO_OPPORTUNITY_TITLE: Final[str] = "Nigeria-Australia lithium processing skills corridor"
HERO_STAKEHOLDER: Final[str] = "Rosalind Petrakis"

_TABLE: Final[re.Pattern[str]] = re.compile(r"\b(?:from|join)\s+([a-z_]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Part 1: structural and pure
# ---------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


@pytest.mark.parametrize("name", sorted(CONTEXT_MODELS))
def test_each_context_module_reads_only_its_own_context(name: str) -> None:
    imports = _imports(OUTCOMES_DIR / f"{name}.py")
    assert {module for module in imports if module.startswith("app.models")} == CONTEXT_MODELS[name]
    services = {
        module
        for module in imports
        if module.startswith("app.services.") and not module.startswith("app.services.outcomes")
    }
    assert services <= CONTEXT_SERVICES[name], services
    siblings = {module for module in imports if module.startswith("app.services.outcomes.")}
    assert siblings <= {"app.services.outcomes.common", "app.services.outcomes.anchors"}, (
        "a context module never reads another context's outcomes"
    )


def test_the_board_composes_and_never_queries() -> None:
    path = OUTCOMES_DIR / "board.py"
    imports = _imports(path)
    assert not {module for module in imports if module.startswith("app.models")}
    assert not {
        module
        for module in imports
        if module.startswith("sqlalchemy") and module != "sqlalchemy.orm"
    }
    source = path.read_text(encoding="utf-8")
    for call in (".execute(", ".scalar(", ".scalars(", "select("):
        assert call not in source, f"board.py must not issue statements ({call})"


def test_a_gate_is_all_of_and_names_what_is_missing() -> None:
    officer = principal_for_role(RoleCode.CONSULAR_OFFICER)
    gate = authorise(officer, Permission.READ_MEETING, Permission.READ_OPPORTUNITY)
    assert not gate.granted
    assert gate.missing == (Permission.READ_OPPORTUNITY,)
    assert authorise(officer, Permission.READ_MEETING).granted


def test_wording_helpers() -> None:
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    assert humanise("CONTACT_PLANNED") == "Contact planned"
    assert days_ago(now, now) == "today"
    assert days_ago(datetime(2026, 9, 9, 11, tzinfo=UTC), now) == "6 days ago"
    assert days_ago(datetime(2026, 9, 18, 13, tzinfo=UTC), now) == "in 4 days"


def test_the_thread_names_five_anchors_and_two_capabilities() -> None:
    spec = thread_spec()
    assert set(spec.anchor_slugs) == {"opportunity", "stakeholder", "meeting", "case"}
    assert spec.diaspora_search_id == "hero-lithium-skills-corridor"
    assert [facet.expertise_tag for facet in spec.diaspora_facets] == [
        "XP_LITHIUM_PROCESSING_ENG",
        "XP_MIGRATION_PATHWAY_ACADEMIC",
    ]


# ---------------------------------------------------------------------------
# Part 2: the seeded mission (Postgres)
# ---------------------------------------------------------------------------


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose enclosing transaction is always rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")
    if hero_thread().opportunity_id is None:
        pytest.skip("seed manifest missing; run `make demo-reset`")

    from app.core.db import get_engine

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


NOW: Final[datetime] = datetime.now(UTC)


def _board(session: Session, principal: Principal) -> OutcomesBoard:
    return build_outcomes_board(session, principal, now=NOW)


def _captured(session: Session, principal: Principal) -> tuple[OutcomesBoard, list[str]]:
    from app.core.db import get_engine

    statements: list[str] = []

    def capture(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        board = _board(session, principal)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    return board, statements


def _domains(statement: str) -> set[str]:
    return {
        TABLE_DOMAIN[table.lower()]
        for table in _TABLE.findall(statement)
        if table.lower() in TABLE_DOMAIN
    }


def _granted(board: OutcomesBoard) -> frozenset[str]:
    return frozenset(section.key for section in board.sections if section.gate.granted)


def _values(board: OutcomesBoard) -> dict[str, list[tuple[str, int | None, int | None]]]:
    return {
        section.key: [(figure.key, figure.value, figure.of) for figure in section.figures]
        for section in board.sections
    }


@pytest.mark.integration
@pytest.mark.parametrize("role", list(EXPECTED_SECTIONS))
def test_each_role_gets_its_own_composition(db: Session, role: RoleCode) -> None:
    board, statements = _captured(db, principal_for_role(role))

    assert _granted(board) == EXPECTED_SECTIONS[role]
    touched = set().union(*(_domains(statement) for statement in statements))
    assert touched <= EXPECTED_SECTIONS[role], f"{role.value} queried {touched}"

    for section in board.sections:
        if section.gate.granted:
            assert section.figures and section.counted_across
            continue
        # Withheld: never queried, and never rendered as a count of zero.
        assert section.figures == () and section.counted_across == ()
        assert section.gate.missing == (SECTION_PERMISSION[section.key],)


@pytest.mark.integration
def test_the_three_demo_roles_see_three_different_boards(db: Session) -> None:
    boards = {
        role: _granted(_board(db, principal_for_role(role)))
        for role in (RoleCode.AMBASSADOR, RoleCode.CONSULAR_OFFICER, RoleCode.DIASPORA_OFFICER)
    }
    assert len(set(boards.values())) == 3, boards
    assert "citizen_service" in boards[RoleCode.CONSULAR_OFFICER]
    assert "bilateral" not in boards[RoleCode.CONSULAR_OFFICER]
    assert "citizen_service" not in boards[RoleCode.DIASPORA_OFFICER]


@pytest.mark.integration
def test_no_statement_pools_two_domains(db: Session) -> None:
    _board, statements = _captured(db, principal_for_role(RoleCode.AMBASSADOR))
    assert statements
    pooled = [statement for statement in statements if len(_domains(statement)) > 1]
    assert not pooled, pooled[0] if pooled else None
    touched = set().union(*(_domains(statement) for statement in statements))
    assert touched == set(SECTION_KEYS), "every domain counted itself"


@pytest.mark.integration
@pytest.mark.parametrize("key", SECTION_KEYS)
def test_removing_one_permission_withholds_exactly_one_domain(db: Session, key: str) -> None:
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    reduced = dataclasses.replace(
        ambassador, permissions=ambassador.permissions - {SECTION_PERMISSION[key]}
    )

    full = _board(db, ambassador)
    partial = _board(db, reduced)

    assert _granted(partial) == frozenset(SECTION_KEYS) - {key}
    full_values, partial_values = _values(full), _values(partial)
    for other in SECTION_KEYS:
        if other == key:
            assert partial_values[other] == []
        else:
            assert partial_values[other] == full_values[other], other


@pytest.mark.integration
def test_the_corridor_match_is_its_own_authorisation(db: Session) -> None:
    ambassador = principal_for_role(RoleCode.AMBASSADOR)
    no_search = dataclasses.replace(
        ambassador, permissions=ambassador.permissions - {Permission.SEARCH_DIASPORA_PROFILE}
    )
    board = _board(db, no_search)
    diaspora = next(section for section in board.sections if section.key == "diaspora")
    figures = {figure.key: figure for figure in diaspora.figures}

    assert diaspora.gate.granted, "counting the directory does not need search"
    assert figures["matched_to_corridor"].value is None
    assert figures["matched_to_corridor"].gate.missing == (Permission.SEARCH_DIASPORA_PROFILE,)
    assert figures["consented_reachable"].value is not None
    step = next(step for step in board.thread if step.key == "diaspora")
    assert step.found is None and step.facts == ()


@pytest.mark.integration
def test_figures_are_each_domains_own_counts_within_clearance(db: Session) -> None:
    for role in (RoleCode.AMBASSADOR, RoleCode.DIASPORA_OFFICER):
        principal = principal_for_role(role)
        zones = readable_classifications_for(principal)
        values = {
            (section.key, figure.key): figure.value
            for section in _board(db, principal).sections
            for figure in section.figures
        }

        assert values[("bilateral", "partnerships_concluded")] == db.scalar(
            select(func.count())
            .select_from(Opportunity)
            .where(
                Opportunity.stage == OpportunityStage.PARTNERED,
                Opportunity.classification.in_(zones),
            )
        )
        assert values[("meetings", "approved_followups_sent")] == db.scalar(
            select(func.count())
            .select_from(MeetingFollowup)
            .join(Meeting, Meeting.id == MeetingFollowup.meeting_id)
            .where(
                MeetingFollowup.status == FollowupStatus.SENT,
                MeetingFollowup.classification.in_(zones),
                Meeting.classification.in_(zones),
            )
        )
        assert values[("diaspora", "consented_reachable")] == db.scalar(
            select(func.count())
            .select_from(DiasporaProfile)
            .where(
                DiasporaProfile.consent_status.in_(
                    [ConsentStatus.GIVEN_DIRECTORY_ONLY, ConsentStatus.GIVEN_CONTACTABLE]
                ),
                DiasporaProfile.is_tombstoned.is_(False),
                DiasporaProfile.classification.in_(zones),
            )
        )
        if role is RoleCode.AMBASSADOR:
            assert values[("citizen_service", "cases_resolved")] == db.scalar(
                select(func.count())
                .select_from(Case)
                .where(
                    Case.status.in_([CaseStatus.RESOLVED, CaseStatus.CLOSED]),
                    Case.classification.in_(zones),
                )
            )


@pytest.mark.integration
def test_consular_counts_respect_clearance_not_just_permission(db: Session) -> None:
    trade = principal_for_role(RoleCode.TRADE_OFFICER)
    uncleared = dataclasses.replace(
        trade, permissions=trade.permissions | {Permission.READ_CONSULAR_CASE}
    )
    section = next(
        section for section in _board(db, uncleared).sections if section.key == "citizen_service"
    )
    assert section.gate.granted
    assert Classification.CONSULAR_SENSITIVE not in section.counted_across
    assert all(figure.value == 0 for figure in section.figures), (
        "without the consular compartment no CONSULAR_SENSITIVE case is counted"
    )


@pytest.mark.integration
def test_the_hero_thread_runs_across_the_contexts(db: Session) -> None:
    board = _board(db, principal_for_role(RoleCode.AMBASSADOR))
    steps = {step.key: step for step in board.thread}

    assert [step.key for step in board.thread] == [
        "opportunity",
        "stakeholder",
        "meeting",
        "diaspora",
        "consular",
    ]
    assert all(step.found for step in board.thread)

    opportunity = steps["opportunity"]
    assert opportunity.headline == HERO_OPPORTUNITY_TITLE
    assert opportunity.tone == "proposed"
    assert opportunity.trace_id == db.scalar(
        select(Opportunity.proposal_trace_id).where(Opportunity.id == hero_thread().opportunity_id)
    )

    diaspora = {fact.label: fact.value for fact in steps["diaspora"].facts}
    assert diaspora["Lithium-processing engineer"].startswith(HERO_ENGINEER)
    assert diaspora["Migration-pathway academic"].startswith(HERO_ACADEMIC)
    assert "Western Australia" in diaspora["Lithium-processing engineer"]

    meeting = {fact.label: fact.value for fact in steps["meeting"].facts}
    assert meeting["Recorded against the corridor opportunity"] == "Yes"
    assert steps["stakeholder"].headline is not None
    assert steps["stakeholder"].headline.startswith(HERO_STAKEHOLDER)
    consular = {fact.label: fact.value for fact in steps["consular"].facts}
    assert set(consular) == {"Reference", "Status", "Service level"}, "metadata only"


@pytest.mark.integration
def test_a_withheld_step_carries_nothing(db: Session) -> None:
    board = _board(db, principal_for_role(RoleCode.CONSULAR_OFFICER))
    steps = {step.key: step for step in board.thread}
    for key in ("opportunity", "stakeholder", "diaspora"):
        step = steps[key]
        assert step.found is None and not step.gate.granted
        assert step.headline is None and step.facts == () and step.trace_id is None
        assert step.href is None
    assert steps["meeting"].found and steps["consular"].found


@pytest.mark.integration
def test_an_anchor_outside_clearance_reads_as_not_found(db: Session) -> None:
    db.execute(
        update(Opportunity)
        .where(Opportunity.id == hero_thread().opportunity_id)
        .values(classification=Classification.CONFIDENTIAL)
    )
    db.flush()

    trade = next(
        step
        for step in _board(db, principal_for_role(RoleCode.TRADE_OFFICER)).thread
        if step.key == "opportunity"
    )
    assert trade.found is False and trade.headline is None
    assert trade.note == NOT_FOUND_NOTE

    ambassador = next(
        step
        for step in _board(db, principal_for_role(RoleCode.AMBASSADOR)).thread
        if step.key == "opportunity"
    )
    assert ambassador.found is True


def test_a_section_is_ticked_only_for_risk_or_warning() -> None:
    from app.services.outcomes.common import Gate, OutcomeFigure

    gate = Gate(required=(), missing=())

    def figure(tone: Any) -> OutcomeFigure:
        return OutcomeFigure(key="k", label="l", gate=gate, value=1, detail="d", tone=tone)

    assert section_tone([figure("ok"), figure("proposed")]) == "neutral"
    assert section_tone([figure("ok"), figure("warn")]) == "warn"
    assert section_tone([figure("warn"), figure("risk")]) == "risk"


# ---------------------------------------------------------------------------
# Part 3: HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request sessions and audit writes join a transaction rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")
    if hero_thread().opportunity_id is None:
        pytest.skip("seed manifest missing; run `make demo-reset`")

    from app.core.db import get_engine, get_session
    from app.main import create_app

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    audit_factory = sessionmaker(
        bind=connection,
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    app = create_app(audit_session_factory=audit_factory)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            client.cookies.clear()
            yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def _as(client: TestClient, role: RoleCode, session: Session) -> dict[str, Any]:
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))
    response = client.get("/v1/outcomes")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.integration
@pytest.mark.parametrize(
    "role", [RoleCode.AMBASSADOR, RoleCode.CONSULAR_OFFICER, RoleCode.DIASPORA_OFFICER]
)
def test_the_board_over_http_is_composed_for_the_role(
    api: tuple[TestClient, Session], role: RoleCode
) -> None:
    client, session = api
    body = _as(client, role, session)

    granted = {
        section["key"] for section in body["sections"] if section["authorisation"]["granted"]
    }
    assert granted == EXPECTED_SECTIONS[role]
    assert body["domains_readable"] == len(EXPECTED_SECTIONS[role])
    assert body["domains_total"] == len(SECTION_KEYS)

    for section in body["sections"]:
        if section["authorisation"]["granted"]:
            for figure in section["figures"]:
                assert figure["label"] and figure["detail"] and figure["tone"]
                assert (figure["value"] is None) is (not figure["authorisation"]["granted"])
        else:
            assert section["figures"] == [] and section["counted_across"] == []
            assert section["authorisation"]["missing_permissions"]

    for step in body["thread"]["steps"]:
        if not step["authorisation"]["granted"]:
            assert step["found"] is None and step["facts"] == [] and step["headline"] is None


@pytest.mark.integration
def test_nothing_withheld_leaks_into_a_consular_officers_payload(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    text = str(_as(client, RoleCode.CONSULAR_OFFICER, session))
    for withheld in (HERO_OPPORTUNITY_TITLE, HERO_STAKEHOLDER, HERO_ENGINEER, HERO_ACADEMIC):
        assert withheld not in text


@pytest.mark.integration
def test_admin_reaches_the_board_and_every_domain_is_withheld(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    body = _as(client, RoleCode.ADMIN, session)
    assert body["domains_readable"] == 0
    assert all(section["figures"] == [] for section in body["sections"])
    assert all(step["found"] is None for step in body["thread"]["steps"])


@pytest.mark.integration
def test_the_board_refuses_a_caller_without_a_session(api: tuple[TestClient, Session]) -> None:
    client, _session = api
    response = client.get("/v1/outcomes")
    assert response.status_code in {401, 403}, response.text
