"""Diaspora capability search: consent is the gate, inside the query. Candidates only.

Three halves, as for knowledge answers.

**Part 1 is pure.** Coarse location, the selection rule (facets first, then depth), the result
schema -- which has no field a city, a language or a contact detail could go in -- and the
Gateway with a substituted search: nobody fitting asks no model even with the live path on, and a
live answer that names someone the query did not return, or promotes a directory-only profile to
contactable, is refused at stage 8 and replaced by the rule-selected candidates.

**Part 2 needs Postgres** and runs the real search over the seeded directory, inside a transaction
that is always rolled back. It proves the W4.1 VERIFY properties: the hero search returns BOTH the
lithium-processing engineer and the migration-pathway academic; the two strong skills matches
whose consent is not given or withdrawn are never loaded -- the consent predicate is in the SQL
that reads profiles, no such row reaches the session, and flipping their consent (inside the
rolled-back transaction) is exactly what would make them appear; and the demo searches file does
what it says.

**Part 3 is HTTP**: coarse location only on the wire, no contact or outreach route anywhere in
the API, the overview counting consented profiles only, and a role without the diaspora
permissions getting a deny rather than a crash.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.ai import gateway as gateway_module
from app.ai.diaspora_match import candidate_set, no_candidates, verify_candidates
from app.ai.gateway import _ProviderResponse, generate_traced
from app.ai.purposes import PURPOSES
from app.ai.schemas import DiasporaMatch, DiasporaMatchResult, GatewayContext
from app.core.config import Settings
from app.domain.diaspora import (
    MAX_CANDIDATES,
    CandidateExpertise,
    DirectoryCandidate,
    DirectorySearch,
    ScoredProfile,
    coarse_location,
    required_matches,
    select_candidates,
    split_requirement_terms,
)
from app.domain.enums import AiPurpose, ApprovalStatus, Classification, ConsentStatus, RoleCode
from app.models.diaspora import CONSENT_SEARCHABLE, DiasporaProfile
from app.security.principal import demo_persona, principal_for_role
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.diaspora import SuggestedSearch, search_directory, suggested_searches
from app.services.session import ensure_persona_user

HERO_ID: Final[str] = "hero-lithium-skills-corridor"
HERO_ENGINEER: Final[str] = "Ijeoma Nwachukwu"
HERO_ACADEMIC: Final[str] = "Femi Balogun-Wright"
HERO_ENGINEER_TAG: Final[str] = "XP_LITHIUM_PROCESSING_ENG"
HERO_ACADEMIC_TAG: Final[str] = "XP_MIGRATION_PATHWAY_ACADEMIC"

#: Strong skills matches for the hero search whose consent forbids listing them.
NOT_GIVEN_ENGINEER: Final[str] = "Abdulmalik Jibril"
WITHDRAWN_ACADEMIC: Final[str] = "Adanna Chukwu"
NON_CONSENTED: Final[frozenset[str]] = frozenset({NOT_GIVEN_ENGINEER, WITHDRAWN_ACADEMIC})

SEARCH_ROLES: Final[tuple[RoleCode, ...]] = (
    RoleCode.AMBASSADOR,
    RoleCode.DEPUTY,
    RoleCode.TRADE_OFFICER,
    RoleCode.DIASPORA_OFFICER,
)
DENIED_ROLES: Final[tuple[RoleCode, ...]] = (RoleCode.CONSULAR_OFFICER, RoleCode.ADMIN)

#: What a result must never carry: a finer location, a sensitive attribute, a way to reach someone.
FORBIDDEN_FIELDS: Final[frozenset[str]] = frozenset(
    {"city", "languages", "summary", "email", "phone", "telephone", "address", "contact"}
)


def _hero() -> SuggestedSearch:
    return next(search for search in suggested_searches() if search.id == HERO_ID)


def _as_result(result: object) -> dict[str, Any]:
    assert isinstance(result, DiasporaMatchResult), type(result)
    return result.model_dump(mode="json")


def _names(result: dict[str, Any]) -> set[str]:
    return {match["display_name"] for match in result["matches"]}


# ---------------------------------------------------------------------------
# Part 1: pure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "city", "expected"),
    [
        ("AU", "Perth", "Western Australia, Australia"),
        ("AU", "Canberra", "Australian Capital Territory, Australia"),
        ("au", " kwinana ", "Western Australia, Australia"),
        ("NG", "Abuja", "Federal Capital Territory, Nigeria"),
        ("AU", "Somewhere Unmapped", "Australia"),
        ("AU", None, "Australia"),
    ],
)
def test_a_location_is_a_state_and_country_never_the_city(
    country: str, city: str | None, expected: str
) -> None:
    assert coarse_location(country, city) == expected


def test_framing_words_are_not_capabilities() -> None:
    terms, ignored = split_requirement_terms({"could", "advis", "lithium", "diaspora", "pathway"})
    assert terms == {"lithium", "pathway"}
    assert ignored == {"could", "advis", "diaspora"}


def _scored(key: str, terms: set[str], score: float, *, contactable: bool = True) -> ScoredProfile:
    return ScoredProfile(
        key=key, matched=frozenset(terms), score=score, contactable=contactable, name=key.upper()
    )


def test_selection_covers_each_facet_before_adding_depth() -> None:
    engineer = _scored("a", {"lithium", "process"}, 4.0)
    second_engineer = _scored("b", {"lithium", "process"}, 4.0, contactable=False)
    academic = _scored("c", {"migrat", "pathway"}, 1.5)
    one_term = _scored("d", {"lithium"}, 5.0)
    weak_engineer = _scored("e", {"lithium", "process"}, 1.0)

    chosen = select_candidates([weak_engineer, one_term, academic, second_engineer, engineer], 4)

    assert [entry.key for entry in chosen] == ["a", "b", "c"]
    assert "d" not in {entry.key for entry in chosen}, "one shared term cannot qualify a profile"
    assert "c" in {entry.key for entry in chosen}, "the only migration match covers its facet"
    assert "e" not in {entry.key for entry in chosen}, "depth needs half the best score"


def test_selection_is_capped_and_needs_a_requirement() -> None:
    many = [_scored(f"p{index}", {"lithium", "process"}, 2.0) for index in range(10)]
    assert len(select_candidates(many, 2)) == MAX_CANDIDATES
    assert required_matches(1) == 1 and required_matches(5) == 2
    assert select_candidates(many, 0) == ()


def test_a_result_has_nowhere_to_put_a_city_a_language_or_a_contact_detail() -> None:
    assert not FORBIDDEN_FIELDS & set(DiasporaMatch.model_fields)
    assert not FORBIDDEN_FIELDS & set(DiasporaMatchResult.model_fields)
    with pytest.raises(ValidationError):
        DiasporaMatch.model_validate(
            {
                "profile_ref": "x",
                "display_name": "A synthetic person",
                "why_matched": "Holds the capability.",
                "consent_status": "GIVEN_CONTACTABLE",
                "contactable": True,
                "email": "someone@example.invalid",
            }
        )


def test_a_non_consented_match_is_refused_by_the_schema_too() -> None:
    for consent in (ConsentStatus.NOT_GIVEN, ConsentStatus.WITHDRAWN):
        with pytest.raises(ValidationError):
            DiasporaMatch.model_validate(
                {
                    "profile_ref": "x",
                    "display_name": "A synthetic person",
                    "why_matched": "Holds the capability.",
                    "consent_status": consent.value,
                    "contactable": False,
                }
            )


def test_a_result_is_candidates_or_a_reason_never_both_and_never_neither() -> None:
    base: dict[str, Any] = {"query": "lithium", "rationale": "Why.", "confidence": 0.5}
    match = {
        "profile_ref": "x",
        "display_name": "A synthetic person",
        "why_matched": "Holds the capability.",
        "consent_status": "GIVEN_DIRECTORY_ONLY",
        "contactable": False,
    }
    with pytest.raises(ValidationError):
        DiasporaMatchResult.model_validate({**base, "matches": []})
    with pytest.raises(ValidationError):
        DiasporaMatchResult.model_validate(
            {**base, "matches": [match], "no_match": {"reason": "Nobody."}}
        )
    with pytest.raises(ValidationError):
        DiasporaMatchResult.model_validate({**base, "matches": [match, match]})
    with pytest.raises(ValidationError):
        DiasporaMatchResult.model_validate({**base, "matches": [match], "candidates_only": False})
    assert DiasporaMatchResult.model_validate({**base, "matches": [match]}).candidates_only


def test_diaspora_is_never_answered_from_a_snapshot() -> None:
    assert PURPOSES[AiPurpose.DIASPORA_MATCH].snapshot_fallback is False


def _candidate(
    name: str = "A synthetic engineer",
    consent: ConsentStatus = ConsentStatus.GIVEN_DIRECTORY_ONLY,
) -> DirectoryCandidate:
    return DirectoryCandidate(
        profile_id=uuid.uuid4(),
        display_name=name,
        headline="Lithium hydroxide process engineer",
        sector_code="CM_LITHIUM",
        sector_label="Lithium and Battery Materials",
        institution="An Australian university (Western Australia)",
        organisation="A lithium refiner",
        coarse_location="Western Australia, Australia",
        consent_status=consent,
        availability="Advisory only",
        expertise=(
            CandidateExpertise(
                code=HERO_ENGINEER_TAG,
                label="Lithium processing engineering",
                sector_code="CM_LITHIUM",
                proficiency="PRIMARY",
            ),
        ),
        score=4.0,
        matched_terms=("lithium", "process"),
    )


def _search(*candidates: DirectoryCandidate) -> DirectorySearch:
    return DirectorySearch(
        consulted=True,
        requirement_terms=("lithium", "process"),
        ignored_terms=(),
        searchable_count=28,
        candidates=candidates,
        coverage=1.0 if candidates else 0.0,
    )


def test_the_candidate_set_carries_consent_exactly_and_checks_against_the_query() -> None:
    directory_only = _candidate()
    search = _search(directory_only)
    result = candidate_set(GatewayContext(question="lithium processing"), search)

    assert result.matches[0].contactable is False
    assert result.matches[0].coarse_location == "Western Australia, Australia"
    assert "contacts no one" in result.rationale
    assert verify_candidates(result, search)[0]

    promoted = result.model_copy(
        update={
            "matches": [
                result.matches[0].model_copy(
                    update={"consent_status": ConsentStatus.GIVEN_CONTACTABLE, "contactable": True}
                )
            ]
        }
    )
    assert not verify_candidates(promoted, search)[0]
    assert not verify_candidates(result, _search(_candidate("Someone else")))[0]

    nobody = no_candidates(GatewayContext(question="nuclear reactors"), _search())
    assert nobody.matches == [] and nobody.no_match is not None
    assert nobody.declines_to_answer()


def _live_settings() -> Settings:
    return Settings(app_env="test", ai_gateway_live=True, anthropic_api_key="sk-ant-not-a-real-key")


def _tripwire(**_: Any) -> _ProviderResponse:
    raise AssertionError("no model may be asked when no consented profile fits")


def test_when_nobody_fits_no_model_is_asked_even_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)
    monkeypatch.setattr(gateway_module, "_call_provider", _tripwire)
    monkeypatch.setattr(gateway_module, "_search_directory", lambda *_a, **_k: _search())

    outcome = generate_traced(
        AiPurpose.DIASPORA_MATCH,
        Classification.MISSION_INTERNAL,
        GatewayContext(question="Nuclear reactor licensing"),
        principal_for_role(RoleCode.DIASPORA_OFFICER),
    )

    result = _as_result(outcome.envelope.result)
    assert result["matches"] == [] and result["no_match"]["reason"]
    assert outcome.envelope.evidence == []
    assert outcome.trace.model_used is None
    assert outcome.trace.live is False and outcome.trace.fallback is False
    assert outcome.trace.citation_check_passed is True
    generation = next(stage for stage in outcome.trace.stages if stage.stage == "generation")
    assert "no model was asked" in generation.detail


def _live_match(
    profile_ref: str, consent: ConsentStatus, *, contactable: bool
) -> _ProviderResponse:
    return _ProviderResponse(
        payload={
            "query": "lithium processing",
            "matches": [
                {
                    "profile_ref": profile_ref,
                    "display_name": "A name a model wrote",
                    "why_matched": "A reason a model wrote.",
                    "consent_status": consent.value,
                    "contactable": contactable,
                }
            ],
            "rationale": "A rationale a model wrote.",
            "confidence": 0.9,
        },
        model_used="claude-test",
        input_tokens=1,
        output_tokens=1,
    )


@pytest.mark.parametrize("forgery", ["unknown-person", "promoted-consent"])
def test_a_live_answer_naming_anyone_but_the_consented_candidates_falls_back(
    monkeypatch: pytest.MonkeyPatch, forgery: str
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)
    monkeypatch.setattr(gateway_module, "_search_directory", lambda *_a, **_k: _search(candidate))
    forged = (
        _live_match(str(uuid.uuid4()), ConsentStatus.GIVEN_DIRECTORY_ONLY, contactable=False)
        if forgery == "unknown-person"
        else _live_match(candidate.profile_ref, ConsentStatus.GIVEN_CONTACTABLE, contactable=True)
    )
    monkeypatch.setattr(gateway_module, "_call_provider", lambda **_: forged)

    outcome = generate_traced(
        AiPurpose.DIASPORA_MATCH,
        Classification.MISSION_INTERNAL,
        GatewayContext(question="lithium processing"),
        principal_for_role(RoleCode.DIASPORA_OFFICER),
    )

    assert outcome.trace.fallback_reason == "CITATION_CHECK_FAILED"
    assert outcome.trace.snapshot_key is None
    result = _as_result(outcome.envelope.result)
    assert [match["profile_ref"] for match in result["matches"]] == [candidate.profile_ref]
    assert result["matches"][0]["contactable"] is False


def test_a_live_answer_from_the_consented_candidates_is_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(gateway_module, "get_settings", _live_settings)
    monkeypatch.setattr(gateway_module, "_search_directory", lambda *_a, **_k: _search(candidate))
    monkeypatch.setattr(
        gateway_module,
        "_call_provider",
        lambda **_: _live_match(
            candidate.profile_ref, ConsentStatus.GIVEN_DIRECTORY_ONLY, contactable=False
        ),
    )

    outcome = generate_traced(
        AiPurpose.DIASPORA_MATCH,
        Classification.MISSION_INTERNAL,
        GatewayContext(question="lithium processing"),
        principal_for_role(RoleCode.DIASPORA_OFFICER),
    )

    assert outcome.trace.model_used == "claude-test"
    assert outcome.trace.fallback is False
    assert outcome.trace.citation_check_passed is True


# ---------------------------------------------------------------------------
# Part 2: the seeded directory (Postgres)
# ---------------------------------------------------------------------------


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    """A session whose enclosing transaction is always rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_engine

    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        # Ids only: the directory tests watch which profile entities a search loads.
        hero = session.scalar(
            select(DiasporaProfile.id).where(DiasporaProfile.full_name == HERO_ENGINEER)
        )
        if hero is None:
            pytest.skip("diaspora directory not seeded; run `make demo-reset`")
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _match(session: Session, role: RoleCode, requirement: str) -> gateway_module.GatewayOutcome:
    return generate_traced(
        AiPurpose.DIASPORA_MATCH,
        Classification.MISSION_INTERNAL,
        GatewayContext(question=requirement),
        principal_for_role(role),
        session=session,
    )


