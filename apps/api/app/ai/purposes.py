"""The purpose allowlist: seven registered purposes and the rules that bound each one.

ADR-0001 stage 1: ``purpose`` must be a member of a registered allowlist, and "an
unregistered purpose is a hard error, never a free-form prompt. There is no 'general chat'
purpose and there will not be one."

Each :class:`PurposeSpec` carries five things the Gateway needs and one thing the demo
needs:

===========================  ==============================================================
``output_schema``            The Pydantic v2 class stage 7 validates against.
``max_classification``       The most sensitive zone this purpose may process (stage 2).
``consequential``            Whether the artefact is a proposal a human must approve.
``model_route`` / rule       The default route recorded at stage 5.
``context_policy``           What the caller is allowed to put in :class:`GatewayContext`.
``scenario_*``               How the ADR-0002 snapshot key is derived.
===========================  ==============================================================

**Why ``permitted_classifications`` is a set and not a comparison.** Ranking is the wrong
tool: ``CLASSIFICATION_RANK`` orders ``PUBLIC < MISSION_INTERNAL < CONSULAR_SENSITIVE <
CONFIDENTIAL`` because that is the *propagation* order, so a naive ``rank <= max_rank``
test on a purpose capped at ``CONFIDENTIAL`` would silently admit ``CONSULAR_SENSITIVE``
citizen material. ``CONSULAR_SENSITIVE`` is a compartment, not a level (ADR-0006), so it is
admitted only by a purpose that names it explicitly -- and **exactly one does**:
``CONSULAR_TRIAGE``, whose CONSULAR-SENSITIVE route makes no model call at all and answers
with metadata-only rules (``app.ai.metadata_triage``, W3.3). That is the conservative reading
of ``docs/OPEN_QUESTIONS.md`` Q-06 implemented as data rather than as a comment.
``app.models.ai`` records the same rank trap for the same reason.

**Q-06 and ``CONSULAR_TRIAGE`` -- REVERSIBLE, pending the architect.** Q-06 asks whether
``CONSULAR_SENSITIVE`` content may reach a third-party model at all. Option (c), the
strongest and most limiting, is implemented here: triage runs on **metadata only** -- case
type, case age, SLA state, status -- and the context policy *rejects* narrative outright
rather than trimming it. The refusal is recorded in the trace (``app.ai.gateway`` stage 4)
so the control is demonstrable rather than asserted. If Q-06 is answered permissively, the
reversal is two edits in this file -- :data:`_CONSULAR_TRIAGE_FACTS` and the purpose's
``max_classification`` -- plus a relaxation of three validators in
:class:`~app.ai.schemas.ConsularTriageResult`. Nothing else in the pipeline knows.

**Q-12 and routing -- PLACEHOLDER, deferred by decision.** The architect deferred the
routing table to the Week 2 to 3 handoff (``docs/OPEN_QUESTIONS.md`` Q-12): "Week 1 records
the route decision field and renders it; it does not decide the table." So
:func:`route_for` implements a documented placeholder rule and says so in the sentence it
writes into ``ai_traces.route_reason``. The *seam* is real; the table is not ours to pick.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from app.ai.schemas import (
    ConsularTriageResult,
    DiasporaMatchResult,
    GatewayContext,
    GroundedResult,
    KnowledgeAnswerResult,
    MeetingFollowupResult,
    MeetingPrepResult,
    MorningBriefResult,
    OpportunityScoreResult,
)
from app.core.config import get_settings
from app.domain.enums import (
    CLASSIFICATION_RANK,
    AiPurpose,
    ApprovalStatus,
    Classification,
    RoleCode,
)

__all__ = [
    "DEFAULT_SCENARIO",
    "HEAVY_BUDGET_SECONDS",
    "NO_EXTERNAL_MODEL_ROUTE",
    "PURPOSES",
    "ContextPolicy",
    "ContextRefusal",
    "ModelRoute",
    "PurposeNotAllowedError",
    "PurposeSpec",
    "budget_for",
    "check_context",
    "resolve_purpose",
    "route_for",
    "scenario_for",
]

#: The scenario key served when a call derives no specific one, and the last-resort key of
#: the fallback harness (ADR-0002, "Missing snapshot"). A real value, not a placeholder:
#: it is written to ``ai_traces.scenario`` verbatim.
DEFAULT_SCENARIO: Final[str] = "__default__"

#: Characters a scenario key may contain. It becomes a filename, is written to a
#: ``String(96)`` column, and is read aloud during rehearsal, so it stays boring.
_SCENARIO_SAFE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9_-]+")

#: ``ai_traces.scenario`` is ``String(96)``. Truncating here rather than at the INSERT means
#: the key that names the snapshot file is the key that is recorded.
_SCENARIO_MAX_LENGTH: Final[int] = 96


class PurposeNotAllowedError(ValueError):
    """Stage 1 refused: the requested purpose is not on the allowlist.

    A ``ValueError`` and not an :class:`~app.core.errors.AppError` on purpose. Every route
    in this system passes an :class:`~app.domain.enums.AiPurpose` member, so reaching this
    means a caller constructed a purpose out of a string or a new enum member was added
    without a registration. That is our bug, not the client's, and it must surface loudly:
    there is no snapshot for an unregistered purpose, so there is nothing safe to serve.
    """


@dataclass(frozen=True, slots=True)
class ContextRefusal:
    """Why a :class:`~app.ai.schemas.GatewayContext` was refused, in reviewable detail."""

    reason: str
    detail: str
    offending_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """What a caller may put in the context for one purpose.

    The default is permissive-but-bounded: any fact key, scalar values up to
    :attr:`max_fact_value_length`, and a free-text question. ``CONSULAR_TRIAGE`` narrows
    all three.

    ``allowed_fact_fields`` is an **allowlist**, deliberately, and not a blocklist of
    narrative-sounding names. A blocklist has to guess every spelling of "the story of the
    case" and is wrong the first time somebody writes ``details`` instead of ``narrative``.
    The length cap is the second half of the same control: it stops a narrative smuggled
    into an allowed key such as ``status``.
    """

    allowed_fact_fields: frozenset[str] | None = None
    max_fact_value_length: int = 2000
    allows_question: bool = True
    #: Stated on the refusal so an officer reading the trace drawer learns the rule, not
    #: merely that something was rejected.
    rationale: str = ""


#: Budget for a purpose that generates prose. Architect ruling after the W2.2 review: the
#: flat 4s budget guaranteed a timeout on every heavy call, so the live path was theatre.
#:
#: The ruling names morning_brief and meeting_prep. It is applied here to every STRONG-tier
#: generative purpose, meeting_followup and knowledge_answer included, because the reasoning
#: is identical -- they generate prose against the same models -- and leaving those two at
#: 4s would reintroduce exactly the bug the ruling exists to fix. Flagged for confirmation
#: rather than assumed: see docs/OPEN_QUESTIONS.md Q-21.
HEAVY_BUDGET_SECONDS: Final[float] = 25.0


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """The stage 5 routing decision, per ``BUILD_BIBLE.md`` section 4a.

    Sensitivity is the PRIMARY key and capability tier the secondary one, so this carries
    both, plus the badge the trace drawer renders. The badge is a plain string on purpose:
    section 4a requires it to be "legible to a non-technical Ambassador", and a drawer that
    renders JSON fails that test however complete the JSON is.
    """

    route: str
    reason: str
    model_requested: str | None
    #: "fast" | "strong" | None. None where the route forbids generation entirely.
    tier: str | None = None
    #: The section 4a trace-drawer badge, rendered verbatim.
    badge: str = ""
    #: Whether an external provider call is permitted on this route AT ALL. False for
    #: CONSULAR_SENSITIVE (never leaves), for RESTRICTED (never routed), and - this week -
    #: for CONFIDENTIAL, whose restricted lane is simulated rather than built.
    live_eligible: bool = False
    #: Retention posture, recorded so the drawer can state it without inferring it.
    retention: str = "n/a"


@dataclass(frozen=True, slots=True)
class PurposeSpec:
    """One registered purpose and everything the Gateway needs to run it."""

    purpose: AiPurpose
    output_schema: type[GroundedResult]
    max_classification: Classification
    consequential: bool
    default_model_route: str
    context_policy: ContextPolicy
    #: Whether the snapshot key includes the caller's role. True where the same question
    #: has a materially different answer per persona -- the morning brief above all.
    scenario_includes_role: bool = False
    #: Sector codes used to narrow stage 3 retrieval when the caller names none.
    default_sector_codes: tuple[str, ...] = ()
    #: Wall-clock budget for the live call, in seconds. ``None`` takes the global default
    #: (``AI_GATEWAY_TIMEOUT_SECONDS``, 4s), which is right for a purpose that returns a
    #: number and a sentence. A purpose that generates prose needs far longer, and a flat
    #: 4s budget for those meant the live path timed out essentially every time -- the
    #: Gateway was "live" only in the sense that it tried. See ADR-0002 and the W2.2 review.
    budget_seconds: float | None = None
    #: Whether the caller is offered token-level progress while a long call runs. A 25s
    #: wait with no feedback reads as a hung demo, which is a worse failure than a slow one.
    stream: bool = False
    #: Whether this purpose may EVER make an external call, independent of the band its
    #: context happens to fall in. Section 4a routes by data class, and consular_triage
    #: caps at MISSION_INTERNAL precisely because narrative never enters it -- so by the
    #: table alone its de-identified metadata would route externally. That is a decision
    #: about consular data that the architect has not taken, and the demo's central claim
    #: is that consular material is not transmitted off-box. So the purpose is held to the
    #: deterministic path until someone rules otherwise, and the band table stays intact.
    live_eligible: bool = True
    summary: str = ""
    #: Zones this purpose may process. Derived, never hand-written -- see the module
    #: docstring on why a rank comparison would be wrong.
    permitted_classifications: frozenset[Classification] = field(init=False)

    def __post_init__(self) -> None:
        ceiling = CLASSIFICATION_RANK[self.max_classification]
        permitted = {zone for zone in Classification if CLASSIFICATION_RANK[zone] <= ceiling}
        if self.max_classification is not Classification.CONSULAR_SENSITIVE:
            permitted.discard(Classification.CONSULAR_SENSITIVE)
        object.__setattr__(self, "permitted_classifications", frozenset(permitted))

    @property
    def approval_status(self) -> ApprovalStatus:
        """The status a successful call carries.

        Set by the Gateway and never by the caller (ADR-0001). A consequential purpose
        returns ``PENDING_APPROVAL`` whether the answer came from the model or from a
        snapshot: a cached draft is still a draft, and ADR-0002 is explicit that "a
        fallback follow-up draft still blocks on human approval".
        """
        if self.consequential:
            return ApprovalStatus.PENDING_APPROVAL
        return ApprovalStatus.NOT_REQUIRED

    @property
    def schema_name(self) -> str:
        """Name recorded in ``ai_traces.output_schema_name``."""
        return self.output_schema.__name__

    def permits(self, classification: Classification) -> bool:
        """Whether this purpose may process content in ``classification``."""
        return classification in self.permitted_classifications


# ---------------------------------------------------------------------------
# Context policies
# ---------------------------------------------------------------------------

#: The ONLY fact keys ``CONSULAR_TRIAGE`` accepts (Q-06 option (c)).
#:
#: Case type, age, SLA state and status, exactly as the question frames them, plus two
#: derived scalars the SLA clock produces (``docs/OPEN_QUESTIONS.md`` Q-15: the clock
#: pauses in ``AWAITING_CITIZEN``, so "how long has it been paused" is metadata about the
#: process, not about the citizen). Nothing here can carry a sentence about a person.
_CONSULAR_TRIAGE_FACTS: Final[frozenset[str]] = frozenset(
    {
        "case_type",
        "case_age_days",
        "sla_state",
        "sla_days_remaining",
        "days_paused",
        "status",
    }
)

_CONSULAR_TRIAGE_POLICY: Final[ContextPolicy] = ContextPolicy(
    allowed_fact_fields=_CONSULAR_TRIAGE_FACTS,
    # 48 characters holds "AWAITING_CITIZEN" or "PASSPORT_RENEWAL" and holds no story.
    max_fact_value_length=48,
    allows_question=False,
    rationale=(
        "consular_triage runs on case metadata only -- case type, age, SLA state and status. "
        "Case narrative is never transmitted to a model. This is the conservative reading of "
        "docs/OPEN_QUESTIONS.md Q-06 (option c) and is reversible if the architect answers "
        "otherwise."
    ),
)

_OPEN_POLICY: Final[ContextPolicy] = ContextPolicy(
    rationale="Metadata and identifiers only; the Gateway retrieves its own evidence.",
)

_NO_QUESTION_POLICY: Final[ContextPolicy] = ContextPolicy(
    allows_question=False,
    rationale=(
        "This purpose summarises a known object rather than answering a free-text question, "
        "so no user-supplied prompt is accepted."
    ),
)


# ---------------------------------------------------------------------------
# The seven
# ---------------------------------------------------------------------------

_SPECS: Final[tuple[PurposeSpec, ...]] = (
    PurposeSpec(
        purpose=AiPurpose.MORNING_BRIEF,
        budget_seconds=HEAVY_BUDGET_SECONDS,
        stream=True,
        output_schema=MorningBriefResult,
        max_classification=Classification.MISSION_INTERNAL,
        consequential=False,
        default_model_route="standard-brief",
        context_policy=_NO_QUESTION_POLICY,
        scenario_includes_role=True,
        default_sector_codes=("critical-minerals", "lithium", "skilled-migration", "education"),
        summary=(
            "The daily brief. Winning moment #1: every item carries a resolving public "
            "citation, and the brief is capped at MISSION_INTERNAL so a confidential "
            "negotiating position can never be summarised onto a dashboard."
        ),
    ),
    PurposeSpec(
        purpose=AiPurpose.OPPORTUNITY_SCORE,
        output_schema=OpportunityScoreResult,
        max_classification=Classification.CONFIDENTIAL,
        consequential=False,
        default_model_route="standard-analysis",
        context_policy=_NO_QUESTION_POLICY,
        default_sector_codes=("critical-minerals", "lithium", "trade"),
        summary=(
            "Scores an opportunity and states what the score rests on. Cleared to "
            "CONFIDENTIAL because a negotiating position is legitimately part of the "
            "judgement; the caller's own clearance is checked separately at stage 2."
        ),
    ),
    PurposeSpec(
        purpose=AiPurpose.MEETING_PREP,
        budget_seconds=HEAVY_BUDGET_SECONDS,
        stream=True,
        output_schema=MeetingPrepResult,
        max_classification=Classification.CONFIDENTIAL,
        consequential=False,
        default_model_route="standard-analysis",
        context_policy=_NO_QUESTION_POLICY,
        default_sector_codes=("critical-minerals", "lithium", "workforce-skills"),
        summary="The pre-read an officer takes into the room.",
    ),
    PurposeSpec(
        purpose=AiPurpose.MEETING_FOLLOWUP,
        budget_seconds=HEAVY_BUDGET_SECONDS,
        stream=True,
        output_schema=MeetingFollowupResult,
        max_classification=Classification.MISSION_INTERNAL,
        consequential=True,
        default_model_route="standard-drafting",
        context_policy=_NO_QUESTION_POLICY,
        default_sector_codes=("critical-minerals", "lithium", "education"),
        summary=(
            "Winning moment #2. Drafts an outbound communication and returns it "
            "PENDING_APPROVAL; SENT is reachable only from APPROVED (docs/workflows.md 2). "
            "Capped at MISSION_INTERNAL: an outbound email is the last place confidential "
            "material should be able to reach."
        ),
    ),
    PurposeSpec(
        purpose=AiPurpose.CONSULAR_TRIAGE,
        output_schema=ConsularTriageResult,
        live_eligible=False,
        # CONSULAR_SENSITIVE since W3.3 (reversing assumption A-12). The case IS consular
        # material, so the call declares the zone it is about and BUILD_BIBLE section 4a routes
        # it to "no external route - metadata-only - generation withheld". Q-06 option (c) is
        # still what makes that safe: the context policy admits six metadata fields and refuses
        # narrative, and on that route the Gateway asks no model at all.
        max_classification=Classification.CONSULAR_SENSITIVE,
        consequential=True,
        default_model_route="restricted-metadata-only",
        context_policy=_CONSULAR_TRIAGE_POLICY,
        default_sector_codes=("consular", "passports"),
        summary=(
            "Proposes a case type, priority and rationale from case METADATA ONLY, and "
            "returns PENDING_APPROVAL. It never fires an event and never makes a "
            "determination (BUILD_BIBLE.md section 6, docs/workflows.md section 3)."
        ),
    ),
    PurposeSpec(
        purpose=AiPurpose.KNOWLEDGE_ANSWER,
        budget_seconds=HEAVY_BUDGET_SECONDS,
        stream=True,
        output_schema=KnowledgeAnswerResult,
        max_classification=Classification.MISSION_INTERNAL,
        consequential=False,
        default_model_route="standard-grounded",
        context_policy=_OPEN_POLICY,
        default_sector_codes=(),
        summary=(
            "Answers a staff question from approved sources only, and says so in the "
            "result. The one purpose that accepts a free-text question."
        ),
    ),
    PurposeSpec(
        purpose=AiPurpose.DIASPORA_MATCH,
        output_schema=DiasporaMatchResult,
        max_classification=Classification.MISSION_INTERNAL,
        consequential=False,
        default_model_route="standard-analysis",
        context_policy=_OPEN_POLICY,
        default_sector_codes=("diaspora", "skilled-migration", "lithium"),
        summary=(
            "Consent-filtered capability search across diaspora profiles. Consent, not "
            "classification, is the gate here (app.domain.enums.ConsentStatus)."
        ),
    ),
)

#: The allowlist. Closed, immutable, and total over :class:`~app.domain.enums.AiPurpose`.
PURPOSES: Final[Mapping[AiPurpose, PurposeSpec]] = MappingProxyType(
    {spec.purpose: spec for spec in _SPECS}
)

_MISSING_REGISTRATIONS: Final[frozenset[AiPurpose]] = frozenset(AiPurpose) - frozenset(PURPOSES)
if _MISSING_REGISTRATIONS:  # pragma: no cover - import-time guard
    _names = ", ".join(sorted(purpose.value for purpose in _MISSING_REGISTRATIONS))
    _msg = (
        f"AiPurpose members without a PurposeSpec: {_names}. Every purpose needs an output "
        "schema and a fallback snapshot before it can run (ADR-0001 stage 1, ADR-0002)."
    )
    raise RuntimeError(_msg)


def resolve_purpose(purpose: object) -> PurposeSpec:
    """Stage 1. Return the spec for ``purpose`` or refuse.

    Accepts ``object`` rather than :class:`~app.domain.enums.AiPurpose` on purpose: the
    allowlist has to hold against a caller that reached the Gateway from JSON, from a
    config file, or from a future enum member nobody registered. A signature that only
    accepted valid input would move the check to mypy, which does not run in production.

    Raises:
        PurposeNotAllowedError: for anything that is not a registered purpose.
    """
    if isinstance(purpose, AiPurpose):
        spec = PURPOSES.get(purpose)
        if spec is not None:
            return spec
    registered = ", ".join(sorted(member.value for member in PURPOSES))
    msg = (
        f"{purpose!r} is not a registered AI Gateway purpose. Registered: {registered}. "
        "There is no free-form purpose and no general-chat purpose (ADR-0001 stage 1)."
    )
    raise PurposeNotAllowedError(msg)


def check_context(spec: PurposeSpec, context: GatewayContext) -> ContextRefusal | None:
    """Stage 4 gate. Return a refusal if ``context`` violates ``spec``'s policy, else None.

    Returns rather than raises: a refusal is a *recorded outcome* of the pipeline (it
    becomes a stage row and a BLOCKED envelope), not an exception to be caught somewhere
    and turned into a 500. ADR-0001 stage 2 uses the same wording for the classification
    gate -- "failures are recorded refusals, not exceptions swallowed into an empty
    result".
    """
    policy = spec.context_policy

    if context.question is not None and not policy.allows_question:
        return ContextRefusal(
            reason="question_not_accepted",
            detail=(
                f"{spec.purpose.value} accepts no free-text question. {policy.rationale}"
            ).strip(),
            offending_fields=("question",),
        )

    allowed = policy.allowed_fact_fields
    if allowed is not None:
        rejected = tuple(sorted(key for key in context.facts if key not in allowed))
        if rejected:
            return ContextRefusal(
                reason="field_not_allowlisted",
                detail=(
                    f"{spec.purpose.value} accepts only these context fields: "
                    f"{', '.join(sorted(allowed))}. Rejected: {', '.join(rejected)}. "
                    f"{policy.rationale}"
                ).strip(),
                offending_fields=rejected,
            )

    oversized = tuple(
        sorted(
            key
            for key, value in context.facts.items()
            if isinstance(value, str) and len(value) > policy.max_fact_value_length
        )
    )
    if oversized:
        return ContextRefusal(
            reason="value_too_long",
            detail=(
                f"{spec.purpose.value} caps context values at {policy.max_fact_value_length} "
                f"characters; a longer value is narrative, whatever the field is called. "
                f"Rejected: {', '.join(oversized)}. {policy.rationale}"
            ).strip(),
            offending_fields=oversized,
        )

    return None


def _slug(value: str) -> str:
    """Lower-case ``value`` and reduce it to the scenario character class."""
    return _SCENARIO_SAFE.sub("-", value.strip().lower()).strip("-_")


def scenario_for(spec: PurposeSpec, role: RoleCode, context: GatewayContext) -> str:
    """Derive the ADR-0002 snapshot scenario key.

    A pure function of ``(purpose, role, primary object)``, exactly as
    ``docs/OPEN_QUESTIONS.md`` A-05 requires -- no clock, no randomness, no request id. The
    same call on the same seed produces the same key on every run, which is what makes
    rehearsal and screenshot comparison possible at all.

    An explicit ``context.scenario`` wins, so a demo beat can pin its snapshot without
    inventing a fake subject. Everything else composes ``role`` (where the purpose says the
    answer differs by persona) with the subject slug, and falls back to
    :data:`DEFAULT_SCENARIO`.
    """
    if context.scenario:
        return _slug(context.scenario)[:_SCENARIO_MAX_LENGTH] or DEFAULT_SCENARIO

    parts: list[str] = []
    if spec.scenario_includes_role:
        parts.append(_slug(role.value))
    if context.subject_ref:
        parts.append(_slug(context.subject_ref))

    key = "_".join(part for part in parts if part)
    return key[:_SCENARIO_MAX_LENGTH] or DEFAULT_SCENARIO


#: Section 4a renders the trace-drawer badge with middle dots. The separator is a
#: constant because the badge is a contract with the drawer and with the VERIFY block,
#: not incidental formatting.
BADGE_SEP: Final[str] = " · "


#: Which capability tier each purpose takes, per BUILD_BIBLE section 4a: "Fast:
#: classify/score - Strong: briefs/meeting-prep". Scoring and matching produce a number
#: and a short rationale, where the strong tier buys little; a brief or a draft
#: communication is prose an Ambassador reads aloud, where it buys a lot.
_TIER_BY_PURPOSE: Final[Mapping[AiPurpose, str]] = MappingProxyType(
    {
        AiPurpose.MORNING_BRIEF: "strong",
        AiPurpose.MEETING_PREP: "strong",
        AiPurpose.MEETING_FOLLOWUP: "strong",
        AiPurpose.KNOWLEDGE_ANSWER: "strong",
        AiPurpose.OPPORTUNITY_SCORE: "fast",
        AiPurpose.DIASPORA_MATCH: "fast",
        # consular_triage has no tier: its route forbids generation outright.
        AiPurpose.CONSULAR_TRIAGE: "fast",
    }
)


def budget_for(spec: PurposeSpec) -> float:
    """Wall-clock budget for this purpose's live call, in seconds."""
    if spec.budget_seconds is not None:
        return spec.budget_seconds
    return get_settings().ai_gateway_timeout_seconds


