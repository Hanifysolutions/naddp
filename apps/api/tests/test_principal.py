"""``Principal``, clearance ranks and the ADR-0006 read rule.

Pure unit tests. ADR-0006's enforcement table asks for "a table-driven test over role x
zone asserting ``may_read`` for all 24 cells"; :func:`test_may_read_matrix` is that test,
written out cell by cell rather than computed, so it fails when the rule changes rather
than agreeing with whatever the rule has become.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from typing import Final

import pytest

from app.core.config import taxonomy_path
from app.domain.enums import Classification, RoleCode
from app.security.matrix import ROLE_PERMISSIONS
from app.security.principal import (
    CLASSIFICATION_ACCESS,
    CONSULAR_COMPARTMENT,
    DEMO_PERSONAS,
    ROLE_CLEARANCE_RANK,
    ROLE_COMPARTMENTS,
    Principal,
    demo_persona,
    principal_for_role,
    readable_classifications_for,
)

C = Classification
R = RoleCode

#: ADR-0006 and ``docs/OPEN_QUESTIONS.md`` A-01, transcribed.
EXPECTED_RANKS: Final[dict[RoleCode, int]] = {
    R.AMBASSADOR: 40,
    R.DEPUTY: 30,
    R.CONSULAR_OFFICER: 20,
    R.TRADE_OFFICER: 20,
    R.DIASPORA_OFFICER: 20,
    R.ADMIN: 10,
}

#: Only three roles hold the one compartment that exists.
EXPECTED_COMPARTMENTS: Final[dict[RoleCode, frozenset[str]]] = {
    R.AMBASSADOR: frozenset({CONSULAR_COMPARTMENT}),
    R.DEPUTY: frozenset({CONSULAR_COMPARTMENT}),
    R.CONSULAR_OFFICER: frozenset({CONSULAR_COMPARTMENT}),
    R.TRADE_OFFICER: frozenset(),
    R.DIASPORA_OFFICER: frozenset(),
    R.ADMIN: frozenset(),
}

#: All 24 cells of role x zone. ``True`` means the principal may read that zone.
#:
#: The two interesting columns: ``CONSULAR_SENSITIVE`` needs rank 20 *and* the compartment,
#: so seniority alone never opens a case file; ``CONFIDENTIAL`` needs rank 30, so a
#: consular officer cannot read a negotiating position by role alone.
MAY_READ_MATRIX: Final[dict[tuple[RoleCode, Classification], bool]] = {
    (R.AMBASSADOR, C.PUBLIC): True,
    (R.AMBASSADOR, C.MISSION_INTERNAL): True,
    (R.AMBASSADOR, C.CONSULAR_SENSITIVE): True,
    (R.AMBASSADOR, C.CONFIDENTIAL): True,
    (R.DEPUTY, C.PUBLIC): True,
    (R.DEPUTY, C.MISSION_INTERNAL): True,
    (R.DEPUTY, C.CONSULAR_SENSITIVE): True,
    (R.DEPUTY, C.CONFIDENTIAL): True,
    (R.TRADE_OFFICER, C.PUBLIC): True,
    (R.TRADE_OFFICER, C.MISSION_INTERNAL): True,
    (R.TRADE_OFFICER, C.CONSULAR_SENSITIVE): False,
    (R.TRADE_OFFICER, C.CONFIDENTIAL): False,
    (R.CONSULAR_OFFICER, C.PUBLIC): True,
    (R.CONSULAR_OFFICER, C.MISSION_INTERNAL): True,
    (R.CONSULAR_OFFICER, C.CONSULAR_SENSITIVE): True,
    (R.CONSULAR_OFFICER, C.CONFIDENTIAL): False,
    (R.DIASPORA_OFFICER, C.PUBLIC): True,
    (R.DIASPORA_OFFICER, C.MISSION_INTERNAL): True,
    (R.DIASPORA_OFFICER, C.CONSULAR_SENSITIVE): False,
    (R.DIASPORA_OFFICER, C.CONFIDENTIAL): False,
    (R.ADMIN, C.PUBLIC): True,
    (R.ADMIN, C.MISSION_INTERNAL): True,
    (R.ADMIN, C.CONSULAR_SENSITIVE): False,
    (R.ADMIN, C.CONFIDENTIAL): False,
}


# ---------------------------------------------------------------------------
# 1. The taxonomy is the source, and it is loaded intact
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(RoleCode))
def test_clearance_ranks_match_the_taxonomy_file(role: RoleCode) -> None:
    assert ROLE_CLEARANCE_RANK[role] == EXPECTED_RANKS[role]


@pytest.mark.parametrize("role", list(RoleCode))
def test_compartments_match_the_taxonomy_file(role: RoleCode) -> None:
    assert ROLE_COMPARTMENTS[role] == EXPECTED_COMPARTMENTS[role]


def test_loaded_tables_agree_with_the_json_on_disk() -> None:
    """The module must not have drifted from the file it claims to read.

    A second, independent parse of the same document. If this ever fails, one of the two
    is wrong -- and a hard-coded rank in a security module is precisely the failure ADR-0006
    warns about.
    """
    document = json.loads(taxonomy_path("classifications.json").read_text(encoding="utf-8"))
    for entry in document["role_ranks"]:
        role = RoleCode(entry["role"])
        assert ROLE_CLEARANCE_RANK[role] == entry["clearance_rank"]
        assert ROLE_COMPARTMENTS[role] == frozenset(entry["compartments"])
    for entry in document["classifications"]:
        zone = Classification(entry["code"])
        rule = CLASSIFICATION_ACCESS[zone]
        assert rule.rank == entry["rank"]
        assert rule.min_role_rank_to_read == entry["min_role_rank_to_read"]
        assert rule.compartment == entry["compartment"]


def test_only_consular_sensitive_carries_a_compartment() -> None:
    compartmented = {
        zone for zone, rule in CLASSIFICATION_ACCESS.items() if rule.compartment is not None
    }
    assert compartmented == {C.CONSULAR_SENSITIVE}
    assert CLASSIFICATION_ACCESS[C.CONSULAR_SENSITIVE].compartment == CONSULAR_COMPARTMENT


def test_dominance_rank_and_access_rank_are_not_the_same_ordering() -> None:
    """ADR-0006 keeps propagation and access apart; if they collapsed, the ADR is broken."""
    consular = CLASSIFICATION_ACCESS[C.CONSULAR_SENSITIVE]
    confidential = CLASSIFICATION_ACCESS[C.CONFIDENTIAL]
    # CONSULAR_SENSITIVE ranks *below* CONFIDENTIAL for propagation ...
    assert consular.rank < confidential.rank
    # ... yet is harder to reach, because rank alone does not satisfy it.
    assert consular.compartment is not None
    assert confidential.compartment is None


# ---------------------------------------------------------------------------
# 2. may_read, all 24 cells
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "zone", "expected"),
    [
        pytest.param(role, zone, expected, id=f"{role.value}-{zone.value}")
        for (role, zone), expected in MAY_READ_MATRIX.items()
    ],
)
def test_may_read_matrix(role: RoleCode, zone: Classification, expected: bool) -> None:
    assert principal_for_role(role).may_read(zone) is expected


def test_the_matrix_covers_every_cell() -> None:
    assert len(MAY_READ_MATRIX) == len(RoleCode) * len(Classification) == 24


def test_a_trade_officer_cannot_read_a_consular_case() -> None:
    """The refusal exercised by evals/security/prompt_injection.example.jsonl."""
    assert not principal_for_role(R.TRADE_OFFICER).may_read(C.CONSULAR_SENSITIVE)


def test_seniority_alone_does_not_open_a_case_file() -> None:
    """A principal at rank 40 without the compartment is refused; rank is not enough."""
    ambassador = principal_for_role(R.AMBASSADOR)
    without_compartment = dataclasses.replace(ambassador, compartments=frozenset())
    assert ambassador.may_read(C.CONSULAR_SENSITIVE)
    assert not without_compartment.may_read(C.CONSULAR_SENSITIVE)


def test_admin_cannot_read_case_content_despite_being_an_administrator() -> None:
    """Separation of duties (ADR-0003). Both gates refuse, independently."""
    admin = principal_for_role(R.ADMIN)
    assert not admin.may_read(C.CONSULAR_SENSITIVE)
    assert not admin.may_read(C.CONFIDENTIAL)


def test_a_consular_officer_cannot_read_a_confidential_negotiating_position() -> None:
    assert not principal_for_role(R.CONSULAR_OFFICER).may_read(C.CONFIDENTIAL)


def test_every_role_can_read_public() -> None:
    """The readable list is never empty, so a generated ``IN`` clause is always valid SQL."""
    for role in RoleCode:
        assert principal_for_role(role).may_read(C.PUBLIC), role.value


# ---------------------------------------------------------------------------
# 3. readable_classifications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(RoleCode))
def test_readable_classifications_agrees_with_may_read(role: RoleCode) -> None:
    principal = principal_for_role(role)
    readable = readable_classifications_for(principal)
    assert set(readable) == {zone for zone in Classification if principal.may_read(zone)}


@pytest.mark.parametrize("role", list(RoleCode))
def test_readable_classifications_is_ordered_and_unique(role: RoleCode) -> None:
    """Deterministic order keeps a generated SQL string stable and diffable."""
    readable = readable_classifications_for(principal_for_role(role))
    ranks = [CLASSIFICATION_ACCESS[zone].rank for zone in readable]
    assert ranks == sorted(ranks)
    assert len(set(readable)) == len(readable)


def test_readable_classifications_for_a_trade_officer() -> None:
    assert readable_classifications_for(principal_for_role(R.TRADE_OFFICER)) == [
        C.PUBLIC,
        C.MISSION_INTERNAL,
    ]


def test_admin_clears_mission_internal_but_holds_no_permission_to_read_it() -> None:
    """The two gates are genuinely independent, and this is the case that proves it.

    ADMIN's clearance rank of 10 satisfies MISSION_INTERNAL, so the *classification* gate
    opens. It still cannot read an opportunity or a stakeholder, because the *permission*
    gate is closed. Either gate alone would be a wrong answer.
    """
    from app.security.permissions import Permission

    admin = principal_for_role(R.ADMIN)
    assert admin.may_read(C.MISSION_INTERNAL)
    assert not admin.has(Permission.READ_OPPORTUNITY)
    assert not admin.has(Permission.READ_STAKEHOLDER)


# ---------------------------------------------------------------------------
# 4. The Principal object itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(RoleCode))
def test_principal_for_role_carries_the_matrix_permissions(role: RoleCode) -> None:
    assert principal_for_role(role).permissions == ROLE_PERMISSIONS[role]


@pytest.mark.parametrize("role", list(RoleCode))
def test_principal_identity_comes_from_the_persona(role: RoleCode) -> None:
    persona = demo_persona(role)
    principal = principal_for_role(role)
    assert principal.user_id == persona.user_id
    assert principal.email == persona.email
    assert principal.full_name == persona.full_name
    assert principal.role is role


def test_principal_is_frozen() -> None:
    """A principal is a decision already made; nothing downstream may widen it."""
    principal = principal_for_role(R.TRADE_OFFICER)
    with pytest.raises(dataclasses.FrozenInstanceError):
        principal.clearance_rank = 40  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        principal.permissions = frozenset()  # type: ignore[misc]


def test_principal_is_hashable_and_compares_by_value() -> None:
    assert principal_for_role(R.DEPUTY) == principal_for_role(R.DEPUTY)
    assert len({principal_for_role(R.DEPUTY), principal_for_role(R.DEPUTY)}) == 1
    assert principal_for_role(R.DEPUTY) != principal_for_role(R.AMBASSADOR)


def test_has_all_and_missing() -> None:
    from app.security.permissions import Permission

    trade = principal_for_role(R.TRADE_OFFICER)
    wanted = frozenset({Permission.READ_OPPORTUNITY, Permission.EXPORT_BULK})
    assert not trade.has_all(wanted)
    assert trade.missing(wanted) == frozenset({Permission.EXPORT_BULK})
    assert trade.missing(frozenset({Permission.READ_OPPORTUNITY})) == frozenset()


def test_every_principal_is_flagged_as_a_demo_identity() -> None:
    for role in RoleCode:
        assert principal_for_role(role).is_demo_identity


# ---------------------------------------------------------------------------
# 5. Demo personas
# ---------------------------------------------------------------------------


def test_every_role_has_exactly_one_persona() -> None:
    assert set(DEMO_PERSONAS) == set(RoleCode)


def test_persona_ids_are_deterministic_and_distinct() -> None:
    """The seed loader and the API must mint the same ids without coordinating."""
    ids = {persona.user_id for persona in DEMO_PERSONAS.values()}
    assert len(ids) == len(RoleCode)
    assert demo_persona(R.AMBASSADOR).user_id == demo_persona(R.AMBASSADOR).user_id
    for persona in DEMO_PERSONAS.values():
        assert isinstance(persona.user_id, uuid.UUID)
        assert persona.user_id.version == 5


def test_persona_emails_are_unique_and_non_routable() -> None:
    """BUILD_BIBLE section 11: no real address can appear, and none can receive mail."""
    emails = [persona.email for persona in DEMO_PERSONAS.values()]
    assert len(set(emails)) == len(emails)
    for email in emails:
        assert email.endswith("@naddp.demo"), email


def test_a_principal_can_be_constructed_directly_for_a_test_double() -> None:
    """The dataclass is usable without the factory, for a fixture that needs an odd shape."""
    principal = Principal(
        user_id=uuid.uuid4(),
        email="nobody@naddp.demo",
        full_name="Test Officer",
        role=R.TRADE_OFFICER,
        permissions=frozenset(),
        clearance_rank=0,
        compartments=frozenset(),
    )
    assert principal.may_read(C.PUBLIC)
    assert not principal.may_read(C.MISSION_INTERNAL)
