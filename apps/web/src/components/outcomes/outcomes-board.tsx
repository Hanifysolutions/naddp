'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, Lock, ShieldCheck, Sparkles } from 'lucide-react';
import {
  isApiError,
  type OutcomeSection,
  type OutcomesBoard as Board,
} from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { HeroThreadBand } from '@/components/outcomes/hero-thread';
import { formatCount } from '@/components/outcomes/outcome-format';
import { OutcomeSectionCard } from '@/components/outcomes/outcome-section';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchOutcomesBoard } from '@/lib/api-queries';
import { CLASSIFICATION_LABELS, ROLE_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * The Unified Outcomes board: the demo's closing screen, and the answer to its closing question.
 *
 * Not "can AI chat", but "can this mission see, decide, execute and measure from one governed
 * system". One `GET /v1/outcomes` returns every domain's outcomes and the corridor running through
 * them - and each section, figure and thread step arrives with the authorisation it was counted
 * under, because each was counted in its own domain under its own check. The screen says so in
 * plain words at the top, and every figure says which domain counted it.
 *
 * **Nothing here is computed.** Figures, tones, the readable-domain count and every sentence of
 * fact come from the API; this view formats numbers and lays them out. Switching demo identity
 * re-queries and the board recomposes: a withheld domain renders as a refusal, never as zeros.
 */

/** Grid spans per section, on the command centre's twelve-column executive grid. */
const SECTION_LAYOUT: Readonly<
  Record<OutcomeSection['key'], { readonly span: string; readonly figures: string }>
> = {
  bilateral: { span: 'md:col-span-3 xl:col-span-4', figures: 'grid-cols-1' },
  citizen_service: { span: 'md:col-span-3 xl:col-span-4', figures: 'grid-cols-1' },
  diaspora: {
    span: 'md:col-span-6 xl:col-span-4',
    figures: 'grid-cols-1 md:grid-cols-3 xl:grid-cols-1',
  },
  relationships: {
    span: 'md:col-span-3 xl:col-span-6',
    figures: 'grid-cols-1 xl:grid-cols-2',
  },
  meetings: {
    span: 'md:col-span-3 xl:col-span-6',
    figures: 'grid-cols-1 xl:grid-cols-2',
  },
};

const SECTION_GRID = 'grid grid-cols-1 gap-4 md:grid-cols-6 xl:grid-cols-12';

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && [401, 403, 404, 422].includes(error.status)) return false;
  return failureCount < 1;
}

export function OutcomesBoardSkeleton(): React.JSX.Element {
  return (
    <div className="px-4 py-4 laptop:px-6 laptop:py-5" aria-busy="true">
      <Skeleton className="h-6 w-52" />
      <Skeleton className="mt-2 h-4 w-[40rem] max-w-full" />
      <Skeleton className="mt-5 h-16 w-full rounded-lg" />
      <Skeleton className="mt-4 h-72 w-full rounded-lg" />
      <div className={cn('mt-6', SECTION_GRID)}>
        {Object.entries(SECTION_LAYOUT).map(([key, layout]) => (
          <Skeleton key={key} className={cn('h-64 rounded-lg', layout.span)} />
        ))}
      </div>
      <span className="sr-only" role="status">
        Loading the unified outcomes board
      </span>
    </div>
  );
}

