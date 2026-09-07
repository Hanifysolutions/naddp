import {
  isDeniedError,
  type ApiError,
  type CommandTodayResponse,
  type ConsularTile,
  type DiasporaTile,
  type IntelligenceTile,
  type MeetingTile,
  type OpportunityTile,
  type StakeholderTile,
} from '@naddp/contracts';

import {
  CASE_STATUS_LABELS,
  CASE_STATUS_ORDER,
  CONSENT_STATUS_LABELS,
  CONSENT_STATUS_ORDER,
  OPPORTUNITY_STAGE_LABELS,
  OPPORTUNITY_STAGE_ORDER,
  RELATIONSHIP_STRENGTH_LABELS,
  RELATIONSHIP_STRENGTH_ORDER,
} from '@/lib/enum-labels';

/**
 * The command centre's view model: `GET /v1/command/today` projected onto six tiles.
 *
 * This module is pure - no React, no fetching, no clock - so the one question that matters
 * about it can be answered by reading it: **does any number on the board come from
 * somewhere other than the API?** The answer must stay no. Every figure below is a field
 * of the response; there is no arithmetic beyond reading a bucket out of a map the API
 * returned, no defaulting of a missing field to zero, and no derived "estimate" of
 * anything.
 *
 * Three distinctions the board must never collapse:
 *
 *  - **null tile vs zero counts.** A tile is `null` when the caller does not hold its read
 *    permission, and the API never queried those tables. That renders as `forbidden`. A
 *    tile of zeros means the query ran and found nothing. Showing a denial as a zero would
 *    make the whole deny-by-default demonstration invisible - and would be a lie about the
 *    mission's workload.
 *
 *  - **absent bucket vs zero bucket.** The API documents that every enum member is present
 *    in a distribution map, including the empty ones. If one is nonetheless missing,
 *    `readBucket` returns `null` ("not reported") rather than 0. We do not fill in the
 *    API's blanks.
 *
 *  - **refused vs broken.** A 403 is the product working; anything else is the product
 *    failing. They get different states and different words.
 */

/* -------------------------------------------------------------------------- */
/* API tiles                                                                  */
/* -------------------------------------------------------------------------- */

/** The six tiles `GET /v1/command/today` may return, keyed as the API names them. */
export type ApiTileKey =
  | 'opportunities'
  | 'consular'
  | 'stakeholders'
  | 'diaspora'
  | 'intelligence'
  | 'meetings';

/**
 * The permission each API tile requires.
 *
 * CROSS-TRACK CONTRACT: these strings are transcribed from
 * `apps/api/app/security/permissions.py` and must match it exactly. They are used only to
 * *explain* a denial the API already made - never to decide one.
 */
export const API_TILE_PERMISSION: Readonly<Record<ApiTileKey, string>> = {
  opportunities: 'read:opportunity',
  consular: 'read:consular_case',
  stakeholders: 'read:stakeholder',
  diaspora: 'read:diaspora_profile',
  intelligence: 'read:intelligence',
  meetings: 'read:meeting',
};

/** How each API tile is named in a sentence, for denial and provenance copy. */
export const API_TILE_LABEL: Readonly<Record<ApiTileKey, string>> = {
  opportunities: 'opportunity pipeline',
  consular: 'consular casework',
  stakeholders: 'stakeholder relationships',
  diaspora: 'diaspora capability',
  intelligence: 'intelligence signals',
  meetings: 'meetings and follow-ups',
};

/* -------------------------------------------------------------------------- */
/* View model                                                                 */
/* -------------------------------------------------------------------------- */

export type MetricTone = 'default' | 'success' | 'warning' | 'destructive';

/** Why a metric has no value. A metric with a value never carries one of these. */
export type MetricUnavailable =
  /** The caller may not read the tile this figure comes from. */
  | 'restricted'
  /** The tile was returned, but this bucket was not in it. We do not assume zero. */
  | 'not-reported';

export interface TileMetric {
  readonly label: string;
  /** Formatted value, or `null` when unavailable - see {@link MetricUnavailable}. */
  readonly value: string | null;
  readonly unavailable?: MetricUnavailable;
  /** Short qualifier under the value. Always a fact from the response, never a guess. */
  readonly hint?: string;
  readonly tone?: MetricTone;
}

