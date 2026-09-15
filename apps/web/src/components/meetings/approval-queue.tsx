'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, Lock, PenLine, Sparkles } from 'lucide-react';
import { isApiError, type ApprovalQueueItem } from '@naddp/contracts';

import { TraceBadge } from '@/components/intelligence/trace-badge';
import { useDemoSession } from '@/components/layout/session-provider';
import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { ApproveSheet } from '@/components/meetings/approve-sheet';
import { DiscardSheet } from '@/components/meetings/discard-sheet';
import {
  FollowupProvenance,
  RecipientList,
} from '@/components/meetings/followup-document';
import { FollowupStatusChip } from '@/components/meetings/followup-status-chip';
import { MeetingField } from '@/components/meetings/meeting-field';
import { formatDate } from '@/components/meetings/meeting-format';
import { MeetingsSubnav } from '@/components/meetings/meetings-subnav';
import { RefusalAlert } from '@/components/meetings/refusal-alert';
import {
  useFollowupActions,
  type ReasonEvent,
} from '@/components/meetings/use-followup-actions';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchApprovalQueue } from '@/lib/api-queries';
import { CLASSIFICATION_LABELS, MEETING_TYPE_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * The approval queue: outbound communications waiting for a named human decision.
 *
 * **The API decides who may see this page.** The query is not gated on
 * `approve:meeting_followup`: a Trade Officer who opens the URL is refused by the server, on
 * the record, and the page renders that refusal in the API's words. A client that checked
 * the permission and drew its own denial would be making an authorisation decision and would
 * leave the audit log silent about it. (The subnav hides the link as a courtesy; that is not
 * this.)
 *
 * **Cards carry their own actions, from the server.** `available_actions` decides Approve and
 * send, Request changes and Discard per item. A follow-up the reader drafted shows the
 * separation-of-duties sentence instead of an approval control, because the server would
 * refuse it - and says so in `approval.caller_is_drafter`.
 *
 * The body is collapsed by default so a queue of several reads as a list of decisions, not a
 * wall of prose; it is one keypress from full, and the approve sheet restates what is being
 * approved before anything is dispatched.
 */

type OpenSheet =
  | { readonly kind: 'approve'; readonly item: ApprovalQueueItem }
  | { readonly kind: 'reason'; readonly event: ReasonEvent; readonly item: ApprovalQueueItem }
  | null;

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

function QueueHeader(): React.JSX.Element {
  return (
    <header className="space-y-1">
      <h1 className="text-xl font-semibold text-ink">Approval queue</h1>
      <p className="max-w-[72ch] text-sm leading-relaxed text-slate-700">
        Outbound communications waiting for a named human decision. Only follow-ups you are
        cleared to read are listed.
      </p>
    </header>
  );
}

export function ApprovalQueueSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-5" aria-busy="true">
      <Skeleton className="h-8 w-56" />
      <div className="space-y-2">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-[36rem] max-w-full" />
      </div>
      <Skeleton className="h-72 rounded-lg" />
      <span className="sr-only" role="status">
        Loading the approval queue
      </span>
    </div>
  );
}

