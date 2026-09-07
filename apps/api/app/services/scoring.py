"""Explainable opportunity scoring (Blueprint section 5, P0).

**A score is decision-support and never a decision.** Nothing in this module changes a
stage, sends anything, or commits the mission to anything. It produces a number, the seven
factors that number is made of, and the sentence explaining each one. An officer reads the
breakdown and decides; if they disagree with a factor they override it, and the override is
audited and visible in the breakdown forever after.

WHY A WEIGHTED SUM AND NOT A MODEL. A learned score would be more accurate and completely
unusable here: an officer cannot argue with a number they cannot decompose, and a score
nobody can argue with is a score nobody should act on. Weights sum to 1.0, so every
contribution reads directly as "points of the final score out of 100" - the whole breakdown
adds up in front of the reader.

WHY EVIDENCE QUALITY CAPS THE TOTAL. A weighted sum lets six attractive factors outvote
thin evidence, which is exactly backwards: a score is a claim about the world, and a claim
you cannot evidence should not be a strong claim however appealing it is. So evidence
quality also sets a CEILING. When it binds, the breakdown says so rather than quietly
returning a lower number.

WHY THE HERO OPPORTUNITY SCORES LOW. No public source connects an Australian lithium
operator to Nigeria (``docs/OPEN_QUESTIONS.md`` Q-17). The corridor is a synthesis this
platform proposes, so it carries thin corroboration on the one factor that matters most and
its confidence is held below the weakest signal underneath it. The visible gap between a
92-confidence signal and a 61-score opportunity built on it IS the honesty of the product.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.writer import write_audit_event
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.domain.enums import (
    Classification,
    OpportunityStage,
    PolicyResult,
    RelationshipStrength,
)
from app.models.intelligence import Signal
from app.models.opportunities import Opportunity
from app.models.stakeholders import Stakeholder
from app.security.permissions import Permission
from app.security.principal import Principal

__all__ = [
    "FACTORS",
    "OpportunityScore",
    "ScoreFactor",
    "override_factor",
    "score_opportunity",
    "store_score",
]

_logger = get_logger(__name__)

Band = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass(frozen=True, slots=True)
class FactorSpec:
    """One scoring dimension: what it is, what it is worth, and what it means."""

    key: str
    label: str
    weight: float
    question: str


#: The seven factors, weights summing to exactly 1.0.
#:
#: The weights encode a judgement and are meant to be argued with, so they are declared in
#: one visible place rather than scattered through the computation. Strategic alignment and
#: Nigeria applicability lead because a well-evidenced opportunity that does not serve the
#: bilateral relationship is not an opportunity for THIS mission. Evidence quality is
#: weighted modestly here because it also acts as a ceiling - counting it heavily in the sum
#: as well would penalise thin evidence twice.
FACTORS: Final[tuple[FactorSpec, ...]] = (
    FactorSpec(
        "strategic_alignment",
        "Strategic alignment",
        0.20,
        "Does this serve a sector the mission is actually mandated to work in?",
    ),
    FactorSpec(
        "nigeria_applicability",
        "Nigeria applicability",
        0.18,
        "Does the Nigerian side gain something specific, or is it Australia-only?",
    ),
    FactorSpec(
        "stakeholder_readiness",
        "Stakeholder readiness",
        0.15,
        "Is there a named counterpart, and is the relationship warm enough to use?",
    ),
    FactorSpec(
        "economic_knowledge_value",
        "Economic and knowledge value",
        0.15,
        "What is at stake if it works - trade value, or capability transferred?",
    ),
    FactorSpec(
        "evidence_quality",
        "Evidence quality",
        0.12,
        "How much independent, verified public evidence stands behind this?",
    ),
    FactorSpec(
        "time_sensitivity",
        "Time sensitivity",
        0.10,
        "Is there a window, or would next quarter do just as well?",
    ),
    FactorSpec(
        "execution_feasibility",
        "Execution feasibility",
        0.10,
        "Could this mission, with these people, actually deliver it?",
    ),
)

_FACTOR_BY_KEY: Final[dict[str, FactorSpec]] = {spec.key: spec for spec in FACTORS}

#: Sectors this mission is mandated to work in (BUILD_BIBLE section 2's dual sector),
#: as codes from ``data/taxonomy/sectors.json``. UPPER_SNAKE because that is what the
#: taxonomy and the rows actually use - an earlier version matched kebab-case here and
#: silently scored the hero opportunity 30 instead of 90 on its strongest factor, which is
#: the kind of bug a breakdown makes visible and a bare number would not have.
_PRIORITY_SECTORS: Final[frozenset[str]] = frozenset(
    {
        "CRITICAL_MINERALS",
        "CM_LITHIUM",
        "EDUCATION_SKILLS",
        "ED_SKILLED_MIGRATION",
        "ED_HIGHER_EDUCATION",
        "MINING_SERVICES",
    }
)


def _normalise_sector(code: str | None) -> str:
    """Compare sector codes without tripping over separator or case conventions."""
    return (code or "").strip().upper().replace("-", "_")


#: The evidence ceiling. Zero evidence still permits a MEDIUM-LOW score, because an
#: uncorroborated opportunity is a lead worth someone's afternoon - it is just not
#: something to brief an Ambassador on. Full evidence lifts the ceiling to 100.
_CEILING_FLOOR: Final[float] = 40.0
_CEILING_SLOPE: Final[float] = 0.60

_BAND_HIGH: Final[float] = 70.0
_BAND_MEDIUM: Final[float] = 45.0

_RELATIONSHIP_VALUE: Final[dict[RelationshipStrength, float]] = {
    RelationshipStrength.NONE: 10.0,
    RelationshipStrength.WEAK: 30.0,
    RelationshipStrength.DEVELOPING: 55.0,
    RelationshipStrength.STRONG: 80.0,
    RelationshipStrength.STRATEGIC: 95.0,
}

#: Stage is a proxy for feasibility: an opportunity someone has already carried to a
#: meeting has demonstrated it can be carried.
_STAGE_VALUE: Final[dict[OpportunityStage, float]] = {
    OpportunityStage.DETECTED: 20.0,
    OpportunityStage.QUALIFIED: 40.0,
    OpportunityStage.CONTACT_PLANNED: 55.0,
    OpportunityStage.CONTACTED: 65.0,
    OpportunityStage.MEETING: 80.0,
    OpportunityStage.NEGOTIATION: 90.0,
    OpportunityStage.PARTNERED: 95.0,
    OpportunityStage.CLOSED: 30.0,
}


@dataclass(frozen=True, slots=True)
class ScoreFactor:
    """One factor's contribution, and why it is what it is."""

    key: str
    label: str
    weight: float
    #: 0-100, what the data says about this dimension.
    value: float
    #: ``value * weight`` - points of the final score. The breakdown adds up.
    contribution: float
    #: One sentence naming the rows this value was computed from.
    basis: str
    evidence_ids: tuple[str, ...] = ()
    #: Set only when an officer has adjusted this factor. The machine's original value is
    #: never discarded: an override that hides what it replaced is not auditable.
    machine_value: float | None = None
    overridden_by: str | None = None
    override_reason: str | None = None

    @property
    def is_overridden(self) -> bool:
        return self.machine_value is not None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "factor": self.key,
            "label": self.label,
            "weight": round(self.weight, 3),
            "value": round(self.value, 1),
            "contribution": round(self.contribution, 2),
            "basis": self.basis,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.is_overridden:
            payload["override"] = {
                "machine_value": round(self.machine_value or 0.0, 1),
                "officer_value": round(self.value, 1),
                "by": self.overridden_by,
                "reason": self.override_reason,
            }
        return payload


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    """A score, everything it is made of, and what it is not."""

    opportunity_id: uuid.UUID
    opportunity_ref: str
    score: float
    band: Band
    confidence: float
    is_proposed_by_ai: bool
    factors: tuple[ScoreFactor, ...]
    weighted_total: float
    evidence_ceiling: float
    capped: bool
    caveats: tuple[str, ...]
    #: Set when this is an AI-proposed opportunity whose score was held below the weakest
    #: signal beneath it. ``None`` when the rule did not bind.
    proposal_ceiling: float | None = None

    #: Stated on every score, in the payload, deliberately. The number informs a decision;
    #: it never is one, and nothing downstream may treat it as one.
    decision_support_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "band": self.band,
            "confidence": round(self.confidence, 2),
            "is_proposed_by_ai": self.is_proposed_by_ai,
            "decision_support_only": self.decision_support_only,
            "weighted_total": round(self.weighted_total, 1),
            "evidence_ceiling": round(self.evidence_ceiling, 1),
            "capped_by_evidence": self.capped,
            "ai_proposal_ceiling": (
                round(self.proposal_ceiling, 1) if self.proposal_ceiling is not None else None
            ),
            "caveats": list(self.caveats),
            "factors": [factor.as_dict() for factor in self.factors],
        }

    def explain(self) -> list[str]:
        """The breakdown as lines a person reads. Used by the CLI and the trace drawer."""
        lines = [
            f"{self.opportunity_ref}",
            f"  SCORE {self.score:.0f}/100  ({self.band})   confidence {self.confidence:.2f}",
            "",
            f"  {'factor':<28}{'weight':>7}{'value':>7}{'points':>8}   basis",
            f"  {'-' * 28}{'-' * 7}{'-' * 7}{'-' * 8}   {'-' * 44}",
        ]
        for factor in self.factors:
            marker = " *" if factor.is_overridden else "  "
            lines.append(
                f"  {factor.label:<28}{factor.weight:>7.2f}{factor.value:>7.0f}"
                f"{factor.contribution:>8.1f}{marker} {factor.basis[:70]}"
            )
            if factor.is_overridden:
                lines.append(
                    f"  {'':<28}{'':>7}{'':>7}{'':>8}   * officer-adjusted from "
                    f"{factor.machine_value:.0f} by {factor.overridden_by}: "
                    f"{factor.override_reason}"
                )
        lines.append(f"  {'-' * 28}{'-' * 7}{'-' * 7}{'-' * 8}")
        lines.append(f"  {'weighted total':<28}{'':>7}{'':>7}{self.weighted_total:>8.1f}")
        if self.is_proposed_by_ai:
            lines.append(
                f"  {'':<28}{'':>7}{'':>7}{'':>8}   AI-PROPOSED - pending officer qualification"
            )
        if self.capped:
            lines.append(
                f"  {'evidence ceiling':<28}{'':>7}{'':>7}{self.evidence_ceiling:>8.1f}"
                "   <- BINDING: thin evidence caps this score"
            )
        for caveat in self.caveats:
            lines.append(f"  ! {caveat}")
        lines.append("  Decision support only. The officer decides, not the score.")
        return lines


