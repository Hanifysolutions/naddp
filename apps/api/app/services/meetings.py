"""Meetings (Blueprint section 5, P1): the diary, the pre-read, and the follow-up on the record.

The read side of the Meetings bounded context. ``app.services.followups`` owns the follow-up
machine and every write over it; this module decides what a caller may *see* -- the meeting
index, one meeting in full, and the approval queue -- and assembles it, never in a route
handler (``CLAUDE.md`` section 5).

**Authorisation before assembly, in SQL.** Every list here -- meetings, attendees'
identities, follow-ups, the approval queue, the names of counterparts and opportunities --
carries the caller's clearance predicate inside the statement rather than filtering rows
afterwards, so no count, total or "3 more" can be differenced into knowledge of what was
withheld. A single meeting fetched by id is the one place :func:`assert_may_read` is the
primary check: the caller already asserted the id, and a 403 naming the zone is what makes
the control legible (the same call the opportunity detail route makes).

**Withheld, never guessed.** A meeting the caller may read can point at an organisation, an
opportunity or a stakeholder they may not. The tie is shown -- the id stays -- and the
content is not: the name or title is ``None``, and an attendee carries ``withheld=True``.
Nothing is substituted, because a plausible placeholder on a diplomatic screen is a
fabrication with a nicer font.

**The pre-read is rendered from what was stored, defensively.** ``meetings.pre_read_result``
is documented as a validated ``MeetingPrepResult``, but it is JSONB and this module may not
import that schema (ADR-0001). It is parsed by hand, field by field, and a blob that does
not parse is ``pre_read=None`` plus a warning in the log -- never a 500 on the page an
officer opens on the way into the room. Its citations are resolved through an injected
:data:`CitationResolver` port, bound in the route's composition root exactly as
``app.services.stakeholders`` binds its own; an id the registry does not carry, or carries
unverified, is dropped from the evidence and logged rather than rendered as a claim nobody
can check (``CLAUDE.md`` rule 2.6).

**AI drafting is offered only where it cannot fabricate.** ``app.ai.purposes.scenario_for``
slugs ``subject_ref``, the AI routes pass a meeting UUID, and so every meeting resolves to
the purpose's ``__default__`` snapshot -- and both default meeting snapshots are about the
hero counterpart. In fallback mode an AI draft for any other meeting would put another
meeting's text on this one, on stage. :func:`ai_draft_availability` therefore offers a draft
only for a meeting with a *pinned scenario* (:func:`pinned_ai_scenario`) whose follow-up
snapshot actually exists on disk, below the purpose's classification ceiling, with no live
follow-up, to a caller who may draft. Every "no" carries a sentence the UI shows verbatim.

**Routing decisions are embedded behind the trace route's own gates.** A follow-up's or a
pre-read's ``ai_traces`` row is included only when the caller holds ``read:ai_trace`` AND
clears ``dominant(data_class, result_class)`` -- the two gates of
``GET /v1/ai/traces/{trace_id}``, re-applied here as ``app.services.briefs.trace_for_brief``
re-applies them, so that embedding is not a side channel around them.

**ADR-0001.** Nothing here imports ``app.ai``. The two facts this module needs from the
Gateway's side -- the default scenario key and the follow-up purpose's ceiling -- are
restated as constants, and ``tests/test_meetings_api.py`` asserts they still agree.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import SNAPSHOT_DIR, snapshot_path
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.domain.enums import (
    CLASSIFICATION_RANK,
    AiPurpose,
    ApprovalStatus,
    Classification,
    FollowupAction,
    FollowupStatus,
    MeetingType,
    RoleCode,
    dominant,
)
from app.models.ai import AiTrace
from app.models.governance import User
from app.models.intelligence import Document
from app.models.meetings import (
    FOLLOWUP_LIVE_STATUSES,
    Meeting,
    MeetingAttendee,
    MeetingFollowup,
)
from app.models.opportunities import Opportunity
from app.models.stakeholders import Organisation, Stakeholder
from app.security.deps import assert_may_read, readable_classifications
from app.security.permissions import Permission
from app.security.principal import DEMO_PERSONAS, Principal
from app.services.followups import (
    DISPATCH_IS_SIMULATED,
    FOLLOWUP_OBJECT_TYPE,
    MEETING_OBJECT_TYPE,
    PersonRef,
    available_actions,
    eligible_approvers,
    followup_zone,
    live_followup,
)

__all__ = [
    "AI_DRAFT_LIVE_FOLLOWUP_REASON",
    "AI_DRAFT_MAX_CLASSIFICATION",
    "AI_DRAFT_NO_PERMISSION_REASON",
    "AI_DRAFT_NO_SCENARIO_REASON",
    "AI_MEETING_NO_SNAPSHOT_REASON",
    "DEFAULT_AI_SCENARIO",
    "MEETING_FOLLOWUP_SNAPSHOT_PURPOSE",
    "MEETING_PREP_SNAPSHOT_PURPOSE",
    "REASON_AI_DRAFT_UNAVAILABLE",
    "ApprovalQueue",
    "ApprovalQueueItem",
    "Attendee",
    "CitationResolver",
    "CitedSource",
    "FollowupApproval",
    "FollowupSummary",
    "FollowupView",
    "MeetingDetail",
    "MeetingIndex",
    "MeetingRow",
    "PreReadEvidence",
    "PreReadResult",
    "PreReadView",
    "TalkingPointView",
    "ai_draft_availability",
    "approval_queue",
    "followup_for_meeting",
    "followup_view",
    "get_meeting_detail",
    "has_meeting_snapshot",
    "list_meetings",
    "parse_pre_read_result",
    "pinned_ai_scenario",
    "readable_trace",
]

_logger = get_logger(__name__)

#: The Gateway's last-resort scenario key, ``app.ai.purposes.DEFAULT_SCENARIO``. Restated
#: because this module may not import ``app.ai`` (ADR-0001); a test asserts the two agree.
#: A trace recorded under it served a generic snapshot, so it pins nothing.
DEFAULT_AI_SCENARIO: Final[str] = "__default__"

#: The ceiling of the ``MEETING_FOLLOWUP`` purpose (``app.ai.purposes``), restated for the
#: same reason. Above it the Gateway answers ``BLOCKED``, so offering a draft would offer a
#: refusal.
AI_DRAFT_MAX_CLASSIFICATION: Final[Classification] = Classification.MISSION_INTERNAL

#: The snapshot file stem prefixes the Gateway's fallback reads for a meeting pre-read and a
#: follow-up draft (``app.ai.fallback.snapshot_file`` lower-cases the purpose value).
MEETING_PREP_SNAPSHOT_PURPOSE: Final[str] = AiPurpose.MEETING_PREP.value.lower()
MEETING_FOLLOWUP_SNAPSHOT_PURPOSE: Final[str] = AiPurpose.MEETING_FOLLOWUP.value.lower()

#: ``reason`` on the 409 answered when no AI output is offered for a meeting -- by
#: ``POST /v1/meetings/{meeting_id}/followups`` and by both ``/v1/ai/meetings`` routes.
REASON_AI_DRAFT_UNAVAILABLE: Final[str] = "ai_draft_unavailable"

#: Why the ``/v1/ai/meetings/{meeting_id}/prep`` and ``/followup`` routes produce nothing for a
#: meeting with no pinned scenario of its own. Shown verbatim.
AI_MEETING_NO_SNAPSHOT_REASON: Final[str] = (
    "No deterministic fallback covers this meeting, so no AI output is produced for it. The "
    "platform never shows a pre-read or a draft that was written for a different meeting."
)

#: Why no AI draft is offered. Sentences, because the UI renders them verbatim.
AI_DRAFT_NO_PERMISSION_REASON: Final[str] = (
    "Your role does not hold draft:meeting_followup, so it may not draft follow-ups."
)
AI_DRAFT_LIVE_FOLLOWUP_REASON: Final[str] = (
    "This meeting already has a follow-up in progress. Send it or discard it before "
    "drafting another."
)
AI_DRAFT_NO_SCENARIO_REASON: Final[str] = (
    "No meeting-specific AI draft is available for this meeting. The platform will not "
    "offer a generic one, because a generic draft would put another meeting's content into "
    "this meeting's follow-up."
)

#: How an officer whose ``users`` row cannot be found is named. Honest rather than
#: plausible: the foreign keys make this unreachable, and if it is ever reached the screen
#: must say so rather than invent a person.
_UNRESOLVED_PERSON: Final[str] = "Unrecorded officer"

#: Demo persona role by user id, so a :class:`PersonRef` can name the role an officer holds.
_PERSONA_ROLE_BY_USER: Final[Mapping[uuid.UUID, RoleCode]] = MappingProxyType(
    {persona.user_id: role for role, persona in DEMO_PERSONAS.items()}
)


# ---------------------------------------------------------------------------
# The citation port (ADR-0001)
# ---------------------------------------------------------------------------


class CitedSource(Protocol):
    """The shape :data:`CitationResolver` returns: a subset of the registry entry.

    Structural, so this module never imports ``app.ai``. ``supports_claims`` is here, unlike
    in ``app.services.stakeholders.SourceLike``, because a pre-read's evidence renders the
    same six keys a brief's does (Q-23), and the quote is the first claim the registry
    records the page as supporting -- never a paraphrase.
    """

    @property
    def id(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def url(self) -> str: ...
    @property
    def publisher(self) -> str: ...
    @property
    def verified(self) -> bool: ...
    @property
    def supports_claims(self) -> Sequence[str]: ...


#: Resolves citation ids to registry entries. Bound in ``app/api/v1/meetings.py``.
CitationResolver = Callable[[], Mapping[str, CitedSource]]


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FollowupSummary:
    """The one follow-up a meeting row shows: the live one, else the most recent."""

    id: uuid.UUID
    status: FollowupStatus
    subject: str
    updated_at: datetime
    #: The follow-up's own zone, so the route can report what it served. Not on the wire.
    classification: Classification


@dataclass(frozen=True, slots=True)
class MeetingRow:
    """One line of the meeting index."""

    id: uuid.UUID
    title: str
    meeting_type: MeetingType
    scheduled_start: datetime
    scheduled_end: datetime
    location: str | None
    classification: Classification
    organisation_id: uuid.UUID | None
    #: ``None`` when there is no counterpart, or when the caller may not read it.
    organisation_name: str | None
    opportunity_id: uuid.UUID | None
    #: ``None`` when there is no opportunity, or when the caller may not read it.
    opportunity_title: str | None
    owner_name: str | None
    attendee_count: int
    has_pre_read: bool
    followup: FollowupSummary | None


@dataclass(frozen=True, slots=True)
class MeetingIndex:
    """The diary: what is coming, and what has happened, narrowed to the caller's zones."""

    upcoming: tuple[MeetingRow, ...]
    recent: tuple[MeetingRow, ...]
    total: int
    #: The dominant zone of everything served, for ``record_access``.
    served_classification: Classification


