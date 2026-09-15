"""Fifteen consular cases, their immutable timelines and their evidence.

**Everything here is invented, and the design assumes it will be screenshotted.** Subject
names are made up; ``subject_reference`` is a ``DEMO-SUBJ-nnn`` token and is emphatically
not a passport number; no date of birth, address, telephone number or next-of-kin detail is
recorded anywhere. ``public_ref`` is minted by ``app.core.ids.new_public_ref`` -- CSPRNG,
no timestamp, no context tag (ADR-0007) -- so it discloses nothing about the case even to
someone holding it.

**Two hero cases (W3.3).** The ROUTINE hero is a passport renewal for a Nigerian student in
Australia, mid-way through its budget -- it ties the consular thread to the skilled-migration
half of the narrative. The URGENT hero is an emergency travel document for a lost passport,
untriaged and a few business hours from its deadline. Side by side on the dashboard they let
the metadata-only triage show the prioritisation a consular officer then confirms: the AI
recommends, the officer disposes.

**Ageing is real, in business days** (Q-15). ``opened_days_ago`` is read as business days
and every timestamp on a case's timeline is laid out along business time, so a reset on a
Monday and one on a Thursday tell the same story: cases past their SLA, the urgent hero due
soon, cases with the clock paused in ``AWAITING_CITIZEN`` (where it does not run at all) and
the rest healthy. ``sla_due_at`` is computed by ``app.domain.sla`` from that timeline,
exactly as the API computes it. A tile that is uniformly green demonstrates nothing.

**Idempotency note.** ``case_events`` refuses ``UPDATE`` and ``DELETE`` at the database
trigger, so timeline rows are written with ``insert_once`` and never touched again. And
``cases.public_ref`` is generated once and then *preserved* on re-run: re-minting it would
invalidate every reference a rehearsal had written down, and re-running the seed is
supposed to be safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.core.ids import new_public_ref
from app.domain.enums import (
    CaseEventType,
    CaseStatus,
    Classification,
    EvidenceType,
    Priority,
    RoleCode,
)
from app.domain.sla import (
    add_business_days,
    compute_sla,
    pauses_from_transitions,
    subtract_business_days,
)
from app.models.consular import Case, CaseEvent, CaseEvidence
from app.models.governance import User
from seed_parts.context import SeedContext, ulid_str
from seed_parts.objectstore import assert_resolves, store_text
from seed_parts.registry import case_type

__all__ = ["CASE_SPECS", "HERO_CASE", "HERO_URGENT_CASE", "CaseSpec", "seed_cases"]

#: The ROUTINE hero: a passport renewal, mid-budget.
HERO_CASE: Final[str] = "case-hero-passport-renewal"

#: The URGENT hero: an emergency travel document, untriaged and close to its deadline.
HERO_URGENT_CASE: Final[str] = "case-etd-lost-passport-hobart"

#: Which non-terminal statuses have been through which earlier states. Used to build a
#: timeline that is consistent with ``docs/workflows.md`` section 3 rather than a random
#: pile of entries -- a case sitting in ``IN_REVIEW`` whose timeline never shows an
#: assignment is a case whose history contradicts its state.
_PATH: Final[dict[CaseStatus, tuple[CaseStatus, ...]]] = {
    CaseStatus.NEW: (),
    CaseStatus.TRIAGED: (CaseStatus.TRIAGED,),
    CaseStatus.ASSIGNED: (CaseStatus.TRIAGED, CaseStatus.ASSIGNED),
    CaseStatus.AWAITING_CITIZEN: (
        CaseStatus.TRIAGED,
        CaseStatus.ASSIGNED,
        CaseStatus.AWAITING_CITIZEN,
    ),
    CaseStatus.IN_REVIEW: (CaseStatus.TRIAGED, CaseStatus.ASSIGNED, CaseStatus.IN_REVIEW),
    CaseStatus.ESCALATED: (CaseStatus.TRIAGED, CaseStatus.ASSIGNED, CaseStatus.ESCALATED),
    CaseStatus.RESOLVED: (
        CaseStatus.TRIAGED,
        CaseStatus.ASSIGNED,
        CaseStatus.IN_REVIEW,
        CaseStatus.RESOLVED,
    ),
    CaseStatus.CLOSED: (
        CaseStatus.TRIAGED,
        CaseStatus.ASSIGNED,
        CaseStatus.IN_REVIEW,
        CaseStatus.RESOLVED,
        CaseStatus.CLOSED,
    ),
}

_EVENT_TYPE: Final[dict[CaseStatus, CaseEventType]] = {
    CaseStatus.TRIAGED: CaseEventType.STATUS_CHANGE,
    CaseStatus.ASSIGNED: CaseEventType.ASSIGNMENT,
    CaseStatus.AWAITING_CITIZEN: CaseEventType.COMMUNICATION,
    CaseStatus.IN_REVIEW: CaseEventType.STATUS_CHANGE,
    CaseStatus.ESCALATED: CaseEventType.STATUS_CHANGE,
    CaseStatus.RESOLVED: CaseEventType.DETERMINATION,
    CaseStatus.CLOSED: CaseEventType.STATUS_CHANGE,
}

_EVENT_NOTE: Final[dict[CaseStatus, str]] = {
    CaseStatus.TRIAGED: (
        "Triaged by a consular officer: case type, priority and classification confirmed "
        "by a human. An AI proposal may inform this step and may never make it."
    ),
    CaseStatus.ASSIGNED: "Assigned to the responsible consular officer.",
    CaseStatus.AWAITING_CITIZEN: (
        "Information requested from the applicant. The SLA clock is paused while the case "
        "waits on the citizen."
    ),
    CaseStatus.IN_REVIEW: "Review started by the assigned officer.",
    CaseStatus.ESCALATED: (
        "Escalated for a decision above the assigned officer: complexity, sensitivity or "
        "SLA breach risk."
    ),
    CaseStatus.RESOLVED: (
        "Determination made and communicated. A consular determination is never made "
        "autonomously (BUILD_BIBLE section 6)."
    ),
    CaseStatus.CLOSED: "Administratively closed with a recorded reason.",
}


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """One synthetic consular case."""

    slug: str
    case_type_code: str
    status: CaseStatus
    priority: Priority
    subject_name: str
    country: str
    channel: str
    summary: str
    opened_days_ago: float
    assigned: RoleCode | None = RoleCode.CONSULAR_OFFICER
    determination: str | None = None
    determined_by: RoleCode | None = None
    close_reason: str | None = None
    closed_by: RoleCode | None = None
    evidence: tuple[tuple[str, EvidenceType, bool], ...] = ()
    #: Anchor the clock by what is LEFT rather than by age: the case is laid out so that this
    #: many business days of budget remain at seed time. Used for the urgent hero, whose
    #: "a few hours from its deadline" must hold on whatever day the demo is reset.
    remaining_business_days: float | None = None


CASE_SPECS: Final[tuple[CaseSpec, ...]] = (
    CaseSpec(
        slug=HERO_CASE,
        case_type_code="PASSPORT_RENEWAL",
        status=CaseStatus.IN_REVIEW,
        priority=Priority.NORMAL,
        subject_name="Chinedu Okonkwo-Bello",
        country="AU",
        channel="ONLINE_PORTAL",
        summary=(
            "Standard passport renewal for a Nigerian postgraduate student resident in "
            "Western Australia. Applicant reports a student visa expiry inside the "
            "renewal window and has asked whether the renewal can be prioritised. "
            "Identity documents received; biometric appointment completed. Under review "
            "by the assigned officer."
        ),
        opened_days_ago=10.0,
        evidence=(
            ("Completed renewal application form", EvidenceType.FORM, True),
            ("Expiring passport biodata page (synthetic)", EvidenceType.IDENTITY_PROOF, True),
            ("Applicant correspondence: request to prioritise", EvidenceType.CORRESPONDENCE, False),
        ),
    ),
    CaseSpec(
        slug="case-etd-stranded-traveller",
        case_type_code="EMERGENCY_TRAVEL_DOCUMENT",
        status=CaseStatus.ESCALATED,
        priority=Priority.URGENT,
        subject_name="Amaka Ndubuisi",
        country="AU",
        channel="TELEPHONE",
        summary=(
            "Traveller reports a lost passport with an onward flight booked. Identity "
            "satisfaction on reduced evidence is required, which is why this is a "
            "determination and not an administrative step. SLA BREACHED."
        ),
        opened_days_ago=3.2,
        evidence=(
            ("Police lost-property report (synthetic)", EvidenceType.DOCUMENT, True),
            ("Flight itinerary supplied by traveller", EvidenceType.DOCUMENT, False),
        ),
    ),
    CaseSpec(
        slug="case-welfare-check-melbourne",
        case_type_code="CITIZEN_WELFARE_CHECK",
        status=CaseStatus.ASSIGNED,
        priority=Priority.HIGH,
        subject_name="Obinna Achebe-Falade",
        country="AU",
        channel="EMAIL",
        summary=(
            "Family in Nigeria has asked the mission to establish the wellbeing of a "
            "relative in Melbourne after a period without contact. Consent to disclose "
            "has not been established; the first contact attempt is the critical step."
        ),
        opened_days_ago=2.6,
    ),
    CaseSpec(
        slug="case-notarial-attestation-degree",
        case_type_code="NOTARIAL_ATTESTATION",
        status=CaseStatus.AWAITING_CITIZEN,
        priority=Priority.NORMAL,
        subject_name="Yewande Ogunlesi",
        country="AU",
        channel="COUNTER",
        summary=(
            "Attestation of a Nigerian degree certificate for use in an Australian skills "
            "assessment. Awaiting the original transcript from the applicant; the SLA "
            "clock is paused."
        ),
        opened_days_ago=9.4,
        evidence=(("Degree certificate copy (synthetic)", EvidenceType.DOCUMENT, True),),
    ),
    CaseSpec(
        slug="case-birth-registration-perth",
        case_type_code="BIRTH_REGISTRATION",
        status=CaseStatus.IN_REVIEW,
        priority=Priority.NORMAL,
        subject_name="Infant of Kelechi and Nneka Uzoma",
        country="AU",
        channel="ONLINE_PORTAL",
        summary=(
            "Registration of a birth in Western Australia establishing civil status and "
            "the basis for citizenship documentation. Parental documentation received and "
            "under verification."
        ),
        opened_days_ago=5.1,
        evidence=(
            ("Australian birth certificate (synthetic)", EvidenceType.DOCUMENT, True),
            ("Parental passports (synthetic)", EvidenceType.IDENTITY_PROOF, True),
        ),
    ),
    CaseSpec(
        slug="case-visa-enquiry-referral",
        case_type_code="VISA_ENQUIRY_REFERRAL",
        status=CaseStatus.TRIAGED,
        priority=Priority.LOW,
        subject_name="Tolu Ajibade",
        country="AU",
        channel="EMAIL",
        summary=(
            "Enquiry about entry to Australia. The mission does not decide Australian "
            "entry and the enquiry will be referred; no determination is required."
        ),
        opened_days_ago=1.1,
        assigned=None,
    ),
    CaseSpec(
        slug="case-detention-notification-sydney",
        case_type_code="DETENTION_NOTIFICATION",
        status=CaseStatus.ESCALATED,
        priority=Priority.URGENT,
        subject_name="Emeka Nwafor",
        country="AU",
        channel="OFFICIAL_NOTIFICATION",
        summary=(
            "Notification received of the detention of a Nigerian national. Consular "
            "access and welfare are the immediate questions. Escalated on sensitivity."
        ),
        opened_days_ago=0.6,
    ),
    CaseSpec(
        slug="case-death-of-national-brisbane",
        case_type_code="DEATH_OF_NATIONAL_ABROAD",
        status=CaseStatus.RESOLVED,
        priority=Priority.URGENT,
        subject_name="Estate of A. Balogun-Idris",
        country="AU",
        channel="OFFICIAL_NOTIFICATION",
        summary=(
            "Death of a Nigerian national in Queensland. Repatriation documentation "
            "issued and the family informed. Resolved; not yet closed."
        ),
        opened_days_ago=12.6,
        determination=(
            "Documentation for repatriation issued and communicated to the next of kin "
            "through the family's nominated representative."
        ),
        determined_by=RoleCode.CONSULAR_OFFICER,
        evidence=(("Certified documentation set (synthetic)", EvidenceType.DOCUMENT, True),),
    ),
    CaseSpec(
        slug="case-passport-renewal-adelaide",
        case_type_code="PASSPORT_RENEWAL",
        status=CaseStatus.NEW,
        priority=Priority.NORMAL,
        subject_name="Ifeanyi Mbakwe",
        country="AU",
        channel="ONLINE_PORTAL",
        summary="Renewal application received through the contactless system. Not yet assessed.",
        opened_days_ago=0.4,
        assigned=None,
    ),
    CaseSpec(
        slug="case-passport-renewal-darwin",
        case_type_code="PASSPORT_RENEWAL",
        status=CaseStatus.ASSIGNED,
        priority=Priority.HIGH,
        subject_name="Zainab Aliyu-Musa",
        country="AU",
        channel="COUNTER",
        summary=(
            "Renewal delayed pending confirmation of a name change against the previous "
            "travel document. SLA BREACHED; the applicant has been kept informed."
        ),
        opened_days_ago=24.3,
        evidence=(
            ("Name change instrument (synthetic)", EvidenceType.DOCUMENT, False),
            ("Previous passport biodata page (synthetic)", EvidenceType.IDENTITY_PROOF, True),
        ),
    ),
    CaseSpec(
        slug="case-notarial-poa-canberra",
        case_type_code="NOTARIAL_ATTESTATION",
        status=CaseStatus.RESOLVED,
        priority=Priority.NORMAL,
        subject_name="Adaobi Eze-Nwankwo",
        country="AU",
        channel="COUNTER",
        summary=(
            "Power of attorney executed and attested for use in Nigeria. Attestation "
            "carries legal effect, so the officer's determination is recorded."
        ),
        opened_days_ago=20.2,
        determination="Power of attorney attested; execution witnessed and recorded.",
        determined_by=RoleCode.CONSULAR_OFFICER,
        evidence=(("Executed power of attorney (synthetic)", EvidenceType.FORM, True),),
    ),
    CaseSpec(
        slug="case-welfare-check-cairns",
        case_type_code="CITIZEN_WELFARE_CHECK",
        status=CaseStatus.CLOSED,
        priority=Priority.NORMAL,
        subject_name="Segun Adeoye",
        country="AU",
        channel="TELEPHONE",
        summary=(
            "Welfare enquiry from a family member. Contact established; the subject "
            "declined to have their contact details passed on, which the mission recorded "
            "and respected."
        ),
        opened_days_ago=30.7,
        determination=(
            "Wellbeing established by direct contact. Subject withheld consent to "
            "disclose their whereabouts to the enquirer."
        ),
        determined_by=RoleCode.CONSULAR_OFFICER,
        close_reason=(
            "Resolved: contact established and the enquirer informed to the extent the "
            "subject consented to."
        ),
        closed_by=RoleCode.CONSULAR_OFFICER,
    ),
    CaseSpec(
        slug=HERO_URGENT_CASE,
        case_type_code="EMERGENCY_TRAVEL_DOCUMENT",
        status=CaseStatus.NEW,
        # NORMAL is the intake default: nobody has confirmed a priority yet. The metadata-only
        # triage recommends URGENT, and the officer's triage is what records it.
        priority=Priority.NORMAL,
        subject_name="Blessing Uchendu",
        country="AU",
        channel="TELEPHONE",
        summary=(
            "Caller reports a lost passport and onward travel booked within days. Identity "
            "satisfaction on reduced evidence will be needed before any document can issue. "
            "Not yet triaged: the case type and priority await a consular officer."
        ),
        opened_days_ago=1.8,
        assigned=None,
        evidence=(
            ("Police loss report (synthetic)", EvidenceType.DOCUMENT, True),
            (
                "Identity evidence supplied by the caller (synthetic)",
                EvidenceType.IDENTITY_PROOF,
                False,
            ),
        ),
        remaining_business_days=0.45,
    ),
    CaseSpec(
        slug="case-birth-registration-canberra",
        case_type_code="BIRTH_REGISTRATION",
        status=CaseStatus.AWAITING_CITIZEN,
        priority=Priority.LOW,
        subject_name="Infant of T. and G. Oyelaran",
        country="AU",
        channel="ONLINE_PORTAL",
        summary=(
            "Registration paused pending a certified translation of one parental "
            "document. SLA clock paused."
        ),
        opened_days_ago=11.8,
    ),
    CaseSpec(
        slug="case-visa-enquiry-student-cohort",
        case_type_code="VISA_ENQUIRY_REFERRAL",
        status=CaseStatus.TRIAGED,
        priority=Priority.NORMAL,
        subject_name="Nkechi Obiora",
        country="AU",
        channel="EMAIL",
        summary=(
            "Enquiry from a student about post-study work arrangements, referred to the "
            "published Australian guidance. No determination required."
        ),
        opened_days_ago=2.1,
        assigned=None,
    ),
)


def _opened_at(ctx: SeedContext, spec: CaseSpec, budget_days: int) -> datetime:
    """When the case arrived, on business time: by age, or by what remains of the budget."""
    if spec.remaining_business_days is not None:
        return subtract_business_days(ctx.now, budget_days - spec.remaining_business_days)
    return subtract_business_days(ctx.now, spec.opened_days_ago)


def _transition_times(opened_at: datetime, spec: CaseSpec) -> list[tuple[CaseStatus, datetime]]:
    """Each status on the case's path, spread evenly along business time since intake."""
    path = _PATH[spec.status]
    span = max(spec.opened_days_ago * 0.85, 0.05)
    step = span / (len(path) + 1)
    return [
        (status, add_business_days(opened_at, step * position))
        for position, status in enumerate(path, start=1)
    ]


