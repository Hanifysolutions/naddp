import type { PolicyResult } from '@naddp/contracts';

/**
 * Plain-language labels for the audit log, shared by the Governance page and the command centre.
 *
 * `audit_events.action` is a closed vocabulary of dotted tokens (`apps/api/app/audit/actions.py`):
 * exactly right for querying, and unreadable to a Head of Mission. Every token gets two phrasings
 * here, because the same action reads differently when the policy refused it - a refused send is
 * "Follow-up send refused", not "Follow-up sent" beside a Denied badge.
 *
 * `apps/api/tests/test_audit_labels.py` reads this file and fails if an action in the closed
 * vocabulary has no label, or a label names an action that does not exist. A token nobody labelled
 * still renders, humanised, rather than disappearing.
 */

interface ActionLabel {
  /** What happened, when the policy allowed it. */
  readonly allowed: string;
  /** What was attempted, when the policy refused it. */
  readonly denied: string;
}

export const AUDIT_ACTION_LABELS: Readonly<Record<string, ActionLabel>> = {
  'session.role_assumed': { allowed: 'Role assumed', denied: 'Role assumption refused' },
  'access.denied': { allowed: 'Access refused', denied: 'Access refused' },
  'access.privileged_read': {
    allowed: 'Sensitive record read',
    denied: 'Sensitive record read refused',
  },
  'export.performed': { allowed: 'Bulk export', denied: 'Bulk export refused' },
  'opportunity.detected': {
    allowed: 'Opportunity detected',
    denied: 'Opportunity detection refused',
  },
  'opportunity.qualified': {
    allowed: 'Opportunity qualified',
    denied: 'Opportunity qualification refused',
  },
  'opportunity.contact_planned': {
    allowed: 'Contact planned',
    denied: 'Contact planning refused',
  },
  'opportunity.contacted': {
    allowed: 'Counterpart contacted',
    denied: 'Contact step refused',
  },
  'opportunity.meeting_scheduled': {
    allowed: 'Meeting scheduled',
    denied: 'Meeting step refused',
  },
  'opportunity.negotiation_opened': {
    allowed: 'Negotiation opened',
    denied: 'Negotiation step refused',
  },
  'opportunity.partnered': {
    allowed: 'Partnership concluded',
    denied: 'Partnership commitment refused',
  },
  'opportunity.closed': {
    allowed: 'Opportunity closed',
    denied: 'Opportunity closure refused',
  },
  'opportunity.reverted': {
    allowed: 'Opportunity stage reverted',
    denied: 'Stage reversal refused',
  },
  'opportunity.transition_rejected': {
    allowed: 'Opportunity change refused',
    denied: 'Opportunity change refused',
  },
  'meeting_followup.drafted': {
    allowed: 'Follow-up drafted',
    denied: 'Follow-up draft refused',
  },
  'meeting_followup.edited': {
    allowed: 'Follow-up edited',
    denied: 'Follow-up edit refused',
  },
  'meeting_followup.submitted': {
    allowed: 'Follow-up submitted for approval',
    denied: 'Follow-up submission refused',
  },
  'meeting_followup.approved': {
    allowed: 'Follow-up approved',
    denied: 'Follow-up approval refused',
  },
  'meeting_followup.changes_requested': {
    allowed: 'Follow-up changes requested',
    denied: 'Change request refused',
  },
  'meeting_followup.sent': {
    allowed: 'Follow-up sent',
    denied: 'Follow-up send refused',
  },
  'meeting_followup.approval_revoked': {
    allowed: 'Follow-up approval revoked',
    denied: 'Approval revocation refused',
  },
  'meeting_followup.discarded': {
    allowed: 'Follow-up discarded',
    denied: 'Follow-up discard refused',
  },
  'meeting_followup.transition_rejected': {
    allowed: 'Follow-up change refused',
    denied: 'Follow-up change refused',
  },
  'case.created': { allowed: 'Case opened', denied: 'Case opening refused' },
  'case.triaged': { allowed: 'Case triaged', denied: 'Case triage refused' },
  'case.assigned': { allowed: 'Case assigned', denied: 'Case assignment refused' },
  'case.reassigned': { allowed: 'Case reassigned', denied: 'Case reassignment refused' },
  'case.information_requested': {
    allowed: 'Information requested from the citizen',
    denied: 'Information request refused',
  },
  'case.review_started': {
    allowed: 'Case review started',
    denied: 'Case review refused',
  },
  'case.escalated': { allowed: 'Case escalated', denied: 'Case escalation refused' },
  'case.information_received': {
    allowed: 'Information received from the citizen',
    denied: 'Information receipt refused',
  },
  'case.de_escalated': {
    allowed: 'Case returned from escalation',
    denied: 'Return from escalation refused',
  },
  'case.resolved': { allowed: 'Case resolved', denied: 'Case resolution refused' },
  'case.reopened': { allowed: 'Case reopened', denied: 'Case reopening refused' },
  'case.closed': { allowed: 'Case closed', denied: 'Case closure refused' },
  'case.transition_rejected': {
    allowed: 'Case change refused',
    denied: 'Case change refused',
  },
};