export interface DistributionEntry {
  readonly key: string;
  readonly label: string;
  /** `null` when the API did not report this bucket. Never defaulted to 0. */
  readonly value: number | null;
}

export interface Distribution {
  readonly caption: string;
  readonly entries: readonly DistributionEntry[];
  /** The largest reported bucket, which is what the bars are scaled against. */
  readonly peak: number;
}

/**
 * The five honest states a tile can be in. There is no sixth state called "looks
 * plausible": a tile either carries data the API returned or says clearly that it does not.
 */
export type TileState = 'loading' | 'ready' | 'empty' | 'error' | 'forbidden';

export type TileId =
  | 'today'
  | 'opportunity-health'
  | 'citizen-service-health'
  | 'relationship-health'
  | 'diaspora-capability'
  | 'mission-outcomes';

export interface TileDefinition {
  readonly id: TileId;
  readonly title: string;
  readonly description: string;
  /** Tailwind column spans for the 12-column executive grid. */
  readonly span: string;
  /**
   * Column count for the metric row, as Tailwind classes.
   *
   * Declared per tile rather than once for the grid because tiles are different widths at
   * the same breakpoint: at 1440px a `xl:col-span-3` tile is about 250px of inner width,
   * where three metric columns would truncate every label, while a `xl:col-span-6` tile
   * has room for three. Tailwind 3.4 has no container queries without a plugin, so the
   * tile's own span is the only thing that knows how wide it is.
   */
  readonly metricGridClass: string;
  /** Which API tiles this card is built from. */
  readonly sources: readonly ApiTileKey[];
  /**
   * The screen this tile summarises, when one exists in this build.
   *
   * A summary the reader cannot act on is a dead end, and BUILD_BIBLE section 0 does not
   * allow those. The link is rendered only in the `ready` and `empty` states: a tile the
   * caller may not read must not offer a route to the thing they may not read either -
   * the API would refuse them, but the offer itself would leak that it exists.
   */
  readonly href?: string;
}

/** Three metric columns from 1280px up. For tiles spanning four columns or more. */
const WIDE_METRICS = 'grid-cols-2 xl:grid-cols-3';

/** Two columns until 1920px. For the half-width tiles in the top row at 1440px. */
const NARROW_METRICS = 'grid-cols-2 desk:grid-cols-3';

export interface TileView {
  readonly definition: TileDefinition;
  readonly state: TileState;
  readonly metrics: readonly TileMetric[];
  readonly distribution: Distribution | null;
  /** One line of context drawn from the response, or null. */
  readonly note: string | null;
  /** Sources this role may not read. Non-empty only on a partially-visible tile. */
  readonly restrictedSources: readonly ApiTileKey[];
  /** Populated in the `error` state, and in `forbidden` when the API explained itself. */
  readonly statusMessage: string | null;
}

/* -------------------------------------------------------------------------- */
/* Tile definitions                                                           */
/* -------------------------------------------------------------------------- */

/**
 * The six executive tiles, in reading order.
 *
 * The grid is twelve columns above 1280px, six between 768px and 1280px, one below.
 * MEASURED in headless Chrome against both rehearsal targets (BUILD_BIBLE §11), with an
 * unseeded database - the worst case, because every tile then carries its "every count is
 * zero" panel as well as its metrics:
 *
 *   1920x1080  rail 240px + content -> three 533px tiles per row, two rows, document
 *              height exactly 1080px against the 1080px viewport: the whole board, no scroll.
 *   1440x900   rail 240px + content -> a 6/3/3 top row (561px, 272px, 272px) and a 4/4/4
 *              second row (368px each). Document height 1303px, so the second row is
 *              reached by scrolling. That is accepted rather than fixed: shrinking the
 *              type to fit would cost legibility on a projector, which is the one thing
 *              this screen cannot trade away. The narrow tiles drop to two metric columns
 *              at this width (see `metricGridClass`) so no label truncates.
 */