def seed_cases(ctx: SeedContext, users: dict[RoleCode, User]) -> dict[str, Case]:
    """Load the cases, their timelines and their evidence."""
    cases: dict[str, Case] = {}
    for index, spec in enumerate(CASE_SPECS, start=1):
        case_id = ctx.register("case", spec.slug)
        taxonomy = case_type(spec.case_type_code)
        budget = int(taxonomy["default_sla_days"])
        opened_at = _opened_at(ctx, spec, budget)
        times = _transition_times(opened_at, spec)
        at = dict(times)

        transitions: list[tuple[datetime, CaseStatus | None, CaseStatus | None]] = [
            (opened_at, None, CaseStatus.NEW)
        ]
        previous = CaseStatus.NEW
        for status, moment in times:
            transitions.append((moment, previous, status))
            previous = status
        stopped_at = at.get(CaseStatus.RESOLVED) or at.get(CaseStatus.CLOSED)
        clock = compute_sla(
            clock_started_at=opened_at,
            budget_business_days=budget,
            pauses=pauses_from_transitions(transitions),
            now=ctx.now,
            stopped_at=stopped_at,
        )

        fields: dict[str, object] = {
            "case_type_code": spec.case_type_code,
            "status": spec.status,
            "priority": spec.priority,
            "subject_name": spec.subject_name,
            "subject_reference": f"DEMO-SUBJ-{index:03d}",
            "country": spec.country,
            "channel": spec.channel,
            "summary": spec.summary,
            "opened_at": opened_at,
            "sla_due_at": clock.due_at,
            "closed_at": at.get(CaseStatus.CLOSED) if spec.close_reason is not None else None,
            "closed_by_user_id": users[spec.closed_by].id if spec.closed_by else None,
            "close_reason": spec.close_reason,
            "assigned_user_id": users[spec.assigned].id if spec.assigned else None,
            "requires_human_determination": bool(taxonomy["requires_human_determination"]),
            "determination": spec.determination,
            "determined_by_user_id": (users[spec.determined_by].id if spec.determined_by else None),
            "determined_at": (
                at.get(CaseStatus.RESOLVED) if spec.determination is not None else None
            ),
            "classification": Classification(str(taxonomy["classification"])),
        }
        # public_ref is minted once and preserved. Re-minting on every run would break
        # every reference written down at a rehearsal, and the value is CSPRNG-derived
        # rather than seeded precisely so it carries no inference channel (ADR-0007).
        existing = ctx.session.get(Case, case_id)
        if existing is None:
            fields["public_ref"] = new_public_ref()
        case = ctx.upsert(Case, case_id, **fields)
        ctx.session.flush()
        ctx.note("case_public_ref", spec.slug, case.public_ref)
        cases[spec.slug] = case

        _seed_case_timeline(ctx, users, spec, case, opened_at, times)
        _seed_case_evidence(ctx, users, spec, case, opened_at)

    ctx.session.flush()
    return cases


