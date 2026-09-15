'use client';

import * as React from 'react';
import { AlertTriangle, ShieldCheck, Sparkles } from 'lucide-react';
import type { Followup, FollowupAction, MeetingDetail } from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { ApprovalBlock } from '@/components/meetings/approval-block';
import { ApproveSheet } from '@/components/meetings/approve-sheet';
import { DiscardSheet } from '@/components/meetings/discard-sheet';
import { FollowupDocument } from '@/components/meetings/followup-document';
import { FollowupStatusChip } from '@/components/meetings/followup-status-chip';
import { formatDate, formatDateTime } from '@/components/meetings/meeting-format';
import { RefusalAlert } from '@/components/meetings/refusal-alert';
import {
  useFollowupActions,
  type ReasonEvent,
} from '@/components/meetings/use-followup-actions';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';

/**
 * The follow-up panel: where "AI drafts, humans decide" is demonstrated, not described.
 *
 * **Every button is the server's.** The actions come from `followup.available_actions` and the
 * approval block from `followup.approval`; the only thing read from the session is whether
 * the reader holds the approval grant at all, and that decides a sentence ("you drafted this,
 * so another officer must approve it"), never a control. A client that derived Approve from a
 * role would be a client that could disagree with the server, and the one believed would be
 * the wrong one.
 *
 * **Send does not send.** On a draft the dispatch route refuses (403 `approval_required`, a
 * DENY row), submits the draft for approval and answers 202; the panel then shows the block,
 * plus the one sentence that says the refusal was the server's and is on the record. That
 * sentence is shown only for the view that is the fresh result of the 202 - on a later visit
 * the block alone is the truth, and repeating "the server refused" would describe a click
 * this reader never made.
 *
 * States, one per `status`, and one for "nothing drafted":
 *   none            the sentence, and a draft button only when the API offers one
 *   DRAFTED         the document, Send, Discard, and the rule that a second officer approves
 *   OFFICER_REVIEW  the block, then the approver's actions, then the document
 *   APPROVED        who approved it, Send and Revoke when offered, then the document
 *   SENT            when, who approved, who sent, and that dispatch is simulated
 *   DISCARDED       who, when and why - kept on record, never deleted - and a new AI
 *                   draft only when the API offers one
 *
 * After any mutation the hook refetches the meeting, the diary, the queue and the command
 * centre; nothing is patched locally.
 */

type OpenSheet =
  | { readonly kind: 'approve'; readonly followup: Followup }
  | { readonly kind: 'reason'; readonly event: ReasonEvent; readonly followup: Followup }
  | null;

const REASON_DONE: Readonly<Record<ReasonEvent, string>> = {
  discard: 'Follow-up discarded. It is kept on record with your reason.',
  request_changes: 'Follow-up returned to draft for changes.',
  revoke_approval: 'Approval revoked. The follow-up is back in draft.',
};

function offers(followup: Followup, action: FollowupAction): boolean {
  return followup.available_actions.includes(action);
}