@dataclass(frozen=True, slots=True)
class Attendee:
    """Who is in the room. The identity is withheld when the caller may not read the person."""

    stakeholder_id: uuid.UUID
    full_name: str | None
    organisation_name: str | None
    attendee_role: str
    is_confirmed: bool
    withheld: bool


@dataclass(frozen=True, slots=True)
class TalkingPointView:
    """One thing to say, and the ids of the sources that let the officer say it."""

    point: str
    detail: str
    #: Only ids that resolved to a VERIFIED registry entry, so every one is in the evidence.
    citation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreReadResult:
    """The structured pre-read, as parsed from ``meetings.pre_read_result``."""

    meeting_ref: str
    counterpart: str
    objectives: tuple[str, ...]
    talking_points: tuple[TalkingPointView, ...]
    questions_to_ask: tuple[str, ...]
    sensitivities: tuple[str, ...]
    #: 0-1 as the Gateway produced it, or ``None`` when the blob did not carry one.
    confidence: float | None


@dataclass(frozen=True, slots=True)
class PreReadEvidence:
    """One source behind the pre-read, in the brief's evidence shape (Q-23)."""

    citation_id: str
    document_id: uuid.UUID | None
    title: str
    quote: str | None
    url: str
    publisher: str


@dataclass(frozen=True, slots=True)
class PreReadView:
    """The pre-read in the AI response shape: result, evidence, trace id, approval status."""

    result: PreReadResult
    evidence: tuple[PreReadEvidence, ...]
    trace_id: uuid.UUID | None
    approval_status: ApprovalStatus
    #: The routing decision, when this caller may inspect it (see the module docstring).
    trace: AiTrace | None


