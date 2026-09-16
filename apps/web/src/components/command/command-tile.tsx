import * as React from 'react';
import Link from 'next/link';
import {
  Ban,
  Inbox,
  Lock,
  LoaderCircle,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';

import { DistributionBars } from '@/components/command/distribution-bars';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  API_TILE_LABEL,
  API_TILE_PERMISSION,
  type ApiTileKey,
  type MetricTone,
  type TileMetric,
  type TileState,
  type TileView,
} from '@/lib/command-view';
import { cn } from '@/lib/utils';

/**
 * One executive tile.
 *
 * Purely presentational: it renders a `TileView` built by `lib/command-view.ts` and makes
 * no decision of its own about what a number means. Its whole job is to keep five states
 * visually distinct, because three of them are routinely confused in dashboards and the
 * confusion is always in the same direction - towards looking healthier than the truth:
 *
 *   loading    a request is in flight
 *   ready      the API returned data and it is on screen
 *   empty      the API answered and every count was zero - a real answer, not a placeholder
 *   error      the request failed; the API's own reason is shown
 *   forbidden  this role may not read the source at all, so nothing was even queried
 *
 * `empty` and `forbidden` are the pair that must never blur. A zero is a statement about
 * the mission's workload; a denial is a statement about the reader. Rendering a denial as a
 * zero would tell an Ambassador there are no consular cases when the truth is that this
 * role cannot see them.
 *
 * VISUAL CONTRACT (DESIGN_SYSTEM.md v1.0, "Layout & structure"). The tile is an instrument
 * panel rather than a card: hairline --line edge, the 6px radius, no shadow, and exactly
 * ONE loud element - the primary figure. Everything else is deliberately quiet, so that a
 * Head of Mission scanning six tiles reads six numbers rather than six boxes.
 */

/**
 * Figure colour by tone.
 *
 * Mission Slate reserves the semantic colours for STATE and never for decoration, so a
 * figure is coloured only because the number itself is saying something: `riskTone` in
 * `lib/command-view.ts` escalates a tone only above zero, which means a calm board is a
 * genuinely calm board. `warning` resolves to --warn-ink rather than --warn, because plain
 * warn measures 3.47:1 on paper and is reserved for borders, icons and state ticks
 * (globals.css, documented departure (b)).
 */
const TONE_CLASS: Readonly<Record<MetricTone, string>> = {
  default: 'text-ink',
  success: 'text-ok',
  warning: 'text-warn-ink',
  destructive: 'text-risk',
};

export interface CommandTileProps {
  view: TileView;
  /** Extra body content for the `ready` and `empty` states, e.g. a short evidence list. */
  children?: React.ReactNode;
}

export function CommandTile({ view, children }: CommandTileProps): React.JSX.Element {
  const {
    definition,
    state,
    metrics,
    distribution,
    note,
    restrictedSources,
    statusMessage,
  } = view;
  const headingId = `tile-${definition.id}`;
  const showsBody = state === 'ready' || state === 'empty';

  return (
    <Card
      // `aria-busy` lets assistive tech announce the loading state without a live region
      // per tile.
      aria-busy={state === 'loading'}
      aria-labelledby={headingId}
      className={cn('flex h-full flex-col overflow-hidden', definition.span)}
    >
      {/*
       * The state tick rides an inner wrapper rather than the Card itself, and that is a
       * cascade fact rather than a layout preference: `.tick-*` is declared in @layer
       * components while the Card's own `border-line` is a utility, so a tick on the same
       * element would keep its 2px width and lose its semantic colour to the hairline. On
       * a bare wrapper it wins cleanly, and `overflow-hidden` on the Card clips it to the
       * 6px radius so it reads as a rule down the inside edge.
       */}
      <div className={cn('flex h-full flex-col', tickClass(view))}>
        <CardHeader className="gap-1 pb-3">
          <div className="flex items-start justify-between gap-2">
            <h3 id={headingId} className="text-sm font-semibold leading-tight text-ink">
              {definition.title}
            </h3>
            <StateChip state={state} />
          </div>
          <p className="text-xs leading-snug text-slate-700">{definition.description}</p>
          {definition.href === undefined || !showsBody ? null : (
            <Link
              href={definition.href}
              className="w-fit text-label font-medium text-accent underline underline-offset-4 hover:text-ink"
            >
              Open
              {/* The visible word carries the accent; the destination is spoken, so the
                  board is not a column of identical "Open" links to a screen-reader user
                  reading it out of context. */}
              <span className="sr-only">{` ${definition.title}`}</span>
            </Link>
          )}
        </CardHeader>

        <CardContent className="flex flex-1 flex-col gap-3">
          {state === 'loading' ? <LoadingBody /> : null}

          {showsBody ? (
            <>
              <MetricRow
                metrics={metrics}
                gridClass={definition.metricGridClass}
                accentPrimary={definition.href !== undefined}
              />
              {distribution === null ? null : (
                <DistributionBars distribution={distribution} />
              )}
              {children}
              {state === 'empty' ? (
                <StatusBody
                  icon={Inbox}
                  headline="Every count here is zero"
                  detail="The API answered and the query found no rows in the zones you are cleared to read. These are real counts, not placeholders - the demonstration dataset has not been loaded, or nothing matches yet."
                />
              ) : null}
              {note === null ? null : (
                <p className="mt-auto text-2xs leading-snug text-slate-700">{note}</p>
              )}
              {restrictedSources.length === 0 ? null : (
                <RestrictedFootnote sources={restrictedSources} />
              )}
            </>
          ) : null}

          {state === 'error' ? (
            <StatusBody
              icon={TriangleAlert}
              tone="destructive"
              headline="Could not load this tile"
              detail={
                statusMessage === null
                  ? 'The request failed and the API did not supply a reason. These figures are unavailable rather than zero.'
                  : `${statusMessage} These figures are unavailable rather than zero.`
              }
            />
          ) : null}

          {state === 'forbidden' ? (
            <StatusBody
              icon={Ban}
              headline="Your role does not have access"
              detail={forbiddenDetail(restrictedSources, statusMessage)}
            />
          ) : null}
        </CardContent>
      </div>
    </Card>
  );
}

