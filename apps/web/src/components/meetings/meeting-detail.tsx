'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, FileText, Lock } from 'lucide-react';
import { isApiError, type MeetingAttendee, type MeetingDetail } from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { FollowupPanel } from '@/components/meetings/followup-panel';
import { MeetingField } from '@/components/meetings/meeting-field';
import {
  formatMeetingDay,
  formatTimeRange,
  zoneName,
} from '@/components/meetings/meeting-format';
import { PreReadPanel } from '@/components/meetings/pre-read-panel';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchMeeting } from '@/lib/api-queries';
import { CLASSIFICATION_LABELS, MEETING_TYPE_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * One meeting: the pre-read before it, and the follow-up after it.
 *
 * **Layout serves winning moment #2.** On a laptop the follow-up panel is the right column,
 * sticky under the top bar, so the Send button - and the block it turns into - is on screen
 * at 1440x900 without scrolling, while the pre-read, agenda and attendees scroll beside it.
 * The panel comes first in the DOM and is placed right by the grid: below the laptop
 * breakpoint the columns stack, and the governed action should not sit beneath a long
 * pre-read on a phone. Keyboard order follows the DOM, so it reaches the action first too.
 *
 * The meeting is one `GET /v1/meetings/{id}`. It is identity-scoped - the follow-up actions
 * are statements about this caller - so the query key carries the role and the query is not
 * gated on a permission: a 403 for a meeting above the reader's zone is the API's to make.
 */

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && [403, 404, 422].includes(error.status)) return false;
  return failureCount < 2;
}

export function MeetingDetailSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-5" aria-busy="true">
      <Skeleton className="h-4 w-24" />
      <Skeleton className="h-8 w-[40rem] max-w-full" />
      <Skeleton className="h-4 w-[52rem] max-w-full" />
      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1fr)_28rem] laptop:items-start">
        <Skeleton className="h-[30rem] rounded-lg laptop:col-start-2 laptop:row-start-1" />
        <Skeleton className="h-[44rem] rounded-lg laptop:col-start-1 laptop:row-start-1" />
      </div>
      {/* Skeletons are aria-hidden by design, so the region has to announce itself. */}
      <span className="sr-only" role="status">
        Loading the meeting
      </span>
    </div>
  );
}

