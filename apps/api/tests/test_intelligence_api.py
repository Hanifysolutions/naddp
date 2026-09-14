"""``/v1/intelligence`` over HTTP: grounding, honesty, and two gates that both bite.

The morning brief is winning moment #1, and the claim it makes is narrow and checkable:
*this is not a chatbot*. Three properties carry that claim, and each of them is a test
below rather than a sentence in a deck.

1. **The brief is grounded.** Every item carries evidence, every piece of evidence carries
   a citation id, and at least one of them resolves to a public ``https://`` page an
   Ambassador can open in front of the room. An item that cannot cite itself is not a
   brief item, and a suite that does not check that is not checking the product.
2. **The brief is honest about itself** (``docs/OPEN_QUESTIONS.md`` Q-17). Exactly one item
   on the Ambassador's brief rests on a corridor the platform *proposed* rather than one a
   source *reported*, and it must render at strictly lower confidence than the evidenced
   items beside it. The gap is computed from the payload here, never hard-coded: writing
   ``61 < 92`` would keep passing after somebody inverted the flag.
3. **Two officers get materially different briefs, decided by the server.** Not one brief
   behind a mask -- different rows, built from different material, asserted as a set
   difference over item ids.

Both authorisation gates are exercised and neither implies the other (ADR-0003 rule 3).
``CONSULAR_OFFICER`` is refused at the *permission* gate even though the seed holds a brief
scoped to its own desk, and the ``CONSULAR_SENSITIVE`` brief is unreachable for the four
roles that do hold ``read:intelligence`` -- for two of them because of the role-scope
predicate alone, for the other two because of the clearance predicate as well.

Every test runs inside a transaction this module rolls back, including the audit
middleware's own session -- ``audit_events`` is append-only (ADR-0004), so the 403
assertions below would otherwise deposit permanent ``access.denied`` rows in the demo's
timeline, one per run, forever.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import (
    BriefItemType,
    Classification,
    OpportunityStage,
    PolicyResult,
    RoleCode,
)
from app.models.ai import AiTrace
from app.models.governance import AuditEvent
from app.models.intelligence import BriefItem
from app.models.opportunities import Opportunity
from app.security.matrix import ROLE_PERMISSIONS
from app.security.permissions import Permission
from app.security.principal import (
    demo_persona,
    principal_for_role,
    readable_classifications_for,
)
from app.security.session import SESSION_COOKIE_NAME, issue_session
from app.services.session import ensure_persona_user

pytestmark = pytest.mark.integration

BRIEF_URL: Final[str] = "/v1/intelligence/brief"
HISTORY_URL: Final[str] = "/v1/intelligence/briefs"
TEST_SECTOR: Final[str] = "TEST_INTELLIGENCE_API"

#: The roles ``read:intelligence`` admits, per the Q-02b grant table. Stated here rather
#: than derived from ``ROLE_PERMISSIONS``, on purpose: deriving the expectation from the
#: matrix the route is enforced by would make every assertion below a tautology. The one
#: test that *does* read the matrix is
#: :func:`test_exactly_four_roles_hold_the_intelligence_read`, which exists so that
#: widening the grant breaks this module's premise loudly instead of silently.
PERMITTED_ROLES: Final[tuple[RoleCode, ...]] = (
    RoleCode.AMBASSADOR,
    RoleCode.DEPUTY,
    RoleCode.TRADE_OFFICER,
    RoleCode.DIASPORA_OFFICER,
)

#: The deny state. ``CONSULAR_OFFICER`` is refused a brief of its own (see Q-02b, and the
#: docstring on :func:`test_a_consular_officer_is_refused_even_its_own_brief`); ``ADMIN``
#: holds no content read at all.
REFUSED_ROLES: Final[tuple[RoleCode, ...]] = (RoleCode.CONSULAR_OFFICER, RoleCode.ADMIN)

#: U+00B7 MIDDLE DOT, the separator the Gateway renders between the segments of a section
#: 4a badge. Written as an escape so this file stays pure ASCII and cannot be corrupted by
#: a tool that guesses an encoding. The tests treat the badge as opaque: the separator is
#: evidence that a *rendered* badge arrived, never an invitation to split on it.
BADGE_SEPARATOR: Final[str] = "\u00b7"


@pytest.fixture
def api(database_available: bool) -> Iterator[tuple[TestClient, Session]]:
    """A client whose request and audit sessions both roll back."""
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
    """Act as ``role``: provision its persona row and set the signed session cookie."""
    ensure_persona_user(session, demo_persona(role))
    session.flush()
    client.cookies.set(SESSION_COOKIE_NAME, issue_session(role))


def _brief(client: TestClient) -> dict[str, Any]:
    """Fetch the morning brief and assert it came back."""
    response = client.get(BRIEF_URL)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _history(client: TestClient, **params: int) -> dict[str, Any]:
    """Fetch the history rail and assert it came back."""
    response = client.get(HISTORY_URL, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _readable_zones(role: RoleCode) -> set[str]:
    """The zone codes ``role`` may read, read from the same source the ``SELECT`` uses."""
    return {zone.value for zone in readable_classifications_for(principal_for_role(role))}


def _today() -> str:
    """The API's current UTC date, ISO-formatted, as the payload renders it."""
    return datetime.now(UTC).date().isoformat()