@pytest.mark.integration
@pytest.mark.parametrize("role", SEARCH_ROLES)
def test_the_hero_search_returns_the_engineer_and_the_academic(db: Session, role: RoleCode) -> None:
    outcome = _match(db, role, _hero().requirement)
    result = _as_result(outcome.envelope.result)

    by_name = {match["display_name"]: match for match in result["matches"]}
    assert {HERO_ENGINEER, HERO_ACADEMIC} <= set(by_name), sorted(by_name)
    assert HERO_ENGINEER_TAG in by_name[HERO_ENGINEER]["expertise_tags"]
    assert HERO_ACADEMIC_TAG in by_name[HERO_ACADEMIC]["expertise_tags"]
    assert not NON_CONSENTED & set(by_name)
    assert result["candidates_only"] is True

    for match in result["matches"]:
        assert match["consent_status"] in {status.value for status in CONSENT_SEARCHABLE}
        assert match["contactable"] is (match["consent_status"] == "GIVEN_CONTACTABLE")
        assert match["expertise"] and match["sector_label"] and match["institution"]
        assert re.fullmatch(r"[A-Za-z ]+, (Australia|Nigeria)", match["coarse_location"])

    # Routing (section 4a) and the in-query filter, as the trace recorded them.
    assert outcome.envelope.approval_status is not ApprovalStatus.BLOCKED
    assert outcome.envelope.evidence == []
    assert outcome.trace.data_class is Classification.MISSION_INTERNAL
    assert outcome.trace.route_badge
    assert outcome.trace.model_used is None and outcome.trace.snapshot_key is None
    assert outcome.trace.fallback is True and outcome.trace.citation_check_passed is True
    applied = outcome.trace.retrieval_filter
    assert applied["applied"] == "inside_the_query_before_ranking"
    assert applied["filter"]["consent_status"] == [status.value for status in CONSENT_SEARCHABLE]
    assert applied["returns"].startswith("candidates only")