def _linked_signals(session: Session, opportunity: Opportunity) -> list[Signal]:
    return list(
        session.scalars(
            select(Signal).where(Signal.opportunity_id == opportunity.id).order_by(Signal.id)
        )
    )


def _evidence_from(signals: list[Signal], session: Session) -> tuple[list[str], list[str]]:
    """Distinct citation ids and distinct publishers behind an opportunity's signals."""
    from app.models.intelligence import Document

    document_ids = [s.document_id for s in signals if s.document_id is not None]
    if not document_ids:
        return [], []
    rows = session.execute(
        select(Document.citation_id, Document.source_id).where(Document.id.in_(document_ids))
    ).all()
    citations = sorted({str(c) for c, _ in rows if c})
    publishers = sorted({str(s) for _, s in rows if s})
    return citations, publishers


def _f(key: str, value: float, basis: str, evidence: tuple[str, ...] = ()) -> ScoreFactor:
    spec = _FACTOR_BY_KEY[key]
    bounded = max(0.0, min(100.0, value))
    return ScoreFactor(
        key=spec.key,
        label=spec.label,
        weight=spec.weight,
        value=bounded,
        contribution=bounded * spec.weight,
        basis=basis,
        evidence_ids=evidence,
    )


def _as_override(
    machine: ScoreFactor, value: float, by: str | None, reason: str | None
) -> ScoreFactor:
    """A factor carrying an officer's value with the machine's kept beside it."""
    clamped = max(0.0, min(100.0, value))
    return replace(
        machine,
        value=clamped,
        contribution=clamped * machine.weight,
        machine_value=machine.value,
        overridden_by=by,
        override_reason=reason,
    )