export function FollowupPanel({ meeting }: { meeting: MeetingDetail }): React.JSX.Element {
  const session = useDemoSession();
  const actions = useFollowupActions(session.role);
  const headingId = React.useId();

  const [sheet, setSheet] = React.useState<OpenSheet>(null);
  const [blocked, setBlocked] = React.useState<{
    followupId: string;
    detail: string | null;
  } | null>(null);
  const [draftDeclined, setDraftDeclined] = React.useState<string | null>(null);
  const [announcement, setAnnouncement] = React.useState('');

  const current = meeting.followups[0] ?? null;
  const earlier = meeting.followups.slice(1);
  const busy =
    actions.dispatch.isPending ||
    actions.approve.isPending ||
    actions.transition.isPending ||
    actions.draft.isPending;

  const send = (followup: Followup): void => {
    setAnnouncement('');
    actions.dispatch.mutate(
      { meetingId: meeting.id, followup },
      {
        onSuccess: (result) => {
          if (result.blocked) {
            // The block itself is a status region and announces; no second announcement.
            setBlocked({ followupId: result.followup.id, detail: result.block_detail ?? null });
            return;
          }
          setBlocked(null);
          if (result.dispatched) {
            setAnnouncement(
              'Follow-up sent. Dispatch is recorded here; no message leaves this system.',
            );
          }
        },
      },
    );
  };

  const draft = (): void => {
    setDraftDeclined(null);
    setAnnouncement('');
    actions.draft.mutate(
      { meetingId: meeting.id },
      {
        onSuccess: (result) => {
          if (result.followup == null) {
            // A BLOCKED envelope is an answer, not an error: the Gateway declined to draft.
            setDraftDeclined(
              result.envelope.explanation ??
                'The AI Gateway returned no draft, so nothing was drafted.',
            );
            return;
          }
          setAnnouncement('Follow-up drafted. It has not been sent.');
        },
      },
    );
  };

  const approve = (followup: Followup): void => {
    setAnnouncement('');
    actions.approve.mutate(
      { meetingId: meeting.id, followup },
      {
        onSuccess: (result) => {
          setAnnouncement(
            result.dispatched
              ? 'Approved and sent. Your approval is recorded against your name.'
              : 'Approved, but the send was refused. The follow-up rests at approved, not sent.',
          );
        },
        // Closed either way, so a refusal alert in the panel is not hidden behind the sheet.
        onSettled: () => setSheet(null),
      },
    );
  };

  const reasoned = (followup: Followup, event: ReasonEvent, reason: string): void => {
    setAnnouncement('');
    actions.transition.mutate(
      { meetingId: meeting.id, followup, event, reason },
      {
        onSuccess: () => setAnnouncement(REASON_DONE[event]),
        onSettled: () => setSheet(null),
      },
    );
  };

  const openReason = (followup: Followup, event: ReasonEvent) => (): void =>
    setSheet({ kind: 'reason', event, followup });

  return (
    <Card role="region" aria-labelledby={headingId}>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0 pb-3">
        <h2 id={headingId} className="text-base font-semibold text-ink">
          Follow-up
        </h2>
        {current === null ? null : <FollowupStatusChip status={current.status} />}
      </CardHeader>

      <CardContent className="space-y-4">
        {actions.refusal === null ? null : <RefusalAlert refusal={actions.refusal} />}

        {draftDeclined === null ? null : (
          <Alert variant="warning" role="status">
            <AlertTriangle aria-hidden="true" />
            <AlertTitle className="leading-snug text-warn-ink">No draft was produced</AlertTitle>
            <AlertDescription className="max-w-[72ch]">{draftDeclined}</AlertDescription>
          </Alert>
        )}

        {current === null ? (
          <div className="space-y-3">
            <p className="text-sm text-ink">No follow-up has been drafted for this meeting.</p>
            <DraftOffer
              meeting={meeting}
              label="Draft follow-up with AI"
              busy={busy}
              drafting={actions.draft.isPending}
              onDraft={draft}
            />
          </div>
        ) : null}

        {current?.status === 'DRAFTED' ? (
          <>
            <FollowupDocument followup={current} bounded />
            <div className="space-y-2 border-t border-line pt-4">
              {offers(current, 'dispatch') || offers(current, 'discard') ? (
                <div className="flex flex-wrap gap-2">
                  {offers(current, 'dispatch') ? (
                    <Button
                      onClick={() => send(current)}
                      disabled={busy}
                      className={`min-w-24 ${PRIMARY_ACTION}`}
                    >
                      {actions.dispatch.isPending ? 'Requesting…' : 'Send'}
                    </Button>
                  ) : null}
                  {offers(current, 'discard') ? (
                    <Button
                      variant="outline"
                      onClick={openReason(current, 'discard')}
                      disabled={busy}
                    >
                      Discard
                    </Button>
                  ) : null}
                </div>
              ) : null}
              <p className="text-label text-slate-700">
                Outbound messages need approval by a second, authorised officer.
              </p>
            </div>
          </>
        ) : null}

        {current?.status === 'OFFICER_REVIEW' ? (
          <>
            <ApprovalBlock
              followup={current}
              refusal={
                blocked !== null && blocked.followupId === current.id
                  ? { detail: blocked.detail }
                  : null
              }
            />
            {offers(current, 'approve_and_dispatch') ||
            offers(current, 'request_changes') ||
            offers(current, 'discard') ? (
              <div className="flex flex-wrap gap-2">
                {offers(current, 'approve_and_dispatch') ? (
                  <Button
                    onClick={() => setSheet({ kind: 'approve', followup: current })}
                    disabled={busy}
                    className={PRIMARY_ACTION}
                  >
                    Approve and send
                  </Button>
                ) : null}
                {offers(current, 'request_changes') ? (
                  <Button
                    variant="outline"
                    onClick={openReason(current, 'request_changes')}
                    disabled={busy}
                  >
                    Request changes
                  </Button>
                ) : null}
                {offers(current, 'discard') ? (
                  <Button
                    variant="outline"
                    onClick={openReason(current, 'discard')}
                    disabled={busy}
                  >
                    Discard
                  </Button>
                ) : null}
              </div>
            ) : null}
            {current.approval.caller_is_drafter && session.can('approve:meeting_followup') ? (
              <p className="text-label text-slate-700">
                You drafted this, so another authorised officer must approve it.
              </p>
            ) : null}
            <FollowupDocument followup={current} bounded />
          </>
        ) : null}

        {current?.status === 'APPROVED' ? (
          <>
            <div className="space-y-1 border-l-2 border-slate-400 py-0.5 pl-3">
              <p className="text-sm font-semibold text-ink">
                Approved by {current.approved_by?.full_name ?? 'an officer not named here'}
                {current.approved_by?.title == null ? null : (
                  <span className="font-normal text-slate-700">
                    , {current.approved_by.title}
                  </span>
                )}
              </p>
              {current.approved_at == null ? null : (
                <p className="tabular text-label text-slate-700">
                  Approved <time dateTime={current.approved_at}>{formatDateTime(current.approved_at)}</time>
                </p>
              )}
              <p className="text-label text-slate-700">
                Not sent. The approval is recorded; nothing has left the mission.
              </p>
            </div>
            {offers(current, 'dispatch') ||
            offers(current, 'revoke_approval') ||
            offers(current, 'discard') ? (
              <div className="flex flex-wrap gap-2">
                {offers(current, 'dispatch') ? (
                  <Button
                    onClick={() => send(current)}
                    disabled={busy}
                    className={`min-w-24 ${PRIMARY_ACTION}`}
                  >
                    {actions.dispatch.isPending ? 'Sending…' : 'Send'}
                  </Button>
                ) : null}
                {offers(current, 'revoke_approval') ? (
                  <Button
                    variant="outline"
                    onClick={openReason(current, 'revoke_approval')}
                    disabled={busy}
                  >
                    Revoke approval
                  </Button>
                ) : null}
                {offers(current, 'discard') ? (
                  <Button
                    variant="outline"
                    onClick={openReason(current, 'discard')}
                    disabled={busy}
                  >
                    Discard
                  </Button>
                ) : null}
              </div>
            ) : null}
            <FollowupDocument followup={current} bounded />
          </>
        ) : null}

        {current?.status === 'SENT' ? (
          <>
            <div className="tick-ok space-y-1.5 rounded-r-md bg-ok/5 p-4">
              <p className="flex items-start gap-2.5 text-base font-semibold leading-snug text-ink">
                <ShieldCheck aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-ok" />
                <span>
                  Sent
                  {current.sent_at == null ? null : (
                    <>
                      {' '}
                      <time dateTime={current.sent_at} className="tabular">
                        {formatDateTime(current.sent_at)}
                      </time>
                    </>
                  )}
                </span>
              </p>
              {current.approved_by == null ? null : (
                <p className="pl-[1.875rem] text-sm text-ink">
                  Approved by <span className="font-medium">{current.approved_by.full_name}</span>
                  {current.approved_by.title == null ? null : (
                    <span className="text-slate-700">, {current.approved_by.title}</span>
                  )}
                </p>
              )}
              {current.sent_by == null ? null : (
                <p className="pl-[1.875rem] text-sm text-ink">
                  Sent by <span className="font-medium">{current.sent_by.full_name}</span>
                </p>
              )}
              {current.dispatch_is_simulated ? (
                <p className="pl-[1.875rem] pt-0.5 text-label leading-snug text-slate-700">
                  Demo: dispatch is recorded here; no message leaves this system, and
                  recipients are labels, not addresses.
                </p>
              ) : null}
            </div>
            <FollowupDocument followup={current} bounded />
          </>
        ) : null}

        {current?.status === 'DISCARDED' ? (
          <>
            <div className="space-y-2 border-l-2 border-slate-400 py-0.5 pl-3">
              <p className="text-sm font-semibold text-ink">
                Discarded by {current.discarded_by?.full_name ?? 'an officer not named here'}
                {current.discarded_at == null ? null : (
                  <>
                    {' '}
                    on{' '}
                    <time dateTime={current.discarded_at} className="tabular">
                      {formatDate(current.discarded_at)}
                    </time>
                  </>
                )}
              </p>
              {current.discard_reason == null ? null : (
                <blockquote className="max-w-[72ch] text-sm italic leading-relaxed text-ink">
                  {current.discard_reason}
                </blockquote>
              )}
              <p className="text-label text-slate-700">
                Kept on record. A drafted communication is never deleted.
              </p>
            </div>
            {/* A new draft is offered when the API offers one. The unavailable-reason sentence
                belongs to the empty state; beside a discarded draft it would read as a second
                refusal about something nobody asked for. */}
            {meeting.ai_draft_available ? (
              <DraftOffer
                meeting={meeting}
                label="Draft a new follow-up"
                busy={busy}
                drafting={actions.draft.isPending}
                onDraft={draft}
              />
            ) : null}
            <details className="rounded-md border border-line">
              <summary className="cursor-pointer rounded-md px-3 py-2 text-sm font-medium text-ink">
                Show the discarded draft
              </summary>
              <div className="border-t border-line p-3">
                <FollowupDocument followup={current} />
              </div>
            </details>
          </>
        ) : null}

        {earlier.length === 0 ? null : <EarlierFollowups followups={earlier} />}

        {/* One persistent live region for outcomes the block does not announce itself. It is
            mounted before any mutation, which is what makes the change reliably spoken. */}
        <p role="status" className="sr-only">
          {announcement}
        </p>
      </CardContent>

      {sheet?.kind === 'approve' ? (
        <ApproveSheet
          followup={sheet.followup}
          meetingTitle={meeting.title}
          submitting={actions.approve.isPending}
          onCancel={() => setSheet(null)}
          onConfirm={() => approve(sheet.followup)}
        />
      ) : null}

      {sheet?.kind === 'reason' ? (
        <DiscardSheet
          event={sheet.event}
          followup={sheet.followup}
          submitting={actions.transition.isPending}
          onCancel={() => setSheet(null)}
          onConfirm={(reason) => reasoned(sheet.followup, sheet.event, reason)}
        />
      ) : null}
    </Card>
  );
}