@pytest.mark.integration
def test_strong_matches_without_consent_are_never_loaded_the_filter_is_in_the_query(
    db: Session,
) -> None:
    from app.core.db import get_engine

    statements: list[tuple[str, Any]] = []
    loaded: list[tuple[str, ConsentStatus, bool]] = []

    def capture(_conn: Any, _cursor: Any, statement: str, parameters: Any, *_rest: Any) -> None:
        statements.append((statement, parameters))

    def on_load(_session: Session, instance: object) -> None:
        if isinstance(instance, DiasporaProfile):
            loaded.append((instance.full_name, instance.consent_status, instance.is_tombstoned))

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    event.listen(db, "loaded_as_persistent", on_load)
    try:
        search = search_directory(
            db, principal_for_role(RoleCode.DIASPORA_OFFICER), _hero().requirement
        )
    finally:
        event.remove(db, "loaded_as_persistent", on_load)
        event.remove(engine, "before_cursor_execute", capture)

    # 1. Every statement that read profiles carried the consent gate in its WHERE clause.
    profile_reads = [
        (sql, params) for sql, params in statements if "from diaspora_profiles" in sql.lower()
    ]
    assert profile_reads, "the search read no profiles at all"
    for sql, params in profile_reads:
        where = re.split(r"\bwhere\b", sql.lower(), maxsplit=1)
        assert len(where) == 2, f"a profile read without a WHERE clause: {sql}"
        assert "diaspora_profiles.consent_status in" in where[1], sql
        assert "diaspora_profiles.is_tombstoned is false" in where[1], sql
        bound = json.dumps(params, default=str)
        assert "GIVEN_CONTACTABLE" in bound and "GIVEN_DIRECTORY_ONLY" in bound
        assert "NOT_GIVEN" not in bound and "WITHDRAWN" not in bound

    # 2. So no profile without consent ever reached the session -- not even to be scored.
    assert len(loaded) == search.searchable_count
    assert all(consent in CONSENT_SEARCHABLE for _name, consent, _tombstoned in loaded)
    assert not any(tombstoned for _name, _consent, tombstoned in loaded)
    assert not NON_CONSENTED & {name for name, _consent, _tombstoned in loaded}
    assert not NON_CONSENTED & {candidate.display_name for candidate in search.candidates}

    # 3. They are strong matches, excluded by consent alone: flip it (rolled back) and they appear.
    rows = dict(
        db.execute(
            select(DiasporaProfile.full_name, DiasporaProfile.consent_status).where(
                DiasporaProfile.full_name.in_(NON_CONSENTED)
            )
        )
        .tuples()
        .all()
    )
    assert rows == {
        NOT_GIVEN_ENGINEER: ConsentStatus.NOT_GIVEN,
        WITHDRAWN_ACADEMIC: ConsentStatus.WITHDRAWN,
    }
    db.execute(
        update(DiasporaProfile)
        .where(DiasporaProfile.full_name.in_(NON_CONSENTED))
        .values(consent_status=ConsentStatus.GIVEN_CONTACTABLE)
    )
    db.flush()
    db.expunge_all()
    consented = search_directory(
        db, principal_for_role(RoleCode.DIASPORA_OFFICER), _hero().requirement
    )
    assert {candidate.display_name for candidate in consented.candidates} >= NON_CONSENTED