def tier_for(purpose: AiPurpose) -> str:
    """Capability tier for ``purpose``. Secondary to sensitivity, never overriding it."""
    return _TIER_BY_PURPOSE.get(purpose, "strong")


def _model_for_tier(tier: str) -> str:
    settings = get_settings()
    return settings.anthropic_model_fast if tier == "fast" else settings.anthropic_model


def _purpose_withheld(spec: PurposeSpec, effective_class: Classification, tier: str) -> ModelRoute:
    """A route the BAND would allow but the PURPOSE declines.

    Only reachable where the band is live-eligible, so the withholding is genuinely the
    purpose's own decision and the badge says so rather than implying the band forbade it.
    """
    return ModelRoute(
        route=spec.default_model_route,
        reason=(
            f"The {effective_class.value} band permits an external call, but "
            f"{spec.purpose.value} declines it: this purpose works on consular process "
            "metadata, and no ruling permits that leaving the mission. Section 4a's table "
            "is unchanged - this purpose simply does not use its external lane."
        ),
        model_requested=None,
        tier=tier,
        badge=BADGE_SEP.join(
            [effective_class.value, "purpose withholds external", "metadata-only"]
        ),
        live_eligible=False,
        retention="no external call at all",
    )


#: The route string for the CONSULAR-SENSITIVE band. Named, because the Gateway keys the
#: metadata-only answer off it and a typo in either place would silently route nothing there.
NO_EXTERNAL_MODEL_ROUTE: Final[str] = "no-external-model"


