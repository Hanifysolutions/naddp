'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Sparkles } from 'lucide-react';
import { isApiError, type ApiError } from '@naddp/contracts';

import { CommandTile } from '@/components/command/command-tile';
import { AuditTrailStrip } from '@/components/command/audit-trail-strip';
import { OpportunityStrip } from '@/components/command/opportunity-strip';
import { useDemoSession } from '@/components/layout/session-provider';
import { Badge } from '@/components/ui/badge';
import { fetchAuditEvents, fetchCommandToday, fetchOpportunities } from '@/lib/api-queries';
import {
  buildBoard,
  COMMAND_GRID_CLASS,
  type BoardInput,
  type TileId,
  type TileView,
} from '@/lib/command-view';
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * The command centre, wired to the API.
 *
 * One `GET /v1/command/today` supplies all six tiles, because that endpoint is already the
 * role-scoped aggregate - fetching six times would ask the same authorisation question six
 * times and could return six mutually inconsistent snapshots.
 *
 * Two further reads hang off it, each gated on a permission this session actually holds:
 *
 *   `GET /v1/opportunities`   the rows behind the pipeline counts, so the AI-proposed hero
 *                             opportunity can be badged as proposed rather than reported.
 *   `GET /v1/audit/events`    the newest governed actions, for the roles that may read the
 *                             log at all.
 *
 * The permission check before each is a courtesy to the console, not a control: asking
 * without the permission would produce an expected 403 on every load for four of the six
 * roles, and a console full of expected errors is how a real one gets missed. The API
 * re-checks regardless and is the only authority.
 */

/** How many opportunity rows the tile shows. Small on purpose: this is a summary. */
const OPPORTUNITY_PREVIEW_LIMIT = 4;

/** How many audit rows the Today tile shows. */
const AUDIT_PREVIEW_LIMIT = 4;

export function CommandBoard(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const today = useQuery({
    queryKey: queryKeys.commandToday(role),
    queryFn: ({ signal }) => fetchCommandToday(signal),
    // No session means a certain 403. Fail closed and say so, rather than firing a request
    // whose answer we already know.
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  const opportunities = useQuery({
    queryKey: queryKeys.opportunities(role, OPPORTUNITY_PREVIEW_LIMIT),
    queryFn: ({ signal }) => fetchOpportunities(OPPORTUNITY_PREVIEW_LIMIT, signal),
    enabled: role !== null && session.can('read:opportunity'),
    retry: retryUnlessRefused,
  });

  const audit = useQuery({
    queryKey: queryKeys.auditEvents(role, AUDIT_PREVIEW_LIMIT),
    queryFn: ({ signal }) => fetchAuditEvents(AUDIT_PREVIEW_LIMIT, signal),
    enabled: role !== null && session.can('read:audit'),
    retry: retryUnlessRefused,
  });

  const input = React.useMemo<BoardInput>(() => {
    if (role === null) {
      return {
        kind: 'error',
        error: {
          status: 403,
          message:
            'No demo identity has been assumed, so the API has nothing to authorise. Choose a role in the top bar.',
          traceId: null,
          requestId: null,
          code: 'permission_denied',
          missingPermissions: [],
        },
      };
    }
    if (today.data !== undefined) return { kind: 'ready', data: today.data };
    if (today.error !== null) return { kind: 'error', error: asApiError(today.error) };
    return { kind: 'loading' };
  }, [role, today.data, today.error]);

  const tiles = React.useMemo(() => buildBoard(input), [input]);
  const byId = React.useMemo(() => indexTiles(tiles), [tiles]);

  const generatedAt = today.data?.generated_at ?? null;
  const zones = today.data?.readable_classifications ?? [];

  return (
    <div className="px-4 py-4 laptop:px-6 laptop:py-6">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold leading-tight tracking-tight text-foreground">
            Command centre
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            One governed picture of the mission. Every figure is counted by the API under
            your permissions and your clearance before it is returned - a tile you may not
            read is never queried, and never shown as zero.
          </p>
        </div>

        <div className="flex flex-col items-start gap-1.5 laptop:items-end">
          {zones.length === 0 ? null : (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-2xs uppercase tracking-wide text-muted-foreground">
                Counted across
              </span>
              {zones.map((zone) => (
                <Badge key={zone} variant="outline" className="text-2xs font-medium">
                  {CLASSIFICATION_LABELS[zone]}
                </Badge>
              ))}
            </div>
          )}
          <GeneratedAt value={generatedAt} isFetching={today.isFetching} />
        </div>
      </header>

      <section aria-label="Executive tiles" className={COMMAND_GRID_CLASS}>
        <CommandTile view={byId.today}>
          <AuditTrailStrip
            canRead={session.can('read:audit')}
            data={audit.data ?? null}
            error={audit.error === null ? null : asApiError(audit.error)}
            isPending={audit.isPending && audit.fetchStatus !== 'idle'}
          />
        </CommandTile>

        <CommandTile view={byId['opportunity-health']}>
          <OpportunityStrip
            canRead={session.can('read:opportunity')}
            data={opportunities.data ?? null}
            error={opportunities.error === null ? null : asApiError(opportunities.error)}
            isPending={opportunities.isPending && opportunities.fetchStatus !== 'idle'}
          />
        </CommandTile>

        <CommandTile view={byId['citizen-service-health']} />
        <CommandTile view={byId['relationship-health']} />
        <CommandTile view={byId['diaspora-capability']} />
        <CommandTile view={byId['mission-outcomes']} />
      </section>

      <p className="mt-4 flex items-start gap-2 text-2xs leading-relaxed text-muted-foreground">
        <Sparkles aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          Every record in this environment is synthetic. Counts come from{' '}
          <code className="font-mono">GET /v1/command/today</code>, which applies the
          permission and classification predicates inside its SQL; nothing on this screen is
          computed in the browser beyond formatting.
        </span>
      </p>
    </div>
  );
}

/**
 * Retry transient failures only.
 *
 * A 401, 403 or 404 is a settled answer: retrying it wastes the first seconds of a demo and
 * writes a second denial into the audit log for no reason. Anything else gets one retry,
 * which absorbs a cold API container without turning a real failure into a long silent hang.
 */
function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && [401, 403, 404, 422].includes(error.status)) return false;
  return failureCount < 1;
}

