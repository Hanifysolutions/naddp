"""Ten meetings, their attendees, and the action items that come out of them.

**Winning moment #2 lives in this file.** ``mtg-covalent-lithium-bilateral`` carries a
Gateway-drafted follow-up sitting at ``DRAFTED``: the text exists, the trace behind it
exists, and it has not been sent and cannot be. ``SENT`` is reachable from ``APPROVED``
and from nowhere else (``docs/workflows.md`` section 2), and the approver may not be the
drafter -- so the trade officer who owns the meeting cannot approve their own draft
however senior the demo makes them feel.

The other nine meetings exist so that the blocked one is visibly *not* a special case: one
is at ``OFFICER_REVIEW`` waiting on the Deputy, one is ``APPROVED`` but still unsent, two
have gone out, one was discarded, and three have no follow-up at all. Two constraints in
the schema are load-bearing here and are satisfied rather than worked around --
``followup_sent_at`` requires a named approver, and status ``SENT`` requires a send
timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

from app.domain.enums import (
    ActionStatus,
    ApprovalStatus,
    Classification,
    FollowupStatus,
    MeetingType,
    Priority,
    RoleCode,
)
from app.models.ai import AiTrace
from app.models.consular import Case
from app.models.governance import User
from app.models.meetings import Action, Meeting, MeetingAttendee
from app.models.opportunities import Opportunity
from app.models.stakeholders import Organisation, Stakeholder
from seed_parts.context import SeedContext

__all__ = ["ACTION_SPECS", "HERO_MEETING", "MEETING_SPECS", "seed_actions", "seed_meetings"]

#: The meeting the demo narrative walks to. Its follow-up is the one that blocks.
HERO_MEETING: Final[str] = "mtg-covalent-lithium-bilateral"

_MISSION_ROOM: Final[str] = "Nigeria High Commission, Canberra - Meeting Room 2"


@dataclass(frozen=True, slots=True)
class MeetingSpec:
    """One meeting, with whatever follow-up state it has reached."""

    slug: str
    title: str
    meeting_type: MeetingType
    days_offset: float
    duration_hours: float
    agenda: str
    owner: RoleCode
    location: str | None = None
    organisation_slug: str | None = None
    opportunity_slug: str | None = None
    attendee_slugs: tuple[str, ...] = field(default_factory=tuple)
    confirmed_attendees: tuple[str, ...] = field(default_factory=tuple)
    pre_read: str | None = None
    pre_read_trace: str | None = None
    followup_status: FollowupStatus | None = None
    followup_draft: str | None = None
    followup_trace: str | None = None
    approver: RoleCode | None = None
    classification: Classification = Classification.MISSION_INTERNAL


MEETING_SPECS: Final[tuple[MeetingSpec, ...]] = (
    MeetingSpec(
        slug=HERO_MEETING,
        title="Introductory bilateral: lithium processing skills and training pathways",
        meeting_type=MeetingType.BILATERAL,
        days_offset=-4.0,
        duration_hours=1.0,
        location=_MISSION_ROOM,
        owner=RoleCode.TRADE_OFFICER,
        organisation_slug="org-covalent-lithium",
        opportunity_slug="opp-au-lithium-ng-skills-corridor",
        attendee_slugs=("stk-covalent-external-affairs", "stk-covalent-process-superintendent"),
        agenda=(
            "1. Mission introduction and purpose (5 min).\n"
            "2. The mission's read of the Australian processing-skills position, from "
            "public sources only: refinery ramp-up at Kwinana toward nameplate, the "
            "separate and separately-approved Mt Holland concentrator expansion, and the "
            "AusIMM and Engineers Australia findings on metallurgical engineering supply "
            "(20 min).\n"
            "3. Nigeria's stated policy direction on domestic value addition (10 min).\n"
            "4. Open question to the counterpart: is there any interest in a structured "
            "training or mobility conversation? No proposal is tabled (20 min).\n"
            "5. Next steps (5 min).\n\n"
            "NOTE FOR THE RECORD: this is a mission-initiated approach. The counterpart "
            "has been invited and has not confirmed, and nothing in the mission's file "
            "records any position held by the organisation named."
        ),
        pre_read=(
            "PRE-READ (AI-generated, deterministic snapshot, see trace).\n\n"
            "WHAT IS ACTUALLY HAPPENING. Covalent's Kwinana lithium hydroxide refinery "
            "produced first battery-grade product in July 2025 and, per the operator's own "
            "27 May 2026 statement, continues to progress through ramp-up toward an "
            "integrated design capacity of about 50,000 tpa. Separately, shareholders "
            "approved the Mt Holland Expansion Project in July 2026, doubling spodumene "
            "concentrate production at the MINE AND CONCENTRATOR. These are two different "
            "assets. Do not say 'the refinery is expanding' in the room: it is not, no "
            "Australian lithium refinery currently is, and being corrected on that point "
            "would cost the mission the rest of the meeting.\n\n"
            "WHY THE MISSION IS INTERESTED. AusIMM puts Australia's professional "
            "metallurgical engineering workforce at roughly 960 people and finds the "
            "graduate shortfall worsening; Engineers Australia reports persistent "
            "engineering shortages; MRIWA and the FBICRC have published a vocational "
            "skills gap assessment for the battery value chain. Migration WA carries "
            "Metallurgist on Schedule 2 of the state occupation list.\n\n"
            "WHAT TO EXPECT AS PUSHBACK. AREEA's September 2025 forecast reports new "
            "lithium investment falling to a single expansion project and 50 new jobs "
            "across 2025-2030. The honest answer is that AREEA models the operational "
            "phase of NEW projects and excludes ramp-up hiring at commissioned plant -- "
            "concede the number, keep the distinction.\n\n"
            "WHAT NOT TO DO. Do not offer, imply or accept any commitment. Nothing "
            "connecting this operator to Nigeria has been reported anywhere; the corridor "
            "is the platform's proposal and the mission's question, and it is to be put as "
            "a question."
        ),
        pre_read_trace="trace-meeting-prep-covalent",
        followup_status=FollowupStatus.DRAFTED,
        followup_draft=(
            "DRAFT - NOT SENT. Sending requires approval by an officer other than the "
            "drafter (docs/workflows.md section 2; BUILD_BIBLE section 6).\n\n"
            "Subject: Thank you for your time - lithium processing skills\n\n"
            "Dear [counterpart],\n\n"
            "Thank you for meeting the High Commission. To summarise our side accurately: "
            "we were interested in the workforce implications of the Kwinana refinery's "
            "ramp toward nameplate, and we noted separately the shareholder-approved "
            "expansion of the Mt Holland mine and concentrator. We are not suggesting the "
            "refinery is expanding.\n\n"
            "We raised one question and made no proposal: whether there is any appetite "
            "for a conversation about processing skills pipelines, including the Nigerian "
            "training institutions we work with. We would welcome your view in your own "
            "time and are content to hear that the answer is no.\n\n"
            "With thanks,\n[Trade Officer]\nNigeria High Commission, Canberra"
        ),
        followup_trace="trace-meeting-followup-covalent",
    ),
    MeetingSpec(
        slug="mtg-southmetro-acept-site-visit",
        title="Site visit: process training facility, Kwinana industrial corridor",
        meeting_type=MeetingType.SITE_VISIT,
        days_offset=-12.0,
        duration_hours=3.0,
        location="Munster, Western Australia",
        owner=RoleCode.TRADE_OFFICER,
        organisation_slug="org-southmetro-tafe",
        opportunity_slug="opp-wa-process-operator-training-pipeline",
        attendee_slugs=("stk-southmetro-acept", "stk-mriwa-programs"),
        confirmed_attendees=("stk-southmetro-acept", "stk-mriwa-programs"),
        agenda=(
            "Walkthrough of the process training plant; discussion of the vocational roles "
            "identified in the published WA battery skills gap assessment; whether a "
            "trainer-of-trainers cohort is deliverable and what it would cost."
        ),
        followup_status=FollowupStatus.OFFICER_REVIEW,
        followup_draft=(
            "SUBMITTED FOR APPROVAL - content is locked while under review.\n\n"
            "Subject: Follow-up from the site visit - trainer-of-trainers scoping\n\n"
            "Thank you for hosting the mission. As discussed, we will send through the "
            "Nigerian institutional profile and the qualification mapping we hold, so that "
            "you can tell us whether a trainer-of-trainers cohort is realistic on your "
            "current capacity. We are not asking for a commitment at this stage."
        ),
    ),
    MeetingSpec(
        slug="mtg-migration-wa-nomination-briefing",
        title="Briefing call: state nomination and metallurgist occupations",
        meeting_type=MeetingType.CALL,
        days_offset=2.0,
        duration_hours=0.75,
        owner=RoleCode.DIASPORA_OFFICER,
        organisation_slug="org-migration-wa",
        opportunity_slug="opp-wasmol-metallurgist-nomination-brief",
        attendee_slugs=("stk-migration-wa-nomination",),
        agenda=(
            "The mission's picture of Nigerian supply against the metallurgist occupations "
            "carried on the state list; the review cycle for the list; what evidence the "
            "programme would find useful."
        ),
    ),
    MeetingSpec(
        slug="mtg-curtin-conversion-pathways",
        title="Introductory meeting: graduate metallurgy conversion pathways",
        meeting_type=MeetingType.INTRODUCTORY,
        days_offset=5.0,
        duration_hours=1.0,
        location=_MISSION_ROOM,
        owner=RoleCode.TRADE_OFFICER,
        organisation_slug="org-curtin",
        opportunity_slug="opp-metallurgy-conversion-scholarships",
        attendee_slugs=("stk-curtin-wasm-head", "stk-curtin-international"),
        agenda=(
            "Entry requirements and capacity for the graduate conversion qualification; "
            "how Nigerian engineering degrees are currently assessed; whether a cohort "
            "scholarship model has precedent."
        ),
    ),
    MeetingSpec(
        slug="mtg-coren-recognition-roundtable",
        title="Roundtable: qualification recognition after the Washington Accord decision",
        meeting_type=MeetingType.ROUNDTABLE,
        days_offset=-8.0,
        duration_hours=2.0,
        location="Video conference",
        owner=RoleCode.DIASPORA_OFFICER,
        organisation_slug="org-engineers-australia",
        opportunity_slug="opp-coren-engineers-australia-recognition",
        attendee_slugs=("stk-engineers-australia-assessment", "stk-coren-registrar"),
        confirmed_attendees=("stk-engineers-australia-assessment", "stk-coren-registrar"),
        agenda=(
            "What provisional signatory status does and does not change for migration "
            "skills assessment; the practical sequence for a Nigerian graduate; what each "
            "side would need from the other to shorten it."
        ),
        followup_status=FollowupStatus.APPROVED,
        followup_draft=(
            "APPROVED, NOT YET SENT.\n\n"
            "Subject: Roundtable readout and proposed next step\n\n"
            "Thank you both. Our readout is attached. We propose a short technical session "
            "on assessment evidence requirements, with no commitment on either side beyond "
            "attending."
        ),
        approver=RoleCode.DEPUTY,
    ),
    MeetingSpec(
        slug="mtg-nimg-curriculum-signing",
        title="Curriculum partnership: agreement session",
        meeting_type=MeetingType.BILATERAL,
        days_offset=-17.0,
        duration_hours=1.5,
        location="Jos, Nigeria (video link to Canberra)",
        owner=RoleCode.TRADE_OFFICER,
        organisation_slug="org-nimg",
        opportunity_slug="opp-nimg-curriculum-partnership",
        attendee_slugs=("stk-nimg-deputy-provost", "stk-nimg-metallurgy-faculty"),
        confirmed_attendees=("stk-nimg-deputy-provost", "stk-nimg-metallurgy-faculty"),
        agenda=(
            "Confirmation of the curriculum collaboration scope, staff exchange and review points."
        ),
        followup_status=FollowupStatus.SENT,
        followup_draft=(
            "SENT.\n\nSubject: Confirmed - curriculum collaboration scope\n\n"
            "Thank you for concluding this. The agreed scope, the exchange schedule and "
            "the first review point are set out below for the record."
        ),
        approver=RoleCode.AMBASSADOR,
    ),
    MeetingSpec(
        slug="mtg-mca-workforce-briefing",
        title="Industry briefing: resources workforce and skilled migration",
        meeting_type=MeetingType.ROUNDTABLE,
        days_offset=-22.0,
        duration_hours=1.5,
        location="Video conference",
        owner=RoleCode.TRADE_OFFICER,
        organisation_slug="org-mca",
        attendee_slugs=("stk-mca-skills", "stk-ausimm-policy"),
        confirmed_attendees=("stk-mca-skills",),
        agenda=(
            "Industry read of the resources workforce outlook; where skilled migration "
            "sits against domestic training in the sector's own view."
        ),
        followup_status=FollowupStatus.SENT,
        followup_draft=(
            "SENT.\n\nSubject: Thank you - resources workforce briefing\n\n"
            "Thank you for the briefing. We have noted the industry position that "
            "migration is complementary to, not a substitute for, domestic training."
        ),
        approver=RoleCode.DEPUTY,
    ),
    MeetingSpec(
        slug="mtg-msmd-beneficiation-technical",
        title="Technical exchange: beneficiation licensing implementation",
        meeting_type=MeetingType.BILATERAL,
        days_offset=-10.0,
        duration_hours=2.0,
        location="Video conference",
        owner=RoleCode.DEPUTY,
        organisation_slug="org-msmd",
        opportunity_slug="opp-nigeria-beneficiation-technical-exchange",
        attendee_slugs=("stk-msmd-beneficiation", "stk-msmd-director-mines"),
        confirmed_attendees=("stk-msmd-director-mines",),
        agenda=(
            "CONFIDENTIAL. Substantive discussion of implementation options and the "
            "mission's position. Classified under ADR-0006 because terms are under "
            "discussion; not readable by role alone below clearance rank 30."
        ),
        followup_status=FollowupStatus.DISCARDED,
        followup_draft=(
            "DISCARDED. Superseded by a written note cleared through the Deputy; the draft "
            "is retained rather than deleted because discarding a drafted diplomatic "
            "communication is itself a consequential act (OPEN_QUESTIONS Q-05)."
        ),
        classification=Classification.CONFIDENTIAL,
    ),
    MeetingSpec(
        slug="mtg-austrade-critical-minerals-catchup",
        title="Standing catch-up: critical minerals programme",
        meeting_type=MeetingType.CALL,
        days_offset=6.0,
        duration_hours=0.5,
        owner=RoleCode.TRADE_OFFICER,
        organisation_slug="org-austrade",
        attendee_slugs=("stk-austrade-critical-minerals",),
        agenda="Standing fortnightly catch-up: programme news, upcoming publications, referrals.",
    ),
    MeetingSpec(
        slug="mtg-diaspora-capability-roundtable",
        title="Diaspora capability roundtable: processing and migration expertise",
        meeting_type=MeetingType.ROUNDTABLE,
        days_offset=-3.0,
        duration_hours=2.0,
        location=_MISSION_ROOM,
        owner=RoleCode.DIASPORA_OFFICER,
        attendee_slugs=("stk-home-affairs-occupation-lists", "stk-rmit-international"),
        confirmed_attendees=("stk-rmit-international",),
        agenda=(
            "Mapping the capability the Nigerian-Australian professional community already "
            "holds; consent and contactability; what the mission may and may not do with a "
            "directory."
        ),
        followup_status=FollowupStatus.DRAFTED,
        followup_draft=(
            "DRAFT - NOT SENT.\n\nSubject: Roundtable notes and a consent question\n\n"
            "Thank you all. Before we circulate anything further we need to be explicit "
            "about consent: we will contact only those who have recorded consent to be "
            "contacted, and we will say so in writing."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """One action item. Deliberately a different lifecycle from a follow-up."""

    slug: str
    title: str
    description: str
    status: ActionStatus
    priority: Priority
    assignee: RoleCode
    creator: RoleCode
    due_days: float | None
    meeting_slug: str | None = None
    opportunity_slug: str | None = None
    case_slug: str | None = None
    requires_approval: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    approver: RoleCode | None = None
    classification: Classification = Classification.MISSION_INTERNAL


ACTION_SPECS: Final[tuple[ActionSpec, ...]] = (
    ActionSpec(
        "act-hero-followup-approval",
        "Route the Covalent follow-up to the Deputy for approval",
        (
            "The follow-up is drafted and cannot be sent by its drafter. Submit it for "
            "review so an officer other than the drafter can approve or reject it."
        ),
        ActionStatus.OPEN,
        Priority.HIGH,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        1.0,
        meeting_slug=HERO_MEETING,
        opportunity_slug="opp-au-lithium-ng-skills-corridor",
        requires_approval=True,
        approval_status=ApprovalStatus.PENDING_APPROVAL,
    ),
    ActionSpec(
        "act-hero-qualify-opportunity",
        "Qualify or reject the AI-proposed corridor opportunity",
        (
            "The opportunity is AI-proposed and carries a score of 61 against signals "
            "scoring 88-97. An officer decides whether it is real before it advances."
        ),
        ActionStatus.IN_PROGRESS,
        Priority.HIGH,
        RoleCode.TRADE_OFFICER,
        RoleCode.DEPUTY,
        2.0,
        opportunity_slug="opp-au-lithium-ng-skills-corridor",
    ),
    ActionSpec(
        "act-hero-counter-evidence-note",
        "Write the counter-evidence note on the AREEA forecast",
        (
            "One page: what AREEA measures, what it excludes, and why the mission's read "
            "survives it. Needed before any senior conversation, not after."
        ),
        ActionStatus.DONE,
        Priority.NORMAL,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        -3.0,
        opportunity_slug="opp-au-lithium-ng-skills-corridor",
    ),
    ActionSpec(
        "act-acept-qualification-mapping",
        "Send the Nigerian qualification mapping to the training provider",
        "Map NIMG technical qualifications against the vocational roles in the WA plan.",
        ActionStatus.IN_PROGRESS,
        Priority.NORMAL,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        4.0,
        meeting_slug="mtg-southmetro-acept-site-visit",
        opportunity_slug="opp-wa-process-operator-training-pipeline",
    ),
    ActionSpec(
        "act-acept-costing-request",
        "Request an indicative cohort costing",
        "Ask what a twelve-person trainer-of-trainers cohort would cost and when it could run.",
        ActionStatus.OPEN,
        Priority.NORMAL,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        9.0,
        opportunity_slug="opp-wa-process-operator-training-pipeline",
    ),
    ActionSpec(
        "act-curtin-entry-requirements",
        "Confirm entry requirements for Nigerian engineering degrees",
        "Establish what is currently accepted, in writing, before the meeting.",
        ActionStatus.OPEN,
        Priority.HIGH,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        3.0,
        meeting_slug="mtg-curtin-conversion-pathways",
        opportunity_slug="opp-metallurgy-conversion-scholarships",
    ),
    ActionSpec(
        "act-scholarship-funding-scan",
        "Scan existing scholarship instruments for a fit",
        (
            "Australia Awards Africa and provider scholarships: what already exists "
            "before inventing one."
        ),
        ActionStatus.BLOCKED,
        Priority.NORMAL,
        RoleCode.DIASPORA_OFFICER,
        RoleCode.TRADE_OFFICER,
        -6.0,
        opportunity_slug="opp-metallurgy-conversion-scholarships",
    ),
    ActionSpec(
        "act-coren-technical-session",
        "Schedule the assessment evidence technical session",
        "Follow the approved roundtable readout with a working-level session.",
        ActionStatus.OPEN,
        Priority.NORMAL,
        RoleCode.DIASPORA_OFFICER,
        RoleCode.DIASPORA_OFFICER,
        7.0,
        meeting_slug="mtg-coren-recognition-roundtable",
        opportunity_slug="opp-coren-engineers-australia-recognition",
    ),
    ActionSpec(
        "act-nomination-evidence-pack",
        "Assemble the state nomination evidence pack",
        "Supply figures the nomination programme can actually use, sourced not asserted.",
        ActionStatus.IN_PROGRESS,
        Priority.HIGH,
        RoleCode.DIASPORA_OFFICER,
        RoleCode.DEPUTY,
        1.5,
        meeting_slug="mtg-migration-wa-nomination-briefing",
        opportunity_slug="opp-wasmol-metallurgist-nomination-brief",
    ),
    ActionSpec(
        "act-beneficiation-position-clearance",
        "Clear the beneficiation position with the Ambassador",
        (
            "CONFIDENTIAL. Written position requires head-of-mission clearance before "
            "the next session."
        ),
        ActionStatus.OPEN,
        Priority.URGENT,
        RoleCode.DEPUTY,
        RoleCode.DEPUTY,
        2.0,
        meeting_slug="mtg-msmd-beneficiation-technical",
        opportunity_slug="opp-nigeria-beneficiation-technical-exchange",
        requires_approval=True,
        approval_status=ApprovalStatus.PENDING_APPROVAL,
        classification=Classification.CONFIDENTIAL,
    ),
    ActionSpec(
        "act-nimg-exchange-schedule",
        "Publish the staff exchange schedule",
        "Agreed at signature; publish so both institutions work to one calendar.",
        ActionStatus.DONE,
        Priority.NORMAL,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        -8.0,
        meeting_slug="mtg-nimg-curriculum-signing",
        opportunity_slug="opp-nimg-curriculum-partnership",
        requires_approval=True,
        approval_status=ApprovalStatus.APPROVED,
        approver=RoleCode.AMBASSADOR,
    ),
    ActionSpec(
        "act-mca-readout-circulate",
        "Circulate the industry briefing readout",
        "One page to the Deputy and the Ambassador.",
        ActionStatus.DONE,
        Priority.LOW,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        -18.0,
        meeting_slug="mtg-mca-workforce-briefing",
    ),
    ActionSpec(
        "act-kemerton-close-note",
        "Record the reason the Kemerton attachment was closed",
        "So the next officer does not re-derive the same negative.",
        ActionStatus.DONE,
        Priority.LOW,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        -24.0,
        opportunity_slug="opp-kemerton-skills-attachment",
    ),
    ActionSpec(
        "act-agritech-appetite-test",
        "Test appetite with two Australian grains handling suppliers",
        "Before any effort is committed, establish whether anyone is interested.",
        ActionStatus.OPEN,
        Priority.LOW,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        11.0,
        opportunity_slug="opp-agritech-grains-storage-cooperation",
    ),
    ActionSpec(
        "act-remittance-cost-baseline",
        "Establish a remittance cost baseline",
        "Overdue. Without a baseline the diaspora complaint cannot be turned into a case.",
        ActionStatus.OPEN,
        Priority.NORMAL,
        RoleCode.DIASPORA_OFFICER,
        RoleCode.DIASPORA_OFFICER,
        -4.0,
        opportunity_slug="opp-fintech-remittance-corridor",
    ),
    ActionSpec(
        "act-health-workforce-framework-scan",
        "Scan existing ethical recruitment frameworks",
        "Do not draft a framework before establishing which ones already bind either party.",
        ActionStatus.IN_PROGRESS,
        Priority.NORMAL,
        RoleCode.DIASPORA_OFFICER,
        RoleCode.DEPUTY,
        6.0,
        opportunity_slug="opp-health-workforce-ethical-recruitment",
    ),
    ActionSpec(
        "act-minigrid-research-intro",
        "Make the introduction through the research grouping",
        "Commercial approach would be premature; go through the institute.",
        ActionStatus.OPEN,
        Priority.LOW,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        8.0,
        opportunity_slug="opp-renewables-minigrid-technology",
    ),
    ActionSpec(
        "act-vet-standards-comparison",
        "Complete the competency standards comparison table",
        "Slow work; the thing that makes every later education opportunity cheaper.",
        ActionStatus.IN_PROGRESS,
        Priority.LOW,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        20.0,
        opportunity_slug="opp-vet-standards-mutual-recognition",
    ),
    ActionSpec(
        "act-diaspora-consent-language",
        "Agree the consent language for the capability directory",
        "Nothing is contacted until the wording is agreed and recorded.",
        ActionStatus.OPEN,
        Priority.HIGH,
        RoleCode.DIASPORA_OFFICER,
        RoleCode.DIASPORA_OFFICER,
        5.0,
        meeting_slug="mtg-diaspora-capability-roundtable",
    ),
    ActionSpec(
        "act-superseded-outreach-draft",
        "Cancelled: draft general outreach email to the operator list",
        (
            "Cancelled. Untargeted outreach to named operators was not an approach the "
            "mission would take."
        ),
        ActionStatus.CANCELLED,
        Priority.LOW,
        RoleCode.TRADE_OFFICER,
        RoleCode.TRADE_OFFICER,
        -12.0,
    ),
)


def seed_meetings(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    organisations: dict[str, Organisation],
    stakeholders: dict[str, Stakeholder],
    opportunities: dict[str, Opportunity],
    traces: dict[str, AiTrace],
) -> dict[str, Meeting]:
    """Load meetings and their attendee rows."""
    meetings: dict[str, Meeting] = {}
    for spec in MEETING_SPECS:
        start = ctx.days_ahead(spec.days_offset, hour=10)
        status = spec.followup_status
        approver_id = users[spec.approver].id if spec.approver is not None else None

        # The follow-up timeline hangs off the meeting: submitted the same afternoon,
        # approved that evening, sent after. Ordering matters more than the exact hours --
        # an approval timestamped before its submission would be a visible nonsense in the
        # audit timeline.
        reviewed = {FollowupStatus.OFFICER_REVIEW, FollowupStatus.APPROVED, FollowupStatus.SENT}
        submitted_at = start + timedelta(hours=6) if status in reviewed else None
        approved_at = (
            start + timedelta(hours=8)
            if status in {FollowupStatus.APPROVED, FollowupStatus.SENT}
            else None
        )
        sent_at = start + timedelta(hours=9) if status is FollowupStatus.SENT else None
        discarded_at = start + timedelta(hours=7) if status is FollowupStatus.DISCARDED else None

        meetings[spec.slug] = ctx.upsert(
            Meeting,
            ctx.register("meeting", spec.slug),
            title=spec.title,
            meeting_type=spec.meeting_type,
            scheduled_start=start,
            scheduled_end=start + timedelta(hours=spec.duration_hours),
            location=spec.location,
            virtual_link=None,
            organisation_id=(
                organisations[spec.organisation_slug].id if spec.organisation_slug else None
            ),
            opportunity_id=(
                opportunities[spec.opportunity_slug].id if spec.opportunity_slug else None
            ),
            owner_user_id=users[spec.owner].id,
            agenda=spec.agenda,
            pre_read=spec.pre_read,
            pre_read_trace_id=traces[spec.pre_read_trace].id if spec.pre_read_trace else None,
            followup_status=status,
            followup_draft=spec.followup_draft,
            followup_trace_id=traces[spec.followup_trace].id if spec.followup_trace else None,
            followup_submitted_at=submitted_at,
            followup_approved_by_user_id=approver_id,
            followup_approved_at=approved_at,
            followup_sent_at=sent_at,
            followup_discarded_at=discarded_at,
            classification=spec.classification,
        )
    ctx.session.flush()

    for spec in MEETING_SPECS:
        meeting = meetings[spec.slug]
        for stakeholder_slug in spec.attendee_slugs:
            ctx.upsert(
                MeetingAttendee,
                {
                    "meeting_id": meeting.id,
                    "stakeholder_id": stakeholders[stakeholder_slug].id,
                },
                attendee_role="COUNTERPART",
                is_confirmed=stakeholder_slug in spec.confirmed_attendees,
            )
    ctx.session.flush()
    return meetings


def seed_actions(
    ctx: SeedContext,
    users: dict[RoleCode, User],
    meetings: dict[str, Meeting],
    opportunities: dict[str, Opportunity],
    cases: dict[str, Case],
) -> dict[str, Action]:
    """Load the action items."""
    actions: dict[str, Action] = {}
    for spec in ACTION_SPECS:
        approved_at = None
        approver_id = None
        if spec.approval_status is ApprovalStatus.APPROVED:
            # ck_actions_approved_requires_approver: APPROVED without a named human is
            # refused by the database, and rightly -- an approval nobody made is not one.
            approver_id = users[spec.approver or RoleCode.DEPUTY].id
            approved_at = ctx.days_ago(abs(spec.due_days or 1.0) + 1.0)
        actions[spec.slug] = ctx.upsert(
            Action,
            ctx.register("action", spec.slug),
            title=spec.title,
            description=spec.description,
            status=spec.status,
            priority=spec.priority,
            due_at=None if spec.due_days is None else ctx.days_ahead(spec.due_days, hour=17),
            completed_at=(
                ctx.days_ago(abs(spec.due_days or 1.0))
                if spec.status is ActionStatus.DONE
                else None
            ),
            assignee_user_id=users[spec.assignee].id,
            created_by_user_id=users[spec.creator].id,
            opportunity_id=(
                opportunities[spec.opportunity_slug].id if spec.opportunity_slug else None
            ),
            meeting_id=meetings[spec.meeting_slug].id if spec.meeting_slug else None,
            case_id=cases[spec.case_slug].id if spec.case_slug else None,
            requires_approval=spec.requires_approval,
            approval_status=spec.approval_status,
            approved_by_user_id=approver_id,
            approved_at=approved_at,
            classification=spec.classification,
        )
    ctx.session.flush()
    return actions