/**
 * The left border-tick, which is information rather than decoration.
 *
 * It answers one question for someone scanning the whole board at arm's length: does this
 * tile hold something that needs a human? Only two things can put it there - a figure the
 * view model already escalated above zero, or a read that failed outright - and the colour
 * is never the only carrier, because the escalated figure is also coloured and the failed
 * read also prints an icon and a headline.
 *
 * A `forbidden` tile deliberately gets NO tick. A refusal is the product working exactly as
 * designed; ticking it would file a working control beside a breached SLA, and its own
 * panel already says so in words, with a lock.
 */
function tickClass(view: TileView): string | null {
  if (view.state === 'error') return 'tick-risk';
  const tones = view.metrics.map((entry) => entry.tone ?? 'default');
  if (tones.includes('destructive')) return 'tick-risk';
  if (tones.includes('warning')) return 'tick-warn';
  return null;
}

/**
 * The words a denial gets.
 *
 * It names the permission the API wanted, because a refusal a viewer can trace is a
 * demonstrable control and a refusal they cannot is just a broken screen. It never implies
 * the underlying figure is zero, and never suggests it might appear later.
 */
function forbiddenDetail(
  sources: readonly ApiTileKey[],
  statusMessage: string | null,
): string {
  const base =
    sources.length === 0
      ? 'The API refused this read for your current demo role.'
      : `Reading ${joinWords(sources.map((source) => API_TILE_LABEL[source]))} requires ${joinWords(
          sources.map((source) => API_TILE_PERMISSION[source]),
        )}, which your current demo role does not hold. The underlying tables were never queried, so this is not a count of zero - it is a refusal.`;

  return statusMessage === null ? base : `${base} The API said: ${statusMessage}`;
}

