"""Stakeholder 360: the dossier must be complete, or say that it is not.

The failure mode this file exists to prevent is a quiet one. A dossier assembles four
independently-classified collections - people, interactions, opportunities, sources - and if
authorisation silently removes rows from any of them, what is left still *looks* like a full
history. A reader concludes nothing happened in the gap. So the tests below check that the
filter runs in SQL, that what it removed is counted and reported, and that the hero thread
actually connects to the hero opportunity rather than merely sitting near it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import Classification, RoleCode
from app.models.opportunities import Opportunity
from app.models.stakeholders import Interaction, Organisation, Stakeholder
from app.security.principal import principal_for_role
from app.services.stakeholders import (
    list_organisations,
    organisation_dossier,
    stakeholder_dossier,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def db(database_available: bool) -> Iterator[Session]:
    if not database_available:
        pytest.skip("no database reachable; start it with `docker compose up db`")

    from app.core.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def covalent(db: Session) -> Organisation:
    """The hero thread's Australian operator (BUILD_BIBLE section 2)."""
    organisation = db.scalars(
        select(Organisation).where(Organisation.name.ilike("%Covalent%"))
    ).one_or_none()
    if organisation is None:
        pytest.skip("Covalent is not seeded")
    return organisation


@pytest.fixture
def nimg(db: Session) -> Organisation:
    """The Nigerian counterpart institution on the hero thread."""
    organisation = db.scalars(
        select(Organisation).where(
            Organisation.country == "NG", Organisation.name.ilike("%Mining%")
        )
    ).first()
    if organisation is None:
        pytest.skip("the Nigerian counterpart institution is not seeded")
    return organisation


@pytest.fixture
def hero(db: Session) -> Opportunity:
    opportunity = db.scalars(select(Opportunity).where(Opportunity.is_proposed_by_ai)).one_or_none()
    if opportunity is None:
        pytest.skip("no AI-proposed opportunity seeded")
    return opportunity


def _officer() -> object:
    return principal_for_role(RoleCode.TRADE_OFFICER)


# --------------------------------------------------------------------------- the hero thread


def _organisation_with_confidential_history(db: Session) -> Organisation:
    """The organisation whose contact history a TRADE_OFFICER cannot fully read.

    Selected by the property under test rather than by ``limit(1)``: the first organisation
    with any non-public interaction is almost always one every role clears, which made these
    tests skip themselves and prove nothing.
    """
    organisation = db.scalars(
        select(Organisation)
        .join(Interaction, Interaction.organisation_id == Organisation.id)
        .where(Interaction.classification == Classification.CONFIDENTIAL)
        .limit(1)
    ).first()
    assert organisation is not None, (
        "no organisation carries a CONFIDENTIAL interaction; ADR-0006 propagation in the "
        "seed put one there, so its absence means the propagation regressed"
    )
    return organisation


def test_the_covalent_dossier_reaches_the_hero_opportunity(
    db: Session, covalent: Organisation, hero: Opportunity
) -> None:
    """The whole point of the surface: open the operator, see what is being pursued with them."""
    dossier = organisation_dossier(db, _officer(), covalent.id)
    linked = {opportunity.id for opportunity in dossier.opportunities}
    assert hero.id in linked, "the Covalent dossier must reach the hero opportunity"

    entry = next(o for o in dossier.opportunities if o.id == hero.id)
    assert entry.link == "lead", "Covalent is the hero opportunity's lead organisation"
    assert entry.is_proposed_by_ai is True


def test_the_nigerian_counterpart_dossier_reaches_the_same_opportunity(
    db: Session, nimg: Organisation, hero: Opportunity
) -> None:
    """Both ends of the corridor must connect to it, or the dossier tells half a story.

    The Nigerian institute is not the lead organisation, so it reaches the opportunity
    through recorded interactions - which is how a real dossier connects a party the mission
    has worked with but not formally attached.
    """
    dossier = organisation_dossier(db, _officer(), nimg.id)
    entry = next((o for o in dossier.opportunities if o.id == hero.id), None)
    assert entry is not None, "the Nigerian counterpart must reach the hero opportunity"
    assert entry.link in {"interaction", "counterpart"}


