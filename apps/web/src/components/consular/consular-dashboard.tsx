'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, Lock } from 'lucide-react';
import { isApiError, type CaseRow, type ConsularDashboard } from '@naddp/contracts';

import { DistributionBars } from '@/components/command/distribution-bars';
import { CaseStatusChip, SlaChip } from '@/components/consular/case-chips';
import { slaPhrase, slaTick } from '@/components/consular/consular-format';
import { useDemoSession } from '@/components/layout/session-provider';
import { formatDateTime } from '@/components/meetings/meeting-format';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { fetchConsularDashboard } from '@/lib/api-queries';
import type { Distribution, DistributionEntry } from '@/lib/command-view';
import { CASE_STATUS_LABELS, CASE_STATUS_ORDER, PRIORITY_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * Consular command: caseload, ageing, service-level risk and volumes by type.
 *
 * **The queue is the screen.** Open cases arrive from the API most at risk first - breached,
 * then due soon, then on track, then paused - and are rendered in that order, never re-sorted
 * here. A breached case carries the `--risk` tick and a near-breach the `--warn` tick, so the
 * one urgent emergency-travel-document case that is about to breach is the second thing a
 * reader's eye lands on after the counts that say it exists.
 *
 * **Every figure is what this reader may read.** The clearance predicate is in the API's SQL,
 * so a count is never "of N". A role without `read:consular_case` gets a refusal panel in the
 * server's own words - never an empty caseload, because "no cases" and "not yours to see" are
 * different statements and the second is a control working.
 *
 * **Priority is shown as confirmed or not.** A case still in NEW has had no human triage, so its
 * column says "Awaiting triage" rather than repeating the intake default as if someone chose it.
 */

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

export function ConsularDashboardSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-5" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-4 w-[40rem] max-w-full" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-20 rounded-md" />
        ))}
      </div>
      <Skeleton className="h-96 rounded-lg" />
      <span className="sr-only" role="status">
        Loading the consular dashboard
      </span>
    </div>
  );
}

