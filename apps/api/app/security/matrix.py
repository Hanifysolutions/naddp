"""``ROLE_PERMISSIONS`` -- the authoritative role-to-permission matrix.

The single source of truth for authorisation, consumed by the API (through
``app.security.deps.require``), by the seed loader (which mirrors it into
``role_permissions`` for provenance) and by the web navigation rail (which receives a
principal's permission set from ``GET /v1/session/me`` and renders the intersection with
its own catalogue). There is no second copy: ``docs/OPEN_QUESTIONS.md`` Q-02b makes this
module the decision record, and ``apps/web/src/lib/nav.ts`` is explicit that if the two
disagree, the server is the authority.

Four principles, transcribed from Q-02b:

1. **Deny by default.** A role holds only what is listed. An absent grant is a denial;
   there is no implicit "authenticated users may read" tier, and no role inherits from
   another. The mapping is total over :class:`RoleCode`, so "role not in the matrix" is
   not a state the system can reach.
2. **``ADMIN`` is not a content super-user.** It administers users and roles and reads the
   audit log. It holds no read permission over intelligence, opportunities, stakeholders,
   consular cases or diaspora, which is consistent with its clearance rank of 10 and its
   empty compartment list in ``data/taxonomy/classifications.json``. A consular audience
   reads "the administrator can read every case file" as an unmitigated insider risk, so
   the matrix does not say it.
3. **``export:bulk`` is the narrowest grant here** -- ``AMBASSADOR`` and ``DEPUTY`` only.
   A ``TRADE_OFFICER`` reads the entire pipeline and cannot export it. This is the separate
   bulk-export control of ADR-0003 rule 4, and it is demonstrable on stage.
4. **Permission and classification are independent gates, and both must pass**
   (ADR-0006). Holding ``read:consular_case`` does not by itself grant sight of a
   ``CONSULAR_SENSITIVE`` row; the ``consular`` compartment does. The matrix below settles
   only the first gate. ``app.security.principal.Principal.may_read`` settles the second.

**The consular block and the meeting-follow-up block are transcribed unchanged from
``docs/workflows.md``** sections 3 and 2 respectively. Those tables are normative: if this
module disagrees with them, this module is wrong. ``tests/test_security_matrix.py`` holds
its own literal copy of both grant tables and compares, so a silent edit here fails a test
rather than a demo.

Everything else follows the summary table in ``docs/OPEN_QUESTIONS.md`` Q-02b.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from app.domain.enums import RoleCode
from app.security.permissions import Permission

__all__ = [
    "ROLE_PERMISSIONS",
    "granted_roles",
    "permissions_for_role",
    "role_has",
]

# ---------------------------------------------------------------------------
# Grant groups
# ---------------------------------------------------------------------------
#
# Named groups rather than 40 lines per role. Six explicit 40-row lists would be six
# places to make the same mistake, and a reviewer cannot diff them by eye. A group is
# a row of the Q-02b table; each role below is assembled from the rows it holds, which
# is exactly how the decision was written down.

#: The command centre. Every role reaches it; the tiles inside it are filtered
#: individually by the reader's other permissions and clearance.
_COMMAND: Final[frozenset[Permission]] = frozenset({Permission.READ_COMMAND})

#: The trade / relationship reading surface. Q-02b row 2.
_TRADE_READS: Final[frozenset[Permission]] = frozenset(
    {
        Permission.READ_INTELLIGENCE,
        Permission.READ_OPPORTUNITY,
        Permission.READ_STAKEHOLDER,
        Permission.READ_DIASPORA_PROFILE,
    }
)

#: Shared working surfaces every officer role reads. Q-02b row 3.
_SHARED_READS: Final[frozenset[Permission]] = frozenset(
    {
        Permission.READ_MEETING,
        Permission.READ_KNOWLEDGE_ARTICLE,
    }
)

#: The trace drawer. Held by all six roles: a routing decision nobody may inspect is not
#: a demonstrable control (BUILD_BIBLE.md section 5).
_AI_TRACE: Final[frozenset[Permission]] = frozenset({Permission.READ_AI_TRACE})

#: Opportunity pipeline work, short of the commitment control. Q-02b row 6.
_OPPORTUNITY_WORK: Final[frozenset[Permission]] = frozenset(
    {
        Permission.CREATE_OPPORTUNITY,
        Permission.QUALIFY_OPPORTUNITY,
        Permission.ADVANCE_OPPORTUNITY,
        Permission.CLOSE_OPPORTUNITY,
    }
)

#: Concluding a partnership and correcting a mis-advance. AMBASSADOR / DEPUTY only
#: (docs/workflows.md section 1 grant table).
_OPPORTUNITY_SENIOR: Final[frozenset[Permission]] = frozenset(
    {
        Permission.COMMIT_OPPORTUNITY,
        Permission.REVERT_OPPORTUNITY,
    }
)

#: Stakeholder record-keeping. Q-02b row 7.
_STAKEHOLDER_WORK: Final[frozenset[Permission]] = frozenset(
    {
        Permission.CREATE_STAKEHOLDER,
        Permission.UPDATE_STAKEHOLDER,
        Permission.CREATE_INTERACTION,
    }
)

#: Meeting record-keeping. Q-02b row 8. Distinct from the follow-up machine below.
_MEETING_WORK: Final[frozenset[Permission]] = frozenset(
    {
        Permission.CREATE_MEETING,
        Permission.UPDATE_MEETING,
    }
)

#: Follow-up permissions held by every *officer* role.
#:
#: docs/workflows.md section 2 grant table: draft / submit / send / discard are ticked for
#: AMBASSADOR, DEPUTY, TRADE_OFFICER, CONSULAR_OFFICER and DIASPORA_OFFICER, and are blank
#: for ADMIN. `send` sitting in this group rather than the senior one is deliberate and is
#: the sharper demonstration: the officer who drafted it may dispatch it, but SENT is
#: reachable only from APPROVED, so holding `send` without an approval achieves nothing.
_FOLLOWUP_OFFICER: Final[frozenset[Permission]] = frozenset(
    {
        Permission.DRAFT_MEETING_FOLLOWUP,
        Permission.SUBMIT_MEETING_FOLLOWUP,
        Permission.SEND_MEETING_FOLLOWUP,
        Permission.DISCARD_MEETING_FOLLOWUP,
    }
)

#: The approval gate on outbound communication. AMBASSADOR / DEPUTY only. This is the
#: permission whose absence the demo shows blocking a send -- winning moment #2.
_FOLLOWUP_APPROVAL: Final[frozenset[Permission]] = frozenset({Permission.APPROVE_MEETING_FOLLOWUP})

#: Consular casework held by DEPUTY and CONSULAR_OFFICER (docs/workflows.md section 3).
#: `create:consular_case` is NOT here: intake is CONSULAR_OFFICER alone.
_CONSULAR_CASEWORK: Final[frozenset[Permission]] = frozenset(
    {
        Permission.TRIAGE_CONSULAR_CASE,
        Permission.ASSIGN_CONSULAR_CASE,
        Permission.WORK_CONSULAR_CASE,
        Permission.ESCALATE_CONSULAR_CASE,
        Permission.RESOLVE_CONSULAR_CASE,
        Permission.CLOSE_CONSULAR_CASE,
        Permission.REOPEN_CONSULAR_CASE,
    }
)

#: Knowledge authoring, held by every officer role.
_KNOWLEDGE_AUTHOR: Final[frozenset[Permission]] = frozenset({Permission.CREATE_KNOWLEDGE_ARTICLE})

#: Diaspora capability search. Q-02b row 9.
_DIASPORA_SEARCH: Final[frozenset[Permission]] = frozenset({Permission.SEARCH_DIASPORA_PROFILE})

#: The senior cross-cutting grants: reading the audit log, publishing a groundable source,
#: and bulk extraction. Deliberately the narrowest rows in the matrix. ``read:audit``
#: appears here and in :data:`_ADMINISTRATION`; the union is the same either way, and
#: stating it in both places keeps each group readable as a row of the Q-02b table rather
#: than as a set-arithmetic puzzle.
_SENIOR_CROSS_CUTTING: Final[frozenset[Permission]] = frozenset(
    {
        Permission.APPROVE_KNOWLEDGE_ARTICLE,
        Permission.EXPORT_BULK,
        Permission.READ_AUDIT,
    }
)

#: Platform administration. Confers no content access whatsoever.
_ADMINISTRATION: Final[frozenset[Permission]] = frozenset(
    {
        Permission.ADMIN_USER,
        Permission.ADMIN_ROLE,
        Permission.READ_AUDIT,
    }
)


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------

#: Role to the exact set of permissions it holds. Deny-by-default: absence is refusal.
#:
#: ``MappingProxyType`` and ``frozenset`` make this genuinely immutable. A service that
#: could widen a role at runtime is the failure mode that quietly defeats deny-by-default,
#: and it would be invisible in review because the code that does it looks like a fix.
ROLE_PERMISSIONS: Final[Mapping[RoleCode, frozenset[Permission]]] = MappingProxyType(
    {
        # Head of mission: accountable for everything the mission does, and therefore able
        # to read everything and to make the two commitments. Note what is absent:
        # AMBASSADOR does not triage, assign, work, resolve or close a consular case, and
        # cannot open one. Head-of-mission oversight, not casework -- so the one consular
        # verb held is `escalate` (docs/workflows.md section 3).
        RoleCode.AMBASSADOR: (
            _COMMAND
            | _TRADE_READS
            | _SHARED_READS
            | _AI_TRACE
            | frozenset({Permission.READ_CONSULAR_CASE})
            | _OPPORTUNITY_WORK
            | _OPPORTUNITY_SENIOR
            | _STAKEHOLDER_WORK
            | _MEETING_WORK
            | _FOLLOWUP_OFFICER
            | _FOLLOWUP_APPROVAL
            | frozenset({Permission.ESCALATE_CONSULAR_CASE})
            | _DIASPORA_SEARCH
            | _KNOWLEDGE_AUTHOR
            | _SENIOR_CROSS_CUTTING
        ),
        # Acts for the Ambassador and carries the consular casework the Ambassador does
        # not: triage, assign, work, resolve, close, reopen (docs/workflows.md section 3).
        # Still cannot open a case -- intake belongs to the consular officer.
        RoleCode.DEPUTY: (
            _COMMAND
            | _TRADE_READS
            | _SHARED_READS
            | _AI_TRACE
            | frozenset({Permission.READ_CONSULAR_CASE})
            | _OPPORTUNITY_WORK
            | _OPPORTUNITY_SENIOR
            | _STAKEHOLDER_WORK
            | _MEETING_WORK
            | _FOLLOWUP_OFFICER
            | _FOLLOWUP_APPROVAL
            | _CONSULAR_CASEWORK
            | _DIASPORA_SEARCH
            | _KNOWLEDGE_AUTHOR
            | _SENIOR_CROSS_CUTTING
        ),
        # Full working access to the trade and intelligence surface, and no consular
        # need-to-know at all -- not even `read:consular_case`. Holds no `export:bulk`,
        # which is the demonstrable point of principle 3: reading the pipeline and
        # extracting it are different risks.
        RoleCode.TRADE_OFFICER: (
            _COMMAND
            | _TRADE_READS
            | _SHARED_READS
            | _AI_TRACE
            | _OPPORTUNITY_WORK
            | _STAKEHOLDER_WORK
            | _MEETING_WORK
            | _FOLLOWUP_OFFICER
            | _DIASPORA_SEARCH
            | _KNOWLEDGE_AUTHOR
        ),
        # The only role that may open a consular case, and the role that works it end to
        # end. Holds no trade or intelligence read: a consular officer's need-to-know is
        # deep, not broad (ADR-0006), and the matrix mirrors the clearance model rather
        # than contradicting it.
        RoleCode.CONSULAR_OFFICER: (
            _COMMAND
            | _SHARED_READS
            | _AI_TRACE
            | frozenset({Permission.READ_CONSULAR_CASE})
            | _MEETING_WORK
            | _FOLLOWUP_OFFICER
            | frozenset({Permission.CREATE_CONSULAR_CASE})
            | _CONSULAR_CASEWORK
            | _KNOWLEDGE_AUTHOR
        ),
        # Works the diaspora and stakeholder surface. Reads the pipeline but does not
        # move it: no opportunity verb at all.
        RoleCode.DIASPORA_OFFICER: (
            _COMMAND
            | _TRADE_READS
            | _SHARED_READS
            | _AI_TRACE
            | _STAKEHOLDER_WORK
            | _MEETING_WORK
            | _FOLLOWUP_OFFICER
            | _DIASPORA_SEARCH
            | _KNOWLEDGE_AUTHOR
        ),
        # Platform administration and audit reading, and NOTHING else. Five permissions.
        # It cannot read a case, an opportunity, a stakeholder, a signal or a diaspora
        # profile; it cannot draft, approve or send a communication; it cannot export.
        # This is principle 2, and it is the assertion a security reviewer will test
        # first.
        RoleCode.ADMIN: (_COMMAND | _AI_TRACE | _ADMINISTRATION),
    }
)


def permissions_for_role(role: RoleCode) -> frozenset[Permission]:
    """Return the exact permission set held by ``role``.

    Raises:
        KeyError: if ``role`` has no row. The mapping is total over :class:`RoleCode` and
            a test asserts it, so this cannot happen at runtime -- and failing loudly is
            the only safe behaviour if it somehow did. Returning an empty set instead
            would look like a working deny-by-default while actually hiding a bug that
            locks a whole role out of the product.
    """
    return ROLE_PERMISSIONS[role]


def role_has(role: RoleCode, permission: Permission) -> bool:
    """Return whether ``role`` holds ``permission``. Deny-by-default."""
    return permission in ROLE_PERMISSIONS[role]


def granted_roles(permission: Permission) -> frozenset[RoleCode]:
    """Return every role holding ``permission``.

    The inverse view of the matrix. Used by tests and by the seed loader; also the
    quickest way to answer "who could have done this" when reading the audit log.
    """
    return frozenset(role for role, held in ROLE_PERMISSIONS.items() if permission in held)