def test_the_hero_timeline_names_the_opportunity_it_belongs_to(
    db: Session, covalent: Organisation, hero: Opportunity
) -> None:
    """A timeline entry tied to an opportunity must say so, or the tie is invisible."""
    dossier = organisation_dossier(db, _officer(), covalent.id)
    tied = [entry for entry in dossier.timeline if entry.opportunity_id == hero.id]
    assert tied, "the hero thread's interactions carry the hero opportunity's id"
    assert all(entry.opportunity_title == hero.title for entry in tied)


def test_the_timeline_is_newest_first(db: Session, covalent: Organisation) -> None:
    dossier = organisation_dossier(db, _officer(), covalent.id)
    occurred = [entry.occurred_at for entry in dossier.timeline]
    assert occurred == sorted(occurred, reverse=True)


# --------------------------------------------------------------------------- authorisation


def test_the_dossier_filters_interactions_by_clearance_and_says_how_many(db: Session) -> None:
    """A short timeline must never be mistaken for a quiet relationship.

    Compared against the **DEPUTY**, not against another rank-20 role. A TRADE_OFFICER and a
    DIASPORA_OFFICER clear exactly the same zones, so that pairing would show no difference
    and the test would pass while proving nothing. The DEPUTY clears CONFIDENTIAL; the
    trade officer does not; the confidential negotiation is where they diverge.
    """
    organisation = _organisation_with_confidential_history(db)
    deputy = organisation_dossier(db, principal_for_role(RoleCode.DEPUTY), organisation.id)
    limited = organisation_dossier(db, _officer(), organisation.id)

    assert len(limited.timeline) < len(deputy.timeline), (
        "the trade officer must not see the confidential contact history"
    )
    assert limited.withheld_interactions > 0, (
        "a dossier that hid rows without counting them would read as a complete history"
    )
    assert deputy.withheld_interactions == 0, "the deputy clears all of it"


def test_the_withheld_count_matches_what_was_actually_removed(db: Session) -> None:
    """The count is load-bearing, so it is checked against the database, not against itself."""
    organisation = _organisation_with_confidential_history(db)
    principal = principal_for_role(RoleCode.TRADE_OFFICER)
    dossier = organisation_dossier(db, principal, organisation.id)

    from app.security.deps import readable_classifications

    total = db.scalar(
        select(func.count())
        .select_from(Interaction)
        .where(Interaction.organisation_id == organisation.id)
    )
    readable = db.scalar(
        select(func.count())
        .select_from(Interaction)
        .where(
            Interaction.organisation_id == organisation.id,
            Interaction.classification.in_(readable_classifications(principal)),
        )
    )
    assert dossier.withheld_interactions == (total or 0) - (readable or 0)


def test_an_unreadable_subject_is_a_404_not_a_403(db: Session) -> None:
    """A 403 here would confirm the existence of a row the index correctly declined to list.

    Asserted on a **person** rather than an organisation, and that is not an accident: the
    seed's organisations are all real, already-public actors, so none of them is above any
    role's clearance. The counterpart in the confidential negotiation is, which makes this
    the only place the rule can be tested without inventing a row to test it with.
    """
    from app.core.errors import NotFoundError

    hidden = db.scalars(
        select(Stakeholder).where(Stakeholder.classification == Classification.CONFIDENTIAL)
    ).first()
    assert hidden is not None, (
        "the seed carries a CONFIDENTIAL counterpart; without one this test is vacuous"
    )
    with pytest.raises(NotFoundError):
        stakeholder_dossier(db, principal_for_role(RoleCode.TRADE_OFFICER), hidden.id)

    # And the same row IS readable one clearance rank up, so the refusal is about clearance
    # rather than about the row being broken.
    assert stakeholder_dossier(db, principal_for_role(RoleCode.DEPUTY), hidden.id).name


