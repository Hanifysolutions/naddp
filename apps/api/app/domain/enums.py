"""Domain enumerations and the three workflow transition tables.

Pure data. No I/O, no SQLAlchemy, no FastAPI: ``app.domain`` must stay importable in
isolation (see ``app/domain/__init__.py``).

Every enum subclasses ``(str, Enum)`` so a member serialises through Pydantic v2 and
FastAPI as its *value*, and a value arriving on the wire compares equal to the member.
The persisted representation is the same string: ``app.models.base.pg_enum`` declares
each native Postgres enum with ``values_callable``, so the database labels are exactly
these ``value`` strings. Wire, ORM and DDL therefore cannot silently diverge.

Identifiers are ``UPPER_SNAKE_CASE`` throughout. ``BUILD_BIBLE.md`` section 5 writes the
classification zones with hyphens (``MISSION-INTERNAL``); per ADR-0006 those are display
labels only and are never valid on the wire.

Every class below carries ``# noqa: UP042``. Ruff would prefer ``enum.StrEnum``, and for a
greenfield module it would be right -- ``StrEnum`` fixes ``str(member)`` returning
``"RoleCode.ADMIN"`` instead of ``"ADMIN"``. ``(str, Enum)`` is nevertheless the locked
decision for this phase, because the same shape is specified for the seed loader, the
security matrix and the generated web client; changing it here alone would split the
contract. Use ``member.value`` rather than ``str(member)`` in f-strings and SQL literals.
Revisit as a single deliberate change across all four consumers, never file by file.

Binding sources:

* ``BUILD_BIBLE.md`` section 5 (data zones), section 9 (state machines)
* ``docs/workflows.md`` -- the three transition tables below are transcribed from it
* ``docs/architecture/adr/0006-classification-zones.md`` -- dominance and propagation
* ``data/taxonomy/classifications.json`` -- the ranks in :data:`CLASSIFICATION_RANK`
* ``data/demo-seed/citations.json`` -- the ``SourceType`` and ``Jurisdiction`` value sets

This module holds the transition *tables only*. Checking permissions, running guards,
writing the ``audit_events`` row and performing the state write belong to the workflow
service (``docs/workflows.md`` section 4). Deny-by-default is enforced there; the data
that it enforces lives here.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Final

# ---------------------------------------------------------------------------
# Governance: roles, classification, policy outcome
# ---------------------------------------------------------------------------


class RoleCode(str, Enum):  # noqa: UP042
    """The six demo personas.

    Values come from the role grant tables in ``docs/workflows.md`` sections 1-3 and from
    ``data/taxonomy/classifications.json`` ``role_ranks``, which lists the same six.
    Identity is faked by the role picker; the roles themselves are real (``CLAUDE.md`` 2.4).
    """

    AMBASSADOR = "AMBASSADOR"
    DEPUTY = "DEPUTY"
    TRADE_OFFICER = "TRADE_OFFICER"
    CONSULAR_OFFICER = "CONSULAR_OFFICER"
    DIASPORA_OFFICER = "DIASPORA_OFFICER"
    ADMIN = "ADMIN"


class Classification(str, Enum):  # noqa: UP042
    """The four data zones.

    Values come from ``data/taxonomy/classifications.json`` ``classifications[].code``
    and ADR-0006. A fifth zone requires a migration, not merely a code change.
    """

    PUBLIC = "PUBLIC"
    MISSION_INTERNAL = "MISSION_INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    CONSULAR_SENSITIVE = "CONSULAR_SENSITIVE"


#: Dominance rank, mirroring ``data/taxonomy/classifications.json`` ``rank``.
#:
#: This is the **propagation** order only: ``PUBLIC < MISSION_INTERNAL <
#: CONSULAR_SENSITIVE < CONFIDENTIAL``. It is deliberately *not* the access order.
#: ``CONSULAR_SENSITIVE`` is a compartment (need-to-know about a private individual), so a
#: higher rank never implies permission to read it -- ADR-0006 keeps the two rules apart
#: precisely so that "more senior" cannot quietly become "may read everything". The access
#: rule (``min_role_rank_to_read`` plus ``compartment``) belongs to the security layer.
CLASSIFICATION_RANK: Final[Mapping[Classification, int]] = MappingProxyType(
    {
        Classification.PUBLIC: 0,
        Classification.MISSION_INTERNAL: 10,
        Classification.CONSULAR_SENSITIVE: 20,
        Classification.CONFIDENTIAL: 30,
    }
)


def dominant(*values: Classification) -> Classification:
    """Return the dominating classification of ``values`` -- the ADR-0006 ``max`` rule.

    An aggregate takes the highest rank among its parts, and derived content inherits from
    what it was derived from: a brief assembled from a ``PUBLIC`` signal and a
    ``MISSION_INTERNAL`` note is ``MISSION_INTERNAL``; an AI result grounded in a
    ``CONFIDENTIAL`` document is ``CONFIDENTIAL`` however anodyne its prose reads. A
    summary of a secret is a secret.

    With **no** arguments this returns ``MISSION_INTERNAL``, never ``PUBLIC``. An empty
    part list means "we do not know what this was built from", and ADR-0006 point 6 is
    explicit that unclassified material fails closed rather than open. Returning ``PUBLIC``
    as the identity element would make an empty aggregate world-readable, which is the one
    answer that must never be reachable by accident.

    Nothing computes its way *down* the lattice: a downgrade is a deliberate human act that
    writes an ``object.reclassified`` audit row (ADR-0006 point 5, ADR-0004).
    """
    if not values:
        return Classification.MISSION_INTERNAL
    return max(values, key=lambda value: CLASSIFICATION_RANK[value])


class PolicyResult(str, Enum):  # noqa: UP042
    """Outcome of an authorisation decision, recorded on every ``audit_events`` row.

    Fixed by ADR-0003 and ADR-0004: denials are audited, not only successes, which is what
    makes the RBAC story demonstrable rather than merely assertable.
    """

    ALLOW = "ALLOW"
    DENY = "DENY"


# ---------------------------------------------------------------------------
# Workflow states
# ---------------------------------------------------------------------------


class OpportunityStage(str, Enum):  # noqa: UP042
    """Opportunity pipeline stage. ``BUILD_BIBLE.md`` section 9, ``docs/workflows.md`` 1."""

    DETECTED = "DETECTED"
    QUALIFIED = "QUALIFIED"
    CONTACT_PLANNED = "CONTACT_PLANNED"
    CONTACTED = "CONTACTED"
    MEETING = "MEETING"
    NEGOTIATION = "NEGOTIATION"
    PARTNERED = "PARTNERED"
    CLOSED = "CLOSED"


class FollowupStatus(str, Enum):  # noqa: UP042
    """Meeting follow-up state. ``BUILD_BIBLE.md`` section 9, ``docs/workflows.md`` 2.

    This is winning moment #2: ``SENT`` is reachable from ``APPROVED`` and from nowhere
    else. ``DISCARDED`` is the non-success terminal added by ``docs/workflows.md`` section
    2 (``docs/OPEN_QUESTIONS.md`` Q-05).
    """

    DRAFTED = "DRAFTED"
    OFFICER_REVIEW = "OFFICER_REVIEW"
    APPROVED = "APPROVED"
    SENT = "SENT"
    DISCARDED = "DISCARDED"


class CaseStatus(str, Enum):  # noqa: UP042
    """Consular case state. ``BUILD_BIBLE.md`` section 9, ``docs/workflows.md`` section 3.

    ``AWAITING_CITIZEN`` pauses the SLA clock; ``CLOSED`` is the only terminal state and is
    never reopened -- a subsequent matter is a new case carrying ``related_case_id``.
    """

    NEW = "NEW"
    TRIAGED = "TRIAGED"
    ASSIGNED = "ASSIGNED"
    AWAITING_CITIZEN = "AWAITING_CITIZEN"
    IN_REVIEW = "IN_REVIEW"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class ApprovalStatus(str, Enum):  # noqa: UP042
    """Human-approval state of an AI artefact or a consequential action.

    Values are the ``approval_status`` field of the AI Gateway response contract
    (``BUILD_BIBLE.md`` section 4, ``CLAUDE.md`` 2.2). ``PENDING_APPROVAL`` is what the
    Gateway returns for a proposal, and a proposal is data, never an event
    (``docs/workflows.md`` 0.7). ``BLOCKED`` is what the UI renders when one of the section
    6 non-autonomous controls refuses to proceed -- winning moment #2 made visible.
    """

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class ActionStatus(str, Enum):  # noqa: UP042
    """Lifecycle of a meeting action item (``meetings.actions``).

    Ordinary task tracking, deliberately distinct from :class:`FollowupStatus`, which
    governs the approval-gated outbound communication attached to a meeting.
    """

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# Intelligence
# ---------------------------------------------------------------------------


class SignalStatus(str, Enum):  # noqa: UP042
    """Triage state of an intelligence signal.

    ``LINKED`` means the signal has been promoted into an opportunity, which is the
    ``detect`` creation event of the opportunity machine (``docs/workflows.md`` 1, row 1).
    """

    NEW = "NEW"
    TRIAGED = "TRIAGED"
    LINKED = "LINKED"
    DISMISSED = "DISMISSED"


class SignalType(str, Enum):  # noqa: UP042
    """What kind of change a signal reports. Intelligence vocabulary, ``BUILD_BIBLE.md`` 2."""

    POLICY = "POLICY"
    MARKET = "MARKET"
    PROJECT = "PROJECT"
    REGULATORY = "REGULATORY"
    TENDER = "TENDER"
    RESEARCH = "RESEARCH"
    EVENT = "EVENT"
    MEDIA = "MEDIA"


class SourceType(str, Enum):  # noqa: UP042
    """Kind of publisher behind a citation.

    The member set is the ``source_type`` vocabulary of ``data/demo-seed/citations.json``.
    Verified against all 163 registry entries, which use seven of these eight:
    ``government`` (89), ``industry`` (21), ``statistical_agency`` (18), ``university``
    (15), ``diplomatic_mission`` (10), ``multilateral`` (7), ``other`` (3). ``NEWS`` is
    declared but currently unused by the registry.

    The registry spells those values in lower case, while every other identifier in the
    system -- ``classification`` and ``jurisdiction`` in that same file, every taxonomy
    ``code``, and every other enum here -- is ``UPPER_SNAKE_CASE``. Rather than let one
    native Postgres enum type carry lower-case labels and one API field carry a lower-case
    value, the canonical value is upper case and :meth:`_missing_` accepts the registry
    spelling. The normalisation therefore lives in exactly one place instead of being
    sprinkled through the seed loader.
    """

    GOVERNMENT = "GOVERNMENT"
    STATISTICAL_AGENCY = "STATISTICAL_AGENCY"
    UNIVERSITY = "UNIVERSITY"
    INDUSTRY = "INDUSTRY"
    DIPLOMATIC_MISSION = "DIPLOMATIC_MISSION"
    MULTILATERAL = "MULTILATERAL"
    NEWS = "NEWS"
    OTHER = "OTHER"

    @classmethod
    def _missing_(cls, value: object) -> SourceType | None:
        """Accept the lower-case spelling used by ``data/demo-seed/citations.json``.

        ``SourceType("government")`` resolves to ``SourceType.GOVERNMENT``. Anything that
        is not a known member after upper-casing still raises ``ValueError``, so this
        widens the accepted *input* without widening the value set.
        """
        if isinstance(value, str):
            return cls.__members__.get(value.strip().upper())
        return None


class Jurisdiction(str, Enum):  # noqa: UP042
    """Which jurisdiction a source or record belongs to.

    Values are the ``jurisdiction`` vocabulary of ``data/demo-seed/citations.json``,
    verified against all 163 entries: ``AU`` (132), ``NG`` (25), ``INTL`` (6).
    """

    AU = "AU"
    NG = "NG"
    INTL = "INTL"


class BriefStatus(str, Enum):  # noqa: UP042
    """Workflow state of a morning brief: DRAFT -> IN_REVIEW -> APPROVED -> PUBLISHED.

    A brief is a mission product carrying AI-generated analysis over real sources, so it
    goes through the same shape of review as any other: generated as a DRAFT, submitted
    IN_REVIEW, APPROVED by a named human, and only then PUBLISHED to the mission. Nothing
    reaches an Ambassador's screen without a person having approved it.

    ``PUBLISHED`` is terminal. A correction is a new brief, because a published brief is
    what officers acted on that morning and rewriting it would destroy that record.
    """

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"


class BriefItemType(str, Enum):  # noqa: UP042
    """What a brief item points at -- the polymorphic target of ``brief_items``.

    One member per bounded context in ``BUILD_BIBLE.md`` section 8 that produces a
    briefable artefact.
    """

    SIGNAL = "SIGNAL"
    OPPORTUNITY = "OPPORTUNITY"
    CASE = "CASE"
    MEETING = "MEETING"
    KNOWLEDGE = "KNOWLEDGE"


# ---------------------------------------------------------------------------
# Stakeholders
# ---------------------------------------------------------------------------


class OrganisationType(str, Enum):  # noqa: UP042
    """Kind of counterpart organisation. Stakeholder 360 vocabulary, ``BUILD_BIBLE.md`` 12."""

    COMPANY = "COMPANY"
    GOVERNMENT = "GOVERNMENT"
    UNIVERSITY = "UNIVERSITY"
    NGO = "NGO"
    MULTILATERAL = "MULTILATERAL"
    INDUSTRY_BODY = "INDUSTRY_BODY"
    EDUCATION_PROVIDER = "EDUCATION_PROVIDER"


class InteractionType(str, Enum):  # noqa: UP042
    """How an interaction with a stakeholder happened.

    ``docs/workflows.md`` section 1, row 6 makes this load-bearing: ``record_contact``
    requires a linked ``stakeholders.interaction``, so the interaction row is the evidence
    that the approach was actually made.
    """

    EMAIL = "EMAIL"
    CALL = "CALL"
    MEETING = "MEETING"
    EVENT = "EVENT"
    NOTE = "NOTE"


class InteractionDirection(str, Enum):  # noqa: UP042
    """Who initiated an interaction.

    ``INTERNAL`` records mission-side activity that involved no counterpart, which keeps
    internal notes out of the outbound-contact count.
    """

    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    INTERNAL = "INTERNAL"


class InfluenceLevel(str, Enum):  # noqa: UP042
    """How much weight a stakeholder carries on the objectives the mission is pursuing."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RelationshipStrength(str, Enum):  # noqa: UP042
    """Current state of the mission's relationship with a stakeholder.

    ``NONE`` is a real, meaningful value -- an identified but unengaged counterpart -- and
    is distinct from a NULL column, which means "not yet assessed".
    """

    NONE = "NONE"
    WEAK = "WEAK"
    DEVELOPING = "DEVELOPING"
    STRONG = "STRONG"
    STRATEGIC = "STRATEGIC"


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------