export const TILE_DEFINITIONS: readonly TileDefinition[] = [
  {
    id: 'today',
    title: 'Today',
    description:
      'What arrived overnight and what is waiting on a human decision. Signals from the last seven days, meetings in the next seven.',
    span: 'md:col-span-6 xl:col-span-6 desk:col-span-4',
    metricGridClass: WIDE_METRICS,
    sources: ['intelligence', 'meetings'],
  },
  {
    id: 'opportunity-health',
    title: 'Bilateral Opportunity Health',
    description:
      'Pipeline movement across the critical-minerals and skilled-migration corridors.',
    span: 'md:col-span-3 xl:col-span-3 desk:col-span-4',
    metricGridClass: NARROW_METRICS,
    sources: ['opportunities'],
    href: '/opportunities',
  },
  {
    id: 'citizen-service-health',
    title: 'Citizen Service Health',
    description: 'Consular caseload, service-level risk and what is paused on the citizen.',
    span: 'md:col-span-3 xl:col-span-3 desk:col-span-4',
    metricGridClass: NARROW_METRICS,
    sources: ['consular'],
  },
  {
    id: 'relationship-health',
    title: 'Relationship Health',
    description: 'Contact coverage and assessed strength across the stakeholder map.',
    span: 'md:col-span-3 xl:col-span-4',
    metricGridClass: WIDE_METRICS,
    sources: ['stakeholders'],
    href: '/stakeholders',
  },
  {
    id: 'diaspora-capability',
    title: 'Diaspora Capability',
    description:
      'Expertise the mission may actually approach. Consent, not clearance, gates a profile.',
    span: 'md:col-span-3 xl:col-span-4',
    metricGridClass: WIDE_METRICS,
    sources: ['diaspora'],
  },
  {
    id: 'mission-outcomes',
    title: 'Mission Outcomes',
    description:
      'One governed picture: partnership, casework and diaspora outcomes side by side, each independently authorised.',
    span: 'md:col-span-6 xl:col-span-4',
    metricGridClass: WIDE_METRICS,
    sources: ['opportunities', 'consular', 'diaspora'],
  },
];

export const COMMAND_GRID_CLASS = 'grid grid-cols-1 gap-4 md:grid-cols-6 xl:grid-cols-12';

/* -------------------------------------------------------------------------- */
/* Formatting                                                                 */
/* -------------------------------------------------------------------------- */

const NUMBER_FORMAT = new Intl.NumberFormat('en-AU');

/** Format a count. Zero formats as "0" - a zero is a fact and is never an em dash. */
export function formatCount(value: number): string {
  return NUMBER_FORMAT.format(value);
}

/**
 * Format a money figure for an executive tile.
 *
 * Rounded to the nearest hundred thousand and rendered as "A$24.9m", because the precision
 * the column carries is not the precision the estimate has - printing A$24,900,000 on a
 * summary tile claims an accuracy nobody behind the number would defend.
 */