@pytest.mark.integration
def test_only_the_coarse_location_leaves_the_directory(db: Session) -> None:
    outcome = _match(db, RoleCode.DIASPORA_OFFICER, _hero().requirement)
    result = _as_result(outcome.envelope.result)
    ids = [uuid.UUID(match["profile_ref"]) for match in result["matches"]]
    cities = dict(
        db.execute(
            select(DiasporaProfile.id, DiasporaProfile.city).where(DiasporaProfile.id.in_(ids))
        )
        .tuples()
        .all()
    )
    for match in result["matches"]:
        assert not FORBIDDEN_FIELDS & set(match), sorted(match)
        city = cities[uuid.UUID(match["profile_ref"])]
        if city and city not in match["coarse_location"]:
            assert city not in json.dumps(match), f"{city} leaked into {match['display_name']}"
            assert city not in json.dumps(outcome.trace.retrieval_filter)


def _cases(expect: str) -> list[tuple[SuggestedSearch, RoleCode]]:
    return [
        (search, role)
        for search in suggested_searches()
        if search.expect == expect
        for role in sorted(search.roles, key=lambda r: r.value)
    ]


_WITH_CANDIDATES: Final = _cases("CANDIDATES")
_NO_MATCH: Final = _cases("NO_MATCH")


def test_the_demo_searches_file_names_both_outcomes() -> None:
    assert _WITH_CANDIDATES and _NO_MATCH
    assert _hero().must_include == (HERO_ENGINEER, HERO_ACADEMIC)
    assert set(_hero().must_exclude) == NON_CONSENTED
    for search in suggested_searches():
        assert search.roles <= set(SEARCH_ROLES), search.id