@dataclass(frozen=True, slots=True)
class FollowupApproval:
    """Who could approve a follow-up, and whether the caller is one of them right now."""

    eligible_approvers: tuple[PersonRef, ...]
    caller_is_drafter: bool
    caller_may_approve: bool


@dataclass(frozen=True, slots=True)
class FollowupView:
    """One follow-up as a particular caller sees it. Copied out of the row, so it outlives
    the transaction it was read in."""

    id: uuid.UUID
    meeting_id: uuid.UUID
    status: FollowupStatus
    subject: str
    recipients: tuple[str, ...]
    body: str
    #: The zone that governs it: ``dominant(own zone, meeting zone)``.
    classification: Classification
    is_live: bool
    is_ai_drafted: bool
    trace_id: uuid.UUID | None
    trace: AiTrace | None
    drafted_by: PersonRef
    drafted_at: datetime
    submitted_by: PersonRef | None
    submitted_at: datetime | None
    approved_by: PersonRef | None
    approved_at: datetime | None
    sent_by: PersonRef | None
    sent_at: datetime | None
    discarded_by: PersonRef | None
    discarded_at: datetime | None
    discard_reason: str | None
    supersedes_followup_id: uuid.UUID | None
    approval: FollowupApproval
    available_actions: tuple[FollowupAction, ...]
    dispatch_is_simulated: bool


@dataclass(frozen=True, slots=True)
class MeetingDetail:
    """One meeting in full, as this caller may see it."""

    id: uuid.UUID
    title: str
    meeting_type: MeetingType
    scheduled_start: datetime
    scheduled_end: datetime
    location: str | None
    virtual_link: str | None
    classification: Classification
    agenda: str
    organisation_id: uuid.UUID | None
    organisation_name: str | None
    opportunity_id: uuid.UUID | None
    opportunity_title: str | None
    owner_name: str | None
    attendees: tuple[Attendee, ...]
    pre_read: PreReadView | None
    followups: tuple[FollowupView, ...]
    ai_draft_available: bool
    ai_draft_unavailable_reason: str | None
    pinned_ai_scenario: str | None
    #: The dominant zone of everything served, for ``record_access``.
    served_classification: Classification


@dataclass(frozen=True, slots=True)
class ApprovalQueueItem:
    """One follow-up waiting on a named human, with the meeting it follows up."""

    followup: FollowupView
    meeting_id: uuid.UUID
    meeting_title: str
    meeting_type: MeetingType
    scheduled_start: datetime
    organisation_name: str | None


@dataclass(frozen=True, slots=True)
class ApprovalQueue:
    """Every follow-up in ``OFFICER_REVIEW`` the caller may read, oldest submission first."""

    items: tuple[ApprovalQueueItem, ...]
    total: int
    served_classification: Classification


# ---------------------------------------------------------------------------
# Name resolution (one query per kind, never one per row)
# ---------------------------------------------------------------------------


def _people(session: Session, user_ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, PersonRef]:
    """Resolve officer ids to named people in one query.

    ``users`` rows are not classified, so no clearance predicate applies: the name of the
    officer who drafted or approved something the caller may read is part of that thing.
    """
    wanted = {user_id for user_id in user_ids if user_id is not None}
    if not wanted:
        return {}
    rows = session.execute(
        select(User.id, User.full_name, User.title).where(User.id.in_(wanted))
    ).tuples()
    people: dict[uuid.UUID, PersonRef] = {}
    for user_id, full_name, title in rows:
        role = _PERSONA_ROLE_BY_USER.get(user_id)
        persona_title = DEMO_PERSONAS[role].title if role is not None else None
        people[user_id] = PersonRef(
            user_id=user_id,
            full_name=full_name,
            title=title or persona_title,
            role=role,
        )
    return people


def _person(people: Mapping[uuid.UUID, PersonRef], user_id: uuid.UUID | None) -> PersonRef | None:
    """The resolved person for ``user_id``, ``None`` for no id, and an honest label if lost."""
    if user_id is None:
        return None
    found = people.get(user_id)
    if found is not None:
        return found
    _logger.warning(
        "meeting.person_unresolved",
        user_id=str(user_id),
        hint="A follow-up names a users row that does not exist; the foreign key should forbid it.",
    )
    return PersonRef(user_id=user_id, full_name=_UNRESOLVED_PERSON, title=None, role=None)