def route_for(spec: PurposeSpec, effective_class: Classification) -> ModelRoute:
    """Stage 5. Apply the ``BUILD_BIBLE.md`` section 4a routing table.

    Q-12 is RESOLVED and this is the table, no longer a placeholder. Two rules govern it:

    **Sensitivity is the primary key.** The band is chosen by the *effective* class -- the
    maximum over everything the answer was built from (ADR-0006), not the class the caller
    declared. A PUBLIC question answered from a CONFIDENTIAL source is a CONFIDENTIAL
    answer and routes as one.

    **Capability tier is secondary, and never widens the band.** A "strong" purpose in the
    CONSULAR_SENSITIVE band still gets no external call; the tier only chooses which model
    is asked once the band has already permitted asking one.

    THE LIVE GATE. ``live_eligible`` is true for PUBLIC and MISSION_INTERNAL only. That is
    narrower than section 4a's eventual intent for CONFIDENTIAL, deliberately: 4a routes
    CONFIDENTIAL to a "restricted (private-in-prod)" lane, and no such lane exists in the
    demo. Rather than quietly send confidential material down the ordinary external route
    and label it restricted, the route is marked ineligible and the deterministic answer is
    served. Section 4a asks for the sovereign route to be "simulated HONESTLY"; this is the
    same honesty applied one band up.

    RESTRICTED has no ``Classification`` member -- the enum has four zones and 4a names
    five bands -- so the fifth is unreachable rather than unhandled. If it is ever added,
    the ``match`` below has no default arm and mypy will fail until this function is
    updated, which is the failure mode we want.
    """
    tier = tier_for(spec.purpose)

    match effective_class:
        case Classification.CONSULAR_SENSITIVE:
            return ModelRoute(
                route=NO_EXTERNAL_MODEL_ROUTE,
                reason=(
                    "CONSULAR-SENSITIVE never leaves to an external model (BUILD_BIBLE "
                    "section 4a, and Q-06 resolved to the same answer). No external call "
                    "was attempted, generation is withheld, and only metadata-level work "
                    "and the deterministic mission-local answer are available in this zone."
                ),
                model_requested=None,
                tier=None,
                badge=BADGE_SEP.join(
                    [
                        "CONSULAR-SENSITIVE",
                        "no external route",
                        "metadata-only",
                        "generation withheld",
                    ]
                ),
                live_eligible=False,
                retention="no external call at all",
            )

        case Classification.CONFIDENTIAL:
            return ModelRoute(
                route=f"restricted-{spec.default_model_route}",
                reason=(
                    "The most sensitive item in the assembled context is CONFIDENTIAL, so "
                    "section 4a routes this to the restricted lane with enhanced logging. "
                    "That lane is flagged private-in-prod and does not exist in the demo, "
                    "so no external call is made and the deterministic answer is served "
                    "rather than downgrading the material onto the ordinary external route."
                ),
                model_requested=None,
                tier=tier,
                badge=BADGE_SEP.join(
                    ["CONFIDENTIAL", "restricted", _model_for_tier(tier), "enhanced-logging"]
                ),
                live_eligible=False,
                retention="no retention + enhanced logging",
            )

        case Classification.MISSION_INTERNAL:
            model = _model_for_tier("strong")
            if not spec.live_eligible:
                return _purpose_withheld(spec, effective_class, tier)
            return ModelRoute(
                route="external-noret",
                reason=(
                    "The most sensitive item in the assembled context is MISSION-INTERNAL. "
                    "Section 4a routes this to the approved external provider under a "
                    "no-retention agreement, on the strong tier."
                ),
                model_requested=model,
                # 4a fixes MISSION-INTERNAL at the strong tier regardless of purpose: the
                # band, not the purpose, decides here.
                tier="strong",
                badge=BADGE_SEP.join(["INTERNAL", "external-noret", model]),
                live_eligible=True,
                retention="no retention",
            )

        case Classification.PUBLIC:
            model = _model_for_tier(tier)
            if not spec.live_eligible:
                return _purpose_withheld(spec, effective_class, tier)
            return ModelRoute(
                route="external",
                reason=(
                    "Everything in the assembled context is PUBLIC, so section 4a routes "
                    f"this to the approved external provider on the {tier} tier "
                    f"({'briefs and meeting prep' if tier == 'strong' else 'classify and score'})."
                ),
                model_requested=model,
                tier=tier,
                badge=BADGE_SEP.join(["PUBLIC", "external", model, tier]),
                live_eligible=True,
                retention="no training retention",
            )