@pytest.mark.integration
@pytest.mark.parametrize(
    ("search", "role"),
    _WITH_CANDIDATES,
    ids=[f"{s.id}-{r.value}" for s, r in _WITH_CANDIDATES],
)
def test_a_demo_search_with_candidates_returns_who_it_names(
    db: Session, search: SuggestedSearch, role: RoleCode
) -> None:
    result = _as_result(_match(db, role, search.requirement).envelope.result)
    assert set(search.must_include) <= _names(result), sorted(_names(result))
    assert not set(search.must_exclude) & _names(result)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("search", "role"), _NO_MATCH, ids=[f"{s.id}-{r.value}" for s, r in _NO_MATCH]
)
def test_a_demo_search_nobody_fits_returns_nobody_and_says_why(
    db: Session, search: SuggestedSearch, role: RoleCode
) -> None:
    outcome = _match(db, role, search.requirement)
    result = _as_result(outcome.envelope.result)
    assert result["matches"] == []
    assert "consented profiles in your scope" in result["no_match"]["reason"]
    assert outcome.trace.model_used is None and outcome.trace.fallback is False


# ---------------------------------------------------------------------------
# Part 3: HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request sessions and audit writes join a transaction rolled back."""
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

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


def _as(client: TestClient, role: RoleCode, session: Session) -> None:
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))