def _seed_case_timeline(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    spec: CaseSpec,
    case: Case,
    opened_at: datetime,
    times: list[tuple[CaseStatus, datetime]],
) -> None:
    """Write the case's timeline, oldest first, consistent with its current status."""
    officer = users[RoleCode.CONSULAR_OFFICER]
    ctx.insert_once(
        CaseEvent,
        ctx.register("case_event", f"{spec.slug}-created"),
        case_id=case.id,
        occurred_at=opened_at,
        event_type=CaseEventType.CREATED,
        from_status=None,
        to_status=CaseStatus.NEW,
        actor_user_id=None,
        is_system=True,
        note=(
            f"Case created from the {spec.channel.lower().replace('_', ' ')} channel. "
            "A citizen-facing reference was minted at intake."
        ),
        request_id=None,
        trace_id=None,
        classification=Classification.CONSULAR_SENSITIVE,
    )

    previous = CaseStatus.NEW
    for status, moment in times:
        ctx.insert_once(
            CaseEvent,
            ctx.register("case_event", f"{spec.slug}-{status.value.lower()}"),
            case_id=case.id,
            occurred_at=moment,
            event_type=_EVENT_TYPE[status],
            from_status=previous,
            to_status=status,
            actor_user_id=officer.id,
            is_system=False,
            note=_EVENT_NOTE[status],
            request_id=None,
            trace_id=None,
            classification=Classification.CONSULAR_SENSITIVE,
        )
        previous = status

    # A breached case gets the SLA entry a real system would have written.
    if spec.slug in {"case-etd-stranded-traveller", "case-passport-renewal-darwin"}:
        ctx.insert_once(
            CaseEvent,
            ctx.register("case_event", f"{spec.slug}-sla-breach"),
            case_id=case.id,
            occurred_at=add_business_days(opened_at, spec.opened_days_ago * 0.95),
            event_type=CaseEventType.SLA_BREACH,
            from_status=None,
            to_status=None,
            actor_user_id=None,
            is_system=True,
            note=(
                "Service level budget exceeded for this case type. Recorded, not silently absorbed."
            ),
            request_id=None,
            trace_id=None,
            classification=Classification.CONSULAR_SENSITIVE,
        )