def _stored_overrides(
    opportunity: Opportunity, factors: list[ScoreFactor]
) -> dict[str, ScoreFactor]:
    """Re-apply officer overrides recorded on the last stored breakdown.

    The officer's value survives; the machine's does not. ``machine_value`` is taken from
    the factor computed just now rather than the one stored with the override, so the
    breakdown shows the officer's judgment against what the machine thinks *today* - new
    evidence may well have moved it, and showing a stale comparison would hide that.
    """
    rationale = opportunity.score_rationale
    if not isinstance(rationale, dict):
        return {}
    fresh = {factor.key: factor for factor in factors}
    restored: dict[str, ScoreFactor] = {}
    for entry in rationale.get("factors") or []:
        if not isinstance(entry, dict):
            continue
        override = entry.get("override")
        if not isinstance(override, dict):
            continue
        machine = fresh.get(str(entry.get("factor") or ""))
        if machine is None:
            continue
        try:
            value = float(override["officer_value"])
        except (KeyError, TypeError, ValueError):
            continue
        restored[machine.key] = _as_override(
            machine, value, override.get("by"), override.get("reason")
        )
    return restored


def score_opportunity(
    session: Session,
    opportunity: Opportunity,
    *,
    overrides: dict[str, ScoreFactor] | None = None,
    apply_stored_overrides: bool = True,
    now: datetime | None = None,
) -> OpportunityScore:
    """Compute the score and the whole breakdown. Pure: writes nothing.

    ``overrides`` replaces individual factors with officer-adjusted ones, carrying their
    provenance. Everything else is recomputed from the row and its signals, so an override
    never freezes the rest of the score in time.

    Overrides an officer made earlier are re-applied from the stored breakdown unless
    ``apply_stored_overrides`` is false. Without that, a routine :func:`rescore_all` would
    quietly erase a judgment somebody signed their name to - the score would revert to the
    machine's view while the audit trail still said an officer had moved it. Pass false to
    see what the machine alone currently thinks.
    """
    now = now or datetime.now(UTC)
    signals = _linked_signals(session, opportunity)
    citations, publishers = _evidence_from(signals, session)

    # -- 1. strategic alignment ------------------------------------------------
    sector = _normalise_sector(opportunity.sector_code)
    in_priority = sector in _PRIORITY_SECTORS
    sub_in_priority = _normalise_sector(opportunity.sub_sector_code) in _PRIORITY_SECTORS
    alignment = 90.0 if in_priority else (65.0 if sub_in_priority else 30.0)
    factors = [
        _f(
            "strategic_alignment",
            alignment,
            (
                f"sector {sector!r} is a mission priority sector"
                if in_priority
                else f"sector {sector!r} sits outside the mission's priority sectors"
            ),
        )
    ]

    # -- 2. Nigeria applicability ----------------------------------------------
    nigerian_evidence = [c for c in citations if _looks_nigerian(c)]
    focus_ng = (opportunity.country_focus or "").upper() == "NG"
    applicability = 25.0
    if nigerian_evidence:
        applicability = 85.0
    elif focus_ng:
        applicability = 60.0
    factors.append(
        _f(
            "nigeria_applicability",
            applicability,
            (
                f"{len(nigerian_evidence)} Nigerian source(s) behind the linked signals"
                if nigerian_evidence
                else (
                    "country focus is NG but no Nigerian source stands behind it"
                    if focus_ng
                    else "nothing links this to a specific Nigerian gain"
                )
            ),
            tuple(nigerian_evidence),
        )
    )

    # -- 3. stakeholder readiness ----------------------------------------------
    stakeholder = (
        session.get(Stakeholder, opportunity.primary_stakeholder_id)
        if opportunity.primary_stakeholder_id
        else None
    )
    if stakeholder is None:
        readiness, readiness_basis = 5.0, "no named counterpart on this opportunity"
    else:
        readiness = _RELATIONSHIP_VALUE.get(stakeholder.relationship_strength, 30.0)
        stale_days = (
            (now - stakeholder.last_contact_at).days if stakeholder.last_contact_at else None
        )
        if stale_days is not None and stale_days > 120:
            # A strong relationship nobody has used in four months is not ready.
            readiness *= 0.7
            readiness_basis = (
                f"{stakeholder.relationship_strength.value} relationship, but last contact "
                f"was {stale_days} days ago"
            )
        else:
            readiness_basis = (
                f"{stakeholder.relationship_strength.value} relationship with a named counterpart"
            )
    factors.append(_f("stakeholder_readiness", readiness, readiness_basis))

    # -- 4. economic and knowledge value ---------------------------------------
    value_aud = float(opportunity.value_estimate_aud or Decimal(0))
    # Log-ish banding rather than linear: the difference between a $1m and a $10m
    # opportunity matters far more than between $100m and $110m.
    if value_aud >= 50_000_000:
        economic = 95.0
    elif value_aud >= 10_000_000:
        economic = 80.0
    elif value_aud >= 1_000_000:
        economic = 60.0
    elif value_aud > 0:
        economic = 40.0
    else:
        economic = 35.0 if "skill" in (opportunity.title or "").lower() else 20.0
    factors.append(
        _f(
            "economic_knowledge_value",
            economic,
            (
                f"estimated value A${value_aud:,.0f}"
                if value_aud
                else "no value estimate; scored on capability transfer alone"
            ),
        )
    )

    # -- 5. evidence quality ----------------------------------------------------
    # Independence matters more than volume: five citations from one publisher is one
    # source repeated, and treating it as five is how a thin story looks well-evidenced.
    evidence_value = min(100.0, len(citations) * 12.0 + len(publishers) * 10.0)
    factors.append(
        _f(
            "evidence_quality",
            evidence_value,
            (
                f"{len(citations)} verified citation(s) across {len(publishers)} "
                f"independent publisher(s) via {len(signals)} linked signal(s)"
            ),
            tuple(citations[:8]),
        )
    )

    # -- 6. time sensitivity ----------------------------------------------------
    if opportunity.next_action_at is not None:
        days = (opportunity.next_action_at - now).days
        urgency = 95.0 if days <= 7 else (75.0 if days <= 30 else 45.0)
        time_basis = f"next action due in {days} day(s)"
    else:
        urgency = 30.0
        time_basis = "no next action scheduled; nothing forces a date"
    factors.append(_f("time_sensitivity", urgency, time_basis))

    # -- 7. execution feasibility ----------------------------------------------
    feasibility = _STAGE_VALUE.get(opportunity.stage, 30.0)
    if opportunity.owner_user_id is None:
        feasibility *= 0.6
        feasibility_basis = f"at {opportunity.stage.value} but nobody owns it"
    else:
        feasibility_basis = f"at {opportunity.stage.value} with a named owner"
    factors.append(_f("execution_feasibility", feasibility, feasibility_basis))

    # -- overrides --------------------------------------------------------------
    # Stored first, then the caller's on top: an override being applied right now supersedes
    # the same officer's earlier one on the same factor.
    effective: dict[str, ScoreFactor] = (
        _stored_overrides(opportunity, factors) if apply_stored_overrides else {}
    )
    effective.update(overrides or {})
    if effective:
        factors = [effective.get(f.key, f) for f in factors]

    weighted_total = sum(f.contribution for f in factors)

    evidence_factor = next(f for f in factors if f.key == "evidence_quality")
    ceiling = _CEILING_FLOOR + _CEILING_SLOPE * evidence_factor.value
    capped = weighted_total > ceiling
    score = min(weighted_total, ceiling)

    caveats: list[str] = []
    if capped:
        caveats.append(
            f"Capped at {ceiling:.0f} by evidence quality: the weighted factors came to "
            f"{weighted_total:.0f}, but a claim cannot be stronger than what stands behind it."
        )

    confidence = min(1.0, evidence_factor.value / 100.0)
    proposal_ceiling: float | None = None
    if opportunity.is_proposed_by_ai:
        signal_confidences = [
            float(s.confidence) / 100.0 for s in signals if s.confidence is not None
        ]
        floor_reference = min(signal_confidences) if signal_confidences else confidence
        # Q-17: the platform's synthesis must render BELOW the sourced signals it rests on.
        confidence = max(0.05, min(confidence, floor_reference - 0.05))

        # And the SCORE, not only the confidence. Both sides of this opportunity may be
        # impeccably evidenced while the LINK between them has no source at all - and if
        # the link is false the opportunity is worth nothing, however good each side looks.
        # So a synthesis may not out-score the weakest fact it rests on. Applied as a
        # visible ceiling rather than folded into a factor, because an officer must be able
        # to see that the machine deliberately held this one back.
        if signal_confidences:
            proposal_ceiling = min(signal_confidences) * 100.0 - 1.0
            if score > proposal_ceiling:
                caveats.append(
                    f"Held at {proposal_ceiling:.0f} by the AI-proposal ceiling: the factors "
                    f"came to {score:.0f}, but a synthesis may not out-score the weakest "
                    f"signal it rests on ({min(signal_confidences) * 100:.0f})."
                )
                score = proposal_ceiling
        caveats.append(
            "AI-PROPOSED, PENDING OFFICER QUALIFICATION. Both sides of this are well "
            "evidenced and the score reflects that; the LINK between them is not. No "
            "public source connects these parties, so this is a synthesis of "
            "independently sourced positions rather than a reported fact."
        )
        if signal_confidences:
            caveats.append(
                f"Confidence {confidence:.2f} is held below the weakest signal it rests on "
                f"({floor_reference:.2f}); the supporting signals range "
                f"{min(signal_confidences):.2f}-{max(signal_confidences):.2f}. A synthesis "
                "may never read as more certain than its own evidence."
            )

    band: Band = "HIGH" if score >= _BAND_HIGH else ("MEDIUM" if score >= _BAND_MEDIUM else "LOW")

    return OpportunityScore(
        opportunity_id=opportunity.id,
        opportunity_ref=opportunity.title,
        score=score,
        band=band,
        confidence=confidence,
        is_proposed_by_ai=opportunity.is_proposed_by_ai,
        factors=tuple(factors),
        weighted_total=weighted_total,
        evidence_ceiling=ceiling,
        capped=capped,
        caveats=tuple(caveats),
        proposal_ceiling=proposal_ceiling,
    )


