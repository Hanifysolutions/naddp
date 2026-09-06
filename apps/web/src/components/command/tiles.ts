import type { CommandTileMetric } from '@/components/command/command-tile';

/**
 * The six executive tiles of the command centre (BUILD_BIBLE §12, W1 "command-center
 * shell"). Defined as data so the page and its `loading.tsx` render exactly the same
 * grid - the loading skeleton cannot drift from the real layout, because it is the same
 * layout in a different state.
 *
 * Metric *labels* are structure: they state what question the tile answers, and they are
 * settled now so the API track knows precisely which figures `/v1/command/today` must
 * return. Metric *values* are `null` until the API supplies them. A null renders as an
 * explicit em dash and an "no value available yet" announcement - never as 0, never as a
 * plausible-looking figure. There is no invented data anywhere in this file.
 */
export interface CommandTileDefinition {
  readonly id: string;
  readonly title: string;
  readonly description: string;
  /** Tailwind column spans for the 12-column executive grid. */
  readonly span: string;
  readonly metrics: readonly CommandTileMetric[];
}

/** Values are supplied by the API. Until then every figure is explicitly unavailable. */
const awaiting = (label: string, hint?: string): CommandTileMetric =>
  hint === undefined ? { label, value: null } : { label, value: null, hint };

export const COMMAND_TILES: readonly CommandTileDefinition[] = [
  {
    id: 'today',
    title: 'Today',
    description:
      'The next twelve hours: your brief, what is waiting on your decision, and what moved overnight.',
    span: 'md:col-span-6 xl:col-span-6 desk:col-span-4',
    metrics: [
      awaiting('Brief items', 'source-backed'),
      awaiting('Awaiting your approval'),
      awaiting('Changed overnight'),
    ],
  },
  {
    id: 'opportunity-health',
    title: 'Bilateral Opportunity Health',
    description:
      'Pipeline movement across the critical-minerals and skilled-migration corridors.',
    span: 'md:col-span-3 xl:col-span-3 desk:col-span-4',
    metrics: [
      awaiting('Active'),
      awaiting('Advanced', 'this week'),
      awaiting('Stalled', '30+ days'),
    ],
  },
  {
    id: 'citizen-service-health',
    title: 'Citizen Service Health',
    description: 'Consular caseload, ageing profile and service-level risk.',
    span: 'md:col-span-3 xl:col-span-3 desk:col-span-4',
    metrics: [
      awaiting('Open cases'),
      awaiting('Breaching SLA'),
      awaiting('Median age', 'days'),
    ],
  },
  {
    id: 'relationship-health',
    title: 'Relationship Health',
    description: 'Contact cadence and coverage across priority stakeholders.',
    span: 'md:col-span-3 xl:col-span-4',
    metrics: [
      awaiting('Priority stakeholders'),
      awaiting('No contact', '90+ days'),
      awaiting('Meetings', 'this month'),
    ],
  },
  {
    id: 'diaspora-capability',
    title: 'Diaspora Capability',
    description: 'Consent-cleared expertise available to the mission.',
    span: 'md:col-span-3 xl:col-span-4',
    metrics: [
      awaiting('Consented profiles'),
      awaiting('Expertise domains'),
      awaiting('Matched to open work'),
    ],
  },
  {
    id: 'mission-outcomes',
    title: 'Mission Outcomes',
    description:
      'Where opportunity, stakeholder, diaspora and citizen-service outcomes meet in one governed picture.',
    span: 'md:col-span-6 xl:col-span-4',
    metrics: [
      awaiting('Outcomes tracked'),
      awaiting('Delivered', 'this quarter'),
      awaiting('At risk'),
    ],
  },
];

/**
 * The executive grid.
 *
 * Twelve columns above 1280px, six between 768px and 1280px, one below. Verified against
 * both rehearsal targets (BUILD_BIBLE §11):
 *
 *   1920x1080  rail 240px + 1680px content -> three tiles per row at `desk:col-span-4`,
 *              two rows, no scrolling to see the whole board.
 *   1440x900   rail 240px + 1200px content -> `xl:` spans give a 6/3/3 top row and a
 *              4/4/4 second row; every metric row still fits three columns.
 */
export const COMMAND_GRID_CLASS =
  'grid grid-cols-1 gap-4 md:grid-cols-6 xl:grid-cols-12';