# ---------------------------------------------------------------------------
# 1. Deny by default
# ---------------------------------------------------------------------------


def test_exactly_four_roles_hold_the_intelligence_read(
    api: tuple[TestClient, Session],
) -> None:
    """The premise every other test in this module rests on, pinned once.

    ``PERMITTED_ROLES`` and ``REFUSED_ROLES`` are written down rather than derived, so a
    future widening of ``read:intelligence`` would leave the deny tests below asserting
    against a role that is no longer refused -- and they would still pass, because they
    would simply stop being run against the role that changed. This test is the tripwire:
    it reads the matrix and fails the moment the grant stops matching the Q-02b table.
    """
    del api  # no request is made; the fixture is here only to skip without a database
    holders = {
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.READ_INTELLIGENCE in permissions
    }
    assert holders == set(PERMITTED_ROLES)
    assert holders.isdisjoint(REFUSED_ROLES)


def test_neither_route_answers_a_caller_with_no_session(
    api: tuple[TestClient, Session],
) -> None:
    """No session, no brief. There is no anonymous tier and no public morning brief."""
    client, _ = api
    for url in (BRIEF_URL, HISTORY_URL):
        response = client.get(url)
        assert response.status_code == 403, url
        assert response.json()["reason"] == "no_session", url


def test_neither_route_answers_a_forged_cookie(api: tuple[TestClient, Session]) -> None:
    """A cookie this build did not sign is indistinguishable from no cookie at all.

    The refusal must not distinguish forged from absent from expired: "your cookie is
    forged" and "your cookie expired" are different invitations (``app.security.deps``).
    """
    client, _ = api
    client.cookies.set(SESSION_COOKIE_NAME, "forged.token")
    for url in (BRIEF_URL, HISTORY_URL):
        assert client.get(url).status_code == 403, url


def test_a_consular_officer_is_refused_even_its_own_brief(
    api: tuple[TestClient, Session],
) -> None:
    """The deny state, and it is deliberate -- do not "fix" it by widening the gate.

    The seed holds a ``CONSULAR_OFFICER``-scoped brief for today, and this role still gets
    a 403. That reads like a bug and is not one: a brief is *intelligence*, and Q-02b grants
    ``read:intelligence`` to the four relationship-facing roles only. A consular officer's
    need-to-know is deep rather than broad (ADR-0006), so the correct behaviour is a refusal
    at gate 1 -- before any row is selected, and before the existence of that brief is
    confirmed to the caller.

    The temptation when the demo shows an empty screen is to widen the dependency to
    ``read:command``. That would also put ``ADMIN`` -- which holds no content read
    whatsoever -- on an intelligence surface, which is the exact wall ADR-0003's second
    principle exists to keep standing. If the product decision changes it changes in
    ``app.security.matrix`` and in Q-02b, and this test is where it is argued.
    """
    client, session = api
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    response = client.get(BRIEF_URL)
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "permission_denied"
    assert Permission.READ_INTELLIGENCE.value in body["missing_permissions"]
    assert body["actor_role"] == RoleCode.CONSULAR_OFFICER.value


def test_admin_is_refused_the_brief_entirely(api: tuple[TestClient, Session]) -> None:
    """Separation of duties: platform administration confers no sight of content.

    ``ADMIN`` reaches the command centre and receives six nulls there. Here it does not
    reach the surface at all, because there is no aggregate-shaped answer to give it -- a
    brief *is* content. Both routes refuse it, so the trace drawer embedded in the brief
    cannot become a side channel around the compartment wall either.
    """
    client, session = api
    _as(client, RoleCode.ADMIN, session)

    for url in (BRIEF_URL, HISTORY_URL):
        response = client.get(url)
        assert response.status_code == 403, url
        assert response.json()["code"] == "permission_denied", url