export function formatAud(value: number): string {
  if (value >= 1_000_000) return `A$${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `A$${Math.round(value / 1_000)}k`;
  return `A$${NUMBER_FORMAT.format(Math.round(value))}`;
}

/**
 * Read one bucket out of a distribution map.
 *
 * Returns `null`, never 0, when the key is absent. The API contracts to send every enum
 * member; if it ever does not, that is a contract change we must show rather than paper
 * over with a zero we made up.
 */
function readBucket(map: Readonly<Record<string, number>>, key: string): number | null {
  const value = map[key];
  return typeof value === 'number' ? value : null;
}

/** A metric whose value the API supplied. */
function metric(
  label: string,
  value: number,
  options: { hint?: string; tone?: MetricTone } = {},
): TileMetric {
  return {
    label,
    value: formatCount(value),
    ...(options.hint === undefined ? {} : { hint: options.hint }),
    ...(options.tone === undefined ? {} : { tone: options.tone }),
  };
}

/**
 * A metric whose value is already formatted - money, so far.
 *
 * Separate from {@link metric} rather than widening its parameter to `number | string`:
 * that widening would also let a caller pass an unformatted number straight through as a
 * string and skip `formatCount`, which is how a tile ends up rendering "24900000".
 */
function formattedMetric(
  label: string,
  value: string,
  options: { hint?: string; tone?: MetricTone } = {},
): TileMetric {
  return {
    label,
    value,
    ...(options.hint === undefined ? {} : { hint: options.hint }),
    ...(options.tone === undefined ? {} : { tone: options.tone }),
  };
}

/** A metric whose value is unavailable, with the reason attached. */
function unavailableMetric(
  label: string,
  unavailable: MetricUnavailable,
  hint?: string,
): TileMetric {
  return {
    label,
    value: null,
    unavailable,
    ...(hint === undefined ? {} : { hint }),
  };
}

/** A bucket metric: a real count, or an explicit "not reported" when the bucket is absent. */
function bucketMetric(
  label: string,
  map: Readonly<Record<string, number>>,
  key: string,
  options: { hint?: string; tone?: MetricTone } = {},
): TileMetric {
  const value = readBucket(map, key);
  return value === null
    ? unavailableMetric(label, 'not-reported', options.hint)
    : metric(label, value, options);
}

/** Escalate tone only when there is something to escalate. Calm is the default. */
function riskTone(value: number, tone: MetricTone): MetricTone {
  return value > 0 ? tone : 'default';
}

function distribution(
  caption: string,
  map: Readonly<Record<string, number>>,
  order: readonly string[],
  labels: Readonly<Record<string, string>>,
): Distribution {
  const entries = order.map((key) => ({
    key,
    label: labels[key] ?? key,
    value: readBucket(map, key),
  }));
  const peak = entries.reduce((max, entry) => Math.max(max, entry.value ?? 0), 0);
  return { caption, entries, peak };
}

/* -------------------------------------------------------------------------- */
/* Per-tile builders                                                          */
/* -------------------------------------------------------------------------- */

/**
 * What one builder produced: the rendered parts, plus the raw counts it read.
 *
 * `observed` is what decides `empty` versus `ready`. A tile is empty when every count the
 * API returned for it is zero - which is the true state of an unseeded database, and is
 * different from both "restricted" and "failed".
 */
interface TileContent {
  readonly metrics: readonly TileMetric[];
  readonly distribution: Distribution | null;
  readonly note: string | null;
  readonly observed: readonly number[];
}

function todayContent(
  intelligence: IntelligenceTile | null,
  meetings: MeetingTile | null,
): TileContent {
  const metrics: TileMetric[] = [];
  const observed: number[] = [];

  if (intelligence === null) {
    metrics.push(unavailableMetric('Signals, last 7 days', 'restricted'));
  } else {
    metrics.push(
      metric('Signals, last 7 days', intelligence.recent_total, {
        hint: `${formatCount(intelligence.total)} in your zones`,
      }),
    );
    observed.push(intelligence.recent_total, intelligence.total);
  }

  if (meetings === null) {
    metrics.push(
      unavailableMetric('Awaiting approval', 'restricted'),
      unavailableMetric('Meetings, next 7 days', 'restricted'),
    );
  } else {
    metrics.push(
      metric('Awaiting approval', meetings.followups_awaiting_approval, {
        hint: 'follow-ups at officer review',
        tone: riskTone(meetings.followups_awaiting_approval, 'warning'),
      }),
      metric('Meetings, next 7 days', meetings.upcoming_total),
    );
    observed.push(meetings.followups_awaiting_approval, meetings.upcoming_total);
  }

  const note =
    intelligence === null
      ? null
      : `${formatCount(intelligence.awaiting_triage)} signal${
          intelligence.awaiting_triage === 1 ? '' : 's'
        } still awaiting triage.`;

  return { metrics, distribution: null, note, observed };
}

function opportunityContent(tile: OpportunityTile): TileContent {
  return {
    metrics: [
      metric('Open', tile.open_total, {
        hint: `${formatCount(tile.total)} readable in total`,
      }),
      metric('Overdue next action', tile.overdue_next_action, {
        tone: riskTone(tile.overdue_next_action, 'destructive'),
      }),
      formattedMetric('Open pipeline', formatAud(tile.pipeline_value_aud), {
        hint: 'sum of estimates, not probability-weighted',
      }),
    ],
    distribution: distribution(
      'Pipeline by stage',
      tile.by_stage,
      OPPORTUNITY_STAGE_ORDER,
      OPPORTUNITY_STAGE_LABELS,
    ),
    // Stated on the tile rather than only on the board: an AI-proposed opportunity that
    // reaches an executive summary without that word attached has quietly become a
    // reported fact (OPEN_QUESTIONS Q-17).
    note:
      tile.ai_proposed > 0
        ? `${formatCount(tile.ai_proposed)} of these ${
            tile.ai_proposed === 1 ? 'was' : 'were'
          } proposed by the AI and ${
            tile.ai_proposed === 1 ? 'is' : 'are'
          } pending officer qualification.`
        : null,
    observed: [tile.total, tile.open_total, tile.overdue_next_action],
  };
}

function consularContent(tile: ConsularTile): TileContent {
  return {
    metrics: [
      metric('Open cases', tile.open_total, {
        hint: `${formatCount(tile.total)} readable in total`,
      }),
      metric('SLA breached', tile.sla_breached, {
        tone: riskTone(tile.sla_breached, 'destructive'),
      }),
      metric('Due within 48h', tile.sla_due_soon, {
        tone: riskTone(tile.sla_due_soon, 'warning'),
      }),
    ],
    distribution: distribution(
      'Caseload by status',
      tile.by_status,
      CASE_STATUS_ORDER,
      CASE_STATUS_LABELS,
    ),
    note: `${formatCount(tile.awaiting_citizen)} case${
      tile.awaiting_citizen === 1 ? ' is' : 's are'
    } paused on the citizen, which stops the SLA clock and is excluded from the breach count.`,
    observed: [tile.total, tile.open_total, tile.sla_breached, tile.sla_due_soon],
  };
}

function stakeholderContent(tile: StakeholderTile): TileContent {
  return {
    metrics: [
      metric('Contacts', tile.total, {
        hint: `across ${formatCount(tile.organisations)} organisations`,
      }),
      metric('Never contacted', tile.never_contacted, {
        tone: riskTone(tile.never_contacted, 'warning'),
      }),
      // Distinct from 'never contacted', and the distinction is the point: a relationship
      // that has gone quiet and one that was never started need different work.
      metric('Dormant 90 days', tile.dormant, {
        tone: riskTone(tile.dormant, 'warning'),
      }),
    ],
    distribution: distribution(
      'Relationship strength',
      tile.by_relationship_strength,
      RELATIONSHIP_STRENGTH_ORDER,
      RELATIONSHIP_STRENGTH_LABELS,
    ),
    note: null,
    observed: [tile.total, tile.never_contacted, tile.dormant],
  };
}

function diasporaContent(tile: DiasporaTile): TileContent {
  return {
    metrics: [
      metric('Live profiles', tile.total),
      metric('Contactable', tile.contactable, { hint: 'consent given to be approached' }),
      bucketMetric('Directory only', tile.by_consent_status, 'GIVEN_DIRECTORY_ONLY'),
    ],
    distribution: distribution(
      'Consent status',
      tile.by_consent_status,
      CONSENT_STATUS_ORDER,
      CONSENT_STATUS_LABELS,
    ),
    note: 'Consent is filtered inside the query, not after it: a profile without consent is never loaded.',
    observed: [tile.total, tile.contactable],
  };
}

function outcomesContent(
  opportunities: OpportunityTile | null,
  consular: ConsularTile | null,
  diaspora: DiasporaTile | null,
): TileContent {
  const metrics: TileMetric[] = [];
  const observed: number[] = [];

  if (opportunities === null) {
    metrics.push(unavailableMetric('Partnerships concluded', 'restricted'));
  } else {
    const partnered = readBucket(opportunities.by_stage, 'PARTNERED');
    metrics.push(
      partnered === null
        ? unavailableMetric('Partnerships concluded', 'not-reported')
        : metric('Partnerships concluded', partnered, {
            tone: riskTone(partnered, 'success'),
          }),
    );
    if (partnered !== null) observed.push(partnered);
  }

  if (consular === null) {
    metrics.push(unavailableMetric('Cases resolved', 'restricted'));
  } else {
    const resolved = readBucket(consular.by_status, 'RESOLVED');
    metrics.push(
      resolved === null
        ? unavailableMetric('Cases resolved', 'not-reported')
        : metric('Cases resolved', resolved),
    );
    if (resolved !== null) observed.push(resolved);
  }

  if (diaspora === null) {
    metrics.push(unavailableMetric('Diaspora reachable', 'restricted'));
  } else {
    metrics.push(metric('Diaspora reachable', diaspora.contactable));
    observed.push(diaspora.contactable);
  }

  return {
    metrics,
    distribution: null,
    note: 'Each figure comes from a different bounded context and a separate authorisation check. The picture is unified; the permissions are not.',
    observed,
  };
}

/* -------------------------------------------------------------------------- */
/* Board assembly                                                             */
/* -------------------------------------------------------------------------- */

export type BoardInput =
  | { readonly kind: 'loading' }
  | { readonly kind: 'error'; readonly error: ApiError }
  | { readonly kind: 'ready'; readonly data: CommandTodayResponse };

/** A source is visible when the API returned a tile for it. `undefined` is `null` here. */
function sourceOf<T>(value: T | null | undefined): T | null {
  return value ?? null;
}

function contentFor(id: TileId, data: CommandTodayResponse): TileContent | null {
  const opportunities = sourceOf(data.opportunities);
  const consular = sourceOf(data.consular);
  const stakeholders = sourceOf(data.stakeholders);
  const diaspora = sourceOf(data.diaspora);
  const intelligence = sourceOf(data.intelligence);
  const meetings = sourceOf(data.meetings);

  switch (id) {
    case 'today':
      return intelligence === null && meetings === null
        ? null
        : todayContent(intelligence, meetings);
    case 'opportunity-health':
      return opportunities === null ? null : opportunityContent(opportunities);
    case 'citizen-service-health':
      return consular === null ? null : consularContent(consular);
    case 'relationship-health':
      return stakeholders === null ? null : stakeholderContent(stakeholders);
    case 'diaspora-capability':
      return diaspora === null ? null : diasporaContent(diaspora);
    case 'mission-outcomes':
      return opportunities === null && consular === null && diaspora === null
        ? null
        : outcomesContent(opportunities, consular, diaspora);
  }
}

/** Which of a tile's sources the API withheld from this caller. */
function restrictedSourcesFor(
  definition: TileDefinition,
  data: CommandTodayResponse,
): readonly ApiTileKey[] {
  return definition.sources.filter((source) => sourceOf(data[source]) === null);
}

/**
 * Build all six tiles from one query state.
 *
 * The whole board shares a single `GET /v1/command/today`, so loading and failure are
 * board-wide facts and every tile reports them identically. Only the `ready` branch
 * differentiates, and it differentiates on what the API actually returned.
 */
export function buildBoard(input: BoardInput): readonly TileView[] {
  return TILE_DEFINITIONS.map((definition): TileView => {
    if (input.kind === 'loading') {
      return {
        definition,
        state: 'loading',
        metrics: [],
        distribution: null,
        note: null,
        restrictedSources: [],
        statusMessage: null,
      };
    }

    if (input.kind === 'error') {
      // A refusal is the product working. Anything else is the product failing. The two
      // get different states, different words and different colours.
      const denied = isDeniedError(input.error);
      return {
        definition,
        state: denied ? 'forbidden' : 'error',
        metrics: [],
        distribution: null,
        note: null,
        restrictedSources: definition.sources,
        statusMessage: input.error.message,
      };
    }

    const content = contentFor(definition.id, input.data);
    const restrictedSources = restrictedSourcesFor(definition, input.data);

    if (content === null) {
      return {
        definition,
        state: 'forbidden',
        metrics: [],
        distribution: null,
        note: null,
        restrictedSources,
        statusMessage: null,
      };
    }

    const isEmpty =
      content.observed.length > 0 && content.observed.every((value) => value === 0);

    return {
      definition,
      state: isEmpty ? 'empty' : 'ready',
      metrics: content.metrics,
      distribution: content.distribution,
      note: content.note,
      restrictedSources,
      statusMessage: null,
    };
  });
}