def _looks_nigerian(citation_id: str) -> bool:
    """Whether a citation id belongs to a Nigerian source.

    Prefix matching on the registry's own id convention (publisher-led slugs). Crude but
    inspectable, and it is used only to explain a factor - never to authorise anything.
    """
    lowered = citation_id.lower()
    return any(
        token in lowered
        for token in ("nigeria", "ng-", "coren", "nipc", "nepc", "statehouse", "fmino", "ngsa")
    )


def store_score(session: Session, opportunity: Opportunity, scored: OpportunityScore) -> None:
    """Persist the number and the whole breakdown onto the opportunity."""
    opportunity.score = Decimal(str(round(scored.score, 2)))
    opportunity.score_rationale = scored.as_dict()
    session.flush()


def _movement(baseline: OpportunityScore, rescored: OpportunityScore) -> str:
    """One sentence on what the override actually did to the published score.

    An override can raise the factors and move the published score not at all, because a
    ceiling is holding it. That is the ceiling working, not the override failing - but an
    audit row reading "moved the score from 71 to 71" looks like a no-op to the next
    reader, so it has to say which of the two happened.
    """
    if rescored.score != baseline.score:
        return f"Score {baseline.score:.0f} -> {rescored.score:.0f}."
    if rescored.score < rescored.weighted_total:
        return (
            f"Factors {baseline.weighted_total:.0f} -> {rescored.weighted_total:.0f}, but the "
            f"published score stays at {rescored.score:.0f}: it is held there by a ceiling, "
            f"not by this factor."
        )
    return f"Score unchanged at {rescored.score:.0f}."


