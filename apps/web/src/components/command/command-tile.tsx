import * as React from 'react';
import { Ban, Inbox, LoaderCircle, TriangleAlert, type LucideIcon } from 'lucide-react';

import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

/**
 * The five honest states a tile can be in. There is no sixth state called
 * "looks plausible" - a tile either has data from the API or says clearly that it does
 * not. No placeholder prose, no invented numbers.
 *
 *   loading    a request is in flight
 *   ready      the API returned data and `metrics`/`children` describe it
 *   empty      the API answered, and the answer is genuinely nothing
 *   error      the request failed; the reason is shown
 *   forbidden  RBAC denied this read for the current role - a feature, shown as one
 */
export type CommandTileState = 'loading' | 'ready' | 'empty' | 'error' | 'forbidden';

export interface CommandTileMetric {
  /** Short label, e.g. "Open cases". */
  readonly label: string;
  /**
   * The formatted value. `null` means "the API has not supplied this yet" and renders as
   * an explicit placeholder glyph - never as 0, and never as a guess.
   */
  readonly value: string | null;
  /** Optional qualifier rendered under the value, e.g. "last 7 days". */
  readonly hint?: string;
  /** Optional emphasis. Always accompanied by the label text; colour is never the cue. */
  readonly tone?: 'default' | 'success' | 'warning' | 'destructive';
}

export interface CommandTileProps {
  /** Tile heading. Rendered as an `<h3>` inside the page's heading hierarchy. */
  title: string;
  /** One line explaining what question this tile answers. */
  description: string;
  state: CommandTileState;
  /** Metric row. Omitted entirely for tiles that are purely narrative. */
  metrics?: readonly CommandTileMetric[];
  /** Message shown in the `error` state. Required to be the real reason. */
  errorMessage?: string;
  /** Optional grid-span classes applied to the outer card. */
  className?: string;
  /** Body content for the `ready` state. */
  children?: React.ReactNode;
}

const TONE_CLASS: Readonly<Record<NonNullable<CommandTileMetric['tone']>, string>> = {
  default: 'text-foreground',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
};

export function CommandTile({
  title,
  description,
  state,
  metrics,
  errorMessage,
  className,
  children,
}: CommandTileProps): React.JSX.Element {
  const headingId = `tile-${slugify(title)}`;

  return (
    <Card
      // `aria-busy` lets assistive tech announce the loading state without us having to
      // render a live region per tile.
      aria-busy={state === 'loading'}
      aria-labelledby={headingId}
      className={cn('flex h-full flex-col', className)}
    >
      <CardHeader className="gap-1 pb-3">
        <div className="flex items-start justify-between gap-2">
          <h3 id={headingId} className="text-sm font-semibold leading-tight tracking-tight">
            {title}
          </h3>
          <StateChip state={state} />
        </div>
        <p className="text-xs leading-snug text-muted-foreground">{description}</p>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col justify-between gap-3">
        {state === 'loading' ? <LoadingBody /> : null}

        {state === 'ready' ? (
          <>
            {metrics === undefined ? null : <MetricRow metrics={metrics} />}
            {children === undefined ? null : <div className="text-sm">{children}</div>}
          </>
        ) : null}

        {state === 'empty' ? (
          <>
            {metrics === undefined ? null : <MetricRow metrics={metrics} />}
            <StatusBody
              icon={Inbox}
              headline="Awaiting data"
              detail="This tile is wired and structured, but the API has not returned any records for it yet. Nothing has been invented to fill the space."
            />
          </>
        ) : null}

        {state === 'error' ? (
          <StatusBody
            icon={TriangleAlert}
            tone="destructive"
            headline="Could not load this tile"
            detail={
              errorMessage ??
              'The request failed and the API did not supply a reason. The figures above are unavailable rather than zero.'
            }
          />
        ) : null}

        {state === 'forbidden' ? (
          <StatusBody
            icon={Ban}
            tone="muted"
            headline="Not available to this role"
            detail="Your current demo role does not hold the permission this view requires. Access is denied by default and enforced by the API, not by hiding it here."
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

function StateChip({ state }: { state: CommandTileState }): React.JSX.Element | null {
  if (state === 'ready') return null;

  const copy: Readonly<Record<Exclude<CommandTileState, 'ready'>, string>> = {
    loading: 'Loading',
    empty: 'Awaiting data',
    error: 'Error',
    forbidden: 'Restricted',
  };

  const tone: Readonly<Record<Exclude<CommandTileState, 'ready'>, string>> = {
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
      {copy[state]}
    </span>
  );
}

function MetricRow({
  metrics,
}: {
  metrics: readonly CommandTileMetric[];
}): React.JSX.Element {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 xl:grid-cols-3">
      {metrics.map((metric) => (
        <div key={metric.label} className="min-w-0">
          <dt className="truncate text-2xs uppercase tracking-wide text-muted-foreground">
            {metric.label}
          </dt>
          <dd
            className={cn(
              'tabular mt-0.5 font-mono text-xl font-semibold leading-none',
              TONE_CLASS[metric.tone ?? 'default'],
            )}
          >
            {metric.value === null ? (
              <>
                <span aria-hidden="true" className="text-muted-foreground">
                  &mdash;
                </span>
                <span className="sr-only">No value available yet</span>
              </>
            ) : (
              metric.value
            )}
          </dd>
          {metric.hint === undefined ? null : (
            <p className="mt-1 truncate text-2xs text-muted-foreground">{metric.hint}</p>
          )}
        </div>
      ))}
    </dl>
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

/** Stable, collision-resistant enough id fragment for a fixed set of tile titles. */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}