export function ConsularDashboardView(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const dashboard = useQuery({
    queryKey: queryKeys.consularDashboard(role),
    queryFn: ({ signal }) => fetchConsularDashboard(signal),
    // Gated on identity only: a role without read:consular_case is refused by the API, on the
    // record, rather than by this page.
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">No demo identity resolved</AlertTitle>
        <AlertDescription>
          Choose a role to see the consular caseload. Nothing is shown without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (dashboard.isPending) return <ConsularDashboardSkeleton />;

  if (dashboard.isError) {
    const apiError = isApiError(dashboard.error) ? dashboard.error : null;
    const forbidden = apiError !== null && apiError.status === 403;
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-ink">Consular</h1>
        <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
          {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <AlertTitle className={forbidden ? 'leading-snug text-warn-ink' : 'leading-snug'}>
            {forbidden
              ? 'This role may not read consular cases'
              : 'The consular dashboard could not be loaded'}
          </AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            <p>
              {apiError === null ? 'The API did not answer.' : apiError.message}
              {apiError !== null && apiError.missingPermissions.length > 0
                ? ` Missing: ${apiError.missingPermissions.join(', ')}.`
                : null}
            </p>
            {forbidden ? (
              <p className="mt-2">
                Consular case files are compartmented. The refusal was made by the API, not by
                this page, and it was written to the audit log. This is not a count of zero.
              </p>
            ) : null}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <DashboardBody dashboard={dashboard.data} />;
}

/** A count from a server-supplied map; a bucket the API did not report stays unreported. */
function countOf(counts: Readonly<Partial<Record<string, number>>>, key: string): number | null {
  const value = counts[key];
  return value === undefined ? null : value;
}

function toDistribution(caption: string, entries: readonly DistributionEntry[]): Distribution {
  const peak = entries.reduce(
    (largest, entry) => (entry.value === null ? largest : Math.max(largest, entry.value)),
    0,
  );
  return { caption, entries, peak };
}

function DashboardBody({ dashboard }: { dashboard: ConsularDashboard }): React.JSX.Element {
  const breached = countOf(dashboard.by_sla_state, 'BREACHED');
  const dueSoon = countOf(dashboard.by_sla_state, 'DUE_SOON');
  const paused = countOf(dashboard.by_sla_state, 'PAUSED');

  const statusDistribution = toDistribution(
    'All cases by status',
    CASE_STATUS_ORDER.map((status) => ({
      key: status,
      label: CASE_STATUS_LABELS[status],
      value: countOf(dashboard.by_status, status),
    })),
  );
  const ageingDistribution = toDistribution(
    'Open cases by chargeable age',
    dashboard.ageing.map((bucket) => ({ key: bucket.label, label: bucket.label, value: bucket.count })),
  );

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-ink">Consular</h1>
        <p className="tabular max-w-[80ch] text-sm leading-relaxed text-slate-700">
          {dashboard.open_total} open {dashboard.open_total === 1 ? 'case' : 'cases'} among the{' '}
          {dashboard.total} you are cleared to read. Service levels are counted in business days
          and pause while a case waits on the citizen. Every consular read is written to the
          audit log.
        </p>
        <p className="text-label text-slate-700">
          Measured <span className="tabular">{formatDateTime(dashboard.measured_at)}</span>.
          Synthetic demonstration data.
        </p>
      </header>

      <dl className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Figure label="Open cases" value={dashboard.open_total} />
        <Figure
          label="Awaiting triage"
          hint="No human has confirmed them yet"
          value={dashboard.awaiting_triage}
        />
        <Figure label="Breached" value={breached} tone={breached !== null && breached > 0 ? 'risk' : 'default'} />
        <Figure label="Due soon" value={dueSoon} tone={dueSoon !== null && dueSoon > 0 ? 'warn' : 'default'} />
        <Figure label="Paused on the citizen" value={paused} />
      </dl>

      <RiskQueue rows={dashboard.queue} />

      <div className="grid grid-cols-1 gap-4 laptop:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <h2 className="text-base font-semibold text-ink">Caseload</h2>
          </CardHeader>
          <CardContent>
            <DistributionBars distribution={statusDistribution} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <h2 className="text-base font-semibold text-ink">Ageing</h2>
          </CardHeader>
          <CardContent>
            <DistributionBars distribution={ageingDistribution} />
            <p className="mt-2 text-2xs leading-snug text-slate-700">
              Chargeable business days: time paused on the citizen is not counted.
            </p>
          </CardContent>
        </Card>
        <TypeVolumes dashboard={dashboard} />
      </div>
    </div>
  );
}

const FIGURE_TONE = {
  default: 'text-ink',
  warn: 'text-warn-ink',
  risk: 'text-risk',
} as const;

const FIGURE_TICK = {
  default: null,
  warn: 'tick-warn',
  risk: 'tick-risk',
} as const;

function Figure({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string;
  value: number | null;
  hint?: string;
  tone?: keyof typeof FIGURE_TONE;
}): React.JSX.Element {
  return (
    <div className="overflow-hidden rounded-md border border-line bg-card">
      <div className={cn('flex h-full flex-col-reverse justify-end gap-1 px-3 py-2.5', FIGURE_TICK[tone])}>
        <dt className="min-w-0">
          <span className="block text-label text-slate-700">{label}</span>
          {hint === undefined ? null : (
            <span className="mt-0.5 block text-2xs text-slate-700">{hint}</span>
          )}
        </dt>
        <dd className={cn('tabular font-display text-figure-sm font-semibold leading-none', FIGURE_TONE[tone])}>
          {value === null ? (
            <>
              <span aria-hidden="true">&mdash;</span>
              <span className="sr-only">Not reported by the API</span>
            </>
          ) : (
            value
          )}
        </dd>
      </div>
    </div>
  );
}

function priorityCell(row: CaseRow): string {
  return row.status === 'NEW' ? 'Awaiting triage' : PRIORITY_LABELS[row.priority];
}

function RiskQueue({ rows }: { rows: readonly CaseRow[] }): React.JSX.Element {
  const headingId = React.useId();

  return (
    <section aria-labelledby={headingId} className="space-y-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={headingId} className="flex items-baseline gap-2 text-base font-semibold text-ink">
          Service-level risk
          <span className="tabular text-label font-normal text-slate-700">
            {rows.length}
            <span className="sr-only"> open case{rows.length === 1 ? '' : 's'}</span>
          </span>
        </h2>
        <p className="text-label text-slate-700">
          Most at risk first: breached, due soon, on track, then paused.
        </p>
      </div>

      {rows.length === 0 ? (
        <p className="rounded-lg border border-line bg-card px-4 py-6 text-center text-sm text-slate-700">
          No open cases that you are cleared to read.
        </p>
      ) : (
        <>
          <div className="hidden overflow-hidden rounded-lg border border-line bg-card md:block">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Case</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Priority</TableHead>
                  <TableHead>Service level</TableHead>
                  <TableHead>Due</TableHead>
                  <TableHead>Officer</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className={cn('min-w-[15rem] py-2.5', slaTick(row.sla.state))}>
                      <Link
                        href={`/consular/cases/${row.id}`}
                        className="font-medium leading-snug text-accent underline underline-offset-2"
                      >
                        {row.case_type_label}
                      </Link>
                      <span className="mt-0.5 block font-mono text-2xs text-slate-700">
                        {row.public_ref}
                      </span>
                    </TableCell>
                    <TableCell>
                      <CaseStatusChip status={row.status} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">{priorityCell(row)}</TableCell>
                    <TableCell>
                      <div className="flex flex-col items-start gap-1">
                        <SlaChip state={row.sla.state} />
                        <span className="tabular text-label text-slate-700">{slaPhrase(row.sla)}</span>
                      </div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm tabular-nums">
                      {row.sla.due_at === null ? (
                        <span className="text-slate-700">No due date</span>
                      ) : (
                        formatDateTime(row.sla.due_at)
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      {row.assigned_officer_name ?? (
                        <span className="text-slate-700">Not assigned</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <ul className="space-y-2 md:hidden">
            {rows.map((row) => (
              <li key={row.id} className="overflow-hidden rounded-lg border border-line bg-card">
                <div className={cn('p-3', slaTick(row.sla.state))}>
                  <Link
                    href={`/consular/cases/${row.id}`}
                    className="text-sm font-medium leading-snug text-accent underline underline-offset-2"
                  >
                    {row.case_type_label}
                  </Link>
                  <span className="block font-mono text-2xs text-slate-700">{row.public_ref}</span>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <SlaChip state={row.sla.state} />
                    <CaseStatusChip status={row.status} />
                  </div>
                  <p className="tabular mt-1 text-label text-slate-700">
                    {slaPhrase(row.sla)}. {priorityCell(row)}.
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function TypeVolumes({ dashboard }: { dashboard: ConsularDashboard }): React.JSX.Element {
  const withCases = dashboard.by_type.filter((volume) => volume.total > 0);
  const omitted = dashboard.by_type.length - withCases.length;

  return (
    <Card>
      <CardHeader className="pb-2">
        <h2 className="text-base font-semibold text-ink">Volumes by type</h2>
      </CardHeader>
      <CardContent>
        {withCases.length === 0 ? (
          <p className="text-sm text-slate-700">No cases on record that you are cleared to read.</p>
        ) : (
          <table className="w-full text-left text-2xs">
            <thead>
              <tr className="text-slate-700">
                <th scope="col" className="pb-1 font-normal">
                  Type
                </th>
                <th scope="col" className="pb-1 text-right font-normal">
                  Open
                </th>
                <th scope="col" className="pb-1 text-right font-normal">
                  All
                </th>
                <th scope="col" className="pb-1 text-right font-normal">
                  Breached
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {withCases.map((volume) => (
                <tr key={volume.code}>
                  <th scope="row" className="py-1 pr-2 font-normal text-ink">
                    {volume.label}
                  </th>
                  <td className="tabular py-1 text-right text-ink">{volume.open_total}</td>
                  <td className="tabular py-1 text-right text-ink">{volume.total}</td>
                  <td
                    className={cn(
                      'tabular py-1 text-right',
                      volume.breached > 0 ? 'font-semibold text-risk' : 'text-ink',
                    )}
                  >
                    {volume.breached}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {omitted > 0 ? (
          <p className="mt-2 text-2xs leading-snug text-slate-700">
            {omitted} case {omitted === 1 ? 'type has' : 'types have'} no cases on record and{' '}
            {omitted === 1 ? 'is' : 'are'} not listed.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