export function OutcomesBoard(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const board = useQuery({
    queryKey: queryKeys.outcomesBoard(role),
    queryFn: ({ signal }) => fetchOutcomesBoard(signal),
    // Gated on identity only. What each domain returns is decided by the API, on the record.
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <div className="px-4 py-4 laptop:px-6 laptop:py-5">
        <Alert variant="warning" role="status">
          <Lock aria-hidden="true" />
          <AlertTitle className="leading-snug text-warn-ink">
            No demo identity resolved
          </AlertTitle>
          <AlertDescription>
            Choose a role to see the mission&apos;s outcomes. Nothing is counted without
            one.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  if (board.isPending) return <OutcomesBoardSkeleton />;

  if (board.isError) {
    const apiError = isApiError(board.error) ? board.error : null;
    const forbidden = apiError !== null && apiError.status === 403;
    return (
      <div className="space-y-4 px-4 py-4 laptop:px-6 laptop:py-5">
        <h1 className="text-xl font-semibold text-ink">Unified outcomes</h1>
        <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
          {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <AlertTitle
            className={forbidden ? 'leading-snug text-warn-ink' : 'leading-snug'}
          >
            {forbidden
              ? 'This role may not open the outcomes board'
              : 'The outcomes board could not be loaded'}
          </AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            <p>
              {apiError === null ? 'The API did not answer.' : apiError.message}
              {apiError !== null && apiError.missingPermissions.length > 0
                ? ` Missing: ${apiError.missingPermissions.join(', ')}.`
                : null}
            </p>
            <p className="mt-2">
              These outcomes are unavailable rather than zero.
              {forbidden
                ? ' The refusal was made by the API, not by this page, and it was written to the audit log.'
                : null}
            </p>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <BoardBody
      data={board.data}
      roleLabel={ROLE_LABELS[role]}
      isFetching={board.isFetching}
    />
  );
}

function BoardBody({
  data,
  roleLabel,
  isFetching,
}: {
  data: Board;
  roleLabel: string;
  isFetching: boolean;
}): React.JSX.Element {
  const domainsId = React.useId();
  return (
    <div className="px-4 py-4 laptop:px-6 laptop:py-5">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold leading-tight text-ink">
            Unified outcomes
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-700">
            Can this mission see, decide, execute and measure from one governed system?
            Every function&apos;s outcomes in one frame, each counted by the API in its
            own domain under your permissions and your clearance before it is returned.
          </p>
        </div>

        <div className="flex flex-col items-start gap-1.5 laptop:items-end">
          {data.readable_classifications.length === 0 ? null : (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-label text-slate-700">Cleared to read</span>
              {data.readable_classifications.map((zone) => (
                <Badge key={zone} variant="outline" className="text-2xs font-medium">
                  {CLASSIFICATION_LABELS[zone]}
                </Badge>
              ))}
            </div>
          )}
          <GeneratedAt value={data.generated_at} isFetching={isFetching} />
        </div>
      </header>

      <section
        aria-label="How this board is governed"
        className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2 rounded-lg border border-line bg-card px-4 py-3"
      >
        <div className="flex min-w-0 items-start gap-2.5">
          <ShieldCheck
            aria-hidden="true"
            className="mt-0.5 h-4 w-4 shrink-0 text-accent"
          />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-ink">
              The picture is unified; the permissions are not.
            </p>
            <p className="mt-0.5 max-w-[80ch] text-label leading-snug text-slate-700">
              Each figure is counted in its own domain, under its own authorisation. A
              domain your role may not read is withheld and never queried - it is a
              refusal, not a count of zero - and nothing from it reaches another
              domain&apos;s figures.
            </p>
          </div>
        </div>
        <p className="tabular text-sm text-slate-700">
          <span className="font-semibold text-ink">
            {formatCount(data.domains_readable)} of {formatCount(data.domains_total)}
          </span>{' '}
          domains readable as {roleLabel}
        </p>
      </section>

      <HeroThreadBand thread={data.thread} />

      <section aria-labelledby={domainsId} className="mt-6">
        <h2
          id={domainsId}
          className="mb-3 text-base font-semibold leading-tight text-ink"
        >
          Outcomes by domain
        </h2>
        <div className={SECTION_GRID}>
          {data.sections.map((section) => (
            <OutcomeSectionCard
              key={section.key}
              section={section}
              className={SECTION_LAYOUT[section.key].span}
              figureGridClass={SECTION_LAYOUT[section.key].figures}
            />
          ))}
        </div>
      </section>

      <p className="mt-4 flex items-start gap-2 text-2xs leading-relaxed text-slate-700">
        <Sparkles aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          Every record in this environment is synthetic. Figures come from{' '}
          <code className="font-mono">GET /v1/outcomes</code>, which counts each domain in
          its own queries under its own authorisation; nothing on this screen is computed
          in the browser beyond formatting.
        </span>
      </p>
    </div>
  );
}

/** When the figures were counted, on the API's clock, formatted in the viewer's locale. */
function GeneratedAt({
  value,
  isFetching,
}: {
  value: string;
  isFetching: boolean;
}): React.JSX.Element | null {
  const [formatted, setFormatted] = React.useState<string | null>(null);

  React.useEffect(() => {
    const parsed = new Date(value);
    setFormatted(
      Number.isNaN(parsed.getTime())
        ? null
        : new Intl.DateTimeFormat(undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
          }).format(parsed),
    );
  }, [value]);

  if (formatted === null) return null;
  return (
    <p className="tabular text-2xs text-slate-700">
      Counted {formatted}.{isFetching ? ' Refreshing now.' : ''}
    </p>
  );
}
