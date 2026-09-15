"""Pydantic v2 contracts for the AI Gateway: the envelope, the context, the seven outputs.

Three kinds of thing live here and nothing else:

1. :class:`EvidenceRef` and :class:`GatewayResult` -- the fixed response envelope
   ``{result, evidence, trace_id, approval_status}`` (``BUILD_BIBLE.md`` section 4,
   ``CLAUDE.md`` 2.2). Raw prose is never a valid AI payload, so every AI answer in this
   system is one of these.
2. :class:`GatewayContext` -- what a caller may hand the Gateway. Deliberately small and
   ``extra="forbid"``: the Gateway retrieves its own evidence under the caller's identity
   (ADR-0001 stage 3), so the context carries *identifiers and metadata*, never a
   pre-fetched blob of document text.
3. The seven purpose output schemas, one per registered :class:`~app.domain.enums.AiPurpose`.

**Alignment with ``packages/contracts/src/index.ts``.** That file hand-declares
``EvidenceRef`` as ``{id, title, url, source}`` and ``AiEnvelope<TResult>`` as
``{result, evidence, trace_id, approval_status}`` with ``result`` nullable. Those field
names are matched here character for character. This module adds exactly one field the
TypeScript contract does not yet carry -- ``EvidenceRef.citation_id`` -- because the stage
8 citation post-check resolves every evidence id against
``data/demo-seed/citations.json`` and the registry key it resolved to is the single most
useful thing to render in the trace drawer. It is nullable and defaults to ``id``, so a
client compiled against the narrower TypeScript type is unaffected. Recorded in the
handover as a contract addition for whoever regenerates the client.

**Every grounded purpose must cite.** :class:`GroundedResult` refuses to validate a result
that carries no evidence id at all, at any depth. That is winning moment #1 ("not a
chatbot") expressed as a type rather than as a convention: an uncited answer cannot be
constructed, so it cannot be returned, so it cannot reach a screen.

No I/O, no SQLAlchemy, no ``anthropic``. This module is importable on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.enums import ApprovalStatus, BriefItemType, ConsentStatus, Jurisdiction, Priority

__all__ = [
    "CITATION_FIELD_NAMES",
    "BriefItem",
    "ConsularTriageResult",
    "DiasporaMatch",
    "DiasporaMatchResult",
    "EvidenceRef",
    "FollowupCommitment",
    "GatewayContext",
    "GatewayResult",
    "GroundedResult",
    "KnowledgeAnswerResult",
    "KnowledgePassage",
    "KnowledgeReferral",
    "MeetingFollowupResult",
    "MeetingPrepResult",
    "MorningBriefResult",
    "OpportunityScoreResult",
    "ScoredFactor",
    "TalkingPoint",
    "dump_result",
]

# ---------------------------------------------------------------------------
# Shared field aliases
# ---------------------------------------------------------------------------

#: A model-stated confidence. A bare float would let 1.7 through and nothing downstream
#: would notice until it rendered as a 170% confident claim on stage.
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

#: One or more evidence ids. ``min_length=1`` is the load-bearing half: a claim-bearing
#: object that cites nothing is exactly what this product exists to refuse.
CitationList = Annotated[list[str], Field(min_length=1, max_length=8)]

#: Non-empty prose. ``strip_whitespace`` so that "   " cannot pass for an answer.
Prose = Annotated[str, StringConstraints(min_length=1, max_length=4000, strip_whitespace=True)]

#: A short label: a title, a name, a state. Bounded so nothing here can carry a narrative.
Label = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]

#: Field names that hold evidence ids. :meth:`GroundedResult.cited_evidence_ids` walks a
#: dumped result looking for these, so a new schema gets the citation post-check for free
#: as long as it spells its citation field one of these two ways.
CITATION_FIELD_NAMES: Final[frozenset[str]] = frozenset({"citations", "evidence_ids"})


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    """One item of provenance behind an AI answer.

    Field names match ``packages/contracts/src/index.ts``. ``url`` and ``source`` are
    nullable there and nullable here: a piece of evidence can be an internal document with
    no public URL, and pretending otherwise would put a dead link in front of the audience.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Label
    title: Label
    url: str | None = None
    source: str | None = None
    quote: str | None = Field(
        default=None,
        max_length=600,
        description=(
            "The exact claim the cited page carries, taken verbatim from that entry's "
            "`supports_claims` in data/demo-seed/citations.json. Never paraphrased and "
            "never synthesised: a quotation an Ambassador cannot find on the page is "
            "attribution laundering (CLAUDE.md 2.6). None when the registry records no "
            "claim for the entry."
        ),
    )
    citation_id: str | None = Field(
        default=None,
        description=(
            "Key of the entry in data/demo-seed/citations.json this evidence resolved to. "
            "Defaults to `id`, which is the same string for every registry-backed item."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _default_citation_id(cls, data: object) -> object:
        """Fill ``citation_id`` from ``id`` when the caller did not state one."""
        if isinstance(data, dict) and not data.get("citation_id"):
            identifier = data.get("id")
            if isinstance(identifier, str):
                return {**data, "citation_id": identifier}
        return data


class GatewayResult(BaseModel):
    """The one shape every AI endpoint returns (``BUILD_BIBLE.md`` section 4).

    ``result`` is ``None`` exactly when ``approval_status`` is
    :attr:`~app.domain.enums.ApprovalStatus.BLOCKED` -- a refusal or an uncovered fallback
    (ADR-0002). Both halves are enforced below, in both directions, because a ``BLOCKED``
    envelope carrying a half-populated result is the failure mode that would put a
    plausible-looking but unauthorised answer on screen.

    ``SerializeAsAny`` on ``result`` is not decoration. The field is annotated as
    :class:`~pydantic.BaseModel` so any purpose schema fits, and without
    ``SerializeAsAny`` Pydantic v2 would serialise it against the *declared* type and emit
    ``{}`` for every answer in the system.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: SerializeAsAny[BaseModel] | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    trace_id: str
    approval_status: ApprovalStatus

    #: Human-readable explanation of a refusal. Not part of the TypeScript envelope and
    #: deliberately optional: it is populated only on a BLOCKED envelope, where the UI has
    #: to say something truthful rather than render an empty success.
    explanation: str | None = None

    @model_validator(mode="after")
    def _blocked_iff_no_result(self) -> GatewayResult:
        """A ``BLOCKED`` envelope has no result, and a result is never ``BLOCKED``."""
        blocked = self.approval_status is ApprovalStatus.BLOCKED
        if blocked and self.result is not None:
            msg = "A BLOCKED envelope must carry result=None; a partial answer is worse than none."
            raise ValueError(msg)
        if not blocked and self.result is None:
            msg = (
                f"approval_status={self.approval_status.value} claims an answer exists, but "
                "result is None. Use ApprovalStatus.BLOCKED for a refusal."
            )
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

#: What a context fact may hold. Scalars only: a nested structure is where a narrative
#: hides from a field-name check.
ContextValue = str | int | float | bool | None


class GatewayContext(BaseModel):
    """What the caller may tell the Gateway about the object being reasoned over.

    Deliberately *not* a place to put document text. ADR-0001 stage 3 is explicit that the
    Gateway "never receives a pre-fetched context blob from a caller; it retrieves its own,
    under the caller identity". Everything here is an identifier, a metadata scalar or the
    user's own question -- and for ``CONSULAR_TRIAGE`` even that is narrowed to an
    allowlist by :class:`~app.ai.purposes.ContextPolicy`.

    ``extra="forbid"`` so that a caller who invents a ``case_narrative`` argument gets a
    validation error rather than a silently ignored field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The primary object this call is about -- an id or a stable slug. Feeds the ADR-0002
    #: scenario key, so it must be low-cardinality and stable across a re-seed.
    subject_ref: str | None = Field(default=None, max_length=96)

    #: Explicit snapshot scenario override. Present so a demo beat can pin its snapshot
    #: without inventing a fake subject; normally left unset.
    scenario: str | None = Field(default=None, max_length=96)

    #: Sector codes (``data/taxonomy/sectors.json``) narrowing stage 3 retrieval.
    sector_codes: tuple[str, ...] = ()

    #: For ``KNOWLEDGE_ANSWER``: restrict grounding to articles restating a source in these
    #: jurisdictions (mission-authored guidance with no external source always qualifies).
    #: Empty means any jurisdiction.
    jurisdictions: tuple[Jurisdiction, ...] = ()

    #: Metadata scalars about the subject. Never narrative (see the purpose's policy).
    facts: Mapping[str, ContextValue] = Field(default_factory=dict)

    #: The user's own question, for ``KNOWLEDGE_ANSWER``. Forbidden for purposes whose
    #: policy sets ``allows_question=False``.
    question: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Grounded result base
# ---------------------------------------------------------------------------


def _collect_citations(value: object, found: set[str]) -> None:
    """Walk a dumped model, gathering every string under a citation field name."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in CITATION_FIELD_NAMES and isinstance(item, list):
                found.update(entry for entry in item if isinstance(entry, str))
            else:
                _collect_citations(item, found)
    elif isinstance(value, list | tuple):
        for item in value:
            _collect_citations(item, found)


class GroundedResult(BaseModel):
    """Base class for every purpose output schema.

    Two guarantees, both enforced at validation time rather than trusted:

    * **Nothing extra.** ``extra="forbid"`` means a model that invents a field fails
      validation and triggers a fallback, instead of quietly returning something the UI
      will not render.
    * **Something cited.** A subclass with :attr:`requires_citations` true cannot validate
      unless at least one evidence id appears somewhere in it. The Gateway's stage 8 then
      checks those ids resolve, are VERIFIED, and were inside the caller's authorised set;
      this validator only guarantees the check has something to work on.

    Frozen, because a validated result is a finished artefact. Stage 8 reads it; nothing
    edits it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Whether this schema must carry at least one citation. True for all seven registered
    #: purposes; declared as a switch so an ungrounded purpose (there is none, and adding
    #: one needs an ADR) would have to say so explicitly.
    requires_citations: ClassVar[bool] = True

    def declines_to_answer(self) -> bool:
        """Whether this result is a refusal to answer rather than an answer.

        False for every purpose but ``KNOWLEDGE_ANSWER``, whose refusal -- no approved source
        supports the question -- is a first-class result that cites nothing, and which stage 8
        passes only if it indeed cites nothing.
        """
        return False

    def cited_evidence_ids(self) -> frozenset[str]:
        """Return every evidence id cited anywhere in this result.

        Walks the dumped model rather than reading a declared field, so a nested claim
        object cannot cite something the post-check never sees.
        """
        found: set[str] = set()
        _collect_citations(self.model_dump(mode="python"), found)
        return frozenset(found)

    @model_validator(mode="after")
    def _must_cite_something(self) -> GroundedResult:
        """Refuse a grounded result that cites nothing at all."""
        if self.requires_citations and not self.cited_evidence_ids():
            msg = (
                f"{type(self).__name__} is a grounded purpose output and carries no evidence "
                "id. An uncited AI claim is not renderable in this product (CLAUDE.md 2.2)."
            )
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# 1. MORNING_BRIEF
# ---------------------------------------------------------------------------


class BriefItem(GroundedResult):
    """One item on the morning brief: what changed, why it matters, and the sources."""

    title: Label
    item_type: BriefItemType
    detail: Prose
    so_what: Prose
    confidence: Confidence
    citations: CitationList


class MorningBriefResult(GroundedResult):
    """Winning moment #1. Every item resolves to a real, public, VERIFIED source."""

    headline: Label
    as_at_label: Label
    summary: Prose
    items: Annotated[list[BriefItem], Field(min_length=1, max_length=8)]
    confidence: Confidence


# ---------------------------------------------------------------------------
# 2. OPPORTUNITY_SCORE
# ---------------------------------------------------------------------------


class ScoredFactor(GroundedResult):
    """One driver or risk behind a score, with the evidence for it."""

    label: Label
    detail: Prose
    weight: Confidence
    citations: CitationList


class OpportunityScoreResult(GroundedResult):
    """A score, its drivers, its risks, and whether the platform *proposed* the link.

    ``is_proposed_by_ai`` mirrors ``opportunities.is_proposed_by_ai`` and is the hero
    thread's honesty flag (``docs/OPEN_QUESTIONS.md`` Q-17): no public source connects an
    Australian lithium operator to Nigeria, so the corridor is a synthesis the platform
    proposes and must render at *lower* confidence than the signals beneath it. A
    validator enforces that asymmetry rather than trusting the author of a snapshot.
    """

    opportunity_ref: Label
    score: Annotated[int, Field(ge=0, le=100)]
    band: Literal["LOW", "MEDIUM", "HIGH"]
    confidence: Confidence
    is_proposed_by_ai: bool
    rationale: Prose
    drivers: Annotated[list[ScoredFactor], Field(min_length=1, max_length=8)]
    risks: Annotated[list[ScoredFactor], Field(min_length=1, max_length=8)]
    recommended_next_step: Prose

    @model_validator(mode="after")
    def _proposal_confidence_below_its_evidence(self) -> OpportunityScoreResult:
        """An AI-proposed link may not be more confident than its own strongest driver."""
        if not self.is_proposed_by_ai:
            return self
        strongest = max(driver.weight for driver in self.drivers)
        if self.confidence >= strongest:
            msg = (
                "An AI-proposed opportunity must carry lower confidence than the signals it "
                f"rests on (Q-17): confidence={self.confidence} but the strongest driver "
                f"weighs {strongest}."
            )
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# 3. MEETING_PREP
# ---------------------------------------------------------------------------


class TalkingPoint(GroundedResult):
    """One thing to say, and the source that lets the officer say it."""

    point: Label
    detail: Prose
    citations: CitationList


class MeetingPrepResult(GroundedResult):
    """The pre-read an officer takes into the room."""

    meeting_ref: Label
    counterpart: Label
    objectives: Annotated[list[Label], Field(min_length=1, max_length=6)]
    talking_points: Annotated[list[TalkingPoint], Field(min_length=1, max_length=8)]
    questions_to_ask: Annotated[list[Label], Field(default_factory=list, max_length=8)]
    sensitivities: Annotated[list[Label], Field(default_factory=list, max_length=6)]
    confidence: Confidence


# ---------------------------------------------------------------------------
# 4. MEETING_FOLLOWUP  (consequential)
# ---------------------------------------------------------------------------


class FollowupCommitment(BaseModel):
    """One thing the mission would be committing to. Never sent without human approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: Prose
    owner_role: Label
    due_label: Label


class MeetingFollowupResult(GroundedResult):
    """A DRAFT outbound communication. Winning moment #2.

    This schema is what the Gateway hands back with ``PENDING_APPROVAL``; the follow-up
    state machine (``docs/workflows.md`` section 2) is what stops it being sent. ``SENT`` is
    reachable only from ``APPROVED``, so nothing here can shortcut a human.

    ``recipients`` holds role and organisation *labels*, never addresses: a drafted email
    with a real address in it is one careless click from being a real email, and the demo
    seed carries no real contact details at all (``BUILD_BIBLE.md`` section 11).
    """

    meeting_ref: Label
    subject: Label
    recipients: Annotated[list[Label], Field(min_length=1, max_length=8)]
    body: Prose
    key_commitments: Annotated[list[FollowupCommitment], Field(default_factory=list, max_length=6)]
    citations: CitationList
    approval_note: Prose

    @field_validator("recipients")
    @classmethod
    def _labels_not_addresses(cls, value: list[str]) -> list[str]:
        """Reject anything that looks like an email address."""
        offenders = [entry for entry in value if "@" in entry]
        if offenders:
            msg = (
                "recipients holds role or organisation labels, never addresses. "
                f"Rejected: {offenders}."
            )
            raise ValueError(msg)
        return value


# ---------------------------------------------------------------------------
# 5. CONSULAR_TRIAGE  (consequential, metadata only)
# ---------------------------------------------------------------------------


class ConsularTriageResult(GroundedResult):
    """A PROPOSED triage, derived from case metadata only. Never a determination.

    ``docs/workflows.md`` section 3: the ``consular_triage`` purpose "produces a *proposed*
    triage -- a case type, a priority and a rationale, with
    ``approval_status = PENDING_APPROVAL``. It never fires an event."

    Three fields exist to make the metadata-only posture visible rather than asserted
    (``docs/OPEN_QUESTIONS.md`` Q-06, conservative option (c)):

    * ``inputs_used`` names the metadata fields the proposal actually rested on, so a
      reviewer can see there was no narrative among them;
    * ``narrative_withheld`` must be ``True``;
    * ``requires_human_determination`` must be ``True``.

    All three are validated, not documented. If Q-06 is later answered permissively, the
    schema relaxes here and the context policy relaxes in ``app/ai/purposes.py``; nothing
    else in the pipeline changes.
    """

    case_ref: Label
    proposed_case_type: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,47}$")]
    proposed_priority: Priority
    proposed_sla_days: Annotated[int, Field(ge=1, le=365)]
    rationale: Prose
    next_steps: Annotated[list[Label], Field(min_length=1, max_length=6)]
    inputs_used: Annotated[list[Label], Field(min_length=1, max_length=12)]
    narrative_withheld: bool
    requires_human_determination: bool
    citations: CitationList
    confidence: Confidence

    @model_validator(mode="after")
    def _controls_hold(self) -> ConsularTriageResult:
        """Refuse a triage proposal that claims to have read narrative or to be final."""
        if not self.narrative_withheld:
            msg = (
                "consular_triage is metadata-only pending docs/OPEN_QUESTIONS.md Q-06: a "
                "result claiming narrative_withheld=false may not be constructed."
            )
            raise ValueError(msg)
        if not self.requires_human_determination:
            msg = (
                "A consular determination may never be autonomous (BUILD_BIBLE.md section 6); "
                "requires_human_determination must be true."
            )
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# 6. KNOWLEDGE_ANSWER
# ---------------------------------------------------------------------------


class KnowledgePassage(BaseModel):
    """Sentences quoted verbatim from one approved article, and the citation that backs it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    article_slug: Label
    article_title: Label
    article_version: Annotated[int, Field(ge=1)]
    citation_id: Label
    text: Prose
    matched_terms: Annotated[list[Label], Field(default_factory=list, max_length=16)]


class KnowledgeReferral(BaseModel):
    """Why a question was refused, and the named officer to take it to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Prose
    guidance: Prose
    refer_to_name: Label
    refer_to_title: Label


class KnowledgeAnswerResult(GroundedResult):
    """A grounded answer, or a refusal. ``answered_from_approved_sources`` is the switch.

    **Answered:** at least one citation, every quoted passage backed by one of them, no referral.
    **Refused:** no citation, no passage, and a referral saying why and to whom. A citation on a
    refusal would be a source nobody consulted, so the schema refuses the combination outright
    rather than leaving it to a reviewer to notice.

    ``requires_citations`` is off because the requirement is conditional; the validator below
    enforces it, and stage 8 still checks every id that is cited.
    """

    requires_citations: ClassVar[bool] = False

    question: Prose
    answer: Prose
    citations: Annotated[list[str], Field(default_factory=list, max_length=8)]
    passages: Annotated[list[KnowledgePassage], Field(default_factory=list, max_length=3)]
    caveats: Annotated[list[Label], Field(default_factory=list, max_length=6)]
    answered_from_approved_sources: bool
    refusal: KnowledgeReferral | None = None
    confidence: Confidence

    def declines_to_answer(self) -> bool:
        return not self.answered_from_approved_sources

    @model_validator(mode="after")
    def _an_answer_cites_and_a_refusal_does_not(self) -> KnowledgeAnswerResult:
        if self.answered_from_approved_sources:
            if not self.citations:
                msg = "An answer from approved sources must cite them."
                raise ValueError(msg)
            if self.refusal is not None:
                msg = "An answer carries no refusal."
                raise ValueError(msg)
            unbacked = {passage.citation_id for passage in self.passages} - set(self.citations)
            if unbacked:
                msg = f"Quoted passages cite ids the answer does not: {sorted(unbacked)}."
                raise ValueError(msg)
        else:
            if self.citations or self.passages:
                msg = (
                    "A refusal cites nothing and quotes nothing: a citation on a refusal would be "
                    "a source nobody consulted."
                )
                raise ValueError(msg)
            if self.refusal is None:
                msg = "A refusal must say why, and where to take the question."
                raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# 7. DIASPORA_MATCH
# ---------------------------------------------------------------------------


class DiasporaMatch(GroundedResult):
    """One matched diaspora profile.

    ``consent_status`` is carried on every match and validated, because diaspora profiles
    are governed by consent rather than by classification
    (``app.domain.enums.ConsentStatus``). A profile that has not consented to appear in the
    directory may not be returned by a search at all, so a match carrying ``NOT_GIVEN`` or
    ``WITHDRAWN`` is refused here as well as in the query that produced it.
    """

    profile_ref: Label
    display_name: Label
    expertise_tags: Annotated[list[Label], Field(min_length=1, max_length=8)]
    why_matched: Prose
    consent_status: ConsentStatus
    contactable: bool
    citations: CitationList

    @model_validator(mode="after")
    def _consent_permits_listing(self) -> DiasporaMatch:
        """Refuse a match for a profile that has not consented to be listed."""
        listable = {ConsentStatus.GIVEN_DIRECTORY_ONLY, ConsentStatus.GIVEN_CONTACTABLE}
        if self.consent_status not in listable:
            msg = (
                f"{self.profile_ref} has consent_status={self.consent_status.value} and may "
                "not be returned by a diaspora search."
            )
            raise ValueError(msg)
        if self.contactable and self.consent_status is not ConsentStatus.GIVEN_CONTACTABLE:
            msg = (
                f"{self.profile_ref} is marked contactable but consent_status is "
                f"{self.consent_status.value}."
            )
            raise ValueError(msg)
        return self


class DiasporaMatchResult(GroundedResult):
    """Capability search across the diaspora. Hero beat returns both sides of the thread."""

    query: Prose
    matches: Annotated[list[DiasporaMatch], Field(min_length=1, max_length=10)]
    rationale: Prose
    confidence: Confidence


def dump_result(result: BaseModel | None) -> dict[str, Any] | None:
    """JSON-safe dump of a purpose result, for logging and snapshot authoring."""
    if result is None:
        return None
    return result.model_dump(mode="json")