# ---------------------------------------------------------------------------
# 2. The four permitted roles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [RoleCode.AMBASSADOR, RoleCode.TRADE_OFFICER])
def test_a_role_with_a_brief_of_its_own_receives_that_one(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """Their desk's brief, not the mission's -- the tie-break inside ``latest_brief``.

    Both briefs are dated today, so freshness cannot separate them. ``role_scope IS NULL``
    ascending is what puts the caller's own desk ahead of the mission-wide row, and it is
    the order a reader expects: my desk first, the mission behind it.
    """
    client, session = api
    _as(client, role, session)
    body = _brief(client)

    assert body["is_mission_wide"] is False
    assert body["role_scope"] == role.value


@pytest.mark.parametrize("role", [RoleCode.DEPUTY, RoleCode.DIASPORA_OFFICER])
def test_a_role_with_no_brief_of_its_own_receives_the_mission_wide_one(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """The fallback that stops the flagship screen dead-ending (Q-W3.1b).

    Four roles hold ``read:intelligence`` and only two have a brief of their own in the
    seed. Without the mission-wide fallback ``DEPUTY`` and ``DIASPORA_OFFICER`` would get a
    404 on winning moment #1, which is precisely the dead end ``CLAUDE.md`` rule 2.5
    forbids. The reader is *told* which one they got rather than left to infer it, which is
    what ``is_mission_wide`` is for.
    """
    client, session = api
    _as(client, role, session)
    body = _brief(client)

    assert body["is_mission_wide"] is True
    assert body["role_scope"] is None


def test_two_officers_receive_materially_different_briefs(
    api: tuple[TestClient, Session],
) -> None:
    """The acceptance claim, asserted as a set difference rather than as a vibe.

    Same URL, same build, two officers, two different sets of items -- decided by the
    server. This is the property that separates "role-aware by construction" from "the UI
    hides a card": the briefs are different *rows*, generated from different material, so
    neither item set is a subset of the other.
    """
    client, session = api

    _as(client, RoleCode.AMBASSADOR, session)
    ambassador = {item["id"] for item in _brief(client)["items"]}
    _as(client, RoleCode.TRADE_OFFICER, session)
    trade = {item["id"] for item in _brief(client)["items"]}

    assert ambassador
    assert trade
    assert ambassador != trade
    assert ambassador - trade
    assert trade - ambassador


# ---------------------------------------------------------------------------
# 3. Clearance, not just permission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", PERMITTED_ROLES)
def test_no_intelligence_reader_ever_receives_the_consular_brief(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """Two independent predicates guard the consular brief, and both are needed.

    ``_visible_briefs`` puts *scope* and *clearance* in the same ``WHERE`` clause, and which
    one does the work depends on who is asking:

    * ``AMBASSADOR`` and ``DEPUTY`` hold the ``consular`` compartment, so the clearance
      predicate happily admits a ``CONSULAR_SENSITIVE`` row for them. Only the ROLE-SCOPE
      predicate -- ``role_scope = my role OR role_scope IS NULL`` -- keeps the consular
      officer's desk out of their brief: a brief assembled for another desk is not theirs
      to read however senior they are.
    * ``TRADE_OFFICER`` and ``DIASPORA_OFFICER`` hold no compartment, so for them the
      CLEARANCE predicate excludes the same row a second time.

    Delete either predicate and half of these four parameters still pass, which is exactly
    why this runs over all four rather than over the convenient one.
    """
    client, session = api
    _as(client, role, session)
    body = _brief(client)

    assert body["role_scope"] != RoleCode.CONSULAR_OFFICER.value
    assert body["classification"] != Classification.CONSULAR_SENSITIVE.value


@pytest.mark.parametrize("role", PERMITTED_ROLES)
def test_every_returned_item_sits_in_a_zone_the_caller_may_read(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """``briefs.classification`` does not vouch for ``brief_items.classification``.

    The parent's zone is a stored column that nothing recomputes as the maximum over its
    children, so a ``MISSION_INTERNAL`` brief could carry an item in a zone the reader does
    not hold. ``readable_items`` therefore applies the clearance predicate to the items
    independently and in SQL, and an item the caller may not read is *absent* rather than
    redacted in place -- a redaction is still a disclosure that something is there.
    """
    client, session = api
    _as(client, role, session)
    body = _brief(client)

    readable = _readable_zones(role)
    assert body["classification"] in readable
    for item in body["items"]:
        assert item["classification"] in readable, item["id"]


# ---------------------------------------------------------------------------
# 4. Grounding -- winning moment #1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", PERMITTED_ROLES)
def test_every_item_carries_evidence_and_every_citation_is_identified(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """An uncited item is the chatbot failure mode, and it has to be unreachable.

    ``generate_brief`` refuses to persist an item with no evidence or one citing an id that
    is not ``VERIFIED`` in ``data/demo-seed/citations.json``; the projection drops an
    evidence entry carrying no ``citation_id`` at all, because the citation id is what makes
    a claim checkable and an uncheckable citation is worse than none (``CLAUDE.md`` 2.6).
    Both halves are asserted over the wire, which is where the Ambassador actually sees them.
    """
    client, session = api
    _as(client, role, session)
    body = _brief(client)

    assert body["items"], f"{role.value} received a brief with no items"
    for item in body["items"]:
        assert item["evidence"], f"{item['id']} carries no evidence"
        for entry in item["evidence"]:
            assert entry["citation_id"], f"{item['id']} carries an unidentified citation"


def test_at_least_one_citation_resolves_to_a_public_https_page(
    api: tuple[TestClient, Session],
) -> None:
    """The demo moment: the Ambassador clicks a citation and a real page opens.

    ``url`` is nullable by contract -- a seeded row may carry a quote and no openable page,
    and the UI renders plain text rather than an anchor with an empty href. What must never
    happen is a brief on which *nothing* resolves, because then the citations are decoration
    and the "not a chatbot" claim is unfalsifiable.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    body = _brief(client)

    urls = [entry["url"] for item in body["items"] for entry in item["evidence"]]
    assert any(url is not None and url.startswith("https://") for url in urls)


# ---------------------------------------------------------------------------
# 5. Q-17 -- the honesty gap. The most important section in this file.
# ---------------------------------------------------------------------------


def test_the_ai_proposed_item_is_less_confident_than_every_evidenced_item(
    api: tuple[TestClient, Session],
) -> None:
    """The payoff of winning moment #1, computed from the payload rather than asserted at.

    No public source links an Australian lithium operator to Nigeria, so the hero corridor
    is a synthesis the platform *proposed* and not a fact any source *reported*
    (``docs/OPEN_QUESTIONS.md`` Q-17). It therefore has to render at visibly lower
    confidence than the evidenced signals beneath it. A platform that cannot show the
    difference between what it read and what it inferred is one nobody should trust with a
    bilateral relationship.

    Both sides of the comparison are derived from the response. Writing ``61 < 92`` would
    keep passing after somebody inverted the flag, renumbered the seed, or started dividing
    the column by 100 -- and every one of those is a regression this test exists to catch.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    items = _brief(client)["items"]

    proposed = [item for item in items if item["is_proposed_by_ai"]]
    assert len(proposed) == 1, "the Ambassador's brief carries exactly one proposed corridor"

    evidenced = [
        item["confidence"]
        for item in items
        if not item["is_proposed_by_ai"] and item["confidence"] is not None
    ]
    assert evidenced, "nothing to compare against: the brief carries no scored evidenced item"

    assert proposed[0]["confidence"] is not None
    assert proposed[0]["confidence"] < max(evidenced)


@pytest.mark.parametrize("role", PERMITTED_ROLES)
def test_an_item_is_flagged_ai_proposed_only_when_it_names_an_opportunity(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """The flag is joined from ``opportunities.is_proposed_by_ai``, so it needs the join key.

    ``brief_items`` carries no provenance column of its own. The flag can only be true for
    an item that actually points at an opportunity row; a true flag on an item with a null
    ``opportunity_id`` would mean the projection had invented provenance it cannot source,
    which is the one thing a provenance flag must never do.
    """
    client, session = api
    _as(client, role, session)

    for item in _brief(client)["items"]:
        if item["is_proposed_by_ai"]:
            assert item["opportunity_id"] is not None, item["id"]


def test_an_opportunity_item_is_not_ai_proposed_merely_for_being_one(
    api: tuple[TestClient, Session],
) -> None:
    """A regression guard against the naive rule ``item_type == OPPORTUNITY``.

    Every ``OPPORTUNITY`` item in the seed happens to be AI-proposed, so a projection that
    keyed the flag off the item type -- or off a confidence threshold, or off the headline
    text -- would pass every other test in this file. This one inserts the case the seed
    lacks: an item pointing at a human-raised opportunity. The flag must come back false
    while the type still reads ``OPPORTUNITY``, and the genuinely proposed item beside it
    must still read true.

    The fixture row borrows the evidence of a real item on the same brief rather than
    inventing a citation, so nothing here asserts against a source that does not exist
    (``CLAUDE.md`` rule 2.6). It lives and dies inside the rolled-back transaction.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)

    brief_id = uuid.UUID(_brief(client)["id"])
    donor = session.scalars(
        select(BriefItem).where(BriefItem.brief_id == brief_id).order_by(BriefItem.position)
    ).first()
    assert donor is not None, "the Ambassador's brief has no item to borrow evidence from"
    highest = session.scalar(
        select(func.max(BriefItem.position)).where(BriefItem.brief_id == brief_id)
    )

    human_raised = Opportunity(
        title="Officer-raised corridor opportunity",
        description="Fixture row: an officer saw this; the platform did not propose it.",
        stage=OpportunityStage.QUALIFIED,
        classification=Classification.MISSION_INTERNAL,
        sector_code=TEST_SECTOR,
        country_focus="AU",
        score=Decimal("70.00"),
    )
    session.add(human_raised)
    session.flush()
    assert human_raised.is_proposed_by_ai is False

    session.add(
        BriefItem(
            brief_id=brief_id,
            position=(highest or 0) + 1,
            item_type=BriefItemType.OPPORTUNITY,
            classification=Classification.MISSION_INTERNAL,
            headline="An opportunity an officer raised.",
            body="Reported by a named officer, not synthesised by the platform.",
            so_what="Provenance is the point of this fixture, not its content.",
            confidence=Decimal("61.00"),
            opportunity_id=human_raised.id,
            evidence=list(donor.evidence),
        )
    )
    session.flush()

    items = _brief(client)["items"]
    added = [item for item in items if item["opportunity_id"] == str(human_raised.id)]
    assert len(added) == 1
    assert added[0]["item_type"] == BriefItemType.OPPORTUNITY.value
    assert added[0]["is_proposed_by_ai"] is False

    still_proposed = [item for item in items if item["is_proposed_by_ai"]]
    assert len(still_proposed) == 1
    assert still_proposed[0]["item_type"] == BriefItemType.OPPORTUNITY.value
    assert still_proposed[0]["id"] != added[0]["id"]


# ---------------------------------------------------------------------------
# 6. Wire types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", PERMITTED_ROLES)
def test_confidence_arrives_as_a_json_number_on_the_zero_to_one_hundred_scale(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """Regression guard for the Decimal-serialises-as-a-string bug (W2_STATUS item 3).

    ``brief_items.confidence`` is ``Numeric(5, 2)``, and Pydantic v2 renders a ``Decimal``
    as a JSON *string* -- which is how ``score?: string | null`` ended up in the generated
    client for opportunities. A client that has to parse a number before it can draw a bar
    will eventually draw the wrong bar, so the cast to ``float`` happens once, in the
    router's projection, and is asserted here on the wire.

    The range assertion pins the *scale* at the same time: the column is 0-100 and stays
    0-100. A future projection that helpfully divided by 100 would still be a number and
    would still pass a type check, and the badge would quietly read 0.61%.
    """
    client, session = api
    _as(client, role, session)

    for item in _brief(client)["items"]:
        value = item["confidence"]
        if value is None:
            continue
        assert not isinstance(value, str), f"{item['id']} sent confidence as a string"
        assert isinstance(value, float | int)
        assert not isinstance(value, bool)
        assert 0 <= value <= 100, f"{item['id']} sent confidence off the 0-100 scale"


def test_is_today_is_a_boolean_that_agrees_with_the_brief_date(
    api: tuple[TestClient, Session],
) -> None:
    """``is_today`` is what stops a stale brief being read as this morning's.

    ``latest_brief`` returns the most recent brief rather than strictly today's, so that
    running the demo on any day but the seed date shows something true instead of a 404.
    The flag is the other half of that bargain: a brief from last week is *labelled* as one.
    A truthy-but-not-boolean value would render as "today" in a JSX conditional whatever the
    date said, so the type is asserted alongside the value.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    body = _brief(client)

    assert isinstance(body["is_today"], bool)
    assert body["is_today"] == (body["brief_date"] == _today())


# ---------------------------------------------------------------------------
# 7. The embedded routing decision (BUILD_BIBLE section 4a)
# ---------------------------------------------------------------------------


def test_a_trace_holder_receives_the_badge_exactly_as_the_gateway_rendered_it(
    api: tuple[TestClient, Session],
) -> None:
    """The badge is an opaque string, and the API must not have touched it.

    There are five badge shapes with three or four segments, and the first segment is not
    always a classification display name -- so any parsing, here or in the service or in the
    UI, is a guess that will be wrong on some route. The assertion is therefore an exact
    comparison against the ``ai_traces`` column: same string, no split, no re-render, no
    normalisation.

    No model name is asserted. ``ANTHROPIC_MODEL`` is configurable and a test that pins it
    fails on a perfectly correct deployment; what matters is that the route was *recorded*,
    not which model it asked for.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    body = _brief(client)

    trace = body["trace"]
    assert trace is not None, "AMBASSADOR holds read:ai_trace and clears the brief's zone"

    row = session.get(AiTrace, uuid.UUID(trace["trace_id"]))
    assert row is not None
    assert trace["route_badge"] == row.route_badge
    assert trace["route_badge"], "an empty badge renders as 'routing not recorded'"
    assert BADGE_SEPARATOR in trace["route_badge"]
    assert trace["model_route"]
    assert isinstance(trace["fallback"], bool)


@pytest.mark.parametrize("role", PERMITTED_ROLES)
def test_a_trace_is_never_embedded_without_the_id_that_identifies_it(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """``trace_id`` is the fact; ``trace`` is the detail this caller may or may not inspect.

    The two are deliberately independent: a populated ``trace_id`` with a null ``trace``
    means "the routing decision exists and you may not see it", which is a sentence the UI
    can honestly print. The combination forbidden here is the reverse -- an embedded trace
    with no id -- which would leave the drawer unable to link back to
    ``GET /v1/ai/traces/{trace_id}`` and would mean the payload disagreed with itself.
    """
    client, session = api
    _as(client, role, session)
    body = _brief(client)

    if body["trace"] is not None:
        assert body["trace_id"] is not None
        assert body["trace"]["trace_id"] == body["trace_id"]


# ---------------------------------------------------------------------------
# 8. The history rail
# ---------------------------------------------------------------------------


def test_the_history_rail_comes_back_newest_first_and_counts_only_itself(
    api: tuple[TestClient, Session],
) -> None:
    """``total`` counts what was returned, never what exists.

    The difference between "how many briefs exist" and "how many you may read" is exactly
    how many you are not cleared for, and publishing that number would undo the clearance
    predicate the ``SELECT`` carries it for. Ordering is asserted because the rail is read
    top-down: the newest brief has to be the first one.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)
    body = _history(client)

    assert body["items"]
    assert body["total"] == len(body["items"])

    dates = [item["brief_date"] for item in body["items"]]
    assert dates == sorted(dates, reverse=True)


def test_the_history_rail_honours_the_limit(api: tuple[TestClient, Session]) -> None:
    """``limit`` narrows the rail from the top, and narrows ``total`` with it."""
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)

    full = _history(client)
    assert len(full["items"]) >= 2, "seed too thin to prove that a limit narrows anything"

    capped = _history(client, limit=1)
    assert len(capped["items"]) == 1
    assert capped["total"] == 1
    assert capped["items"][0]["id"] == full["items"][0]["id"]


@pytest.mark.parametrize("limit", [0, 51])
def test_the_history_rail_rejects_a_limit_outside_its_band(
    api: tuple[TestClient, Session],
    limit: int,
) -> None:
    """A rejected limit is better than a silently clamped one.

    ``ge=1`` because a zero-length page is a client bug rather than a request, and ``le=50``
    because an unbounded page is how a list endpoint becomes an export. Clamping instead of
    refusing would hand the client a page that does not match what it asked for, and give it
    no way to find out.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)

    response = client.get(HISTORY_URL, params={"limit": limit})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize("role", PERMITTED_ROLES)
def test_the_consular_brief_appears_in_no_permitted_role_history(
    api: tuple[TestClient, Session],
    role: RoleCode,
) -> None:
    """The rail runs through the same ``_visible_briefs`` statement as the brief itself.

    A history endpoint is the classic place for a filter to be applied to the page instead
    of to the query, at which point the consular brief is merely not *rendered*. Asserted
    over all four permitted roles, and over the zone as well as the scope, because the two
    predicates exclude it for different reasons (see section 3).
    """
    client, session = api
    _as(client, role, session)

    readable = _readable_zones(role)
    for item in _history(client)["items"]:
        assert item["role_scope"] != RoleCode.CONSULAR_OFFICER.value
        assert item["classification"] != Classification.CONSULAR_SENSITIVE.value
        assert item["classification"] in readable


def test_is_today_marks_exactly_the_briefs_dated_today(
    api: tuple[TestClient, Session],
) -> None:
    """The rail mixes dates, so the flag has to be computed per row and not per request.

    The seed carries today's briefs and yesterday's published mission brief, so this is the
    one place a per-row ``is_today`` can be shown to be right rather than merely constant.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)

    today = _today()
    rows = _history(client)["items"]
    for item in rows:
        assert item["is_today"] == (item["brief_date"] == today), item["id"]
    assert any(item["is_today"] for item in rows)


# ---------------------------------------------------------------------------
# 9. Documentation and the audit trail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [BRIEF_URL, HISTORY_URL])
def test_both_routes_are_documented_in_the_openapi_schema(
    api: tuple[TestClient, Session],
    url: str,
) -> None:
    """The route and Field descriptions are the docs the web track reads.

    ``packages/contracts`` is generated from this schema and the descriptions survive into
    the generated TypeScript as doc comments. An undocumented route is therefore not a
    cosmetic gap -- it is a contract the client author has to guess at.
    """
    client, _ = api
    operation = client.get("/openapi.json").json()["paths"][url]["get"]

    assert operation["summary"]
    assert operation["description"]
    assert operation["tags"] == ["intelligence"]


def test_reading_the_brief_does_not_write_an_audit_row(
    api: tuple[TestClient, Session],
) -> None:
    """Deliberate: an ordinary brief read is not privileged object access.

    Everything a ``read:intelligence`` holder can reach here is ``MISSION_INTERNAL`` -- the
    caller's own brief and the mission-wide one -- which is below the zone at which
    ``app.audit.middleware``'s ``PRIVILEGED_READ`` rules record anything. Registering this
    route would append a row every time somebody opened the flagship screen, which is the
    row-per-page-view flood that middleware exists to avoid, and the ten rows that matter
    would be buried under it. Denials are still recorded, which is the half that counts.

    If a generated brief ever carries a ``CONFIDENTIAL`` or ``CONSULAR_SENSITIVE`` item that
    a ``read:intelligence`` holder can read, that calculus changes (Q-W3.1c) -- and this
    test is where the decision is written down, and where it would be inverted.
    """
    client, session = api
    _as(client, RoleCode.AMBASSADOR, session)

    session.expire_all()
    before = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
    _brief(client)
    _history(client)
    session.expire_all()
    after = session.scalar(select(func.count()).select_from(AuditEvent)) or 0

    assert after == before


def test_a_refusal_does_write_a_deny_row(api: tuple[TestClient, Session]) -> None:
    """The other half of the bargain, and it is automatic rather than wired here.

    ``middleware.decide()`` tests the denial branch *before* it consults the route registry,
    so a 403 on an unregistered path still leaves an ``access.denied`` row carrying the
    actor, the path and the missing permission. That ordering is why this module needs no
    ``_rule(...)`` of its own to record refusals, and why a reviewer can trust the log to
    hold every refusal in the system rather than every refusal somebody remembered to
    register.
    """
    client, session = api
    _as(client, RoleCode.CONSULAR_OFFICER, session)

    session.expire_all()
    before = session.scalar(select(func.count()).select_from(AuditEvent)) or 0
    assert client.get(BRIEF_URL).status_code == 403
    session.expire_all()
    after = session.scalar(select(func.count()).select_from(AuditEvent)) or 0

    assert after == before + 1

    row = session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).one()
    assert row.policy_result is PolicyResult.DENY
    assert row.actor_role is RoleCode.CONSULAR_OFFICER
    assert Permission.READ_INTELLIGENCE.value in row.payload["missing_permissions"]
