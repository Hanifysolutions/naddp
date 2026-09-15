"""Ten meetings, their follow-ups and attendees, and the action items that come out of them.

**Winning moment #2 lives in this file.** ``mtg-covalent-lithium-bilateral`` carries a
Gateway-drafted follow-up sitting at ``DRAFTED``: the text exists, the trace behind it
exists, and it has not been sent and cannot be. ``SENT`` is reachable from ``APPROVED``
and from nowhere else (``docs/workflows.md`` section 2), and the approver may not be the
drafter -- so the trade officer who owns the meeting cannot approve their own draft
however senior the demo makes them feel.

**The hero follow-up is the snapshot the trace says was served.** Its subject, recipients
and body are read verbatim from the ``result`` of the deterministic snapshot named by its
trace's purpose and scenario (``ai_snapshots/meeting_followup_covalent-lithium-bilateral
.json``), and its ``pre_read_result`` is the ``result`` of the matching ``meeting_prep``
snapshot, validated against ``MeetingPrepResult`` before it is written. A follow-up whose
text differed from what its own trace drawer says the Gateway returned would be a
fabricated provenance, which is the one thing the trace drawer exists to rule out.

The other nine meetings exist so that the blocked one is visibly *not* a special case: one
is at ``OFFICER_REVIEW`` waiting on the Deputy, one is ``APPROVED`` but still unsent, two
have gone out, one was discarded, and three have no follow-up at all. Follow-ups are rows
of ``meeting_followups``, and that table's constraints are satisfied rather than worked
around: a sent follow-up names an approver who is not its drafter, a discard carries a
reason, and recipients are labels, never addresses.

**Follow-ups are insert-once.** ``meeting_followups`` refuses ``DELETE`` and refuses any
``UPDATE`` of a sent or discarded row, so the upsert every other table here uses would
raise on a second run. A meeting that already has any follow-up -- from an earlier run, or
copied in by the W3.2 migration on a database that was not reset -- is skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import select

from app.ai.schemas import MeetingFollowupResult, MeetingPrepResult
from app.core.config import snapshot_path
from app.domain.enums import (
    ActionStatus,
    AiPurpose,
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
from app.models.meetings import Action, Meeting, MeetingAttendee, MeetingFollowup
from app.models.opportunities import Opportunity
from app.models.stakeholders import Organisation, Stakeholder
from seed_parts.context import SeedContext

__all__ = ["ACTION_SPECS", "HERO_MEETING", "MEETING_SPECS", "seed_actions", "seed_meetings"]

#: The meeting the demo narrative walks to. Its follow-up is the one that blocks.
HERO_MEETING: Final[str] = "mtg-covalent-lithium-bilateral"

_MISSION_ROOM: Final[str] = "Nigeria High Commission, Canberra - Meeting Room 2"

#: Recipient label for a follow-up on a meeting with no counterpart organisation.
_NO_ORGANISATION_RECIPIENT: Final[str] = "Roundtable participants"

#: The follow-up timeline, in hours after the meeting starts. Ordering matters more than
#: the exact hours: an approval timestamped before its submission would be a visible
#: nonsense in the audit timeline.
_DRAFTED_AFTER: Final[timedelta] = timedelta(hours=5)
_SUBMITTED_AFTER: Final[timedelta] = timedelta(hours=6)
_DISCARDED_AFTER: Final[timedelta] = timedelta(hours=7)
_APPROVED_AFTER: Final[timedelta] = timedelta(hours=8)
_SENT_AFTER: Final[timedelta] = timedelta(hours=9)


@dataclass(frozen=True, slots=True)
class MeetingSpec:
    """One meeting, with whatever follow-up state it has reached.

    A follow-up's content comes from exactly one place. With ``followup_trace`` set it is
    the snapshot that trace served (``followup_subject`` and ``followup_body`` must then be
    empty); otherwise it is ``followup_subject`` and ``followup_body`` as written here.
    ``followup_recipients`` empty means "the counterpart organisation's name", or
    ``Roundtable participants`` for a meeting with no organisation.
    """

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
    pre_read_snapshot: str | None = None
    followup_status: FollowupStatus | None = None
    followup_subject: str | None = None
    followup_recipients: tuple[str, ...] = field(default_factory=tuple)
    followup_body: str | None = None
    followup_trace: str | None = None
    followup_discard_reason: str | None = None
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
        pre_read_snapshot="covalent-lithium-bilateral",
        # Content is the snapshot this trace served; see the module docstring.
        followup_status=FollowupStatus.DRAFTED,
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
        followup_subject="Follow-up from the site visit - trainer-of-trainers scoping",
        followup_body=(
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
        followup_subject="Roundtable readout and proposed next step",
        followup_body=(
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
        followup_subject="Confirmed - curriculum collaboration scope",
        followup_body=(
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
        followup_subject="Thank you - resources workforce briefing",
        followup_body=(
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
        # The draft is retained rather than deleted, because discarding a drafted diplomatic
        # communication is itself a consequential act (OPEN_QUESTIONS Q-05, resolved).
        followup_status=FollowupStatus.DISCARDED,
        followup_subject="Follow-up from the technical exchange - implementation options",
        followup_body=(
            "Thank you for the technical exchange. We will set out the mission's "
            "understanding of the implementation options discussed in a written note, and "
            "will propose a date for the next session once that note has been cleared."
        ),
        followup_discard_reason="Superseded by a written note cleared through the Deputy.",
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
        followup_subject="Roundtable notes and a consent question",
        followup_body=(
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
    """Load meetings, their attendee rows, and their follow-ups."""
    meetings: dict[str, Meeting] = {}
    for spec in MEETING_SPECS:
        start = ctx.days_ahead(spec.days_offset, hour=10)
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
            pre_read_result=_pre_read_result(spec, traces),
            pre_read_trace_id=traces[spec.pre_read_trace].id if spec.pre_read_trace else None,
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

    for spec in MEETING_SPECS:
        if spec.followup_status is not None:
            _seed_followup(ctx, spec, meetings[spec.slug], users, organisations, traces)
    ctx.session.flush()
    return meetings


def _snapshot_result(purpose: AiPurpose, scenario: str) -> dict[str, Any]:
    """Read the ``result`` object of a deterministic snapshot, as ``traces.py`` reads evidence.

    Raises:
        ValueError: if the snapshot carries no ``result`` object. A seeded AI artefact
            with no snapshot behind it would be a provenance the trace cannot back up.
    """
    path = snapshot_path(purpose.value.lower(), scenario)
    document = json.loads(path.read_text(encoding="utf-8"))
    result = document.get("result")
    if not isinstance(result, dict):
        msg = f"snapshot {path.name} has no 'result' object to seed from."
        raise ValueError(msg)
    return result


def _pre_read_result(spec: MeetingSpec, traces: dict[str, AiTrace]) -> dict[str, Any] | None:
    """The structured pre-read for ``spec``, validated against ``MeetingPrepResult``.

    Stored as the snapshot's ``result`` exactly as the file holds it, once it has validated:
    the column is documented as a validated ``MeetingPrepResult``, and a blob that did not
    validate would be a pre-read the Gateway itself would have refused to return.

    Raises:
        ValueError: if the snapshot is named without a pre-read trace, or names a scenario
            other than the one its trace records -- the pre-read and its trace drawer would
            then describe two different generations.
    """
    if spec.pre_read_snapshot is None:
        return None
    if spec.pre_read_trace is None:
        msg = f"{spec.slug}: pre_read_snapshot is set but pre_read_trace is not."
        raise ValueError(msg)
    trace = traces[spec.pre_read_trace]
    if trace.purpose is not AiPurpose.MEETING_PREP or trace.scenario != spec.pre_read_snapshot:
        msg = (
            f"{spec.slug}: pre_read_snapshot {spec.pre_read_snapshot!r} does not match trace "
            f"{spec.pre_read_trace!r} ({trace.purpose.value}, {trace.scenario!r})."
        )
        raise ValueError(msg)
    result = _snapshot_result(AiPurpose.MEETING_PREP, spec.pre_read_snapshot)
    MeetingPrepResult.model_validate(result)
    return result


@dataclass(frozen=True, slots=True)
class _FollowupContent:
    """What a follow-up says and to whom: resolved once, from exactly one source."""

    subject: str
    recipients: list[str]
    body: str


def _followup_content(
    spec: MeetingSpec,
    organisations: dict[str, Organisation],
    traces: dict[str, AiTrace],
) -> _FollowupContent:
    """Resolve the follow-up's content from its trace's snapshot, or from the spec.

    Raises:
        ValueError: if both sources are given, if neither is, or if the trace is not a
            ``MEETING_FOLLOWUP`` trace. Silently preferring one of two sources would let the
            stored text drift from what the trace says was served.
    """
    if spec.followup_trace is not None:
        if spec.followup_subject or spec.followup_body or spec.followup_recipients:
            msg = f"{spec.slug}: an AI-drafted follow-up takes its content from its trace only."
            raise ValueError(msg)
        trace = traces[spec.followup_trace]
        if trace.purpose is not AiPurpose.MEETING_FOLLOWUP or not trace.scenario:
            msg = f"{spec.slug}: {spec.followup_trace!r} is not a MEETING_FOLLOWUP trace."
            raise ValueError(msg)
        served = MeetingFollowupResult.model_validate(
            _snapshot_result(AiPurpose.MEETING_FOLLOWUP, trace.scenario)
        )
        return _FollowupContent(served.subject, list(served.recipients), served.body)

    if not spec.followup_subject or not spec.followup_body:
        msg = f"{spec.slug}: a hand-written follow-up needs followup_subject and followup_body."
        raise ValueError(msg)
    if spec.followup_recipients:
        recipients = list(spec.followup_recipients)
    elif spec.organisation_slug is not None:
        recipients = [organisations[spec.organisation_slug].name]
    else:
        recipients = [_NO_ORGANISATION_RECIPIENT]
    return _FollowupContent(spec.followup_subject, recipients, spec.followup_body)


def _seed_followup(
    ctx: SeedContext,
    spec: MeetingSpec,
    meeting: Meeting,
    users: dict[RoleCode, User],
    organisations: dict[str, Organisation],
    traces: dict[str, AiTrace],
) -> MeetingFollowup | None:
    """Insert the follow-up ``spec`` describes, once. Returns ``None`` when it was skipped.

    Skipped whenever the meeting already has any follow-up. ``meeting_followups`` refuses
    delete and refuses updates to terminal rows, so the seed never tries to reconcile an
    existing row: on a database that was not reset, the rows already there are the record.
    The manifest then names the id actually present, not one the seed would have minted.

    Raises:
        ValueError: if the spec breaks a rule the table would enforce anyway -- an approver
            missing from, or identical to the drafter of, an approved or sent follow-up; a
            discard without a reason. Caught here with the slug in the message rather than
            as an anonymous CHECK violation at flush.
    """
    status = spec.followup_status
    if status is None:
        return None
    slug = f"{spec.slug}--followup"
    identifier = ctx.register("meeting_followup", slug)
    existing = list(
        ctx.session.scalars(
            select(MeetingFollowup.id)
            .where(MeetingFollowup.meeting_id == meeting.id)
            .order_by(MeetingFollowup.drafted_at, MeetingFollowup.id)
        )
    )
    if existing:
        if identifier not in existing:
            ctx.note("meeting_followup", slug, str(existing[-1]))
        return None

    approved = status in {FollowupStatus.APPROVED, FollowupStatus.SENT}
    if approved and spec.approver is None:
        msg = f"{spec.slug}: a {status.value} follow-up must name its approver."
        raise ValueError(msg)
    if spec.approver is not None and spec.approver is spec.owner:
        msg = f"{spec.slug}: the approver may not be the drafter (separation of duties)."
        raise ValueError(msg)
    discarded = status is FollowupStatus.DISCARDED
    if discarded != bool(spec.followup_discard_reason):
        msg = f"{spec.slug}: a discard reason is required on, and only on, a DISCARDED follow-up."
        raise ValueError(msg)

    content = _followup_content(spec, organisations, traces)
    start = meeting.scheduled_start
    owner_id = users[spec.owner].id
    submitted = status in {
        FollowupStatus.OFFICER_REVIEW,
        FollowupStatus.APPROVED,
        FollowupStatus.SENT,
    }
    sent = status is FollowupStatus.SENT
    approver_id = users[spec.approver].id if approved and spec.approver is not None else None
    return ctx.insert_once(
        MeetingFollowup,
        identifier,
        meeting_id=meeting.id,
        status=status,
        subject=content.subject,
        recipients=content.recipients,
        body=content.body,
        trace_id=traces[spec.followup_trace].id if spec.followup_trace else None,
        supersedes_followup_id=None,
        drafted_by_user_id=owner_id,
        drafted_at=start + _DRAFTED_AFTER,
        submitted_by_user_id=owner_id if submitted else None,
        submitted_at=start + _SUBMITTED_AFTER if submitted else None,
        approved_by_user_id=approver_id,
        approved_at=start + _APPROVED_AFTER if approved else None,
        sent_by_user_id=owner_id if sent else None,
        sent_at=start + _SENT_AFTER if sent else None,
        discarded_by_user_id=owner_id if discarded else None,
        discarded_at=start + _DISCARDED_AFTER if discarded else None,
        discard_reason=spec.followup_discard_reason if discarded else None,
        classification=meeting.classification,
    )


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