/** The action filter's groups, in the order a reader thinks about the mission. */
export const AUDIT_ACTION_GROUPS: readonly {
  readonly label: string;
  readonly prefixes: readonly string[];
}[] = [
  { label: 'Access and sessions', prefixes: ['session.', 'access.', 'export.'] },
  { label: 'Opportunities', prefixes: ['opportunity.'] },
  { label: 'Meeting follow-ups', prefixes: ['meeting_followup.'] },
  { label: 'Consular cases', prefixes: ['case.'] },
];

/** The actions in one filter group, in label-map order. */
export function actionsInGroup(prefixes: readonly string[]): readonly string[] {
  return Object.keys(AUDIT_ACTION_LABELS).filter((action) =>
    prefixes.some((prefix) => action.startsWith(prefix)),
  );
}

/** Bounded-context-qualified object types, as a reader names them. */
const AUDIT_OBJECT_LABELS: Readonly<Record<string, string>> = {
  'ai.trace': 'AI trace',
  'consular.case': 'Consular case',
  'diaspora.profile': 'Diaspora profile',
  'governance.access_attempt': 'Access attempt',
  'governance.audit_event': 'Audit log',
  'governance.export': 'Export',
  'governance.user': 'Staff identity',
  'intelligence.brief': 'Morning brief',
  'intelligence.signal': 'Signal',
  'knowledge.article': 'Knowledge article',
  'meetings.followup': 'Meeting follow-up',
  'meetings.meeting': 'Meeting',
  'opportunities.opportunity': 'Opportunity',
  'stakeholders.organisation': 'Organisation',
  'stakeholders.stakeholder': 'Stakeholder',
};

export const POLICY_RESULT_LABELS: Readonly<Record<PolicyResult, string>> = {
  ALLOW: 'Allowed',
  DENY: 'Denied',
};

function capitalise(value: string): string {
  return value.length === 0 ? value : `${value[0]?.toUpperCase() ?? ''}${value.slice(1)}`;
}

/** `opportunity.contact_planned` to "Opportunity: contact planned", for a token nobody labelled. */
function humaniseToken(token: string): string {
  const [context = '', ...rest] = token.split('.');
  const verb = rest.join(' ').replace(/_/g, ' ');
  const head = capitalise(context.replace(/_/g, ' '));
  return verb === '' ? head : `${head}: ${verb}`;
}

/** The action in plain language, phrased for whether the policy allowed or refused it. */
export function auditActionLabel(action: string, result: PolicyResult): string {
  const known = AUDIT_ACTION_LABELS[action];
  if (known === undefined) return humaniseToken(action);
  return result === 'DENY' ? known.denied : known.allowed;
}

/** The object an event is about, in plain language. */
export function auditObjectLabel(objectType: string): string {
  return AUDIT_OBJECT_LABELS[objectType] ?? humaniseToken(objectType);
}