/**
 * The AI draft button, or the server's sentence for why there is none.
 *
 * Offered only when `ai_draft_available` is true. Otherwise the API's own reason is shown
 * verbatim - never a disabled button the server would refuse, and never a paraphrase.
 */
function DraftOffer({
  meeting,
  label,
  busy,
  drafting,
  onDraft,
}: {
  meeting: MeetingDetail;
  label: string;
  busy: boolean;
  drafting: boolean;
  onDraft: () => void;
}): React.JSX.Element | null {
  if (meeting.ai_draft_available) {
    return (
      <Button onClick={onDraft} disabled={busy} className={PRIMARY_ACTION}>
        <Sparkles aria-hidden="true" />
        {drafting ? 'Drafting…' : label}
      </Button>
    );
  }
  const reason = meeting.ai_draft_unavailable_reason ?? null;
  if (reason === null) return null;
  return <p className="max-w-[72ch] text-label leading-snug text-slate-700">{reason}</p>;
}

/** Follow-ups that are no longer the one shown: kept, listed, collapsed. */
function EarlierFollowups({
  followups,
}: {
  followups: readonly Followup[];
}): React.JSX.Element {
  return (
    <details className="border-t border-line pt-3">
      <summary className="cursor-pointer rounded-sm text-sm font-medium text-ink">
        Earlier follow-ups (<span className="tabular">{followups.length}</span>)
      </summary>
      <ul className="mt-2 divide-y divide-line">
        {followups.map((followup) => (
          <li key={followup.id} className="space-y-1 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <FollowupStatusChip status={followup.status} />
              <span className="tabular text-label text-slate-700">
                Drafted{' '}
                <time dateTime={followup.drafted_at}>{formatDate(followup.drafted_at)}</time>
              </span>
            </div>
            <p className="text-sm text-ink">{followup.subject}</p>
            {followup.discard_reason == null ? null : (
              <p className="max-w-[72ch] text-label text-slate-700">
                Reason: {followup.discard_reason}
              </p>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
