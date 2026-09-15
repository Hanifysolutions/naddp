'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, Lock } from 'lucide-react';
import { isApiError, type MeetingRow } from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { FollowupStatusChip } from '@/components/meetings/followup-status-chip';
import { MeetingField } from '@/components/meetings/meeting-field';
import { formatMeetingDay, formatTimeRange } from '@/components/meetings/meeting-format';
import { MeetingsSubnav } from '@/components/meetings/meetings-subnav';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { fetchMeetings } from '@/lib/api-queries';
import { CLASSIFICATION_LABELS, MEETING_TYPE_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * The meeting diary: upcoming and recent, each row carrying its follow-up's state.
 *
 * **Two renderings of one list.** A table on a laptop, where a reader compares dates, owners
 * and follow-up states down a column; stacked cards on a phone, where a seven-column table
 * would scroll sideways. Both render the same `MeetingRow[]` from one request.
 *
 * **`total` is what this reader may read.** The clearance predicate is in the API's SQL, so
 * the Confidential meeting is simply absent for a Trade Officer - the standfirst says "you are
 * cleared to read" and never "of N", because the difference would be the leak.
 *
 * The follow-up column is the diary's governance at a glance: the status chip marks the one
 * follow-up awaiting a named human in warn, and a row with no follow-up says "None drafted"
 * rather than a blank cell a reader might take for a rendering fault.
 */

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

export function MeetingIndexSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-5" aria-busy="true">
      <Skeleton className="h-8 w-48" />
      <div className="space-y-2">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-4 w-[34rem] max-w-full" />
      </div>
      <Skeleton className="h-44 rounded-lg" />
      <Skeleton className="h-80 rounded-lg" />
      <span className="sr-only" role="status">
        Loading meetings
      </span>
    </div>
  );
}

export function MeetingIndex(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const meetings = useQuery({
    queryKey: queryKeys.meetings(role),
    queryFn: ({ signal }) => fetchMeetings(signal),
    // Gated on identity only: a role without read:meeting is refused by the API, on the
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
          Choose a role to see the meeting diary. Nothing is shown without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (meetings.isPending) return <MeetingIndexSkeleton />;

  if (meetings.isError) {
    const apiError = isApiError(meetings.error) ? meetings.error : null;
    const forbidden = apiError !== null && apiError.status === 403;
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-ink">Meetings</h1>
        <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
          {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <AlertTitle className={forbidden ? 'leading-snug text-warn-ink' : 'leading-snug'}>
            {forbidden ? 'This role may not read meetings' : 'The meetings could not be loaded'}
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
                The refusal was made by the API, not by this page, and it was written to the
                audit log.
              </p>
            ) : null}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const data = meetings.data;

  return (
    <div className="space-y-6">
      <MeetingsSubnav />

      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-ink">Meetings</h1>
        <p className="tabular max-w-[72ch] text-sm leading-relaxed text-slate-700">
          {data.total} meeting{data.total === 1 ? '' : 's'} you are cleared to read. Each
          carries its pre-read before and its follow-up after, and no follow-up is sent until
          a named human approves it.
        </p>
      </header>

      <MeetingSection
        title="Upcoming"
        rows={data.upcoming}
        empty="No upcoming meetings that you are cleared to read."
      />
      <MeetingSection
        title="Recent"
        rows={data.recent}
        empty="No recent meetings that you are cleared to read."
      />
    </div>
  );
}

function MeetingSection({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: readonly MeetingRow[];
  empty: string;
}): React.JSX.Element {
  const headingId = React.useId();

  return (
    <section aria-labelledby={headingId} className="space-y-2.5">
      <h2 id={headingId} className="flex items-baseline gap-2 text-base font-semibold text-ink">
        {title}
        <span className="tabular text-label font-normal text-slate-700">
          {rows.length}
          <span className="sr-only"> meeting{rows.length === 1 ? '' : 's'}</span>
        </span>
      </h2>

      {rows.length === 0 ? (
        <p className="rounded-lg border border-line bg-card px-4 py-6 text-center text-sm text-slate-700">
          {empty}
        </p>
      ) : (
        <>
          <div className="hidden overflow-hidden rounded-lg border border-line bg-card md:block">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Meeting</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>When</TableHead>
                  <TableHead>Counterpart</TableHead>
                  <TableHead>Owner</TableHead>
                  <TableHead>Follow-up</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="min-w-[16rem] max-w-[26rem] py-2.5">
                      <Link
                        href={`/meetings/${row.id}`}
                        className="font-medium leading-snug text-accent underline underline-offset-2"
                      >
                        {row.title}
                      </Link>
                      <span className="mt-0.5 flex flex-wrap gap-x-3 text-label text-slate-700">
                        <span>{CLASSIFICATION_LABELS[row.classification]}</span>
                        {row.has_pre_read ? <span>Pre-read prepared</span> : null}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      {MEETING_TYPE_LABELS[row.meeting_type]}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm tabular-nums">
                      <span className="block text-ink">{formatMeetingDay(row.scheduled_start)}</span>
                      <span className="block text-label text-slate-700">
                        {formatTimeRange(row.scheduled_start, row.scheduled_end)}
                      </span>
                    </TableCell>
                    <TableCell className="max-w-[14rem] text-sm">
                      <Counterpart row={row} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm">
                      {row.owner_name ?? 'Not recorded'}
                    </TableCell>
                    <TableCell>
                      <FollowupCell row={row} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <ul className="space-y-2 md:hidden">
            {rows.map((row) => (
              <li key={row.id} className="rounded-lg border border-line bg-card p-3">
                <Link
                  href={`/meetings/${row.id}`}
                  className="text-sm font-medium leading-snug text-accent underline underline-offset-2"
                >
                  {row.title}
                </Link>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <FollowupCell row={row} />
                  <Badge variant="outline" className="text-2xs font-medium">
                    {CLASSIFICATION_LABELS[row.classification]}
                  </Badge>
                </div>
                <dl className="mt-2 space-y-1">
                  <MeetingField label="Type">{MEETING_TYPE_LABELS[row.meeting_type]}</MeetingField>
                  <MeetingField label="When">
                    <span className="tabular">
                      {formatMeetingDay(row.scheduled_start)},{' '}
                      {formatTimeRange(row.scheduled_start, row.scheduled_end)}
                    </span>
                  </MeetingField>
                  <MeetingField label="Counterpart">
                    <Counterpart row={row} />
                  </MeetingField>
                  <MeetingField label="Owner">{row.owner_name ?? 'Not recorded'}</MeetingField>
                </dl>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function Counterpart({ row }: { row: MeetingRow }): React.JSX.Element {
  const organisationId = row.organisation_id ?? null;
  const organisationName = row.organisation_name ?? null;
  if (organisationId === null) return <span className="text-slate-700">None recorded</span>;
  if (organisationName === null) {
    // The tie exists and is shown; the organisation sits above this reader's zone.
    return <span className="text-slate-700">Withheld at your clearance</span>;
  }
  return (
    <Link
      href={`/stakeholders/organisations/${organisationId}`}
      className="text-accent underline underline-offset-2"
    >
      {organisationName}
    </Link>
  );
}

function FollowupCell({ row }: { row: MeetingRow }): React.JSX.Element {
  const followup = row.followup ?? null;
  if (followup === null) return <span className="text-label text-slate-700">None drafted</span>;
  return <FollowupStatusChip status={followup.status} />;
}
