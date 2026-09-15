'use client';

import * as React from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, Lock, Sparkles } from 'lucide-react';
import { isApiError, type NaddpRole } from '@naddp/contracts';

import {
  AuditLog,
  AuditLogSkeleton,
  NO_AUDIT_FILTERS,
  type AuditLogFilters,
} from '@/components/governance/audit-log';
import { ChainVerificationPanel } from '@/components/governance/chain-verification';
import { useDemoSession } from '@/components/layout/session-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchAuditLog } from '@/lib/api-queries';
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * The Governance page: the append-only audit log, and the proof that it has not been edited.
 *
 * **One permission decides the screen.** `read:audit` is held by the Ambassador, the Deputy Head of
 * Mission and the platform administrator. A role without it is refused by the API, on the record,
 * and sees a calm refusal here rather than an empty log. ADMIN is the deliberate case in the other
 * direction: it reads the log without holding a single content read, so it can prove that governed
 * actions happened without reading what they were about - and its clearance keeps the
 * consular-sensitive rows out of its query entirely.
 *
 * **The trust moment is an action a viewer takes.** "Verify audit chain" asks the API to recompute
 * the whole chain and shows the verdict; nothing is claimed on page load.
 */

const PAGE_SIZE = 50;

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && [401, 403, 404, 422].includes(error.status)) return false;
  return failureCount < 1;
}

export function GovernanceConsoleSkeleton(): React.JSX.Element {
  return (
    <div className="px-4 py-4 laptop:px-6 laptop:py-5" aria-busy="true">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="mt-2 h-4 w-[40rem] max-w-full" />
      <Skeleton className="mt-5 h-28 w-full rounded-lg" />
      <div className="mt-6">
        <AuditLogSkeleton />
      </div>
    </div>
  );
}

export function GovernanceConsole(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  if (role === null) {
    return (
      <div className="px-4 py-4 laptop:px-6 laptop:py-5">
        <Alert variant="warning" role="status">
          <Lock aria-hidden="true" />
          <AlertTitle className="leading-snug text-warn-ink">
            No demo identity resolved
          </AlertTitle>
          <AlertDescription>
            Choose a role to open the audit log. Nothing is read without one.
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <ConsoleBody key={role} role={role} />;
}

function ConsoleBody({ role }: { role: NaddpRole }): React.JSX.Element {
  const [filters, setFilters] = React.useState<AuditLogFilters>(NO_AUDIT_FILTERS);

  const log = useInfiniteQuery({
    queryKey: queryKeys.auditLog(role, filters),
    queryFn: ({ pageParam, signal }) =>
      fetchAuditLog(
        {
          limit: PAGE_SIZE,
          cursor: pageParam,
          actorRole: filters.actorRole ?? undefined,
          action: filters.action ?? undefined,
          policyResult: filters.policyResult ?? undefined,
        },
        signal,
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: retryUnlessRefused,
  });

  const apiError = log.isError && isApiError(log.error) ? log.error : null;
  if (apiError !== null && apiError.status === 403) {
    return <Refused message={apiError.message} missing={apiError.missingPermissions} />;
  }

  const firstPage = log.data?.pages[0];
  const events = log.data?.pages.flatMap((page) => page.events) ?? [];
  const zones = firstPage?.applied_classifications ?? [];

  return (
    <div className="px-4 py-4 laptop:px-6 laptop:py-5">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold leading-tight text-ink">Governance</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-700">
            The append-only audit log: every consequential action, approval and refusal,
            recorded inside the same transaction as the change it describes. Entries are
            never edited or deleted - a correction is a new entry - and each is chained to
            the one before it.
          </p>
        </div>
        {zones.length === 0 ? null : (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-label text-slate-700">Cleared to read</span>
            {zones.map((zone) => (
              <Badge key={zone} variant="outline" className="text-2xs font-medium">
                {CLASSIFICATION_LABELS[zone]}
              </Badge>
            ))}
          </div>
        )}
      </header>

      <ChainVerificationPanel />

      {log.isError && apiError?.status !== 403 ? (
        <Alert variant="destructive" role="status" className="mt-6">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle className="leading-snug">
            The audit log could not be loaded
          </AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            {apiError === null ? 'The API did not answer.' : apiError.message} These
            events are unavailable rather than absent.
          </AlertDescription>
        </Alert>
      ) : (
        <AuditLog
          filters={filters}
          onFiltersChange={setFilters}
          events={events}
          totalMatching={firstPage?.total_matching ?? null}
          appliedClassifications={zones}
          isLoading={log.isPending}
          hasMore={log.hasNextPage}
          isLoadingMore={log.isFetchingNextPage}
          onLoadMore={() => void log.fetchNextPage()}
        />
      )}

      <p className="mt-4 flex items-start gap-2 text-2xs leading-relaxed text-slate-700">
        <Sparkles aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          Every record in this environment is synthetic. Rows come from{' '}
          <code className="font-mono">GET /v1/audit/events</code>, filtered and paged by
          the API under your clearance; the verdict comes from{' '}
          <code className="font-mono">GET /v1/audit/chain</code>. Nothing here is computed
          in the browser beyond formatting.
        </span>
      </p>
    </div>
  );
}

function Refused({
  message,
  missing,
}: {
  message: string;
  missing: readonly string[];
}): React.JSX.Element {
  return (
    <div className="space-y-4 px-4 py-4 laptop:px-6 laptop:py-5">
      <h1 className="text-xl font-semibold text-ink">Governance</h1>
      <Alert variant="warning" role="status">
        <Ban aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">
          This role may not read the audit log
        </AlertTitle>
        <AlertDescription className="max-w-[72ch] space-y-2">
          <p>
            {message}
            {missing.length > 0 ? ` Missing: ${missing.join(', ')}.` : null}
          </p>
          <p>
            The audit log is a control with its own permission, held by the Ambassador,
            the Deputy Head of Mission and the platform administrator. The refusal was
            made by the API, not by this page, and it was itself written to the audit log.
          </p>
        </AlertDescription>
      </Alert>
    </div>
  );
}