def _organisation_names(
    session: Session, zones: Sequence[Classification], ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    """Names of the organisations the caller may read, in one query. Others are absent."""
    wanted = {identifier for identifier in ids if identifier is not None}
    if not wanted:
        return {}
    rows = session.execute(
        select(Organisation.id, Organisation.name).where(
            Organisation.id.in_(wanted), Organisation.classification.in_(zones)
        )
    ).tuples()
    return dict(rows.all())


def _opportunity_titles(
    session: Session, zones: Sequence[Classification], ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    """Titles of the opportunities the caller may read, in one query. Others are absent."""
    wanted = {identifier for identifier in ids if identifier is not None}
    if not wanted:
        return {}
    rows = session.execute(
        select(Opportunity.id, Opportunity.title).where(
            Opportunity.id.in_(wanted), Opportunity.classification.in_(zones)
        )
    ).tuples()
    return dict(rows.all())


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


def _may_inspect(principal: Principal, trace: AiTrace) -> bool:
    """The two gates of ``GET /v1/ai/traces/{trace_id}``, applied to a row already loaded."""
    return principal.has(Permission.READ_AI_TRACE) and principal.may_read(
        dominant(trace.data_class, trace.result_class)
    )


def readable_trace(
    session: Session, principal: Principal, trace_id: uuid.UUID | None
) -> AiTrace | None:
    """Return the ``ai_traces`` row ``trace_id``, if this caller may inspect it.

    The same two gates as ``app.services.briefs.trace_for_brief``: ``read:ai_trace``, and
    clearance for the dominant of the zone the call ran in and the zone of its answer.
    ``None`` rather than an error on a refusal: a routing decision this role may not see is
    absent from the view, and the view still reports the id, so the UI can say it exists.
    """
    if trace_id is None or not principal.has(Permission.READ_AI_TRACE):
        return None
    trace = session.get(AiTrace, trace_id)
    if trace is None or not _may_inspect(principal, trace):
        return None
    return trace


def _inspectable_traces(
    session: Session, principal: Principal, trace_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, AiTrace]:
    """:func:`readable_trace` for many ids in one query.

    The rows are fetched by the ids the caller's own follow-ups name -- this is not a trace
    listing, and nothing about a withheld trace is counted -- so the zone gate is applied
    per row, exactly as the single-object form applies it.
    """
    wanted = {trace_id for trace_id in trace_ids if trace_id is not None}
    if not wanted or not principal.has(Permission.READ_AI_TRACE):
        return {}
    rows = session.scalars(select(AiTrace).where(AiTrace.id.in_(wanted)))
    return {row.id: row for row in rows if _may_inspect(principal, row)}


# ---------------------------------------------------------------------------
# The pre-read
# ---------------------------------------------------------------------------


def _text(value: object) -> str | None:
    """A non-blank string, or ``None``."""
    return value if isinstance(value, str) and value.strip() else None


def _texts(value: object, *, required: bool) -> tuple[str, ...] | None:
    """A list of non-blank strings as a tuple; ``None`` if malformed.

    An absent optional list is an empty tuple. A required list must be present and
    non-empty, because a pre-read with no objectives is not a pre-read.
    """
    if value is None:
        return None if required else ()
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for entry in value:
        text = _text(entry)
        if text is None:
            return None
        items.append(text)
    if required and not items:
        return None
    return tuple(items)


def _talking_points(value: object) -> tuple[TalkingPointView, ...] | None:
    """The talking points, each ``{point, detail, citations}``; ``None`` if malformed."""
    if not isinstance(value, list) or not value:
        return None
    points: list[TalkingPointView] = []
    for entry in value:
        if not isinstance(entry, dict):
            return None
        point = _text(entry.get("point"))
        detail = _text(entry.get("detail"))
        citations = _texts(entry.get("citations"), required=False)
        if point is None or detail is None or citations is None:
            return None
        points.append(TalkingPointView(point=point, detail=detail, citation_ids=citations))
    return tuple(points)


def _parse_pre_read(raw: object) -> tuple[PreReadResult | None, str | None]:
    """Parse a stored pre-read. Returns ``(result, None)`` or ``(None, what was wrong)``."""
    if not isinstance(raw, dict):
        return None, "not an object"
    meeting_ref = _text(raw.get("meeting_ref"))
    if meeting_ref is None:
        return None, "meeting_ref"
    counterpart = _text(raw.get("counterpart"))
    if counterpart is None:
        return None, "counterpart"
    objectives = _texts(raw.get("objectives"), required=True)
    if objectives is None:
        return None, "objectives"
    talking_points = _talking_points(raw.get("talking_points"))
    if talking_points is None:
        return None, "talking_points"
    questions = _texts(raw.get("questions_to_ask"), required=False)
    if questions is None:
        return None, "questions_to_ask"
    sensitivities = _texts(raw.get("sensitivities"), required=False)
    if sensitivities is None:
        return None, "sensitivities"

    raw_confidence = raw.get("confidence")
    confidence: float | None
    if raw_confidence is None:
        confidence = None
    elif (
        isinstance(raw_confidence, bool)
        or not isinstance(raw_confidence, int | float)
        or not 0.0 <= float(raw_confidence) <= 1.0
    ):
        return None, "confidence"
    else:
        confidence = float(raw_confidence)

    return (
        PreReadResult(
            meeting_ref=meeting_ref,
            counterpart=counterpart,
            objectives=objectives,
            talking_points=talking_points,
            questions_to_ask=questions,
            sensitivities=sensitivities,
            confidence=confidence,
        ),
        None,
    )


def parse_pre_read_result(raw: object) -> PreReadResult | None:
    """Parse ``meetings.pre_read_result`` defensively; ``None`` for anything malformed.

    Typed over ``object`` because JSONB returns whatever was written. Lenient about keys it
    does not use and strict about the ones it renders: a talking point with no detail, a
    confidence outside 0-1 or a list that holds a number where a sentence belongs makes the
    whole pre-read unrenderable rather than partly rendered, because half a pre-read reads
    as a complete one.
    """
    result, _problem = _parse_pre_read(raw)
    return result


def _documents_by_citation(
    session: Session, zones: Sequence[Classification], citation_ids: Sequence[str]
) -> dict[str, uuid.UUID]:
    """The ingested ``documents`` row behind each citation, where the caller may read one."""
    if not citation_ids:
        return {}
    rows = session.execute(
        select(Document.citation_id, Document.id)
        .where(Document.citation_id.in_(citation_ids), Document.classification.in_(zones))
        .order_by(Document.citation_id, Document.id)
    ).tuples()
    found: dict[str, uuid.UUID] = {}
    for citation_id, document_id in rows:
        if citation_id is not None:
            found.setdefault(citation_id, document_id)
    return found


def _pre_read_view(
    session: Session,
    principal: Principal,
    meeting: Meeting,
    zones: Sequence[Classification],
    resolve_citations: CitationResolver,
) -> PreReadView | None:
    """The meeting's pre-read, with its citations resolved and its trace gated."""
    if meeting.pre_read_result is None:
        return None
    parsed, problem = _parse_pre_read(meeting.pre_read_result)
    if parsed is None:
        _logger.warning(
            "meeting.pre_read_malformed",
            meeting_id=str(meeting.id),
            problem=problem,
            hint="meetings.pre_read_result did not parse as a MeetingPrepResult; not rendered.",
        )
        return None

    cited: list[str] = []
    for point in parsed.talking_points:
        cited.extend(cid for cid in point.citation_ids if cid not in cited)
    registry = resolve_citations() if cited else {}
    kept: list[CitedSource] = []
    for citation_id in cited:
        entry = registry.get(citation_id)
        if entry is None or not entry.verified:
            _logger.warning(
                "meeting.pre_read_citation_dropped",
                meeting_id=str(meeting.id),
                citation_id=citation_id,
                reason="unknown" if entry is None else "not_verified",
            )
            continue
        kept.append(entry)

    kept_ids = {entry.id for entry in kept}
    documents = _documents_by_citation(session, zones, [entry.id for entry in kept])
    evidence = tuple(
        PreReadEvidence(
            citation_id=entry.id,
            document_id=documents.get(entry.id),
            title=entry.title,
            quote=entry.supports_claims[0] if entry.supports_claims else None,
            url=entry.url,
            publisher=entry.publisher,
        )
        for entry in kept
    )
    result = PreReadResult(
        meeting_ref=parsed.meeting_ref,
        counterpart=parsed.counterpart,
        objectives=parsed.objectives,
        talking_points=tuple(
            TalkingPointView(
                point=point.point,
                detail=point.detail,
                citation_ids=tuple(cid for cid in point.citation_ids if cid in kept_ids),
            )
            for point in parsed.talking_points
        ),
        questions_to_ask=parsed.questions_to_ask,
        sensitivities=parsed.sensitivities,
        confidence=parsed.confidence,
    )

    trace_row = (
        session.get(AiTrace, meeting.pre_read_trace_id)
        if meeting.pre_read_trace_id is not None
        else None
    )
    return PreReadView(
        result=result,
        evidence=evidence,
        trace_id=meeting.pre_read_trace_id,
        # The approval status is the envelope's, recorded on the trace. It is not the
        # substance of the call, so it is reported even to a caller who may not open the
        # trace; the routing decision itself stays behind both gates.
        approval_status=(
            trace_row.approval_status if trace_row is not None else ApprovalStatus.NOT_REQUIRED
        ),
        trace=trace_row if trace_row is not None and _may_inspect(principal, trace_row) else None,
    )


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------


def _view(
    principal: Principal,
    followup: MeetingFollowup,
    people: Mapping[uuid.UUID, PersonRef],
    traces: Mapping[uuid.UUID, AiTrace],
) -> FollowupView:
    """Project one follow-up for one caller. No queries: names and traces are pre-resolved."""
    zone = followup_zone(followup)
    status = followup.status
    is_drafter = principal.user_id == followup.drafted_by_user_id
    terminal = status in {FollowupStatus.SENT, FollowupStatus.DISCARDED}
    drafted_by = _person(people, followup.drafted_by_user_id)
    if drafted_by is None:  # drafted_by_user_id is NOT NULL; this keeps mypy honest
        msg = f"meeting follow-up {followup.id} has no drafter"
        raise RuntimeError(msg)
    recipients = followup.recipients if isinstance(followup.recipients, list) else []
    return FollowupView(
        id=followup.id,
        meeting_id=followup.meeting_id,
        status=status,
        subject=followup.subject,
        recipients=tuple(str(label) for label in recipients),
        body=followup.body,
        classification=zone,
        is_live=status in FOLLOWUP_LIVE_STATUSES,
        is_ai_drafted=followup.trace_id is not None,
        trace_id=followup.trace_id,
        trace=traces.get(followup.trace_id) if followup.trace_id is not None else None,
        drafted_by=drafted_by,
        drafted_at=followup.drafted_at,
        submitted_by=_person(people, followup.submitted_by_user_id),
        submitted_at=followup.submitted_at,
        approved_by=_person(people, followup.approved_by_user_id),
        approved_at=followup.approved_at,
        sent_by=_person(people, followup.sent_by_user_id),
        sent_at=followup.sent_at,
        discarded_by=_person(people, followup.discarded_by_user_id),
        discarded_at=followup.discarded_at,
        discard_reason=followup.discard_reason,
        supersedes_followup_id=followup.supersedes_followup_id,
        approval=FollowupApproval(
            # Nobody can approve a sent or discarded follow-up, so a terminal row names no one.
            eligible_approvers=() if terminal else eligible_approvers(followup),
            caller_is_drafter=is_drafter,
            caller_may_approve=(
                status is FollowupStatus.OFFICER_REVIEW
                and principal.has(Permission.APPROVE_MEETING_FOLLOWUP)
                and not is_drafter
                and principal.may_read(zone)
            ),
        ),
        available_actions=available_actions(followup, principal),
        dispatch_is_simulated=DISPATCH_IS_SIMULATED,
    )


def _followup_views(
    session: Session, principal: Principal, followups: Sequence[MeetingFollowup]
) -> tuple[FollowupView, ...]:
    """Project many follow-ups with one query for names and one for traces."""
    if not followups:
        return ()
    people = _people(
        session,
        (
            user_id
            for followup in followups
            for user_id in (
                followup.drafted_by_user_id,
                followup.submitted_by_user_id,
                followup.approved_by_user_id,
                followup.sent_by_user_id,
                followup.discarded_by_user_id,
            )
        ),
    )
    traces = _inspectable_traces(session, principal, (f.trace_id for f in followups))
    return tuple(_view(principal, followup, people, traces) for followup in followups)


def followup_view(
    session: Session, principal: Principal, followup: MeetingFollowup
) -> FollowupView:
    """One follow-up as ``principal`` sees it: names, gated trace, approval, actions.

    Applies no read authorisation of its own. It is called on a follow-up the caller has
    just been allowed to act on, or one a clearance-filtered query returned; the trace inside
    it is gated regardless.
    """
    return _followup_views(session, principal, [followup])[0]


def followup_for_meeting(
    session: Session, meeting_id: uuid.UUID, followup_id: uuid.UUID
) -> MeetingFollowup:
    """Load a follow-up that belongs to ``meeting_id``, or raise 404.

    A follow-up addressed under the wrong meeting is a 404, not a quiet redirect: the URL
    names both, and acting on a follow-up of a different meeting from the one on screen is
    the mistake the nesting exists to catch. No clearance check here, deliberately: the
    machine applies it itself *and writes the DENY row* that makes the refusal evidence.
    """
    followup = session.get(MeetingFollowup, followup_id)
    if followup is None or followup.meeting_id != meeting_id:
        raise NotFoundError(
            "No follow-up with that id on this meeting.",
            extra={
                "object_type": FOLLOWUP_OBJECT_TYPE,
                "object_id": str(followup_id),
                "meeting_id": str(meeting_id),
            },
        )
    return followup


def _live_first(followups: Iterable[MeetingFollowup]) -> list[MeetingFollowup]:
    """The live follow-up first, then the rest newest drafted first."""
    return sorted(
        followups,
        key=lambda followup: (
            followup.status not in FOLLOWUP_LIVE_STATUSES,
            -followup.drafted_at.timestamp(),
            str(followup.id),
        ),
    )


# ---------------------------------------------------------------------------
# AI drafting
# ---------------------------------------------------------------------------


def pinned_ai_scenario(session: Session, meeting: Meeting) -> str | None:
    """The meeting-specific snapshot scenario an AI call about this meeting should use.

    The non-null, non-``__default__`` ``scenario`` of the meeting's pre-read trace, else of
    its follow-up traces in drafting order. ``None`` means no AI call about this meeting has
    ever been served from a scenario of its own, so a fallback would be a generic snapshot
    -- another meeting's content (see the module docstring).
    """
    candidates: list[uuid.UUID] = []
    if meeting.pre_read_trace_id is not None:
        candidates.append(meeting.pre_read_trace_id)
    candidates.extend(
        trace_id
        for trace_id in session.scalars(
            select(MeetingFollowup.trace_id)
            .where(
                MeetingFollowup.meeting_id == meeting.id,
                MeetingFollowup.trace_id.is_not(None),
            )
            .order_by(MeetingFollowup.drafted_at, MeetingFollowup.id)
        )
        if trace_id is not None
    )
    if not candidates:
        return None
    scenarios = dict(
        session.execute(select(AiTrace.id, AiTrace.scenario).where(AiTrace.id.in_(candidates)))
        .tuples()
        .all()
    )
    for trace_id in candidates:
        scenario = scenarios.get(trace_id)
        if scenario and scenario != DEFAULT_AI_SCENARIO:
            return scenario
    return None


def has_meeting_snapshot(purpose_slug: str, scenario: str) -> bool:
    """Whether the Gateway's fallback holds a ``purpose_slug`` snapshot for exactly ``scenario``.

    ``purpose_slug`` is the snapshot file stem prefix -- :data:`MEETING_PREP_SNAPSHOT_PURPOSE`
    or :data:`MEETING_FOLLOWUP_SNAPSHOT_PURPOSE`. The Gateway serves ``__default__`` when a
    scenario has no file of its own (``app.ai.fallback.resolve_snapshot``), and both default
    meeting snapshots are about the hero counterpart, so a pinned scenario is only safe to
    call the Gateway with if its own file exists. The parent check refuses a scenario that
    would resolve outside the snapshot directory.
    """
    path = snapshot_path(purpose_slug, scenario)
    return path.parent == SNAPSHOT_DIR and path.is_file()


def ai_draft_availability(
    session: Session, principal: Principal, meeting: Meeting
) -> tuple[bool, str | None]:
    """Whether an AI draft of a follow-up is offered on ``meeting`` to ``principal``, and why not.

    In order: the caller must hold ``draft:meeting_followup``; the meeting must hold no live
    follow-up; the meeting's zone must be at or below the follow-up purpose's ceiling (by
    ``CLASSIFICATION_RANK``); and a pinned scenario with its own follow-up snapshot must
    exist. Returns ``(True, None)`` or ``(False, a sentence)``.

    Advisory for the page, and enforced by the draft route, which asks the same question
    before it spends a Gateway call.
    """
    if not principal.has(Permission.DRAFT_MEETING_FOLLOWUP):
        return False, AI_DRAFT_NO_PERMISSION_REASON
    if live_followup(session, meeting.id) is not None:
        return False, AI_DRAFT_LIVE_FOLLOWUP_REASON
    if (
        CLASSIFICATION_RANK[meeting.classification]
        > CLASSIFICATION_RANK[AI_DRAFT_MAX_CLASSIFICATION]
    ):
        return False, (
            f"This meeting is classified {meeting.classification.value}. AI drafting of "
            f"outbound communications is limited to {AI_DRAFT_MAX_CLASSIFICATION.value} "
            "material, because an outbound email is the last place confidential material "
            "should reach."
        )
    scenario = pinned_ai_scenario(session, meeting)
    if scenario is None or not has_meeting_snapshot(MEETING_FOLLOWUP_SNAPSHOT_PURPOSE, scenario):
        return False, AI_DRAFT_NO_SCENARIO_REASON
    return True, None


# ---------------------------------------------------------------------------
# Public reads
# ---------------------------------------------------------------------------


def list_meetings(
    session: Session, principal: Principal, *, now: datetime | None = None
) -> MeetingIndex:
    """The meeting index, narrowed to the caller's zones before the query runs.

    ``upcoming`` is every readable meeting starting at or after ``now``, soonest first;
    ``recent`` is the rest, most recent first. ``total`` counts both and nothing else.
    Each row carries the live follow-up, else the most recent one, of those the caller may
    read -- a follow-up above the caller's zone is not summarised, and not counted either.
    """
    moment = now or datetime.now(UTC)
    zones = readable_classifications(principal)
    rows = session.execute(
        select(
            Meeting.id,
            Meeting.title,
            Meeting.meeting_type,
            Meeting.scheduled_start,
            Meeting.scheduled_end,
            Meeting.location,
            Meeting.classification,
            Meeting.organisation_id,
            Meeting.opportunity_id,
            Meeting.owner_user_id,
            # jsonb_typeof, not IS NOT NULL: SQLAlchemy writes an explicit Python None into
            # a JSONB column as JSON 'null', which IS NOT NULL would count as a pre-read.
            func.coalesce(func.jsonb_typeof(Meeting.pre_read_result) == "object", False).label(
                "has_pre_read"
            ),
        )
        .where(Meeting.classification.in_(zones))
        .order_by(Meeting.scheduled_start, Meeting.id)
    ).all()
    if not rows:
        return MeetingIndex(upcoming=(), recent=(), total=0, served_classification=dominant())

    ids = [row.id for row in rows]
    organisations = _organisation_names(session, zones, (row.organisation_id for row in rows))
    opportunities = _opportunity_titles(session, zones, (row.opportunity_id for row in rows))
    owners = _people(session, (row.owner_user_id for row in rows))
    attendee_counts = dict(
        session.execute(
            select(MeetingAttendee.meeting_id, func.count())
            .where(MeetingAttendee.meeting_id.in_(ids))
            .group_by(MeetingAttendee.meeting_id)
        )
        .tuples()
        .all()
    )

    summaries: dict[uuid.UUID, FollowupSummary] = {}
    followup_rows = session.execute(
        select(
            MeetingFollowup.id,
            MeetingFollowup.meeting_id,
            MeetingFollowup.status,
            MeetingFollowup.subject,
            MeetingFollowup.updated_at,
            MeetingFollowup.classification,
        )
        .where(
            MeetingFollowup.meeting_id.in_(ids),
            MeetingFollowup.classification.in_(zones),
        )
        .order_by(
            MeetingFollowup.meeting_id,
            MeetingFollowup.drafted_at.desc(),
            MeetingFollowup.id.desc(),
        )
    ).tuples()
    for followup_id, meeting_id, status, subject, updated_at, classification in followup_rows:
        current = summaries.get(meeting_id)
        # Rows arrive newest first per meeting, so the first is the latest; the live one,
        # of which there is at most one, replaces it wherever it appears.
        if current is None or (
            current.status not in FOLLOWUP_LIVE_STATUSES and status in FOLLOWUP_LIVE_STATUSES
        ):
            summaries[meeting_id] = FollowupSummary(
                id=followup_id,
                status=status,
                subject=subject,
                updated_at=updated_at,
                classification=classification,
            )

    upcoming: list[MeetingRow] = []
    recent: list[MeetingRow] = []
    for row in rows:
        owner = owners.get(row.owner_user_id) if row.owner_user_id is not None else None
        projected = MeetingRow(
            id=row.id,
            title=row.title,
            meeting_type=row.meeting_type,
            scheduled_start=row.scheduled_start,
            scheduled_end=row.scheduled_end,
            location=row.location,
            classification=row.classification,
            organisation_id=row.organisation_id,
            organisation_name=organisations.get(row.organisation_id)
            if row.organisation_id is not None
            else None,
            opportunity_id=row.opportunity_id,
            opportunity_title=opportunities.get(row.opportunity_id)
            if row.opportunity_id is not None
            else None,
            owner_name=owner.full_name if owner is not None else None,
            attendee_count=int(attendee_counts.get(row.id, 0)),
            has_pre_read=bool(row.has_pre_read),
            followup=summaries.get(row.id),
        )
        (upcoming if row.scheduled_start >= moment else recent).append(projected)
    recent.reverse()

    served = [row.classification for row in rows]
    served.extend(summary.classification for summary in summaries.values())
    return MeetingIndex(
        upcoming=tuple(upcoming),
        recent=tuple(recent),
        total=len(rows),
        served_classification=dominant(*served),
    )


def _attendees(
    session: Session, zones: Sequence[Classification], meeting_id: uuid.UUID
) -> tuple[Attendee, ...]:
    """Who is in the room, with an identity only where the caller may read the person.

    The attendee rows are the meeting's (they carry no zone of their own), so all of them
    are listed; the *names* come from a second, clearance-filtered query, and a person that
    query does not return is ``withheld``. Readable people first, by name.
    """
    rows = (
        session.execute(
            select(
                MeetingAttendee.stakeholder_id,
                MeetingAttendee.attendee_role,
                MeetingAttendee.is_confirmed,
            ).where(MeetingAttendee.meeting_id == meeting_id)
        )
        .tuples()
        .all()
    )
    if not rows:
        return ()
    readable = {
        stakeholder_id: (full_name, organisation_id)
        for stakeholder_id, full_name, organisation_id in session.execute(
            select(Stakeholder.id, Stakeholder.full_name, Stakeholder.organisation_id).where(
                Stakeholder.id.in_([row[0] for row in rows]),
                Stakeholder.classification.in_(zones),
            )
        ).tuples()
    }
    organisations = _organisation_names(
        session, zones, (organisation_id for _name, organisation_id in readable.values())
    )
    attendees: list[Attendee] = []
    for stakeholder_id, attendee_role, is_confirmed in rows:
        person = readable.get(stakeholder_id)
        if person is None:
            attendees.append(
                Attendee(
                    stakeholder_id=stakeholder_id,
                    full_name=None,
                    organisation_name=None,
                    attendee_role=attendee_role,
                    is_confirmed=is_confirmed,
                    withheld=True,
                )
            )
            continue
        full_name, organisation_id = person
        attendees.append(
            Attendee(
                stakeholder_id=stakeholder_id,
                full_name=full_name,
                organisation_name=organisations.get(organisation_id)
                if organisation_id is not None
                else None,
                attendee_role=attendee_role,
                is_confirmed=is_confirmed,
                withheld=False,
            )
        )
    attendees.sort(
        key=lambda attendee: (
            attendee.withheld,
            attendee.full_name or "",
            str(attendee.stakeholder_id),
        )
    )
    return tuple(attendees)


def get_meeting_detail(
    session: Session,
    principal: Principal,
    meeting_id: uuid.UUID,
    *,
    resolve_citations: CitationResolver,
) -> MeetingDetail:
    """One meeting in full: agenda, attendees, pre-read, every readable follow-up.

    Raises:
        NotFoundError: no meeting with that id.
        ClassificationDeniedError: 403 naming the zone, when the caller is not cleared for
            the meeting (:func:`assert_may_read`; see the module docstring).
    """
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise NotFoundError(
            "No meeting with that id.",
            extra={"object_type": MEETING_OBJECT_TYPE, "object_id": str(meeting_id)},
        )
    assert_may_read(principal, meeting)

    zones = readable_classifications(principal)
    organisations = _organisation_names(session, zones, [meeting.organisation_id])
    opportunities = _opportunity_titles(session, zones, [meeting.opportunity_id])
    owners = _people(session, [meeting.owner_user_id])
    owner = owners.get(meeting.owner_user_id) if meeting.owner_user_id is not None else None

    followups = _live_first(
        session.scalars(
            select(MeetingFollowup).where(
                MeetingFollowup.meeting_id == meeting.id,
                MeetingFollowup.classification.in_(zones),
            )
        )
    )
    views = _followup_views(session, principal, followups)
    available, reason = ai_draft_availability(session, principal, meeting)

    return MeetingDetail(
        id=meeting.id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        scheduled_start=meeting.scheduled_start,
        scheduled_end=meeting.scheduled_end,
        location=meeting.location,
        virtual_link=meeting.virtual_link,
        classification=meeting.classification,
        agenda=meeting.agenda,
        organisation_id=meeting.organisation_id,
        organisation_name=organisations.get(meeting.organisation_id)
        if meeting.organisation_id is not None
        else None,
        opportunity_id=meeting.opportunity_id,
        opportunity_title=opportunities.get(meeting.opportunity_id)
        if meeting.opportunity_id is not None
        else None,
        owner_name=owner.full_name if owner is not None else None,
        attendees=_attendees(session, zones, meeting.id),
        pre_read=_pre_read_view(session, principal, meeting, zones, resolve_citations),
        followups=views,
        ai_draft_available=available,
        ai_draft_unavailable_reason=reason,
        pinned_ai_scenario=pinned_ai_scenario(session, meeting),
        served_classification=dominant(
            meeting.classification, *(view.classification for view in views)
        ),
    )


def approval_queue(session: Session, principal: Principal) -> ApprovalQueue:
    """Every follow-up waiting on a named human that the caller may read, oldest first.

    ``OFFICER_REVIEW`` follow-ups whose own zone AND whose meeting's zone are readable, both
    predicates in the one joined statement, ordered by submission. The route gates this on
    ``approve:meeting_followup``; each item still says whether *this* caller may approve it,
    because a drafter holding the permission may not approve their own.
    """
    zones = readable_classifications(principal)
    pairs = (
        session.execute(
            select(MeetingFollowup, Meeting)
            .join(Meeting, MeetingFollowup.meeting_id == Meeting.id)
            .where(
                MeetingFollowup.status == FollowupStatus.OFFICER_REVIEW,
                MeetingFollowup.classification.in_(zones),
                Meeting.classification.in_(zones),
            )
            .order_by(MeetingFollowup.submitted_at.asc().nulls_last(), MeetingFollowup.id)
        )
        .tuples()
        .all()
    )
    if not pairs:
        return ApprovalQueue(items=(), total=0, served_classification=dominant())

    organisations = _organisation_names(session, zones, (m.organisation_id for _f, m in pairs))
    views = _followup_views(session, principal, [followup for followup, _meeting in pairs])
    items = tuple(
        ApprovalQueueItem(
            followup=view,
            meeting_id=meeting.id,
            meeting_title=meeting.title,
            meeting_type=meeting.meeting_type,
            scheduled_start=meeting.scheduled_start,
            organisation_name=organisations.get(meeting.organisation_id)
            if meeting.organisation_id is not None
            else None,
        )
        for view, (_followup, meeting) in zip(views, pairs, strict=True)
    )
    return ApprovalQueue(
        items=items,
        total=len(items),
        served_classification=dominant(*(item.followup.classification for item in items)),
    )