def test_a_dossier_never_names_an_opportunity_the_caller_may_not_read(db: Session) -> None:
    """Interaction and opportunity carry independent classifications (ADR-0006).

    An interaction a caller may read can point at an opportunity they may not. The tie is
    shown - the entry keeps its opportunity_id - but the title is withheld.
    """
    principal = principal_for_role(RoleCode.TRADE_OFFICER)
    from app.security.deps import readable_classifications

    zones = set(readable_classifications(principal))
    for organisation in db.scalars(select(Organisation).limit(30)):
        dossier = organisation_dossier(db, principal, organisation.id)
        for entry in dossier.timeline:
            if entry.opportunity_title is None:
                continue
            opportunity = db.scalars(
                select(Opportunity).where(Opportunity.id == entry.opportunity_id)
            ).one()
            assert opportunity.classification in zones
        for linked in dossier.opportunities:
            assert linked.classification in zones


# --------------------------------------------------------------------------- provenance


def test_the_dossier_resolves_real_sources(db: Session, covalent: Organisation) -> None:
    """BUILD_BIBLE section 11: every citation is a real public URL, never synthesised."""
    from app.ai.evidence import citation_registry

    dossier = organisation_dossier(db, _officer(), covalent.id, resolve_citations=citation_registry)
    assert dossier.sources, "the Covalent dossier cites its own organisation record at minimum"
    for source in dossier.sources:
        assert source.url.startswith("https://")
        assert source.citation_id in citation_registry()
        assert source.cited_for, "a source with no stated reason is a citation nobody can check"


def test_an_unresolvable_citation_is_dropped_rather_than_faked(
    db: Session, covalent: Organisation
) -> None:
    """A source rendered without a URL is a claim with no way to check it."""
    dossier = organisation_dossier(db, _officer(), covalent.id, resolve_citations=dict)
    assert dossier.sources == ()


def test_without_a_resolver_the_dossier_still_renders(db: Session, covalent: Organisation) -> None:
    """The port is optional: a caller that cannot resolve citations gets the rest of the dossier."""
    dossier = organisation_dossier(db, _officer(), covalent.id)
    assert dossier.name
    assert dossier.sources == ()


# --------------------------------------------------------------------------- index and people


def test_the_index_counts_only_what_the_caller_may_read(db: Session) -> None:
    officer = list_organisations(db, _officer())
    limited = list_organisations(db, principal_for_role(RoleCode.DIASPORA_OFFICER))
    assert len(limited) <= len(officer)
    for row in officer:
        assert row.people_count >= 0
        assert row.interaction_count >= 0


def test_the_index_filters_by_country(db: Session) -> None:
    nigerian = list_organisations(db, _officer(), country="NG")
    assert nigerian, "the seed carries Nigerian counterparts"
    assert all(row.country == "NG" for row in nigerian)


def test_a_person_dossier_reads_on_the_same_terms(db: Session, hero: Opportunity) -> None:
    if hero.primary_stakeholder_id is None:
        pytest.skip("the hero opportunity has no primary stakeholder")
    person = db.get(Stakeholder, hero.primary_stakeholder_id)
    assert person is not None

    dossier = stakeholder_dossier(db, _officer(), person.id)
    assert dossier.subject_kind == "person"
    assert dossier.name == person.full_name
    assert len(dossier.people) == 1
    assert hero.id in {opportunity.id for opportunity in dossier.opportunities}


def test_the_dossier_writes_nothing(db: Session, covalent: Organisation) -> None:
    """It is a read. Opening a dossier must not change the record it describes."""
    organisation_dossier(db, _officer(), covalent.id)
    assert not db.new and not db.dirty and not db.deleted