class MeetingType(str, Enum):  # noqa: UP042
    """Format of a meeting. Selects the shape of the pre-read the Gateway generates."""

    BILATERAL = "BILATERAL"
    INTRODUCTORY = "INTRODUCTORY"
    SITE_VISIT = "SITE_VISIT"
    ROUNDTABLE = "ROUNDTABLE"
    CALL = "CALL"


class Priority(str, Enum):  # noqa: UP042
    """Shared urgency scale for cases, actions and meetings.

    Used by the consular triage proposal (``docs/workflows.md`` section 3, row 2), which
    suggests a case type, a priority and a rationale -- and never fires the event itself.
    """

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


# ---------------------------------------------------------------------------
# Consular
# ---------------------------------------------------------------------------


class EvidenceType(str, Enum):  # noqa: UP042
    """Kind of artefact attached to a consular case.

    Every attachment to a case is ``CONSULAR_SENSITIVE`` under ADR-0006 whatever its type;
    this enum describes the artefact, never its sensitivity.
    """

    DOCUMENT = "DOCUMENT"
    PHOTO = "PHOTO"
    FORM = "FORM"
    CORRESPONDENCE = "CORRESPONDENCE"
    IDENTITY_PROOF = "IDENTITY_PROOF"
    OTHER = "OTHER"