/** Normalise whatever a query rejected with. A thrown non-ApiError is still reportable. */
function asApiError(error: unknown): ApiError {
  if (isApiError(error)) return error;
  return {
    status: 0,
    message:
      error instanceof Error ? error.message : 'The request failed for an unknown reason.',
    traceId: null,
    requestId: null,
    code: null,
    missingPermissions: [],
  };
}

/**
 * Index the built tiles by id.
 *
 * `buildBoard` always returns all six, but the type system does not know that, and
 * `noUncheckedIndexedAccess` is on. Rather than assert, this throws if a tile is genuinely
 * missing - a bug that would otherwise render as a silently absent card.
 */
function indexTiles(tiles: readonly TileView[]): Readonly<Record<TileId, TileView>> {
  const index = new Map<TileId, TileView>(tiles.map((tile) => [tile.definition.id, tile]));
  const required: readonly TileId[] = [
    'today',
    'opportunity-health',
    'citizen-service-health',
    'relationship-health',
    'diaspora-capability',
    'mission-outcomes',
  ];
  const result: Partial<Record<TileId, TileView>> = {};
  for (const id of required) {
    const tile = index.get(id);
    if (tile === undefined) {
      throw new Error(`Command board is missing the "${id}" tile definition.`);
    }
    result[id] = tile;
  }
  return result as Record<TileId, TileView>;
}

/**
 * When the counts were taken, on the API's clock.
 *
 * Rendered only once data has arrived, and formatted in the viewer's locale on the client.
 * A dashboard without a timestamp invites the audience to assume it is live; this one says
 * exactly how old it is.
 */
function GeneratedAt({
  value,
  isFetching,
}: {
  value: string | null;
  isFetching: boolean;
}): React.JSX.Element | null {
  const [formatted, setFormatted] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (value === null) {
      setFormatted(null);
      return;
    }
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
    <p className="font-mono text-2xs text-muted-foreground">
      Counted {formatted}
      {isFetching ? ' · refreshing' : ''}
    </p>
  );
}
