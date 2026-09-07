"""The role x permission matrix, proved in both directions.

Pure unit tests: no database, no FastAPI, no fixtures beyond the repository's own
documents. ADR-0003 lists a table-driven test over the full matrix as a Week-1 enforcement
mechanism, and this is it.

The transcriptions below are held *twice on purpose*. ``app/security/matrix.py`` assembles
each role from named grant groups, because six explicit forty-row lists would be six places
to make the same mistake. This module holds the flat, literal grant tables copied by hand
from ``docs/workflows.md``. Two independently-written representations that must agree is
the only version of this test that can fail for the right reason -- deriving the expectation
from the thing under test would assert nothing at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from app.api.v1.meta import BOUNDED_CONTEXTS as API_BOUNDED_CONTEXTS
from app.core.config import REPO_ROOT
from app.domain.enums import RoleCode
from app.security.matrix import ROLE_PERMISSIONS, granted_roles, permissions_for_role, role_has
from app.security.permissions import (
    BOUNDED_CONTEXTS,
    SENSITIVE_PERMISSIONS,
    Permission,
    permissions_for_context,
    spec_for,
)

P = Permission
R = RoleCode

WORKFLOWS_DOC: Final[Path] = REPO_ROOT / "docs" / "workflows.md"

#: The full vocabulary, listed literally so the count and the spellings are both asserted.
EXPECTED_CODES: Final[frozenset[str]] = frozenset(
    {
        # read (10)
        "read:command",
        "read:intelligence",
        "read:opportunity",
        "read:stakeholder",
        "read:meeting",
        "read:consular_case",
        "read:diaspora_profile",
        "read:knowledge_article",
        "read:audit",
        "read:ai_trace",
        # opportunity (6)
        "create:opportunity",
        "qualify:opportunity",
        "advance:opportunity",
        "close:opportunity",
        "commit:opportunity",
        "revert:opportunity",
        # stakeholder (3)
        "create:stakeholder",
        "update:stakeholder",
        "create:interaction",
        # meeting (7)
        "create:meeting",
        "update:meeting",
        "draft:meeting_followup",
        "submit:meeting_followup",
        "approve:meeting_followup",
        "send:meeting_followup",
        "discard:meeting_followup",
        # consular (8)
        "create:consular_case",
        "triage:consular_case",
        "assign:consular_case",
        "work:consular_case",
        "escalate:consular_case",
        "resolve:consular_case",
        "close:consular_case",
        "reopen:consular_case",
        # diaspora (1)
        "search:diaspora_profile",
        # knowledge (2)
        "create:knowledge_article",
        "approve:knowledge_article",
        # cross-cutting (1)
        "export:bulk",
        # admin (2)
        "admin:user",
        "admin:role",
    }
)

#: Every permission a state machine names, transcribed from the three grant tables in
#: ``docs/workflows.md``. The matrix must define all of them, and must not define a
#: workflow permission the documents never mention.
WORKFLOW_PERMISSIONS: Final[frozenset[Permission]] = frozenset(
    {
        # section 1
        P.CREATE_OPPORTUNITY,
        P.QUALIFY_OPPORTUNITY,
        P.ADVANCE_OPPORTUNITY,
        P.CLOSE_OPPORTUNITY,
        P.COMMIT_OPPORTUNITY,
        P.REVERT_OPPORTUNITY,
        # section 2
        P.DRAFT_MEETING_FOLLOWUP,
        P.SUBMIT_MEETING_FOLLOWUP,
        P.APPROVE_MEETING_FOLLOWUP,
        P.SEND_MEETING_FOLLOWUP,
        P.DISCARD_MEETING_FOLLOWUP,
        # section 3
        P.CREATE_CONSULAR_CASE,
        P.TRIAGE_CONSULAR_CASE,
        P.ASSIGN_CONSULAR_CASE,
        P.WORK_CONSULAR_CASE,
        P.ESCALATE_CONSULAR_CASE,
        P.RESOLVE_CONSULAR_CASE,
        P.CLOSE_CONSULAR_CASE,
        P.REOPEN_CONSULAR_CASE,
        P.READ_CONSULAR_CASE,
    }
)

#: ``docs/workflows.md`` section 3, "Permission grants" -- transcribed cell by cell.
CONSULAR_GRANTS: Final[dict[Permission, frozenset[RoleCode]]] = {
    P.CREATE_CONSULAR_CASE: frozenset({R.CONSULAR_OFFICER}),
    P.TRIAGE_CONSULAR_CASE: frozenset({R.DEPUTY, R.CONSULAR_OFFICER}),
    P.ASSIGN_CONSULAR_CASE: frozenset({R.DEPUTY, R.CONSULAR_OFFICER}),
    P.WORK_CONSULAR_CASE: frozenset({R.DEPUTY, R.CONSULAR_OFFICER}),
    P.ESCALATE_CONSULAR_CASE: frozenset({R.AMBASSADOR, R.DEPUTY, R.CONSULAR_OFFICER}),
    P.RESOLVE_CONSULAR_CASE: frozenset({R.DEPUTY, R.CONSULAR_OFFICER}),
    P.CLOSE_CONSULAR_CASE: frozenset({R.DEPUTY, R.CONSULAR_OFFICER}),
    P.REOPEN_CONSULAR_CASE: frozenset({R.DEPUTY, R.CONSULAR_OFFICER}),
    P.READ_CONSULAR_CASE: frozenset({R.AMBASSADOR, R.DEPUTY, R.CONSULAR_OFFICER}),
}

#: ``docs/workflows.md`` section 2, "Permission grants" -- transcribed cell by cell.
FOLLOWUP_GRANTS: Final[dict[Permission, frozenset[RoleCode]]] = {
    P.DRAFT_MEETING_FOLLOWUP: frozenset(
        {R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER, R.CONSULAR_OFFICER, R.DIASPORA_OFFICER}
    ),
    P.SUBMIT_MEETING_FOLLOWUP: frozenset(
        {R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER, R.CONSULAR_OFFICER, R.DIASPORA_OFFICER}
    ),
    P.APPROVE_MEETING_FOLLOWUP: frozenset({R.AMBASSADOR, R.DEPUTY}),
    P.SEND_MEETING_FOLLOWUP: frozenset(
        {R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER, R.CONSULAR_OFFICER, R.DIASPORA_OFFICER}
    ),
    P.DISCARD_MEETING_FOLLOWUP: frozenset(
        {R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER, R.CONSULAR_OFFICER, R.DIASPORA_OFFICER}
    ),
}

#: ``docs/workflows.md`` section 1, "Permission grants" -- transcribed cell by cell.
OPPORTUNITY_GRANTS: Final[dict[Permission, frozenset[RoleCode]]] = {
    P.CREATE_OPPORTUNITY: frozenset({R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER}),
    P.QUALIFY_OPPORTUNITY: frozenset({R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER}),
    P.ADVANCE_OPPORTUNITY: frozenset({R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER}),
    P.CLOSE_OPPORTUNITY: frozenset({R.AMBASSADOR, R.DEPUTY, R.TRADE_OFFICER}),
    P.COMMIT_OPPORTUNITY: frozenset({R.AMBASSADOR, R.DEPUTY}),
    P.REVERT_OPPORTUNITY: frozenset({R.AMBASSADOR, R.DEPUTY}),
}

#: Content reads ``ADMIN`` must never hold (ADR-0003, Q-02b principle 2).
ADMIN_FORBIDDEN_READS: Final[frozenset[Permission]] = frozenset(
    {
        P.READ_INTELLIGENCE,
        P.READ_OPPORTUNITY,
        P.READ_STAKEHOLDER,
        P.READ_CONSULAR_CASE,
        P.READ_DIASPORA_PROFILE,
        P.READ_MEETING,
        P.READ_KNOWLEDGE_ARTICLE,
    }
)


# ---------------------------------------------------------------------------
# 1. The vocabulary
# ---------------------------------------------------------------------------


def test_permission_codes_are_exactly_the_expected_forty() -> None:
    assert {member.value for member in Permission} == EXPECTED_CODES
    assert len(Permission) == 40


@pytest.mark.parametrize("permission", list(Permission))
def test_every_code_is_verb_colon_singular_object(permission: Permission) -> None:
    """Q-02a: verb:object, lower snake_case, exactly one colon, singular object."""
    assert re.fullmatch(r"[a-z][a-z_]*:[a-z][a-z_]*", permission.value), permission.value
    _verb, obj = permission.value.split(":")
    # "bulk" is a quantity rather than an entity; every other object is a singular noun.
    assert not obj.endswith("s") or obj == "bulk", permission.value


@pytest.mark.parametrize("permission", list(Permission))
def test_every_permission_has_a_complete_spec(permission: Permission) -> None:
    spec = spec_for(permission)
    assert spec.code is permission
    assert spec.label.strip()
    assert spec.description.strip()
    assert spec.bounded_context in BOUNDED_CONTEXTS


def test_bounded_contexts_agree_with_the_api_module() -> None:
    """``app.security`` mirrors the list rather than importing FastAPI; they must agree."""
    assert BOUNDED_CONTEXTS == API_BOUNDED_CONTEXTS


def test_every_bounded_context_owns_at_least_one_permission() -> None:
    empty = [context for context in BOUNDED_CONTEXTS if not permissions_for_context(context)]
    assert not empty, f"contexts with no permission would render an empty nav group: {empty}"


def test_sensitive_permissions_are_exactly_the_consequential_acts() -> None:
    """Every ``approve:*``, the two outbound controls, the determinations, bulk, admin."""
    assert (
        frozenset(
            {
                P.APPROVE_MEETING_FOLLOWUP,
                P.APPROVE_KNOWLEDGE_ARTICLE,
                P.SEND_MEETING_FOLLOWUP,
                P.COMMIT_OPPORTUNITY,
                P.TRIAGE_CONSULAR_CASE,
                P.RESOLVE_CONSULAR_CASE,
                P.CLOSE_CONSULAR_CASE,
                P.EXPORT_BULK,
                P.ADMIN_USER,
                P.ADMIN_ROLE,
            }
        )
        == SENSITIVE_PERMISSIONS
    )


def test_every_approve_permission_is_marked_sensitive() -> None:
    approvals = {code for code in Permission if code.value.startswith("approve:")}
    assert approvals <= SENSITIVE_PERMISSIONS


# ---------------------------------------------------------------------------
# 2. The matrix is total, closed and immutable
# ---------------------------------------------------------------------------


def test_matrix_covers_every_role() -> None:
    assert set(ROLE_PERMISSIONS) == set(RoleCode)


@pytest.mark.parametrize("role", list(RoleCode))
def test_matrix_grants_only_known_permissions(role: RoleCode) -> None:
    """Direction 1: nothing in the matrix is outside the vocabulary."""
    assert permissions_for_role(role) <= frozenset(Permission)


def test_every_permission_is_granted_to_someone() -> None:
    """Direction 2: a permission no role holds is dead code that reads as a control."""
    ungranted = [code.value for code in Permission if not granted_roles(code)]
    assert not ungranted, f"defined but never granted: {ungranted}"


def test_matrix_cannot_be_mutated_at_runtime() -> None:
    """Deny-by-default is defeated by any code path that can widen a role."""
    with pytest.raises(TypeError):
        ROLE_PERMISSIONS[RoleCode.ADMIN] = frozenset(Permission)  # type: ignore[index]
    for held in ROLE_PERMISSIONS.values():
        assert isinstance(held, frozenset)


# ---------------------------------------------------------------------------
# 3. The workflow grant tables, transcribed from docs/workflows.md
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("permission", "expected_roles"),
    [
        pytest.param(permission, roles, id=permission.value)
        for permission, roles in CONSULAR_GRANTS.items()
    ],
)
def test_consular_grants_match_the_workflow_document(
    permission: Permission, expected_roles: frozenset[RoleCode]
) -> None:
    assert granted_roles(permission) == expected_roles


@pytest.mark.parametrize(
    ("permission", "expected_roles"),
    [
        pytest.param(permission, roles, id=permission.value)
        for permission, roles in FOLLOWUP_GRANTS.items()
    ],
)
def test_meeting_followup_grants_match_the_workflow_document(
    permission: Permission, expected_roles: frozenset[RoleCode]
) -> None:
    assert granted_roles(permission) == expected_roles


@pytest.mark.parametrize(
    ("permission", "expected_roles"),
    [
        pytest.param(permission, roles, id=permission.value)
        for permission, roles in OPPORTUNITY_GRANTS.items()
    ],
)
def test_opportunity_grants_match_the_workflow_document(
    permission: Permission, expected_roles: frozenset[RoleCode]
) -> None:
    assert granted_roles(permission) == expected_roles


def test_trade_diaspora_and_admin_hold_no_consular_permission() -> None:
    """docs/workflows.md section 3: three roles hold none of it, and cannot read a case."""
    consular = frozenset(CONSULAR_GRANTS)
    for role in (R.TRADE_OFFICER, R.DIASPORA_OFFICER, R.ADMIN):
        assert not (permissions_for_role(role) & consular), role.value


def test_ambassador_oversees_consular_but_does_not_do_casework() -> None:
    """Head-of-mission oversight, not casework: escalate and read, nothing else."""
    held = permissions_for_role(R.AMBASSADOR) & frozenset(CONSULAR_GRANTS)
    assert held == frozenset({P.ESCALATE_CONSULAR_CASE, P.READ_CONSULAR_CASE})


def test_only_the_consular_officer_may_open_a_case() -> None:
    assert granted_roles(P.CREATE_CONSULAR_CASE) == frozenset({R.CONSULAR_OFFICER})


# ---------------------------------------------------------------------------
# 4. Both directions against the workflow document itself
# ---------------------------------------------------------------------------


def _permission_tokens_in(document: Path) -> frozenset[str]:
    """Every backticked ``verb:object`` token in a markdown document."""
    text = document.read_text(encoding="utf-8")
    return frozenset(re.findall(r"`([a-z][a-z_]*:[a-z][a-z_]*)`", text))


def test_every_permission_named_in_the_workflow_document_exists() -> None:
    """Direction 1, read from the document rather than from a transcription of it.

    Catches the case a hand-copied expectation cannot: someone edits ``docs/workflows.md``
    to name a permission, and nobody adds it to the matrix. The state machine would then
    reference a code the RBAC layer does not define, and deny-by-default would silently
    lock the transition out of the product.
    """
    tokens = _permission_tokens_in(WORKFLOWS_DOC)
    assert tokens, "found no permission tokens; the scan or the document has changed shape"
    unknown = sorted(tokens - {member.value for member in Permission})
    assert not unknown, f"docs/workflows.md names permissions the matrix does not define: {unknown}"


def test_every_workflow_permission_is_in_the_matrix() -> None:
    """Direction 2: the transcribed workflow set is a subset of what the matrix grants."""
    granted = frozenset().union(*ROLE_PERMISSIONS.values())
    assert granted >= WORKFLOW_PERMISSIONS


def test_the_transcription_and_the_document_agree() -> None:
    """The hand-copied :data:`WORKFLOW_PERMISSIONS` matches what the document actually says."""
    assert {code.value for code in WORKFLOW_PERMISSIONS} == _permission_tokens_in(WORKFLOWS_DOC)


# ---------------------------------------------------------------------------
# 5. The three properties Q-02b makes load-bearing
# ---------------------------------------------------------------------------


def test_admin_is_not_a_content_super_user() -> None:
    """Property (a). The assertion a security reviewer makes first."""
    admin = permissions_for_role(R.ADMIN)
    assert not (admin & ADMIN_FORBIDDEN_READS)
    assert admin == frozenset(
        {P.READ_COMMAND, P.READ_AI_TRACE, P.READ_AUDIT, P.ADMIN_USER, P.ADMIN_ROLE}
    )


def test_admin_holds_no_write_verb_over_any_content() -> None:
    admin = permissions_for_role(R.ADMIN)
    content_verbs = {code for code in Permission if not code.value.startswith(("read:", "admin:"))}
    assert not (admin & content_verbs)


def test_export_bulk_is_the_narrowest_cross_cutting_grant() -> None:
    """Property (b). AMBASSADOR and DEPUTY only.

    Stated precisely, because the loose form is not quite true and a test that overclaims
    is worse than none. Exactly three permissions are held by fewer roles, and all three
    are single-role verbs confined to one screen -- ``create:consular_case`` (intake, the
    consular officer alone) and the two ``admin:*`` codes. Among everything that reaches
    across the product, ``export:bulk`` is the narrowest, and it is the only *read-adjacent*
    capability restricted this far.
    """
    holders = granted_roles(P.EXPORT_BULK)
    assert holders == frozenset({R.AMBASSADOR, R.DEPUTY})
    narrower = {code.value for code in Permission if len(granted_roles(code)) < len(holders)}
    assert narrower == {"create:consular_case", "admin:user", "admin:role"}


def test_a_trade_officer_reads_the_pipeline_and_cannot_export_it() -> None:
    """The demonstrable version of property (b), in one assertion."""
    assert role_has(R.TRADE_OFFICER, P.READ_OPPORTUNITY)
    assert not role_has(R.TRADE_OFFICER, P.EXPORT_BULK)


@pytest.mark.parametrize("role", list(RoleCode))
def test_export_bulk_is_never_implied_by_a_read_permission(role: RoleCode) -> None:
    """ADR-0003 rule 4, asserted for every role rather than for the interesting one."""
    held = permissions_for_role(role)
    reads = {code for code in held if code.value.startswith("read:")}
    if reads and P.EXPORT_BULK in held:
        # Holding both is legitimate for the two senior roles; what must never happen is
        # a role acquiring bulk export *because* it can read. That is asserted by the two
        # roles that read almost everything and export nothing.
        assert role in {R.AMBASSADOR, R.DEPUTY}
    assert not (P.EXPORT_BULK in held and role in {R.TRADE_OFFICER, R.DIASPORA_OFFICER})


def test_approving_an_outbound_communication_is_senior_only() -> None:
    """Winning moment #2: the block is a permission the drafting persona does not hold."""
    assert granted_roles(P.APPROVE_MEETING_FOLLOWUP) == frozenset({R.AMBASSADOR, R.DEPUTY})
    assert role_has(R.TRADE_OFFICER, P.SEND_MEETING_FOLLOWUP)
    assert not role_has(R.TRADE_OFFICER, P.APPROVE_MEETING_FOLLOWUP)


def test_every_role_can_reach_the_command_centre_and_the_trace_drawer() -> None:
    """Navigation is derived, so a role with no landing surface would render nothing."""
    for role in RoleCode:
        assert role_has(role, P.READ_COMMAND), role.value
        assert role_has(role, P.READ_AI_TRACE), role.value