class CaseEventType(str, Enum):  # noqa: UP042
    """Entry kind on the immutable consular case timeline.

    ``case_events`` is the citizen-summarisable record, written in the same transaction as
    the governance ``audit_events`` row (``docs/workflows.md`` section 3) and carrying no
    content the citizen may not see.
    """

    CREATED = "CREATED"
    STATUS_CHANGE = "STATUS_CHANGE"
    NOTE = "NOTE"
    EVIDENCE_ADDED = "EVIDENCE_ADDED"
    ASSIGNMENT = "ASSIGNMENT"
    DETERMINATION = "DETERMINATION"
    COMMUNICATION = "COMMUNICATION"
    SLA_BREACH = "SLA_BREACH"


# ---------------------------------------------------------------------------
# Knowledge, diaspora, AI
# ---------------------------------------------------------------------------


class KnowledgeStatus(str, Enum):  # noqa: UP042
    """Editorial state of a knowledge article. Only ``APPROVED`` articles may ground an answer."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class ConsentStatus(str, Enum):  # noqa: UP042
    """What a diaspora member has consented to.

    Diaspora profiles are governed by consent rather than by the consular compartment
    (``data/taxonomy/classifications.json``, ``DIASPORA_OFFICER``), so this column -- not a
    classification -- decides whether a profile may be listed or contacted. ``NOT_GIVEN``
    is the default and permits neither, and ``WITHDRAWN`` is retained rather than deleted so
    that the withdrawal itself is auditable.
    """

    NOT_GIVEN = "NOT_GIVEN"
    GIVEN_DIRECTORY_ONLY = "GIVEN_DIRECTORY_ONLY"
    GIVEN_CONTACTABLE = "GIVEN_CONTACTABLE"
    WITHDRAWN = "WITHDRAWN"


class AiPurpose(str, Enum):  # noqa: UP042
    """The closed set of AI Gateway purposes.

    ``BUILD_BIBLE.md`` section 4: every call is ``gateway.generate(purpose, ...)``, and the
    purpose selects the prompt, the retrieval scope, the output schema and the
    deterministic cached fallback. A purpose that is not a member here has no fallback and
    therefore must not run -- the demo may never dead-end (``CLAUDE.md`` 2.5).
    """

    MORNING_BRIEF = "MORNING_BRIEF"
    OPPORTUNITY_SCORE = "OPPORTUNITY_SCORE"
    MEETING_PREP = "MEETING_PREP"
    MEETING_FOLLOWUP = "MEETING_FOLLOWUP"
    CONSULAR_TRIAGE = "CONSULAR_TRIAGE"
    KNOWLEDGE_ANSWER = "KNOWLEDGE_ANSWER"
    DIASPORA_MATCH = "DIASPORA_MATCH"


# ---------------------------------------------------------------------------
# Transition tables -- transcribed from docs/workflows.md
# ---------------------------------------------------------------------------
#
# Each table maps ``(from_state, event) -> to_state`` and is deny-by-default: a pair that
# does not appear is illegal and raises ``IllegalTransition`` (HTTP 409). The client sends
# an *event*, never a target state; the server computes the target from these tables
# (``docs/workflows.md`` 0.1-0.2).
#
# The creation rows in ``docs/workflows.md`` (opportunity ``detect``, follow-up ``draft``,
# case ``intake``) have no ``from_state`` and so cannot be keyed here. They are recorded as
# the ``*_CREATION_EVENT`` / ``*_INITIAL`` constants beside each table.
#
# The required permission, audit action, reason requirement and guards for each row belong
# to the workflow service (``docs/workflows.md`` section 4). Only the shape of each machine
# is here, so the domain layer stays free of I/O and the tables can be tested exhaustively
# over the cartesian product of states x events.
#
# ``MappingProxyType`` makes each table genuinely immutable. A service cannot widen a
# machine at runtime, which is the failure mode that would quietly defeat deny-by-default.

OPPORTUNITY_CREATION_EVENT: Final[str] = "detect"
OPPORTUNITY_INITIAL: Final[OpportunityStage] = OpportunityStage.DETECTED

#: ``docs/workflows.md`` section 1, rows 2-14. Row 14 (``revert``) steps back exactly one
#: stage and never crosses a terminal.
OPPORTUNITY_TRANSITIONS: Final[Mapping[tuple[OpportunityStage, str], OpportunityStage]] = (
    MappingProxyType(
        {
            # Forward path.
            (OpportunityStage.DETECTED, "qualify"): OpportunityStage.QUALIFIED,
            (OpportunityStage.QUALIFIED, "plan_contact"): OpportunityStage.CONTACT_PLANNED,
            (OpportunityStage.CONTACT_PLANNED, "record_contact"): OpportunityStage.CONTACTED,
            (OpportunityStage.CONTACTED, "schedule_meeting"): OpportunityStage.MEETING,
            (OpportunityStage.MEETING, "enter_negotiation"): OpportunityStage.NEGOTIATION,
            # Commitment control (BUILD_BIBLE section 6): AMBASSADOR or DEPUTY, never AI.
            (OpportunityStage.NEGOTIATION, "partner"): OpportunityStage.PARTNERED,
            # Exit to the single non-success terminal. Always carries a reason.
            (OpportunityStage.DETECTED, "dismiss"): OpportunityStage.CLOSED,
            (OpportunityStage.QUALIFIED, "close"): OpportunityStage.CLOSED,
            (OpportunityStage.CONTACT_PLANNED, "close"): OpportunityStage.CLOSED,
            (OpportunityStage.CONTACTED, "close"): OpportunityStage.CLOSED,
            (OpportunityStage.MEETING, "close"): OpportunityStage.CLOSED,
            (OpportunityStage.NEGOTIATION, "close"): OpportunityStage.CLOSED,
            # One-step correction of a mis-advance. DEPUTY / AMBASSADOR only.
            (OpportunityStage.QUALIFIED, "revert"): OpportunityStage.DETECTED,
            (OpportunityStage.CONTACT_PLANNED, "revert"): OpportunityStage.QUALIFIED,
            (OpportunityStage.CONTACTED, "revert"): OpportunityStage.CONTACT_PLANNED,
            (OpportunityStage.MEETING, "revert"): OpportunityStage.CONTACTED,
            (OpportunityStage.NEGOTIATION, "revert"): OpportunityStage.MEETING,
        }
    )
)

#: Neither is reopenable. A revived opportunity is a new ``DETECTED`` row carrying
#: ``superseded_opportunity_id`` (``docs/workflows.md`` section 1).
OPPORTUNITY_TERMINAL: Final[frozenset[OpportunityStage]] = frozenset(
    {OpportunityStage.PARTNERED, OpportunityStage.CLOSED}
)


FOLLOWUP_CREATION_EVENT: Final[str] = "draft"
FOLLOWUP_INITIAL: Final[FollowupStatus] = FollowupStatus.DRAFTED

#: ``docs/workflows.md`` section 2, rows 2-10.
#:
#: The load-bearing invariant of winning moment #2: ``SENT`` appears as a target exactly
#: once, from ``APPROVED``. There is no event, no permission, no administrative path and no
#: AI purpose that reaches ``SENT`` from any other state, and a test asserts that directly
#: over this table.
FOLLOWUP_TRANSITIONS: Final[Mapping[tuple[FollowupStatus, str], FollowupStatus]] = MappingProxyType(
    {
        # Self-transition: audited because the content of an outbound communication
        # changed, and the artefact an approver saw must be the artefact that is sent.
        (FollowupStatus.DRAFTED, "edit"): FollowupStatus.DRAFTED,
        (FollowupStatus.DRAFTED, "submit_for_review"): FollowupStatus.OFFICER_REVIEW,
        # External-outreach control (section 6): the approver must not be the drafter.
        (FollowupStatus.OFFICER_REVIEW, "approve"): FollowupStatus.APPROVED,
        (FollowupStatus.OFFICER_REVIEW, "request_changes"): FollowupStatus.DRAFTED,
        # Diplomatic-communication control (section 6). Legal ONLY from APPROVED.
        (FollowupStatus.APPROVED, "send"): FollowupStatus.SENT,
        (FollowupStatus.APPROVED, "revoke_approval"): FollowupStatus.DRAFTED,
        (FollowupStatus.DRAFTED, "discard"): FollowupStatus.DISCARDED,
        (FollowupStatus.OFFICER_REVIEW, "discard"): FollowupStatus.DISCARDED,
        (FollowupStatus.APPROVED, "discard"): FollowupStatus.DISCARDED,
    }
)

#: A follow-up to a sent follow-up is a new artefact (``docs/workflows.md`` section 2).
FOLLOWUP_TERMINAL: Final[frozenset[FollowupStatus]] = frozenset(
    {FollowupStatus.SENT, FollowupStatus.DISCARDED}
)


CASE_CREATION_EVENT: Final[str] = "intake"
CASE_INITIAL: Final[CaseStatus] = CaseStatus.NEW

#: ``docs/workflows.md`` section 3, rows 2-21.
#:
#: Unreachable by design: there is no path from ``NEW`` or ``TRIAGED`` directly to
#: ``RESOLVED``. A determination requires an assigned, accountable officer.
CASE_TRANSITIONS: Final[Mapping[tuple[CaseStatus, str], CaseStatus]] = MappingProxyType(
    {
        # Determination control (section 6): a human confirms type, priority, classification.
        (CaseStatus.NEW, "triage"): CaseStatus.TRIAGED,
        (CaseStatus.TRIAGED, "assign"): CaseStatus.ASSIGNED,
        # Self-transition: records both from_assignee_id and assignee_id.
        (CaseStatus.ASSIGNED, "reassign"): CaseStatus.ASSIGNED,
        (CaseStatus.ASSIGNED, "begin_review"): CaseStatus.IN_REVIEW,
        # request_information pauses the SLA clock wherever it is fired.
        (CaseStatus.ASSIGNED, "request_information"): CaseStatus.AWAITING_CITIZEN,
        (CaseStatus.IN_REVIEW, "request_information"): CaseStatus.AWAITING_CITIZEN,
        (CaseStatus.ESCALATED, "request_information"): CaseStatus.AWAITING_CITIZEN,
        # Resumes the clock and records the paused duration.
        (CaseStatus.AWAITING_CITIZEN, "information_received"): CaseStatus.IN_REVIEW,
        (CaseStatus.ASSIGNED, "escalate"): CaseStatus.ESCALATED,
        (CaseStatus.AWAITING_CITIZEN, "escalate"): CaseStatus.ESCALATED,
        (CaseStatus.IN_REVIEW, "escalate"): CaseStatus.ESCALATED,
        (CaseStatus.ESCALATED, "return_to_officer"): CaseStatus.IN_REVIEW,
        # Determination control (section 6). Never AI-initiated.
        (CaseStatus.IN_REVIEW, "resolve"): CaseStatus.RESOLVED,
        (CaseStatus.ESCALATED, "resolve"): CaseStatus.RESOLVED,
        # Restarts the SLA clock with a fresh budget; the original budget is retained.
        (CaseStatus.RESOLVED, "reopen"): CaseStatus.IN_REVIEW,
        # Case-closure control (section 6). Always carries a reason.
        (CaseStatus.NEW, "close"): CaseStatus.CLOSED,
        (CaseStatus.TRIAGED, "close"): CaseStatus.CLOSED,
        (CaseStatus.AWAITING_CITIZEN, "close"): CaseStatus.CLOSED,
        (CaseStatus.ESCALATED, "close"): CaseStatus.CLOSED,
        (CaseStatus.RESOLVED, "close"): CaseStatus.CLOSED,
    }
)

#: ``CLOSED`` is the only terminal state and is never reopened: reopening would make the
#: case timeline non-monotonic, and that timeline is the record a citizen and an auditor
#: rely on. A subsequent matter is a new case carrying ``related_case_id``.
CASE_TERMINAL: Final[frozenset[CaseStatus]] = frozenset({CaseStatus.CLOSED})

#: Brief workflow. Same shape as the other three machines: a table of (from, event) -> to,
#: so a service cannot invent a transition and a reviewer can read the whole machine.
BRIEF_TRANSITIONS: Final[Mapping[tuple[BriefStatus, str], BriefStatus]] = MappingProxyType(
    {
        (BriefStatus.DRAFT, "submit"): BriefStatus.IN_REVIEW,
        (BriefStatus.IN_REVIEW, "approve"): BriefStatus.APPROVED,
        # Sending it back is a first-class outcome, not an error: a brief with a bad
        # citation must have somewhere to go other than forward.
        (BriefStatus.IN_REVIEW, "return_to_author"): BriefStatus.DRAFT,
        (BriefStatus.APPROVED, "publish"): BriefStatus.PUBLISHED,
        (BriefStatus.APPROVED, "return_to_author"): BriefStatus.DRAFT,
    }
)

#: PUBLISHED is terminal. A correction is a new brief; see BriefStatus.
BRIEF_TERMINAL: Final[frozenset[BriefStatus]] = frozenset({BriefStatus.PUBLISHED})


class ChunkCollection(str, Enum):  # noqa: UP042
    """Logical retrieval collection (Arch section 8).

    Internal notes and public intelligence stay in **separate logical collections even on
    shared infrastructure**. They live in one physical table because one HNSW index over
    one table is cheaper to keep warm than three, but every retrieval call names the
    collections it is allowed to touch and the filter is applied in SQL before the
    similarity operator runs. A caller cannot reach a collection by forgetting to
    restrict it: the parameter is required, and there is no "all collections" value.
    """

    #: Public source material: documents ingested from the citation registry. Everything
    #: here has a resolving public URL, so leaking it is not a disclosure.
    PUBLIC_INTELLIGENCE = "PUBLIC_INTELLIGENCE"
    #: The mission's own analysis, capture notes and internal assessments. Never mixed
    #: into a public-intelligence answer.
    INTERNAL_NOTES = "INTERNAL_NOTES"
    #: Curated knowledge-base articles. Retrieval here carries the extra approval,
    #: audience and validity-window filters (Week 3 grounded answers).
    KNOWLEDGE = "KNOWLEDGE"


class KnowledgeAudience(str, Enum):  # noqa: UP042
    """Who a knowledge article is written for.

    Independent of classification. Classification answers "is this person cleared to see
    it"; audience answers "was this written for them". A trade-desk playbook is not
    CONFIDENTIAL, but surfacing it as the grounded answer to a consular officer's question
    is still wrong, so the two filters compose rather than substitute.
    """

    ALL_STAFF = "ALL_STAFF"
    TRADE = "TRADE"
    CONSULAR = "CONSULAR"
    DIASPORA = "DIASPORA"
    SENIOR = "SENIOR"


#: Which audiences each role may be served from the knowledge base. ``ALL_STAFF`` is in
#: every set; the compartment and clearance checks still apply on top.
KNOWLEDGE_AUDIENCE_BY_ROLE: Final[Mapping[RoleCode, frozenset[KnowledgeAudience]]] = (
    MappingProxyType(
        {
            RoleCode.AMBASSADOR: frozenset(KnowledgeAudience),
            RoleCode.DEPUTY: frozenset(KnowledgeAudience),
            RoleCode.TRADE_OFFICER: frozenset(
                {KnowledgeAudience.ALL_STAFF, KnowledgeAudience.TRADE}
            ),
            RoleCode.CONSULAR_OFFICER: frozenset(
                {KnowledgeAudience.ALL_STAFF, KnowledgeAudience.CONSULAR}
            ),
            RoleCode.DIASPORA_OFFICER: frozenset(
                {KnowledgeAudience.ALL_STAFF, KnowledgeAudience.DIASPORA}
            ),
            # ADMIN holds no business-domain read at all (Q-02b), so it is served nothing.
            RoleCode.ADMIN: frozenset(),
        }
    )
)