def _seed_case_evidence(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    spec: CaseSpec,
    case: Case,
    opened_at: datetime,
) -> None:
    """Attach the case's evidence, and write the placeholder object each row points at."""
    officer = users[RoleCode.CONSULAR_OFFICER]
    for index, (label, evidence_type, is_verified) in enumerate(spec.evidence):
        evidence_id = ctx.register("case_evidence", f"{spec.slug}-ev{index}")
        stored = store_text(
            "consular",
            ulid_str(evidence_id),
            f"{spec.slug}-ev{index}.txt",
            (
                "NADDP DEMO / SYNTHETIC CONSULAR EVIDENCE PLACEHOLDER\n"
                "====================================================\n"
                "This file exists so that case_evidence.object_uri resolves. It contains no "
                "personal information of any kind, because there is none to contain: the "
                "case, the subject and this artefact are all invented.\n\n"
                f"label        : {label}\n"
                f"evidence_type: {evidence_type.value}\n"
                f"case slug    : {spec.slug}\n"
            ),
        )
        assert_resolves(stored.object_uri)
        ctx.upsert(
            CaseEvidence,
            evidence_id,
            case_id=case.id,
            document_id=None,
            label=label,
            evidence_type=evidence_type,
            object_uri=stored.object_uri,
            received_at=add_business_days(
                opened_at, min(0.05 + 0.1 * index, spec.opened_days_ago * 0.3)
            ),
            verified_by_user_id=officer.id if is_verified else None,
            verified_at=(
                add_business_days(opened_at, min(0.1 + 0.1 * index, spec.opened_days_ago * 0.5))
                if is_verified
                else None
            ),
            notes=(
                "Synthetic placeholder attached to a synthetic case. Not a real document "
                "and not derived from one."
            ),
            classification=Classification.CONSULAR_SENSITIVE,
        )
