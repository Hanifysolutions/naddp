/**
 * Navigation is generated from permissions, never hard-coded per persona
 * (CLAUDE.md §2.4). A persona is an accident of which permissions a role happens to
 * hold; encoding personas in the client would let the UI silently diverge from the
 * server's authorisation matrix, and would leak the shape of the product to roles that
 * cannot use it.
 *
 * The rule this file enforces:
 *   1. Every destination declares exactly one required permission.
 *   2. The rail renders the intersection of the catalogue and the permissions the API
 *      says this session holds.
 *   3. The client filter is cosmetic. The API re-checks on every request and is the only
 *      authority. Hiding a link is a courtesy, not a control.
 *
 * This module is deliberately free of React and of component references so a `NavItem[]`
 * can cross the server/client boundary as plain data. Icons are named here and resolved
 * to components in `@/components/layout/nav-icons`.
 */

/**
 * Permission identifiers in `verb:object` form.
 *
 * RESOLVED (OPEN_QUESTIONS Q-02): the repo uses `verb:object`, singular object, matching the
 * grant matrices in `docs/workflows.md`. The alternative `object.verb` form was rejected because
 * it cannot express the transition permissions the state machines already depend on
 * (`approve:meeting_followup`, `resolve:consular_case`) without a second, parallel scheme.
 *
 * CROSS-TRACK CONTRACT: these strings must match the permission codes in
 * `apps/api/app/security/permissions.py` exactly. VERIFIED against that file: all nine are
 * members of its `Permission` enum, spelled identically.
 *
 * They are not generated, and cannot be: the API returns a session's permissions as a
 * `list[str]`, so no enum for them reaches the OpenAPI document. A typo here is therefore
 * not a compile error - it is a link that never appears for anybody, which is the failure
 * mode this comment exists to make findable.
 */
export const PERMISSIONS = [
  'read:command',
  'read:intelligence',
  'read:opportunity',
  'read:stakeholder',
  'read:meeting',
  'read:consular_case',
  'read:diaspora_profile',
  'read:knowledge_article',
  'read:audit',
] as const;

export type Permission = (typeof PERMISSIONS)[number];

/** Icon keys, resolved to Lucide components by the client-side icon registry. */
export type NavIconName =
  | 'command'
  | 'intelligence'
  | 'opportunities'
  | 'stakeholders'
  | 'meetings'
  | 'consular'
  | 'diaspora'
  | 'knowledge'
  | 'governance';

/**
 * Whether the destination exists in this build.
 *
 * `planned` entries are rendered - dimmed, not focusable as links, and labelled with the
 * week that builds them - rather than hidden or linked. Hiding them would misrepresent the
 * product's shape; linking them would put a 404 one click from the command centre, and
 * "the demo must never dead-end" (BUILD_BIBLE §0) is not a suggestion. The permission
 * filter still applies first: a role that may not read consular cases never sees the
 * consular entry at all, planned or not.
 */
export type NavAvailability = 'available' | 'planned';

export interface NavItem {
  /** Route this entry links to. */
  readonly href: string;
  /** Visible label. Also the accessible name of the link. */
  readonly label: string;
  /** Short description, surfaced as a tooltip and as the link's accessible description. */
  readonly description: string;
  /** Icon key. Decorative: always paired with the visible label, never the only cue. */
  readonly icon: NavIconName;
  /** The single permission required to see this entry. */
  readonly permission: Permission;
  /** Whether this build serves the route. */
  readonly availability: NavAvailability;
  /** When a planned destination lands. Rendered verbatim; omitted when available. */
  readonly plannedFor?: string;
}

/**
 * Every destination in the staff application, in reading order. Adding a screen means
 * adding a row here and a permission on the server - there is no other switch.
 */
export const NAV_CATALOGUE: readonly NavItem[] = [
  {
    href: '/command',
    label: 'Command centre',
    description: 'Executive overview across every mission function.',
    icon: 'command',
    permission: 'read:command',
    availability: 'available',
  },
  {
    href: '/intelligence',
    label: 'Intelligence',
    description: 'Signals and the morning brief, every claim source-backed.',
    icon: 'intelligence',
    permission: 'read:intelligence',
    availability: 'available',
  },
  {
    href: '/opportunities',
    label: 'Opportunities',
    description: 'Bilateral opportunity pipeline and stage transitions.',
    icon: 'opportunities',
    permission: 'read:opportunity',
    availability: 'available',
  },
  {
    href: '/stakeholders',
    label: 'Stakeholders',
    description: 'Organisations, contacts and relationship history.',
    icon: 'stakeholders',
    permission: 'read:stakeholder',
    availability: 'available',
  },
  {
    href: '/meetings',
    label: 'Meetings',
    description: 'Pre-reads and follow-ups. Sending requires human approval.',
    icon: 'meetings',
    permission: 'read:meeting',
    availability: 'planned',
    plannedFor: 'Week 3',
  },
  {
    href: '/consular',
    label: 'Consular',
    description: 'Citizen case workload, ageing and service-level risk.',
    icon: 'consular',
    permission: 'read:consular_case',
    availability: 'planned',
    plannedFor: 'Week 3',
  },
  {
    href: '/diaspora',
    label: 'Diaspora',
    description: 'Consent-filtered expertise and capability search.',
    icon: 'diaspora',
    permission: 'read:diaspora_profile',
    availability: 'planned',
    plannedFor: 'Week 4',
  },
  {
    href: '/knowledge',
    label: 'Knowledge',
    description: 'Approved-source answers. Refuses when no source exists.',
    icon: 'knowledge',
    permission: 'read:knowledge_article',
    availability: 'planned',
    plannedFor: 'Week 3',
  },
  {
    href: '/governance',
    label: 'Governance',
    description: 'Append-only audit trail and AI decision traces.',
    icon: 'governance',
    permission: 'read:audit',
    availability: 'planned',
    plannedFor: 'Week 4',
  },
];

/**
 * Filter the catalogue down to what a session may see.
 *
 * Deny-by-default: an unknown, empty or not-yet-loaded permission set yields an empty
 * rail rather than a permissive one. A blank rail is a visible, honest "you have no
 * session yet"; a full rail would be a lie the server would then refuse.
 */
export function navItemsForPermissions(granted: readonly string[]): readonly NavItem[] {
  const allowed = new Set(granted);
  return NAV_CATALOGUE.filter((item) => allowed.has(item.permission));
}