export function MeetingDetailView({ meetingId }: { meetingId: string }): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const meeting = useQuery({
    queryKey: queryKeys.meeting(role, meetingId),
    queryFn: ({ signal }) => fetchMeeting(meetingId, signal),
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">No demo identity resolved</AlertTitle>
        <AlertDescription>
          Choose a role to open this meeting. Nothing is shown without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (meeting.isPending) return <MeetingDetailSkeleton />;

  if (meeting.isError) {
    const apiError = isApiError(meeting.error) ? meeting.error : null;

    if (apiError !== null && apiError.status === 403) {
      return (
        <div className="space-y-4">
          <BackLink />
          <Alert variant="warning" role="status">
            <Ban aria-hidden="true" />
            <AlertTitle className="leading-snug text-warn-ink">
              This meeting is not available to this role
            </AlertTitle>
            <AlertDescription className="max-w-[72ch]">
              <p>{apiError.message}</p>
              <p className="mt-2">
                The refusal was made by the API, not by this page, and it was written to the
                audit log.
              </p>
            </AlertDescription>
          </Alert>
        </div>
      );
    }

    if (apiError !== null && (apiError.status === 404 || apiError.status === 422)) {
      return (
        <div className="space-y-4">
          <BackLink />
          <Alert variant="default" role="status">
            <FileText aria-hidden="true" />
            <AlertTitle className="leading-snug">No such meeting</AlertTitle>
            <AlertDescription className="max-w-[72ch]">{apiError.message}</AlertDescription>
          </Alert>
        </div>
      );
    }

    return (
      <div className="space-y-4">
        <BackLink />
        <Alert variant="destructive" role="status">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle className="leading-snug">The meeting could not be loaded</AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            {apiError === null ? 'The API did not answer.' : apiError.message}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <MeetingDetailBody meeting={meeting.data} />;
}

function BackLink(): React.JSX.Element {
  return (
    <Link
      href="/meetings"
      className="inline-block text-sm text-accent underline underline-offset-2"
    >
      All meetings
    </Link>
  );
}

function MeetingDetailBody({ meeting }: { meeting: MeetingDetail }): React.JSX.Element {
  const zone = zoneName(meeting.scheduled_start);
  const organisationId = meeting.organisation_id ?? null;
  const organisationName = meeting.organisation_name ?? null;
  const opportunityId = meeting.opportunity_id ?? null;
  const opportunityTitle = meeting.opportunity_title ?? null;
  const location = meeting.location ?? null;
  const virtualLink = meeting.virtual_link ?? null;

  return (
    <div className="space-y-5">
      <BackLink />

      <header className="space-y-2.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="max-w-[64ch] text-xl font-semibold leading-snug text-ink">
            {meeting.title}
          </h1>
          <Badge variant="outline" className="text-2xs font-medium">
            {CLASSIFICATION_LABELS[meeting.classification]}
          </Badge>
        </div>

        {/* Discrete labelled fields, not a middle-dot meta line. */}
        <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1.5">
          <MeetingField label="Type">{MEETING_TYPE_LABELS[meeting.meeting_type]}</MeetingField>
          <MeetingField label="When">
            <span className="tabular">
              {formatMeetingDay(meeting.scheduled_start)},{' '}
              {formatTimeRange(meeting.scheduled_start, meeting.scheduled_end)}
              {zone === null ? null : ` ${zone}`}
            </span>
          </MeetingField>
          <MeetingField label="Where">
            {location ?? (virtualLink === null ? 'Not recorded' : 'By video link')}
          </MeetingField>
          <MeetingField label="Counterpart">
            {organisationId === null ? (
              'None recorded'
            ) : organisationName === null ? (
              // The tie is real and shown; the organisation is above this reader's zone.
              'Withheld at your clearance'
            ) : (
              <Link
                href={`/stakeholders/organisations/${organisationId}`}
                className="text-accent underline underline-offset-2"
              >
                {organisationName}
              </Link>
            )}
          </MeetingField>
          {opportunityId === null ? null : (
            <MeetingField label="Opportunity">
              {opportunityTitle === null ? (
                'Withheld at your clearance'
              ) : (
                <Link href="/opportunities" className="text-accent underline underline-offset-2">
                  {opportunityTitle}
                </Link>
              )}
            </MeetingField>
          )}
          <MeetingField label="Owner">{meeting.owner_name ?? 'Not recorded'}</MeetingField>
        </dl>
      </header>

      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1fr)_28rem] laptop:items-start">
        <div className="min-w-0 laptop:sticky laptop:top-20 laptop:col-start-2 laptop:row-start-1 laptop:max-h-[calc(100vh-6rem)] laptop:overflow-y-auto">
          <FollowupPanel meeting={meeting} />
        </div>

        <div className="min-w-0 space-y-4 laptop:col-start-1 laptop:row-start-1">
          <PreReadPanel
            preRead={meeting.pre_read ?? null}
            classification={meeting.classification}
          />

          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-base font-semibold text-ink">Agenda</h2>
            </CardHeader>
            <CardContent>
              {meeting.agenda.trim().length === 0 ? (
                <p className="text-sm text-slate-700">No agenda recorded.</p>
              ) : (
                <p className="max-w-[72ch] whitespace-pre-line text-sm leading-relaxed text-ink">
                  {meeting.agenda}
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-base font-semibold text-ink">Attendees</h2>
              <p className="text-label text-slate-700">
                Counterpart attendees recorded for this meeting.
              </p>
            </CardHeader>
            <CardContent>
              {meeting.attendees.length === 0 ? (
                <p className="text-sm text-slate-700">No attendees recorded.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {meeting.attendees.map((attendee, index) => (
                    <AttendeeRow key={`${attendee.stakeholder_id}#${index}`} attendee={attendee} />
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

/** "COUNTERPART" as "Counterpart". The server's word, re-cased, never replaced. */
function sentenceCase(value: string): string {
  const spaced = value.replaceAll('_', ' ').toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function AttendeeRow({ attendee }: { attendee: MeetingAttendee }): React.JSX.Element {
  const name = attendee.full_name ?? null;
  const organisation = attendee.organisation_name ?? null;

  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5 first:pt-0 last:pb-0">
      <div className="min-w-0">
        {attendee.withheld ? (
          <>
            <p className="text-sm font-medium text-ink">Attendee withheld</p>
            <p className="text-label text-slate-700">
              Someone you are not cleared to read. Who it is is not shown or guessed.
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium text-ink">{name ?? 'Name not recorded'}</p>
            {organisation === null ? null : (
              <p className="text-label text-slate-700">{organisation}</p>
            )}
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="text-2xs font-medium">
          {sentenceCase(attendee.attendee_role)}
        </Badge>
        <span className="text-label text-slate-700">
          {attendee.is_confirmed ? 'Confirmed' : 'Invited, not confirmed'}
        </span>
      </div>
    </li>
  );
}
