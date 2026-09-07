import * as React from 'react';
import Link from 'next/link';
import {
  ArrowRight,
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
 */

const TONE_CLASS: Readonly<Record<MetricTone, string>> = {
  default: 'text-foreground',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
};

export interface CommandTileProps {
  view: TileView;
  /** Extra body content for the `ready` and `empty` states, e.g. a short evidence list. */
  children?: React.ReactNode;
}

export function CommandTile({ view, children }: CommandTileProps): React.JSX.Element {
  const { definition, state, metrics, distribution, note, restrictedSources, statusMessage } =
    view;
  const headingId = `tile-${definition.id}`;
  const showsBody = state === 'ready' || state === 'empty';

  return (
    <Card
      // `aria-busy` lets assistive tech announce the loading state without a live region
      // per tile.
      aria-busy={state === 'loading'}
      aria-labelledby={headingId}
      className={cn('flex h-full flex-col', definition.span)}
    >
      <CardHeader className="gap-1 pb-3">
        <div className="flex items-start justify-between gap-2">
          <h3
            id={headingId}
            className="text-sm font-semibold leading-tight tracking-tight text-foreground"
          >
            {definition.title}
          </h3>
          <StateChip state={state} />
        </div>
        <p className="text-xs leading-snug text-muted-foreground">{definition.description}</p>
        {definition.href === undefined || !showsBody ? null : (
          <Link
            href={definition.href}
            className="inline-flex w-fit items-center gap-1 text-xs font-medium underline underline-offset-2 hover:text-foreground"
          >
            Open <ArrowRight aria-hidden="true" className="size-3" />
          </Link>
        )}
      </CardHeader>

      <CardContent className="flex flex-1 flex-col gap-3">
        {state === 'loading' ? <LoadingBody /> : null}

        {showsBody ? (
          <>
            <MetricRow metrics={metrics} gridClass={definition.metricGridClass} />
            {distribution === null ? null : <DistributionBars distribution={distribution} />}
            {children}
            {state === 'empty' ? (
              <StatusBody
                icon={Inbox}
                headline="Every count here is zero"
                detail="The API answered and the query found no rows in the zones you are cleared to read. These are real counts, not placeholders - the demonstration dataset has not been loaded, or nothing matches yet."
              />
            ) : null}
            {note === null ? null : (
              <p className="mt-auto text-2xs leading-snug text-muted-foreground">{note}</p>
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
    </Card>
  );
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

  const tone: Readonly<Record<Exclude<TileState, 'ready'>, string>> = {
    loading: 'border-input text-muted-foreground',
    empty: 'border-input text-muted-foreground',
    error: 'border-destructive/50 text-destructive',
    forbidden: 'border-input text-muted-foreground',
  };

  return (
    <span
      className={cn(
        'shrink-0 rounded border px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide',
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
}: {
  metrics: readonly TileMetric[];
  gridClass: string;
}): React.JSX.Element | null {
  if (metrics.length === 0) return null;

  return (
    <dl className={cn('grid gap-x-4 gap-y-3', gridClass)}>
      {metrics.map((entry) => (
        <div key={entry.label} className="min-w-0">
          <dt className="truncate text-2xs uppercase tracking-wide text-muted-foreground">
            {entry.label}
          </dt>
          <dd
            className={cn(
              'tabular mt-0.5 font-mono text-xl font-semibold leading-none',
              TONE_CLASS[entry.tone ?? 'default'],
            )}
          >
            {entry.value === null ? <UnavailableValue metric={entry} /> : entry.value}
          </dd>
          {entry.hint === undefined ? null : (
            <p className="mt-1 truncate text-2xs text-muted-foreground">{entry.hint}</p>
          )}
        </div>
      ))}
    </dl>
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
    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
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
    <p className="flex items-start gap-1.5 text-2xs leading-snug text-muted-foreground">
      <Lock aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
      <span>
        {joinWords(sources.map((source) => API_TILE_LABEL[source]))}{' '}
        {sources.length === 1 ? 'is' : 'are'} withheld from this role, so{' '}
        {sources.length === 1 ? 'that figure is' : 'those figures are'} shown as restricted
        rather than as zero.
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

function MetricSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-1.5">
      <Skeleton className="h-2.5 w-16" />
      <Skeleton className="h-5 w-12" />
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
        tone === 'destructive' ? 'border-destructive/40' : 'border-input',
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn(
          'mt-0.5 h-4 w-4 shrink-0',
          tone === 'destructive' ? 'text-destructive' : 'text-muted-foreground',
        )}
      />
      <div className="min-w-0">
        <p className="text-sm font-medium leading-tight text-foreground">{headline}</p>
        <p className="mt-1 text-xs leading-snug text-muted-foreground">{detail}</p>
      </div>
    </div>
  );
}