export function ApprovalQueueView(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;
  const actions = useFollowupActions(role);
  const [sheet, setSheet] = React.useState<OpenSheet>(null);
  const [announcement, setAnnouncement] = React.useState('');

  const queue = useQuery({
    queryKey: queryKeys.approvalQueue(role),
    queryFn: ({ signal }) => fetchApprovalQueue(signal),
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">No demo identity resolved</AlertTitle>
        <AlertDescription>
          Choose a role to see the approval queue. Nothing is shown without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (queue.isPending) return <ApprovalQueueSkeleton />;

  if (queue.isError) {
    const apiError = isApiError(queue.error) ? queue.error : null;
    const forbidden = apiError !== null && apiError.status === 403;
    return (
      <div className="space-y-5">
        <MeetingsSubnav />
        <QueueHeader />
        <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
          {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <AlertTitle className={forbidden ? 'leading-snug text-warn-ink' : 'leading-snug'}>
            {forbidden
              ? 'This role may not approve outbound communications'
              : 'The approval queue could not be loaded'}
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

  const busy =
    actions.approve.isPending || actions.transition.isPending || actions.dispatch.isPending;

  const approve = (item: ApprovalQueueItem): void => {
    setAnnouncement('');
    actions.approve.mutate(
      { meetingId: item.meeting_id, followup: item.followup },
      {
        onSuccess: (result) =>
          setAnnouncement(
            result.dispatched
              ? 'Approved and sent. Your approval is recorded against your name.'
              : 'Approved, but the send was refused. The follow-up rests at approved, not sent.',
          ),
        onSettled: () => setSheet(null),
      },
    );
  };

  const reasoned = (item: ApprovalQueueItem, event: ReasonEvent, reason: string): void => {
    setAnnouncement('');
    actions.transition.mutate(
      { meetingId: item.meeting_id, followup: item.followup, event, reason },
      {
        onSuccess: () =>
          setAnnouncement(
            event === 'discard'
              ? 'Follow-up discarded. It is kept on record with your reason.'
              : 'Follow-up returned to draft for changes.',
          ),
        onSettled: () => setSheet(null),
      },
    );
  };

  const data = queue.data;

  return (
    <div className="space-y-5">
      <MeetingsSubnav />
      <QueueHeader />

      {actions.refusal === null ? null : <RefusalAlert refusal={actions.refusal} />}

      {data.items.length === 0 ? (
        <p className="rounded-lg border border-line bg-card px-4 py-10 text-center text-sm text-slate-700">
          Nothing is waiting for your approval.
        </p>
      ) : (
        <section aria-label="Waiting for a decision" className="space-y-2.5">
          <p className="tabular text-label text-slate-700">
            {data.total} waiting, oldest submission first.
          </p>
          <ul className="space-y-3">
            {data.items.map((item) => (
              <li key={item.followup.id}>
                <QueueCard
                  item={item}
                  busy={busy}
                  onApprove={() => setSheet({ kind: 'approve', item })}
                  onReason={(event) => setSheet({ kind: 'reason', event, item })}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      <p role="status" className="sr-only">
        {announcement}
      </p>

      {sheet?.kind === 'approve' ? (
        <ApproveSheet
          followup={sheet.item.followup}
          meetingTitle={sheet.item.meeting_title}
          submitting={actions.approve.isPending}
          onCancel={() => setSheet(null)}
          onConfirm={() => approve(sheet.item)}
        />
      ) : null}

      {sheet?.kind === 'reason' ? (
        <DiscardSheet
          event={sheet.event}
          followup={sheet.item.followup}
          submitting={actions.transition.isPending}
          onCancel={() => setSheet(null)}
          onConfirm={(reason) => reasoned(sheet.item, sheet.event, reason)}
        />
      ) : null}
    </div>
  );
}

function QueueCard({
  item,
  busy,
  onApprove,
  onReason,
}: {
  item: ApprovalQueueItem;
  busy: boolean;
  onApprove: () => void;
  onReason: (event: ReasonEvent) => void;
}): React.JSX.Element {
  const headingId = React.useId();
  const followup = item.followup;
  const offered = followup.available_actions;
  const trace = followup.trace ?? null;
  const traceId = followup.trace_id ?? null;
  const organisation = item.organisation_name ?? null;
  const hasActions =
    offered.includes('approve_and_dispatch') ||
    offered.includes('request_changes') ||
    offered.includes('discard');

  return (
    <Card role="article" aria-labelledby={headingId}>
      <CardHeader className="gap-2.5 space-y-0">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0 space-y-1">
            <h2 id={headingId} className="text-base font-semibold leading-snug text-ink">
              {followup.subject}
            </h2>
            <p className="text-sm leading-snug">
              <span className="text-slate-700">Follows up </span>
              <Link
                href={`/meetings/${item.meeting_id}`}
                className="text-accent underline underline-offset-2"
              >
                {item.meeting_title}
              </Link>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="text-2xs font-medium">
              {CLASSIFICATION_LABELS[followup.classification]}
            </Badge>
            <FollowupStatusChip status={followup.status} />
          </div>
        </div>
        <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <MeetingField label="Type">{MEETING_TYPE_LABELS[item.meeting_type]}</MeetingField>
          <MeetingField label="Met">
            <span className="tabular">{formatDate(item.scheduled_start)}</span>
          </MeetingField>
          {organisation === null ? null : (
            <MeetingField label="Counterpart">{organisation}</MeetingField>
          )}
        </dl>
      </CardHeader>

      <CardContent className="space-y-3.5">
        <div>
          <p className="text-label text-slate-700">To</p>
          <div className="mt-1">
            <RecipientList recipients={followup.recipients} />
          </div>
        </div>

        <FollowupProvenance followup={followup} withSubmission />

        <div className="flex flex-wrap items-center gap-1.5">
          {followup.is_ai_drafted ? (
            <Badge variant="outline" className="gap-1 text-2xs font-medium">
              <Sparkles aria-hidden="true" />
              AI draft
            </Badge>
          ) : (
            <Badge variant="outline" className="gap-1 text-2xs font-medium">
              <PenLine aria-hidden="true" />
              Written by an officer
            </Badge>
          )}
          <TraceBadge trace={trace} subject="draft" />
          {traceId !== null && trace === null ? (
            <span className="text-label text-slate-700">
              Routing decision withheld for this role.
            </span>
          ) : null}
        </div>

        <details className="rounded-md border border-line">
          <summary className="cursor-pointer rounded-md px-3 py-2 text-sm font-medium text-ink">
            Read the message
          </summary>
          <div className="border-t border-line bg-paper px-3 py-2.5">
            <p className="max-w-[72ch] whitespace-pre-line text-sm leading-relaxed text-ink">
              {followup.body}
            </p>
          </div>
        </details>

        {hasActions || followup.approval.caller_is_drafter ? (
          <div className="space-y-2 border-t border-line pt-3.5">
            {hasActions ? (
              <div className="flex flex-wrap gap-2">
                {offered.includes('approve_and_dispatch') ? (
                  <Button onClick={onApprove} disabled={busy} className={PRIMARY_ACTION}>
                    Approve and send
                  </Button>
                ) : null}
                {offered.includes('request_changes') ? (
                  <Button
                    variant="outline"
                    onClick={() => onReason('request_changes')}
                    disabled={busy}
                  >
                    Request changes
                  </Button>
                ) : null}
                {offered.includes('discard') ? (
                  <Button
                    variant="outline"
                    onClick={() => onReason('discard')}
                    disabled={busy}
                  >
                    Discard
                  </Button>
                ) : null}
              </div>
            ) : null}
            {followup.approval.caller_is_drafter ? (
              <p className="text-label text-slate-700">
                You drafted this, so another authorised officer must approve it.
              </p>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
