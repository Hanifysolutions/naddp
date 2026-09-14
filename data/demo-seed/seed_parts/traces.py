"""``ai_traces`` rows for the artefacts the seed marks as AI-produced.

Three columns in the schema make this table non-optional for the seed rather than a nicety:
``opportunities.proposal_trace_id`` is expected non-NULL whenever ``is_proposed_by_ai`` is
true, ``briefs.trace_id`` is expected non-NULL when ``generated_by`` is ``AI``, and
``meetings.pre_read_trace_id`` / ``followup_trace_id`` are what the UI trace drawer opens.
An AI-badged artefact with no trace behind it is exactly the thing the drawer exists to
disprove.

Every seeded trace records ``live = false`` and ``fallback = true`` with reason
``LIVE_DISABLED``, which is the truth: these artefacts were not produced by a live model
call, they are the deterministic snapshots in ``data/demo-seed/ai_snapshots/`` (ADR-0002).
Recording them as live calls would be a fabricated provenance in the one table whose job
is provenance -- and the demo's own trace drawer would then be lying on stage.

``evidence_ids`` is read out of the snapshot file itself rather than retyped, so a trace
and the answer it explains cannot list different sources.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from app.core.config import get_settings, snapshot_path
from app.domain.enums import AiPurpose, ApprovalStatus, Classification, RoleCode
from app.models.ai import AiTrace
from app.models.governance import User
from seed_parts.context import SEED_MARKER, SeedContext

__all__ = ["TRACE_SPECS", "TraceSpec", "seed_traces"]

#: Routes are the ``default_model_route`` each purpose declares in ``app/ai/purposes.py``.
#: Duplicated here as data rather than imported, because importing the purpose registry
#: would drag the Gateway's settings validation into the seed process for one string.
_ROUTES: Final[dict[AiPurpose, str]] = {
    AiPurpose.MORNING_BRIEF: "standard-brief",
    AiPurpose.OPPORTUNITY_SCORE: "standard-analysis",
    AiPurpose.MEETING_PREP: "standard-analysis",
    AiPurpose.MEETING_FOLLOWUP: "standard-drafting",
    AiPurpose.CONSULAR_TRIAGE: "restricted-metadata-only",
    AiPurpose.KNOWLEDGE_ANSWER: "standard-grounded",
    AiPurpose.DIASPORA_MATCH: "standard-analysis",
}

#: The one purpose whose route forbids an external call outright, so the band would permit
#: generation but the purpose declines it (``app/ai/purposes.py`` ``_purpose_withheld``).
#: Kept as data for the same reason as ``_ROUTES``.
_WITHHOLDS_EXTERNAL: Final[frozenset[AiPurpose]] = frozenset({AiPurpose.CONSULAR_TRIAGE})

#: BUILD_BIBLE section 4a renders the trace-drawer badge with middle dots. Mirrors
#: ``app.ai.purposes.BADGE_SEP``; the badge is a contract with the drawer, not formatting.
_BADGE_SEP: Final[str] = " · "


def _strong_model() -> str:
    """The model the MISSION-INTERNAL band asks for.

    Read from settings rather than written out, so the seeded badge cannot drift from what
    ``route_for()`` would emit when ``ANTHROPIC_MODEL`` changes. ``app.core.config`` is
    already in this module's import graph via ``snapshot_path``, so this costs nothing the
    seed was not already paying -- unlike importing ``app.ai.purposes``, which is what the
    note above ``_ROUTES`` declines to do.
    """
    return get_settings().anthropic_model


def _badge(purpose: AiPurpose, data_class: Classification) -> str:
    """The section 4a badge this call would have carried.

    Every seeded trace runs in the MISSION-INTERNAL band, where 4a fixes the strong tier
    regardless of purpose and the badge is three segments with no tier -- only the PUBLIC
    arm carries a fourth. A purpose that withholds the external call gets the withheld
    badge instead, which states the *purpose* declined rather than implying the band
    forbade it.
    """
    if purpose in _WITHHOLDS_EXTERNAL:
        return _BADGE_SEP.join(
            [data_class.value, "purpose withholds external", "metadata-only"]
        )
    if data_class is Classification.MISSION_INTERNAL:
        return _BADGE_SEP.join(["INTERNAL", "external-noret", _strong_model()])
    if data_class is Classification.PUBLIC:
        return _BADGE_SEP.join(["PUBLIC", "external", _strong_model(), "strong"])
    if data_class is Classification.CONFIDENTIAL:
        return _BADGE_SEP.join(
            ["CONFIDENTIAL", "restricted", _strong_model(), "enhanced-logging"]
        )
    return _BADGE_SEP.join(
        ["CONSULAR-SENSITIVE", "no external route", "metadata-only"]
    )


@dataclass(frozen=True, slots=True)
class TraceSpec:
    """One seeded Gateway call: which purpose, for whom, over what."""

    slug: str
    purpose: AiPurpose
    scenario: str
    actor: RoleCode
    days_ago: float
    data_class: Classification = Classification.MISSION_INTERNAL
    result_class: Classification = Classification.MISSION_INTERNAL
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    output_schema_name: str | None = None


TRACE_SPECS: Final[tuple[TraceSpec, ...]] = (
    TraceSpec(
        "trace-opportunity-score-hero",
        AiPurpose.OPPORTUNITY_SCORE,
        "au-lithium-ng-skills-corridor",
        RoleCode.TRADE_OFFICER,
        2.1,
        output_schema_name="OpportunityScoreResult",
    ),
    TraceSpec(
        "trace-morning-brief-mission",
        AiPurpose.MORNING_BRIEF,
        "__default__",
        RoleCode.DEPUTY,
        0.3,
        output_schema_name="MorningBriefResult",
    ),
    TraceSpec(
        "trace-morning-brief-ambassador",
        AiPurpose.MORNING_BRIEF,
        "ambassador",
        RoleCode.AMBASSADOR,
        0.25,
        output_schema_name="MorningBriefResult",
    ),
    TraceSpec(
        "trace-morning-brief-trade-officer",
        AiPurpose.MORNING_BRIEF,
        "trade_officer",
        RoleCode.TRADE_OFFICER,
        0.24,
        output_schema_name="MorningBriefResult",
    ),
    TraceSpec(
        "trace-morning-brief-consular-officer",
        AiPurpose.MORNING_BRIEF,
        "consular_officer",
        RoleCode.CONSULAR_OFFICER,
        0.23,
        output_schema_name="MorningBriefResult",
    ),
    TraceSpec(
        "trace-meeting-prep-covalent",
        AiPurpose.MEETING_PREP,
        "covalent-lithium-bilateral",
        RoleCode.TRADE_OFFICER,
        4.2,
        output_schema_name="MeetingPrepResult",
    ),
    TraceSpec(
        "trace-meeting-followup-covalent",
        AiPurpose.MEETING_FOLLOWUP,
        "covalent-lithium-bilateral",
        RoleCode.TRADE_OFFICER,
        2.8,
        approval_status=ApprovalStatus.PENDING_APPROVAL,
        output_schema_name="MeetingFollowupResult",
    ),
    TraceSpec(
        "trace-consular-triage-passport",
        AiPurpose.CONSULAR_TRIAGE,
        "passport-renewal",
        RoleCode.CONSULAR_OFFICER,
        5.6,
        approval_status=ApprovalStatus.PENDING_APPROVAL,
        output_schema_name="ConsularTriageResult",
    ),
    TraceSpec(
        "trace-knowledge-answer-pathways",
        AiPurpose.KNOWLEDGE_ANSWER,
        "lithium-processing-skills-pathways",
        RoleCode.TRADE_OFFICER,
        7.4,
        output_schema_name="KnowledgeAnswerResult",
    ),
    TraceSpec(
        "trace-diaspora-match-lithium",
        AiPurpose.DIASPORA_MATCH,
        "lithium-migration",
        RoleCode.DIASPORA_OFFICER,
        3.3,
        output_schema_name="DiasporaMatchResult",
    ),
)


def _snapshot_evidence(purpose: AiPurpose, scenario: str) -> list[str]:
    """Read the evidence ids the deterministic snapshot for this call actually cites."""
    path = snapshot_path(purpose.value.lower(), scenario)
    document = json.loads(path.read_text(encoding="utf-8"))
    ids = document.get("evidence_ids") or []
    return [str(item) for item in ids]


def seed_traces(ctx: SeedContext, users: dict[RoleCode, User]) -> dict[str, AiTrace]:
    """Load one trace per seeded AI artefact. Returns them keyed by slug."""
    traces: dict[str, AiTrace] = {}
    for spec in TRACE_SPECS:
        evidence = _snapshot_evidence(spec.purpose, spec.scenario)
        route = _ROUTES[spec.purpose]
        withheld = spec.purpose in _WITHHOLDS_EXTERNAL
        traces[spec.slug] = ctx.upsert(
            AiTrace,
            ctx.register("ai_trace", spec.slug),
            created_at=ctx.days_ago(spec.days_ago),
            purpose=spec.purpose,
            scenario=spec.scenario,
            user_id=users[spec.actor].id,
            actor_role=spec.actor,
            data_class=spec.data_class,
            result_class=spec.result_class,
            model_route=route,
            route_badge=_badge(spec.purpose, spec.data_class),
            route_reason=(
                (
                    f"The {spec.data_class.value} band permits an external call, but "
                    f"{spec.purpose.value} declines it: this purpose works on process "
                    "metadata only, so no narrative is sent anywhere."
                )
                if withheld
                else (
                    "The most sensitive item in the assembled context is "
                    f"{spec.data_class.value}. Section 4a routes this to the approved "
                    "external provider under a no-retention agreement, on the strong tier."
                )
            ),
            model_requested=None if withheld else _strong_model(),
            model_used=None,
            live=False,
            fallback=True,
            fallback_reason="LIVE_DISABLED",
            latency_ms=ctx.rng.randrange(6, 40),
            input_tokens=None,
            output_tokens=None,
            prompt_hash=None,
            retrieval_filter={
                "stage": "retrieval_authorisation",
                "source": "data/demo-seed/citations.json",
                "actor_role": spec.actor.value,
                "verified_only": True,
                "authorised_count": len(evidence),
                "applied": "before_selection",
            },
            evidence_ids=evidence,
            output_schema_name=spec.output_schema_name,
            schema_valid=True,
            citation_check_passed=True,
            approval_status=spec.approval_status,
            stages=[
                {"stage": 1, "name": "classify", "outcome": spec.data_class.value},
                {"stage": 2, "name": "retrieve", "outcome": f"{len(evidence)} verified citations"},
                {"stage": 5, "name": "route", "outcome": route},
                {"stage": 6, "name": "generate", "outcome": "deterministic snapshot (ADR-0002)"},
                {"stage": 8, "name": "citation_check", "outcome": "passed"},
            ],
            error=None,
            request_id=f"seed-{SEED_MARKER}-{spec.slug}"[:64],
        )
    ctx.session.flush()
    return traces
