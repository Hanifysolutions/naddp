"""Consular triage on the CONSULAR-SENSITIVE route: mission-local rules over metadata, no model.

``BUILD_BIBLE.md`` section 4a gives the CONSULAR-SENSITIVE band exactly one kind of work:
"Metadata classification only; NO narrative generation", with no external call at all.
``docs/OPEN_QUESTIONS.md`` Q-06 resolved to the same answer. This module is that work, done
honestly: a small, readable set of rules over the six metadata fields the context policy
admits (``case_type``, ``case_age_days``, ``sla_state``, ``sla_days_remaining``,
``days_paused``, ``status``). It never sees the case summary, the citizen's name or any
correspondence -- the Gateway's stage 4 refuses those before this function is reachable --
and it writes no prose beyond sentences assembled from those six values.

**It proposes; a human disposes.** The result is a :class:`~app.ai.schemas.ConsularTriageResult`
carrying ``approval_status = PENDING_APPROVAL`` (set by the Gateway), ``narrative_withheld``
and ``requires_human_determination`` both true (validated by the schema). Nothing here fires
an event, sets a priority or touches a case; the officer confirms or replaces the proposal
when they fire ``triage`` (``docs/workflows.md`` section 3, row 2).

**Why rules and not a snapshot.** A deterministic snapshot is one fixed answer per scenario,
so every case would receive the same recommendation -- which cannot show the thing the
consular dashboard exists to show, that an emergency travel document near its deadline
outranks a routine renewal with weeks in hand. Rules over metadata can, without a model.

**The rules, stated once.**

* Priority starts from the case type: the emergency products (emergency travel document,
  detention notification, death of a national abroad) are URGENT because urgency is the
  product; a welfare check is HIGH; a visa enquiry referral is LOW; everything else NORMAL.
* The service-level clock can only raise it: DUE_SOON raises one step, BREACHED raises one
  step and never leaves the case below HIGH. A paused clock raises nothing.
* The proposed type is the recorded type. With no narrative to read there is no basis for
  proposing a different one, and the rationale says so rather than pretending otherwise.

Citations are chosen from the caller's authorised set only, from the mission's own published
consular pages. If none is authorised the function raises, and the Gateway serves its
deterministic snapshot instead -- an uncited proposal is never returned.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.ai.evidence import AuthorisedEvidence
from app.ai.schemas import ConsularTriageResult, ContextValue, GatewayContext
from app.core.case_types import case_type
from app.domain.enums import CaseStatus, Priority

__all__ = ["MAX_CITATIONS", "RULES_VERSION", "propose_consular_triage"]

#: Recorded in the rationale so a reader of an old trace knows which rule set answered.
RULES_VERSION: Final[str] = "consular-metadata-rules-1"

#: At most this many sources cited; the schema allows eight.
MAX_CITATIONS: Final[int] = 3

_PRIORITY_SCALE: Final[tuple[Priority, ...]] = (
    Priority.LOW,
    Priority.NORMAL,
    Priority.HIGH,
    Priority.URGENT,
)

_URGENT_BY_TYPE: Final[frozenset[str]] = frozenset(
    {"EMERGENCY_TRAVEL_DOCUMENT", "DETENTION_NOTIFICATION", "DEATH_OF_NATIONAL_ABROAD"}
)
_HIGH_BY_TYPE: Final[frozenset[str]] = frozenset({"CITIZEN_WELFARE_CHECK"})
_LOW_BY_TYPE: Final[frozenset[str]] = frozenset({"VISA_ENQUIRY_REFERRAL"})

#: The mission's own published pages for the type, most specific first.
_CITATIONS_BY_TYPE: Final[Mapping[str, tuple[str, ...]]] = {
    "PASSPORT_RENEWAL": (
        "nigeria-hc-canberra-standard-passport",
        "nis-passports-diaspora-missions",
    ),
    "EMERGENCY_TRAVEL_DOCUMENT": (
        "nigeria-hc-canberra-emergency-travel-certificate",
        "nis-passports-diaspora-missions",
    ),
}

#: Every consular matter is handled by the High Commission's consular function in Canberra,
#: which these registry entries support. Used for types with no page of their own.
_MISSION_CITATIONS: Final[tuple[str, ...]] = (
    "nigeria-hc-canberra-immigration-services",
    "nigeria-hc-canberra-home",
)

_TYPE_REASON: Final[Mapping[str, str]] = {
    "EMERGENCY_TRAVEL_DOCUMENT": "urgent by type, because urgency is the product",
    "DETENTION_NOTIFICATION": "urgent by type, because consular access rights weaken with delay",
    "DEATH_OF_NATIONAL_ABROAD": "urgent by type, because the first response is time-bound",
    "CITIZEN_WELFARE_CHECK": "high by type, because the first contact attempt is the critical step",
    "VISA_ENQUIRY_REFERRAL": "low by type, because nothing is being determined",
}

_NEXT_STEP_BY_STATUS: Final[Mapping[CaseStatus, str]] = {
    CaseStatus.NEW: "Officer confirms or replaces the case type and priority, then fires triage",
    CaseStatus.TRIAGED: "Assign the case to a named consular officer",
    CaseStatus.ASSIGNED: "Begin review, or request the missing information from the citizen",
    CaseStatus.AWAITING_CITIZEN: "Chase the outstanding information; the SLA clock is paused",
    CaseStatus.IN_REVIEW: "Complete the review and record a determination, or escalate",
    CaseStatus.ESCALATED: "Decide above the assigned officer: resolve, or return with guidance",
    CaseStatus.RESOLVED: "Close with a recorded reason once the determination is communicated",
    CaseStatus.CLOSED: "No action: the case is closed",
}

_HUMAN_DECIDES: Final[str] = "A human decides: this proposal changes nothing on its own"


def _text(facts: Mapping[str, ContextValue], key: str) -> str | None:
    value = facts.get(key)
    return value if isinstance(value, str) and value else None


def _number(facts: Mapping[str, ContextValue], key: str) -> float | None:
    value = facts.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _status(facts: Mapping[str, ContextValue]) -> CaseStatus | None:
    raw = _text(facts, "status")
    try:
        return CaseStatus(raw) if raw is not None else None
    except ValueError:
        return None


def _base_priority(code: str | None) -> Priority:
    if code in _URGENT_BY_TYPE:
        return Priority.URGENT
    if code in _HIGH_BY_TYPE:
        return Priority.HIGH
    if code in _LOW_BY_TYPE:
        return Priority.LOW
    return Priority.NORMAL


def _raise_priority(priority: Priority, sla_state: str | None) -> tuple[Priority, str | None]:
    """Apply the clock. Returns the priority and the clause explaining any raise."""
    rank = _PRIORITY_SCALE.index(priority)
    if sla_state == "BREACHED":
        raised = max(rank + 1, _PRIORITY_SCALE.index(Priority.HIGH))
        raised = min(raised, len(_PRIORITY_SCALE) - 1)
        if raised > rank:
            return _PRIORITY_SCALE[raised], "raised because the service level is breached"
        return priority, None
    if sla_state == "DUE_SOON" and rank < len(_PRIORITY_SCALE) - 1:
        return _PRIORITY_SCALE[rank + 1], "raised because the service level falls due soon"
    return priority, None


def _days(value: float) -> str:
    rounded = round(value, 1)
    return f"{rounded:g} business day{'' if rounded == 1 else 's'}"


def _sla_sentence(
    sla_state: str | None,
    remaining: float | None,
    age: float | None,
    paused: float | None,
) -> str:
    parts: list[str] = []
    if age is not None:
        parts.append(f"{_days(age)} chargeable so far")
    if sla_state == "PAUSED":
        parts.append("clock paused while the case waits on the citizen")
    elif sla_state == "BREACHED" and remaining is not None:
        parts.append(f"budget exceeded by {_days(-remaining)}")
    elif remaining is not None:
        parts.append(f"{_days(remaining)} remaining")
    if paused:
        parts.append(f"{_days(paused)} paused in total")
    state = sla_state.replace("_", " ").lower() if sla_state else "not reported"
    return f"service level {state}" + (f" ({', '.join(parts)})" if parts else "")


def _citations(code: str | None, authorised: AuthorisedEvidence) -> list[str]:
    wanted = (*_CITATIONS_BY_TYPE.get(code or "", ()), *_MISSION_CITATIONS)
    allowed = authorised.ids
    chosen: list[str] = []
    for citation_id in wanted:
        if citation_id in allowed and citation_id not in chosen:
            chosen.append(citation_id)
    return chosen[:MAX_CITATIONS]


def propose_consular_triage(
    context: GatewayContext,
    authorised: AuthorisedEvidence,
) -> ConsularTriageResult:
    """Propose a triage for one case from its metadata alone.

    Raises:
        ValueError: when no citation from the caller's authorised set supports the proposal.
            The Gateway then serves its deterministic snapshot rather than an uncited answer.
    """
    facts = context.facts
    code = _text(facts, "case_type")
    spec = case_type(code)
    sla_state = _text(facts, "sla_state")
    remaining = _number(facts, "sla_days_remaining")
    age = _number(facts, "case_age_days")
    paused = _number(facts, "days_paused")
    status = _status(facts)

    base = _base_priority(code)
    priority, raised_because = _raise_priority(base, sla_state)

    citations = _citations(code, authorised)
    if not citations:
        msg = "no authorised mission consular source is available to cite for this proposal"
        raise ValueError(msg)

    type_label = spec.label if spec is not None else "an unrecognised case type"
    type_reason = _TYPE_REASON.get(code or "", "normal by type")
    priority_clause = f"{priority.value}" + (
        f" ({type_reason}; {raised_because})" if raised_because else f" ({type_reason})"
    )
    rationale = (
        f"Proposed from case metadata only, by mission-local rules ({RULES_VERSION}). "
        f"Case type {code or 'not recorded'}, {type_label}. "
        f"{_sla_sentence(sla_state, remaining, age, paused).capitalize()}. "
        f"Status {status.value if status is not None else 'not reported'}. "
        f"Proposed priority {priority_clause}. "
        "NO CASE NARRATIVE WAS READ AND NO MODEL WAS ASKED: nothing about the citizen, their "
        "circumstances or their correspondence entered this proposal, and the "
        "CONSULAR-SENSITIVE route permits metadata-level rules only."
    )

    steps: list[str] = []
    if status is not None:
        steps.append(_NEXT_STEP_BY_STATUS[status])
    if priority is Priority.URGENT and status in {CaseStatus.NEW, CaseStatus.TRIAGED}:
        steps.append("Assign today: this case type cannot wait for the ordinary queue")
    if sla_state == "BREACHED":
        steps.append("Escalate rather than let the breach run on silently")
    elif sla_state == "DUE_SOON" and remaining is not None:
        steps.append(f"Act before the budget is spent: {_days(remaining)} left")
    steps.append(_HUMAN_DECIDES)

    confidence = 0.82
    if spec is None:
        confidence = 0.35
    elif sla_state is None or status is None:
        confidence = 0.6

    used = sorted(key for key, value in facts.items() if value is not None)
    return ConsularTriageResult(
        case_ref=context.subject_ref or "unreferenced case",
        proposed_case_type=code if spec is not None and code is not None else "UNCLASSIFIED",
        proposed_priority=priority,
        proposed_sla_days=spec.default_sla_days if spec is not None else 10,
        rationale=rationale,
        next_steps=steps[:6],
        inputs_used=used or ["case_type"],
        narrative_withheld=True,
        requires_human_determination=True,
        citations=citations,
        confidence=confidence,
    )