def override_factor(
    session: Session,
    principal: Principal,
    opportunity_id: uuid.UUID,
    *,
    factor_key: str,
    value: float,
    reason: str,
) -> OpportunityScore:
    """Let an authorised officer adjust one factor, and record that they did.

    The machine's value is kept alongside the officer's, not replaced by it: an override
    that hides what it overrode cannot be reviewed. Every override writes an
    ``audit_events`` row carrying both numbers and the officer's reason - a score that can
    be edited invisibly is worse than no score, because it looks objective and is not.
    """
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise NotFoundError(f"No opportunity with id {opportunity_id}.")

    spec = _FACTOR_BY_KEY.get(factor_key)
    if spec is None:
        msg = f"{factor_key!r} is not a scoring factor. Known: {sorted(_FACTOR_BY_KEY)}"
        raise NotFoundError(msg)

    if not principal.has(Permission.QUALIFY_OPPORTUNITY):
        detail = (
            f"{principal.role.value} does not hold {Permission.QUALIFY_OPPORTUNITY.value}, "
            "which adjusting an opportunity score requires."
        )
        write_audit_event(
            session,
            actor=principal,
            action="opportunity.score_override.denied",
            object_type="opportunities.opportunity",
            object_id=opportunity.id,
            policy_result=PolicyResult.DENY,
            classification=opportunity.classification,
            summary=detail,
            payload={"factor": factor_key, "attempted_value": value},
        )
        raise PermissionDeniedError(detail)

    if not reason.strip():
        msg = "An override needs a reason. An unexplained adjustment is not reviewable."
        raise ValueError(msg)

    baseline = score_opportunity(session, opportunity)
    original = next(f for f in baseline.factors if f.key == factor_key)
    # If this factor was already overridden, the machine's value is the one held beside it,
    # not the value on display - otherwise the second override would record the first
    # officer's number as the machine's and the provenance would quietly become fiction.
    machine = (
        replace(original, value=original.machine_value, machine_value=None)
        if original.is_overridden and original.machine_value is not None
        else original
    )
    adjusted = _as_override(
        machine, value, f"{principal.full_name} ({principal.role.value})", reason.strip()
    )
    rescored = score_opportunity(session, opportunity, overrides={factor_key: adjusted})
    store_score(session, opportunity, rescored)

    write_audit_event(
        session,
        actor=principal,
        action="opportunity.score_override",
        object_type="opportunities.opportunity",
        object_id=opportunity.id,
        policy_result=PolicyResult.ALLOW,
        classification=opportunity.classification,
        summary=(
            f"{principal.full_name} ({principal.role.value}) adjusted "
            f"{spec.label!r} from {original.value:.0f} to {adjusted.value:.0f} "
            f"(machine: {machine.value:.0f}). "
            f"{_movement(baseline, rescored)} Reason: {reason.strip()}"
        ),
        payload={
            "factor": factor_key,
            "machine_value": round(machine.value, 1),
            "previous_value": round(original.value, 1),
            "officer_value": round(adjusted.value, 1),
            "score_before": round(baseline.score, 1),
            "score_after": round(rescored.score, 1),
            # The weighted totals as well as the published scores. When a ceiling absorbs
            # an override the two published scores are equal, and without these a reader
            # of the audit row cannot tell an absorbed override from one that did nothing.
            "weighted_total_before": round(baseline.weighted_total, 1),
            "weighted_total_after": round(rescored.weighted_total, 1),
            "held_by_ceiling": rescored.score < rescored.weighted_total,
            "reason": reason.strip(),
        },
    )
    _logger.info(
        "opportunity.score_override",
        opportunity_id=str(opportunity.id),
        factor=factor_key,
        actor_role=principal.role.value,
        score_before=round(baseline.score, 1),
        score_after=round(rescored.score, 1),
    )
    return rescored


def rescore_all(session: Session, *, classification: Classification | None = None) -> int:
    """Recompute and persist every opportunity's score. Returns how many were scored."""
    stmt = select(Opportunity).order_by(Opportunity.id)
    if classification is not None:
        stmt = stmt.where(Opportunity.classification == classification)
    count = 0
    for opportunity in session.scalars(stmt):
        store_score(session, opportunity, score_opportunity(session, opportunity))
        count += 1
    return count
