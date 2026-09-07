"""The RBAC capability vocabulary: 40 permissions in ``verb:object`` form.

This module is one half of the authorisation contract. It answers *"what verbs exist"*;
``app.security.matrix`` answers *"which role holds which"*. Both are pure data with no
I/O, so they can be imported by the API, by the seed loader and by a test without a
database, and so the web client's permission-derived navigation
(``apps/web/src/lib/nav.ts``) can be checked against them string for string.

Identifier scheme (``docs/OPEN_QUESTIONS.md`` Q-02a, RESOLVED): ``verb:object``, singular
object, lower ``snake_case`` on both sides. The verb comes first because the verb is the
part that varies by role and the part a human scans a grant table for, and because
``object.verb`` cannot express a transition permission such as ``approve:meeting_followup``
without a second, parallel scheme. ``docs/workflows.md`` already carries every workflow
permission in this form, so this module is a transcription rather than an invention.

Two properties of this vocabulary are load-bearing and are asserted in
``tests/test_security_matrix.py``:

* **``export:bulk`` is its own permission on its own axis** (ADR-0003 rule 4). It is never
  implied by any read permission. Reading one record and extracting the whole table are
  different risks, and the second is the shape of virtually every real data-loss incident.
* **``admin:user`` / ``admin:role`` are administration, not clearance.** ``ADMIN`` holds
  them and holds no content read permission at all (ADR-0003, ADR-0006, and
  ``docs/OPEN_QUESTIONS.md`` A-01).

:data:`SENSITIVE_PERMISSIONS` marks the codes that gate a ``BUILD_BIBLE.md`` section 6
non-autonomous control or a bulk extraction. It is a **presentation and review aid** -- the
UI badges them, the audit reader highlights them -- and never an authorisation input on its
own. Authorisation is the matrix (this file plus ``matrix.py``) *and* the ADR-0006
classification check. A third, implicit gate hiding in a boolean would make the real rule
harder to reason about, not safer.

:data:`PERMISSION_SPECS` carries the label, description and owning bounded context for
every code, so the ``permissions`` table (``app.models.governance.Permission``) is seeded
from this module rather than from a second, drifting list.

Binding sources:

* ``docs/OPEN_QUESTIONS.md`` Q-02a (identifier scheme) and Q-02b (the matrix decision)
* ``docs/workflows.md`` sections 1-3 (every workflow permission and its grants)
* ``docs/architecture/adr/0003-rbac-real-identity-faked.md``
* ``BUILD_BIBLE.md`` section 6 (the non-autonomous controls) and section 8 (contexts)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

__all__ = [
    "BOUNDED_CONTEXTS",
    "PERMISSION_SPECS",
    "SENSITIVE_PERMISSIONS",
    "Permission",
    "PermissionSpec",
    "permissions_for_context",
    "spec_for",
]


class Permission(str, Enum):  # noqa: UP042
    """One capability in the deny-by-default RBAC matrix.

    ``(str, Enum)`` rather than ``enum.StrEnum`` for the reason given at the top of
    ``app/domain/enums.py``: the same shape is locked across the domain enums, the seed
    loader and the generated web client, and changing it in one module would split the
    contract. Use ``member.value`` in f-strings and SQL literals, never ``str(member)``.

    Membership is closed. A permission that is not a member here cannot be granted, cannot
    be required by a route, and cannot appear in the matrix -- which is what makes
    ``tests/test_security_matrix.py`` able to prove both directions of the contract.
    """

    # -- READ -----------------------------------------------------------------
    # These ten drive navigation (ADR-0003 rule 5): the web client asks the API for its
    # permission set and renders the intersection with its own catalogue. There is no
    # `if (role === 'AMBASSADOR')` anywhere in apps/web.

    READ_COMMAND = "read:command"
    READ_INTELLIGENCE = "read:intelligence"
    READ_OPPORTUNITY = "read:opportunity"
    READ_STAKEHOLDER = "read:stakeholder"
    READ_MEETING = "read:meeting"
    READ_CONSULAR_CASE = "read:consular_case"
    READ_DIASPORA_PROFILE = "read:diaspora_profile"
    READ_KNOWLEDGE_ARTICLE = "read:knowledge_article"
    READ_AUDIT = "read:audit"
    READ_AI_TRACE = "read:ai_trace"

    # -- OPPORTUNITY ----------------------------------------------------------
    # docs/workflows.md section 1. `commit:opportunity` is the BUILD_BIBLE section 6
    # commitment control; `revert:opportunity` corrects a mis-advance by exactly one stage.

    CREATE_OPPORTUNITY = "create:opportunity"
    QUALIFY_OPPORTUNITY = "qualify:opportunity"
    ADVANCE_OPPORTUNITY = "advance:opportunity"
    CLOSE_OPPORTUNITY = "close:opportunity"
    COMMIT_OPPORTUNITY = "commit:opportunity"
    REVERT_OPPORTUNITY = "revert:opportunity"

    # -- STAKEHOLDER ----------------------------------------------------------

    CREATE_STAKEHOLDER = "create:stakeholder"
    UPDATE_STAKEHOLDER = "update:stakeholder"
    CREATE_INTERACTION = "create:interaction"

    # -- MEETING --------------------------------------------------------------
    # docs/workflows.md section 2 -- winning moment #2. `send:meeting_followup` is
    # deliberately widely held: the block on sending is structural (SENT is reachable only
    # from APPROVED), not a permission the demo persona happens to lack.

    CREATE_MEETING = "create:meeting"
    UPDATE_MEETING = "update:meeting"
    DRAFT_MEETING_FOLLOWUP = "draft:meeting_followup"
    SUBMIT_MEETING_FOLLOWUP = "submit:meeting_followup"
    APPROVE_MEETING_FOLLOWUP = "approve:meeting_followup"
    SEND_MEETING_FOLLOWUP = "send:meeting_followup"
    DISCARD_MEETING_FOLLOWUP = "discard:meeting_followup"

    # -- CONSULAR -------------------------------------------------------------
    # docs/workflows.md section 3. The highest-sensitivity machine in the system; every
    # grant below is transcribed unchanged from that document's table.

    CREATE_CONSULAR_CASE = "create:consular_case"
    TRIAGE_CONSULAR_CASE = "triage:consular_case"
    ASSIGN_CONSULAR_CASE = "assign:consular_case"
    WORK_CONSULAR_CASE = "work:consular_case"
    ESCALATE_CONSULAR_CASE = "escalate:consular_case"
    RESOLVE_CONSULAR_CASE = "resolve:consular_case"
    CLOSE_CONSULAR_CASE = "close:consular_case"
    REOPEN_CONSULAR_CASE = "reopen:consular_case"

    # -- DIASPORA -------------------------------------------------------------

    SEARCH_DIASPORA_PROFILE = "search:diaspora_profile"

    # -- KNOWLEDGE ------------------------------------------------------------

    CREATE_KNOWLEDGE_ARTICLE = "create:knowledge_article"
    APPROVE_KNOWLEDGE_ARTICLE = "approve:knowledge_article"

    # -- CROSS-CUTTING --------------------------------------------------------

    EXPORT_BULK = "export:bulk"

    # -- ADMIN ----------------------------------------------------------------

    ADMIN_USER = "admin:user"
    ADMIN_ROLE = "admin:role"


#: The eight FastAPI bounded contexts (``BUILD_BIBLE.md`` section 8).
#:
#: Mirrored from ``app.api.v1.meta.BOUNDED_CONTEXTS`` deliberately rather than imported:
#: ``app.security`` must stay importable without FastAPI so the seed loader and the pure
#: matrix tests can use it. ``tests/test_security_matrix.py`` asserts the two agree.
BOUNDED_CONTEXTS: Final[tuple[str, ...]] = (
    "intelligence",
    "opportunities",
    "stakeholders",
    "meetings",
    "consular",
    "diaspora",
    "knowledge",
    "governance",
)


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """Everything the ``permissions`` table needs about one code.

    Frozen, because this is reference data: a permission code that changed meaning after
    grants had been issued would silently rewrite what those grants allow.
    """

    code: Permission
    label: str
    description: str
    bounded_context: str
    is_sensitive: bool


def _spec(
    code: Permission,
    label: str,
    description: str,
    bounded_context: str,
    *,
    sensitive: bool = False,
) -> tuple[Permission, PermissionSpec]:
    """Build one ``(code, spec)`` pair for :data:`PERMISSION_SPECS`."""
    return code, PermissionSpec(
        code=code,
        label=label,
        description=description,
        bounded_context=bounded_context,
        is_sensitive=sensitive,
    )


#: Label, description, owning context and sensitivity for all 40 permissions.
#:
#: The single source for the ``permissions`` table. The seed loader iterates this mapping;
#: it does not carry its own list, because two lists of 40 strings drift and the drift is
#: a *silent denial* (``app.models.governance.Permission`` column comment).
#:
#: ``read:command`` is filed under ``governance``: the command centre is a cross-context
#: executive surface, and ``BUILD_BIBLE.md`` section 8 fixes exactly eight contexts, so
#: inventing a ninth here would fork the vocabulary the navigation groups by. Its place in
#: the nav rail comes from ``NAV_CATALOGUE`` order, not from this field.
PERMISSION_SPECS: Final[Mapping[Permission, PermissionSpec]] = MappingProxyType(
    dict(
        (
            # -- read -------------------------------------------------------------
            _spec(
                Permission.READ_COMMAND,
                "View the command centre",
                "See the executive overview and its six mission-health tiles. Each tile "
                "is itself filtered by the reader's other permissions and clearance, so "
                "this grants the surface, never its contents.",
                "governance",
            ),
            _spec(
                Permission.READ_INTELLIGENCE,
                "Read intelligence",
                "See signals, sources and the morning brief. Every claim carries a real "
                "public citation.",
                "intelligence",
            ),
            _spec(
                Permission.READ_OPPORTUNITY,
                "Read opportunities",
                "See the bilateral opportunity pipeline and each opportunity's stage, "
                "score and evidence. Does not permit exporting it: see export:bulk.",
                "opportunities",
            ),
            _spec(
                Permission.READ_STAKEHOLDER,
                "Read stakeholders",
                "See organisations, contacts, relationship strength and interaction history.",
                "stakeholders",
            ),
            _spec(
                Permission.READ_MEETING,
                "Read meetings",
                "See meetings, pre-reads, attendees and follow-up artefacts.",
                "meetings",
            ),
            _spec(
                Permission.READ_CONSULAR_CASE,
                "Read consular cases",
                "See consular case records. Insufficient on its own: case content is "
                "CONSULAR_SENSITIVE, so the consular compartment is also required "
                "(ADR-0006). Both gates must pass.",
                "consular",
            ),
            _spec(
                Permission.READ_DIASPORA_PROFILE,
                "Read diaspora profiles",
                "See diaspora capability profiles. Read paths additionally filter on "
                "consent status inside the query, never after it.",
                "diaspora",
            ),
            _spec(
                Permission.READ_KNOWLEDGE_ARTICLE,
                "Read knowledge articles",
                "See the knowledge base. Only APPROVED articles may ground an AI answer.",
                "knowledge",
            ),
            _spec(
                Permission.READ_AUDIT,
                "Read the audit log",
                "See the append-only audit trail, including denials. Held by the two most "
                "senior roles and by ADMIN, which reads the log but not the content it "
                "describes.",
                "governance",
            ),
            _spec(
                Permission.READ_AI_TRACE,
                "Read AI traces",
                "Open the trace drawer for an AI response: purpose, classification "
                "routing decision, evidence and whether a cached fallback was served. "
                "Held by every role, because a control nobody can inspect is not a "
                "control.",
                "governance",
            ),
            # -- opportunity ------------------------------------------------------
            _spec(
                Permission.CREATE_OPPORTUNITY,
                "Create an opportunity",
                "Promote an intelligence signal into a DETECTED opportunity "
                "(docs/workflows.md section 1, row 1).",
                "opportunities",
            ),
            _spec(
                Permission.QUALIFY_OPPORTUNITY,
                "Qualify an opportunity",
                "Judge a DETECTED opportunity real and in scope. Requires a score and at "
                "least one evidence reference.",
                "opportunities",
            ),
            _spec(
                Permission.ADVANCE_OPPORTUNITY,
                "Advance an opportunity",
                "Move an opportunity forward one stage: plan_contact, record_contact, "
                "schedule_meeting, enter_negotiation.",
                "opportunities",
            ),
            _spec(
                Permission.CLOSE_OPPORTUNITY,
                "Close an opportunity",
                "Dismiss or close an opportunity to the CLOSED terminal. Always carries a reason.",
                "opportunities",
            ),
            _spec(
                Permission.COMMIT_OPPORTUNITY,
                "Conclude a partnership",
                "Move NEGOTIATION to PARTNERED. A BUILD_BIBLE section 6 commitment "
                "control: never AI-initiated, AMBASSADOR and DEPUTY only.",
                "opportunities",
                sensitive=True,
            ),
            _spec(
                Permission.REVERT_OPPORTUNITY,
                "Revert an opportunity stage",
                "Step an opportunity back exactly one stage to correct a mis-advance. "
                "Never crosses a terminal; always carries a reason.",
                "opportunities",
            ),
            # -- stakeholder ------------------------------------------------------
            _spec(
                Permission.CREATE_STAKEHOLDER,
                "Create a stakeholder",
                "Add an organisation or a named counterpart contact.",
                "stakeholders",
            ),
            _spec(
                Permission.UPDATE_STAKEHOLDER,
                "Update a stakeholder",
                "Edit stakeholder details, influence and relationship assessment.",
                "stakeholders",
            ),
            _spec(
                Permission.CREATE_INTERACTION,
                "Record an interaction",
                "Log an email, call, meeting, event or note against a stakeholder. The "
                "interaction row is the evidence that an approach was actually made.",
                "stakeholders",
            ),
            # -- meeting ----------------------------------------------------------
            _spec(
                Permission.CREATE_MEETING,
                "Create a meeting",
                "Schedule a meeting and request its AI pre-read.",
                "meetings",
            ),
            _spec(
                Permission.UPDATE_MEETING,
                "Update a meeting",
                "Edit meeting details, attendees and notes.",
                "meetings",
            ),
            _spec(
                Permission.DRAFT_MEETING_FOLLOWUP,
                "Draft a meeting follow-up",
                "Create or edit a follow-up communication. An AI-generated draft arrives "
                "with approval_status = PENDING_APPROVAL and is data, never an event.",
                "meetings",
            ),
            _spec(
                Permission.SUBMIT_MEETING_FOLLOWUP,
                "Submit a follow-up for review",
                "Lock a draft and send it for approval. The artefact an approver sees is "
                "the artefact that gets sent.",
                "meetings",
            ),
            _spec(
                Permission.APPROVE_MEETING_FOLLOWUP,
                "Approve a meeting follow-up",
                "Approve or reject an outbound communication. A BUILD_BIBLE section 6 "
                "external-outreach control: the approver must not be the drafter, and "
                "only AMBASSADOR and DEPUTY hold it. This is the permission whose absence "
                "the demo shows blocking a send.",
                "meetings",
                sensitive=True,
            ),
            _spec(
                Permission.SEND_MEETING_FOLLOWUP,
                "Send a meeting follow-up",
                "Dispatch an APPROVED follow-up. A BUILD_BIBLE section 6 "
                "diplomatic-communication control. Deliberately widely held: holding it "
                "without an APPROVED artefact achieves nothing, because SENT is reachable "
                "only from APPROVED.",
                "meetings",
                sensitive=True,
            ),
            _spec(
                Permission.DISCARD_MEETING_FOLLOWUP,
                "Discard a meeting follow-up",
                "Abandon a follow-up without sending it. Audited: deleting a drafted "
                "diplomatic communication is itself a consequential act.",
                "meetings",
            ),
            # -- consular ---------------------------------------------------------
            _spec(
                Permission.CREATE_CONSULAR_CASE,
                "Create a consular case",
                "Intake a new case and mint its citizen-facing public reference. Held by "
                "CONSULAR_OFFICER alone (docs/workflows.md section 3).",
                "consular",
            ),
            _spec(
                Permission.TRIAGE_CONSULAR_CASE,
                "Triage a consular case",
                "Confirm case type, priority and classification. A BUILD_BIBLE section 6 "
                "consular determination: an AI triage proposal never fires this event, a "
                "human does.",
                "consular",
                sensitive=True,
            ),
            _spec(
                Permission.ASSIGN_CONSULAR_CASE,
                "Assign a consular case",
                "Allocate or reallocate a case to a named consular officer.",
                "consular",
            ),
            _spec(
                Permission.WORK_CONSULAR_CASE,
                "Work a consular case",
                "Begin review, request information from the citizen (pausing the SLA "
                "clock) and record its receipt.",
                "consular",
            ),
            _spec(
                Permission.ESCALATE_CONSULAR_CASE,
                "Escalate a consular case",
                "Raise a case for a decision above the assigned officer, and return it "
                "with guidance. The one consular verb the AMBASSADOR holds: head-of-"
                "mission oversight, not casework.",
                "consular",
            ),
            _spec(
                Permission.RESOLVE_CONSULAR_CASE,
                "Resolve a consular case",
                "Record and communicate a determination. A BUILD_BIBLE section 6 consular "
                "determination; never AI-initiated.",
                "consular",
                sensitive=True,
            ),
            _spec(
                Permission.CLOSE_CONSULAR_CASE,
                "Close a consular case",
                "Move a case to its single terminal state. A BUILD_BIBLE section 6 "
                "case-closure control. A closed case is never reopened; a subsequent "
                "matter is a new case.",
                "consular",
                sensitive=True,
            ),
            _spec(
                Permission.REOPEN_CONSULAR_CASE,
                "Reopen a resolved case",
                "Return a RESOLVED case to IN_REVIEW on new information or a challenge to "
                "the determination. Restarts the SLA clock; the original budget is kept.",
                "consular",
            ),
            # -- diaspora ---------------------------------------------------------
            _spec(
                Permission.SEARCH_DIASPORA_PROFILE,
                "Search diaspora capability",
                "Run a capability search across diaspora profiles. Results are filtered "
                "by consent inside the query.",
                "diaspora",
            ),
            # -- knowledge --------------------------------------------------------
            _spec(
                Permission.CREATE_KNOWLEDGE_ARTICLE,
                "Author a knowledge article",
                "Draft an article. A draft cannot ground an AI answer until it is approved.",
                "knowledge",
            ),
            _spec(
                Permission.APPROVE_KNOWLEDGE_ARTICLE,
                "Approve a knowledge article",
                "Publish an article as an approved, groundable source. Approval is what "
                "makes an answer citable, so it is held by AMBASSADOR and DEPUTY only.",
                "knowledge",
                sensitive=True,
            ),
            # -- cross-cutting ----------------------------------------------------
            _spec(
                Permission.EXPORT_BULK,
                "Export in bulk",
                "Extract more than a page of records, or download a file rather than a "
                "JSON page. Never implied by any read permission (ADR-0003 rule 4): a "
                "TRADE_OFFICER can read the whole pipeline and cannot export it. Every "
                "export writes an audit row carrying the row count and the filter used.",
                "governance",
                sensitive=True,
            ),
            # -- admin ------------------------------------------------------------
            _spec(
                Permission.ADMIN_USER,
                "Administer users",
                "Create, deactivate and edit staff personas. Platform administration, not "
                "clearance: it confers no sight of the content those users work on.",
                "governance",
                sensitive=True,
            ),
            _spec(
                Permission.ADMIN_ROLE,
                "Administer roles",
                "Assign and revoke roles. Every grant and revocation is audited. Confers "
                "no content access of its own.",
                "governance",
                sensitive=True,
            ),
        )
    )
)


#: The permissions that gate a consequential act or a bulk extraction.
#:
#: Every ``approve:*``, ``send:meeting_followup``, ``commit:opportunity``,
#: ``triage``/``resolve``/``close:consular_case``, ``export:bulk`` and both ``admin:*``.
#: Derived from :data:`PERMISSION_SPECS` rather than listed twice, so a spec marked
#: sensitive and a set that forgot to say so cannot disagree.
#:
#: ADVISORY ONLY. Never an authorisation input: see the module docstring.
SENSITIVE_PERMISSIONS: Final[frozenset[Permission]] = frozenset(
    code for code, spec in PERMISSION_SPECS.items() if spec.is_sensitive
)


def spec_for(permission: Permission) -> PermissionSpec:
    """Return the specification for ``permission``.

    Raises:
        KeyError: if a member of :class:`Permission` has no spec. That is a bug in this
            module, not a caller error, and it fails loudly rather than returning a
            plausible-looking default -- ``tests/test_security_matrix.py`` asserts the
            mapping is total so it can never reach a running system.
    """
    return PERMISSION_SPECS[permission]


def permissions_for_context(bounded_context: str) -> frozenset[Permission]:
    """Return every permission owned by ``bounded_context``.

    Used by the permission-derived navigation to decide whether a role sees a section at
    all: a role holding nothing in a context gets no heading for it.
    """
    return frozenset(
        code for code, spec in PERMISSION_SPECS.items() if spec.bounded_context == bounded_context
    )
