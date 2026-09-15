import type {
  BriefItemType,
  BriefStatus,
  CaseStatus,
  ConsentStatus,
  DataClassification,
  FollowupStatus,
  MeetingType,
  OpportunityStage,
  RelationshipStrength,
  SignalStatus,
} from '@naddp/contracts';

/**
 * Display labels for the domain enums, in the order a person reads them.
 *
 * Two properties, both structural rather than conventional:
 *
 *  - **Exhaustive.** Each map is typed `Record<Union, string>` against the union generated
 *    from the API's own enum. If the API adds a pipeline stage or a case status, this file
 *    stops compiling and names the member that has no label - rather than the UI silently
 *    dropping a bucket out of a distribution.
 *
 *  - **Ordered by declaration.** The keys are written in workflow order (the state machines
 *    in `apps/api/app/domain/enums.py`), and `orderedKeys` reads them back in that order.
 *    One source for both the labels and the sequence, so the two cannot disagree.
 *
 * These are captions. The values sent to and from the API are always the enum members.
 */

/**
 * Keys of a label map, in declaration order.
 *
 * JavaScript preserves insertion order for non-numeric string keys, so this is the order
 * written below. The cast is safe by the same construction: the map is a total `Record`
 * over the union, so its keys are exactly the union's members.
 */
function orderedKeys<K extends string>(labels: Record<K, string>): readonly K[] {
  return Object.keys(labels) as K[];
}

/** Opportunity pipeline (BUILD_BIBLE §9). PARTNERED and CLOSED are the two terminals. */
export const OPPORTUNITY_STAGE_LABELS: Record<OpportunityStage, string> = {
  DETECTED: 'Detected',
  QUALIFIED: 'Qualified',
  CONTACT_PLANNED: 'Contact planned',
  CONTACTED: 'Contacted',
  MEETING: 'Meeting',
  NEGOTIATION: 'Negotiation',
  PARTNERED: 'Partnered',
  CLOSED: 'Closed',
};

export const OPPORTUNITY_STAGE_ORDER = orderedKeys(OPPORTUNITY_STAGE_LABELS);

/** Consular case machine (docs/workflows.md §3). CLOSED is the only terminal. */
export const CASE_STATUS_LABELS: Record<CaseStatus, string> = {
  NEW: 'New',
  TRIAGED: 'Triaged',
  ASSIGNED: 'Assigned',
  AWAITING_CITIZEN: 'Awaiting citizen',
  IN_REVIEW: 'In review',
  ESCALATED: 'Escalated',
  RESOLVED: 'Resolved',
  CLOSED: 'Closed',
};

export const CASE_STATUS_ORDER = orderedKeys(CASE_STATUS_LABELS);

/** Relationship assessment, weakest first. */
export const RELATIONSHIP_STRENGTH_LABELS: Record<RelationshipStrength, string> = {
  NONE: 'None',
  WEAK: 'Weak',
  DEVELOPING: 'Developing',
  STRONG: 'Strong',
  STRATEGIC: 'Strategic',
};

export const RELATIONSHIP_STRENGTH_ORDER = orderedKeys(RELATIONSHIP_STRENGTH_LABELS);

/**
 * Diaspora consent. Consent, not clearance, is what gates a profile: a mission may hold a
 * record it is not permitted to act on, and the distinction is the point of the tile.
 */
export const CONSENT_STATUS_LABELS: Record<ConsentStatus, string> = {
  NOT_GIVEN: 'Not given',
  GIVEN_DIRECTORY_ONLY: 'Directory only',
  GIVEN_CONTACTABLE: 'Contactable',
  WITHDRAWN: 'Withdrawn',
};

export const CONSENT_STATUS_ORDER = orderedKeys(CONSENT_STATUS_LABELS);

/** Intelligence signal triage. */
export const SIGNAL_STATUS_LABELS: Record<SignalStatus, string> = {
  NEW: 'New',
  TRIAGED: 'Triaged',
  LINKED: 'Linked',
  DISMISSED: 'Dismissed',
};

export const SIGNAL_STATUS_ORDER = orderedKeys(SIGNAL_STATUS_LABELS);

/**
 * Morning-brief lifecycle, in approval order.
 *
 * DRAFT is where the demo's hero brief actually sits, and the label says "Draft" rather
 * than anything warmer for that reason: the document was written by the Gateway and no
 * named human has agreed with it yet. Renaming DRAFT to something that sounds finished
 * would quietly undo the "AI drafts, humans decide" control this screen is built to show.
 */
export const BRIEF_STATUS_LABELS: Record<BriefStatus, string> = {
  DRAFT: 'Draft',
  IN_REVIEW: 'In review',
  APPROVED: 'Approved',
  PUBLISHED: 'Published',
};

export const BRIEF_STATUS_ORDER = orderedKeys(BRIEF_STATUS_LABELS);

/**
 * What a brief item points at, in the order the bounded contexts are read.
 *
 * The label names the *record* a reader would open, not the section of the brief - CASE is
 * captioned "Consular case" because "Case" alone means nothing on a page that also carries
 * opportunities and meetings.
 */
export const BRIEF_ITEM_TYPE_LABELS: Record<BriefItemType, string> = {
  SIGNAL: 'Signal',
  OPPORTUNITY: 'Opportunity',
  CASE: 'Consular case',
  MEETING: 'Meeting',
  KNOWLEDGE: 'Knowledge',
};

export const BRIEF_ITEM_TYPE_ORDER = orderedKeys(BRIEF_ITEM_TYPE_LABELS);

/**
 * Data zones (BUILD_BIBLE §5, ADR-0006).
 *
 * Shown so a small count reads as "your view of the mission" rather than as "the mission".
 * A reader who cannot see which zones were counted cannot tell a narrow clearance from a
 * quiet week.
 */
export const CLASSIFICATION_LABELS: Record<DataClassification, string> = {
  PUBLIC: 'Public',
  MISSION_INTERNAL: 'Mission internal',
  CONFIDENTIAL: 'Confidential',
  CONSULAR_SENSITIVE: 'Consular sensitive',
};

/**
 * Meeting follow-up lifecycle (docs/workflows.md section 2), in workflow order.
 *
 * OFFICER_REVIEW is captioned for what it means to the person reading it rather than for
 * where it sits in the table: "Awaiting approval" is the state winning moment #2 puts on the
 * screen, and "Officer review" would hide the fact that a named human has to decide.
 * APPROVED says "not sent" out loud because the two are different acts - the approval is
 * recorded, and until dispatch nothing has left the mission.
 */
export const FOLLOWUP_STATUS_LABELS: Record<FollowupStatus, string> = {
  DRAFTED: 'Draft',
  OFFICER_REVIEW: 'Awaiting approval',
  APPROVED: 'Approved, not sent',
  SENT: 'Sent',
  DISCARDED: 'Discarded',
};

/** Meeting formats, sentence case. The API's enum member travels unchanged. */
export const MEETING_TYPE_LABELS: Record<MeetingType, string> = {
  BILATERAL: 'Bilateral',
  INTRODUCTORY: 'Introductory',
  SITE_VISIT: 'Site visit',
  ROUNDTABLE: 'Roundtable',
  CALL: 'Call',
};