@pytest.mark.integration
@pytest.mark.parametrize("role", DENIED_ROLES)
def test_a_role_without_the_diaspora_permissions_gets_a_deny_not_a_crash(
    api: tuple[TestClient, Session], role: RoleCode
) -> None:
    client, session = api
    _as(client, role, session)
    for response in (
        client.post("/v1/ai/diaspora/match", json={"requirement": _hero().requirement}),
        client.get("/v1/diaspora"),
    ):
        assert response.status_code == 403, response.text
        body = response.json()
        assert body["code"] == "permission_denied"
        assert "display_name" not in response.text


@pytest.mark.integration
def test_the_hero_search_over_http_carries_coarse_location_and_consent_only(
    api: tuple[TestClient, Session],
) -> None:
    client, session = api
    _as(client, RoleCode.DIASPORA_OFFICER, session)

    response = client.post("/v1/ai/diaspora/match", json={"requirement": _hero().requirement})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert {HERO_ENGINEER, HERO_ACADEMIC} <= _names(result)
    assert not NON_CONSENTED & _names(result)
    assert NOT_GIVEN_ENGINEER not in response.text and WITHDRAWN_ACADEMIC not in response.text
    for match in result["matches"]:
        assert not FORBIDDEN_FIELDS & set(match)
        assert match["consent_status"] in {"GIVEN_CONTACTABLE", "GIVEN_DIRECTORY_ONLY"}