/** "a", "a and b", "a, b and c". */
function joinWords(parts: readonly string[]): string {
  if (parts.length <= 1) return parts[0] ?? '';
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1] ?? ''}`;
}

function StateChip({ state }: { state: TileState }): React.JSX.Element | null {
  if (state === 'ready') return null;

  const copy: Readonly<Record<Exclude<TileState, 'ready'>, string>> = {
    loading: 'Loading',
    empty: 'No records',
    error: 'Error',
    forbidden: 'Restricted',
  };

  // Sentence case on a hairline edge: the chip reports a state, it does not compete with
  // the figure below it, and it is never a tracked-out cap.
  const tone: Readonly<Record<Exclude<TileState, 'ready'>, string>> = {
    loading: 'border-line text-slate-700',
    empty: 'border-line text-slate-700',
    error: 'border-risk/50 text-risk',
    forbidden: 'border-line text-slate-700',
  };

  return (
    <span
      className={cn(
        'shrink-0 rounded border px-1.5 py-0.5 text-2xs font-medium',
        tone[state],
      )}
    >
      {state === 'loading' ? (
        <LoaderCircle aria-hidden="true" className="mr-1 inline h-3 w-3 animate-spin" />
      ) : null}
      {state === 'forbidden' ? (
        <Lock aria-hidden="true" className="mr-1 inline h-3 w-3" />
      ) : null}
      {copy[state]}
    </span>
  );
}

function MetricRow({
  metrics,
  gridClass,
  accentPrimary,
}: {
  metrics: readonly TileMetric[];
  gridClass: string;
  /** Whether this tile's headline figure is one the reader can go and act on. */
  accentPrimary: boolean;
}): React.JSX.Element | null {
  if (metrics.length === 0) return null;

  return (
    <dl className={cn('grid gap-x-4 gap-y-3', gridClass)}>
      {metrics.map((entry, index) => (
        <Metric
          key={entry.label}
          metric={entry}
          isPrimary={index === 0}
          accentPrimary={accentPrimary}
        />
      ))}
    </dl>
  );
}

/**
 * One figure and its label.
 *
 * Four decisions, each from DESIGN_SYSTEM.md rather than from taste:
 *
 *  - **The figure sits above its label and is the loud element.** The first metric of a
 *    tile is its headline and is set two steps up the type scale; the rest stay quiet. If
 *    every figure were large the tile would have no headline at all. DOM order is still
 *    `dt` then `dd`, as a definition list requires - `flex-col-reverse` does the visual
 *    swap only, so a screen reader still hears the label before the number it belongs to.
 *  - **Tabular figures, not monospace.** Columns align because the figures are tabular.
 *    Mono on a figure or on a label is the generated tell the design system names.
 *  - **The accent is spent on one thing.** A headline figure takes --accent only when the
 *    tile links to the screen where the reader can act on it, and only while nothing is
 *    escalated: a breached or overdue count keeps its semantic colour, because state
 *    outranks affordance.
 *  - **The settle animation is the board's only motion.** It is CSS, so reduced-motion
 *    users get the final value instantly and the real number is in the DOM throughout.
 */
function Metric({
  metric,
  isPrimary,
  accentPrimary,
}: {
  metric: TileMetric;
  isPrimary: boolean;
  accentPrimary: boolean;
}): React.JSX.Element {
  const tone = metric.tone ?? 'default';
  const hasValue = metric.value !== null;
  // A dash is not a figure. An unavailable value stays at the quiet size whatever position
  // it holds, so a restricted tile never shouts an em dash across the room.
  const isLoud = isPrimary && hasValue;

  return (
    <div className="flex min-w-0 flex-col-reverse gap-1">
      <dt className="min-w-0">
        {/* Wraps rather than truncates: a clipped label ("Overdue next ...") is a figure
            nobody can name, and these tiles are read at arm's length across a table. */}
        <span className="block text-label leading-snug text-slate-700">
          {metric.label}
        </span>
        {metric.hint === undefined ? null : (
          <span className="mt-0.5 block text-2xs leading-snug text-slate-700">
            {metric.hint}
          </span>
        )}
      </dt>
      <dd
        className={cn(
          'tabular font-display font-semibold leading-none',
          isLoud ? 'figure-settle text-figure' : 'text-xl',
          isLoud && accentPrimary && tone === 'default'
            ? 'text-accent'
            : TONE_CLASS[tone],
        )}
      >
        {metric.value === null ? <UnavailableValue metric={metric} /> : metric.value}
      </dd>
    </div>
  );
}

/**
 * A missing value, with the reason it is missing.
 *
 * Never a zero, and never a bare dash: the sighted reader gets a lock glyph for a denial,
 * and every reader gets a sentence naming which of the two very different reasons applies.
 */
function UnavailableValue({ metric }: { metric: TileMetric }): React.JSX.Element {
  const restricted = metric.unavailable === 'restricted';

  return (
    <span className="inline-flex items-center gap-1.5 text-slate-700">
      {restricted ? <Lock aria-hidden="true" className="h-3.5 w-3.5" /> : null}
      <span aria-hidden="true">&mdash;</span>
      <span className="sr-only">
        {restricted
          ? 'Restricted: your role does not have access to this figure.'
          : 'Not reported by the API for this response.'}
      </span>
    </span>
  );
}

/** Named on a partially-visible tile, so a short row is never read as a low number. */
function RestrictedFootnote({
  sources,
}: {
  sources: readonly ApiTileKey[];
}): React.JSX.Element {
  return (
    <p className="flex items-start gap-1.5 text-2xs leading-snug text-slate-700">
      <Lock aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
      <span>
        {joinWords(sources.map((source) => API_TILE_LABEL[source]))}{' '}
        {sources.length === 1 ? 'is' : 'are'} withheld from this role, so{' '}
        {sources.length === 1 ? 'that figure is' : 'those figures are'} shown as
        restricted rather than as zero.
      </span>
    </p>
  );
}

function LoadingBody(): React.JSX.Element {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
        <MetricSkeleton />
        <MetricSkeleton />
        <MetricSkeleton />
      </div>
      <Skeleton className="h-3 w-4/5" />
      <Skeleton className="h-3 w-3/5" />
      <span className="sr-only">Loading tile data</span>
    </div>
  );
}

/** Shaped like the figure it stands in for, so little reflows when the real number lands. */
function MetricSkeleton(): React.JSX.Element {
  return (
    <div className="flex flex-col-reverse gap-1">
      <Skeleton className="h-3 w-16" />
      <Skeleton className="h-7 w-14" />
    </div>
  );
}

interface StatusBodyProps {
  icon: LucideIcon;
  headline: string;
  detail: string;
  tone?: 'muted' | 'destructive';
}

function StatusBody({
  icon: Icon,
  headline,
  detail,
  tone = 'muted',
}: StatusBodyProps): React.JSX.Element {
  return (
    <div
      className={cn(
        'flex flex-1 items-start gap-2.5 rounded-md border border-dashed p-3',
        tone === 'destructive' ? 'border-risk/40' : 'border-input',
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn(
          'mt-0.5 h-4 w-4 shrink-0',
          tone === 'destructive' ? 'text-risk' : 'text-slate-700',
        )}
      />
      <div className="min-w-0">
        <p className="text-sm font-medium leading-tight text-ink">{headline}</p>
        <p className="mt-1 text-xs leading-snug text-slate-700">{detail}</p>
      </div>
    </div>
  );
}
