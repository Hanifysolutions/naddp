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
admitted only by a purpose that names it explicitly -- and **no purpose does**, which is
the conservative reading of ``docs/OPEN_QUESTIONS.md`` Q-06 implemented as data rather than
as a comment. ``app.models.ai`` records the same rank trap for the same reason.

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
    "PURPOSES",
    "ContextPolicy",
    "ContextRefusal",
    "ModelRoute",
    "PurposeNotAllowedError",
    "PurposeSpec",
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


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """The stage 5 routing decision: what was chosen, and the sentence explaining why."""

    route: str
    reason: str
    model_requested: str | None


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
        # NOT CONSULAR_SENSITIVE. Q-06 option (c): the narrative never enters, so what this
        # purpose processes is de-identified process metadata, which is MISSION_INTERNAL.
        max_classification=Classification.MISSION_INTERNAL,
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


def route_for(spec: PurposeSpec, effective_class: Classification) -> ModelRoute:
    """Stage 5. Decide the model route and write the sentence that explains it.

    **PLACEHOLDER RULE -- ``docs/OPEN_QUESTIONS.md`` Q-12 is deferred by architect
    decision.** The routing *table* arrives in the Week 2 to 3 handoff. What Week 1 owes is
    the decision *seam* and a route that is visible in the trace drawer
    (``BUILD_BIBLE.md`` section 5), and that is what this is. The rule implemented:

    1. ``CONSULAR_SENSITIVE`` effective classification routes to ``no-external-model``.
       Unreachable today, because no purpose permits that zone and stage 2 refuses first --
       it is recorded so the drawer shows the answer to the sharpest question a consular
       buyer asks, rather than showing nothing because the case never arises.
    2. ``CONFIDENTIAL`` routes to the purpose's route with a ``restricted-`` prefix, so the
       escalation is legible at a glance in the drawer.
    3. Everything else takes the purpose's declared default route.

    The provider model id comes from ``ANTHROPIC_MODEL``; a route that forbids an external
    model requests none, and ``model_requested`` is ``None`` rather than a name nobody
    called.
    """
    model = get_settings().anthropic_model

    if effective_class is Classification.CONSULAR_SENSITIVE:
        return ModelRoute(
            route="no-external-model",
            reason=(
                "The material is CONSULAR_SENSITIVE, so no external model was asked. "
                "Consular case content is not transmitted off-box (docs/OPEN_QUESTIONS.md "
                "Q-06, conservative reading); only the deterministic mission-local answer "
                "is available for this zone."
            ),
            model_requested=None,
        )

    if effective_class is Classification.CONFIDENTIAL:
        return ModelRoute(
            route=f"restricted-{spec.default_model_route}",
            reason=(
                f"Routed to the restricted lane for {spec.purpose.value} because the most "
                "sensitive item in the assembled context is CONFIDENTIAL, and the "
                "classification of an answer is the maximum over what it was built from "
                "(ADR-0006). Routing table deferred as Q-12; this is the Week 1 placeholder "
                "rule."
            ),
            model_requested=model,
        )

    return ModelRoute(
        route=spec.default_model_route,
        reason=(
            f"Routed to the standard lane for {spec.purpose.value} because the most "
            f"sensitive item in the assembled context is {effective_class.value}. Routing "
            "table deferred as Q-12; this is the Week 1 placeholder rule."
        ),
        model_requested=model,
    )