@pytest.mark.integration
def test_no_contact_or_outreach_action_exists_anywhere(api: tuple[TestClient, Session]) -> None:
    client, _session = api
    paths: dict[str, dict[str, Any]] = client.get("/openapi.json").json()["paths"]

    diaspora = {path: sorted(methods) for path, methods in paths.items() if "diaspora" in path}
    assert diaspora == {"/v1/diaspora": ["get"], "/v1/ai/diaspora/match": ["post"]}
    contact_like = re.compile(r"contact|outreach|invite|notify|message|email|approach", re.I)
    assert not [path for path in paths if contact_like.search(path)]


@pytest.mark.integration
def test_the_overview_counts_consented_profiles_only(api: tuple[TestClient, Session]) -> None:
    client, session = api
    _as(client, RoleCode.TRADE_OFFICER, session)

    response = client.get("/v1/diaspora")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "searchable_count",
        "contactable_count",
        "directory_only_count",
        "suggested_searches",
        "selection",
        "measured_at",
    }

    def count(*statuses: ConsentStatus) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(DiasporaProfile)
                .where(
                    DiasporaProfile.consent_status.in_(statuses),
                    DiasporaProfile.is_tombstoned.is_(False),
                )
            )
            or 0
        )

    assert body["contactable_count"] == count(ConsentStatus.GIVEN_CONTACTABLE)
    assert body["directory_only_count"] == count(ConsentStatus.GIVEN_DIRECTORY_ONLY)
    assert body["searchable_count"] == count(*CONSENT_SEARCHABLE)
    assert body["searchable_count"] < int(
        session.scalar(select(func.count()).select_from(DiasporaProfile)) or 0
    )
    assert HERO_ID in {search["id"] for search in body["suggested_searches"]}
    assert set(body["suggested_searches"][0]) == {"id", "requirement", "expect"}
