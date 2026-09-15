'use client';

import * as React from 'react';
import { Clock, Lock } from 'lucide-react';
import type { Followup } from '@naddp/contracts';

import { formatDateTime } from '@/components/meetings/meeting-format';
import { Button } from '@/components/ui/button';

/**
 * The block. Winning moment #2, "AI drafts, humans decide", on one panel.
 *
 * The officer pressed Send; the SERVER refused (a 403 `approval_required` DENY row), submitted
 * the follow-up for approval, and answered 202. This panel is what that looks like, and its
 * job is to be calm, legible and unmistakable to an Ambassador reading it across a room.
 *
 * **Calm, not alarmed.** No red, no triangle, no exclamation: nothing is wrong - the control
 * is working. A warn state tick (`tick-warn`, the named utility, so the hairline cannot paint
 * over it) and a 5% warn wash mark the state structurally; the clock is --warn because it is
 * an icon; the heading is `warn-ink` because --warn is not an AA text colour (globals.css).
 * The explanatory sentence is ink, at full contrast, because it is the sentence that matters.
 *
 * **Named humans, not a permission.** "Who can approve" lists `approval.eligible_approvers`
 * exactly as the server resolved them - officers who hold the grant, are cleared for this
 * follow-up's zone, and did not draft it. The client never derives that list from a role.
 *
 * **Send is shown locked, not removed.** An absent button is indistinguishable from an absent
 * feature; a disabled Send with a lock, inside the block, is the refusal made visible. The
 * kit's disabled opacity is overridden so the measured contrast is the shipped contrast.
 *
 * `role="status"` so a screen reader announces the block when the Send it answers lands.
 * `refusal` is non-null only when this view is the fresh result of that 202, and carries the
 * server's own sentence, shown verbatim.
 */
export function ApprovalBlock({
  followup,
  refusal,
}: {
  followup: Followup;
  refusal: { detail: string | null } | null;
}): React.JSX.Element {
  const headingId = React.useId();
  const approvers = followup.approval.eligible_approvers;
  const submittedBy = followup.submitted_by ?? null;
  const submittedAt = followup.submitted_at ?? null;

  return (
    <section
      role="status"
      aria-labelledby={headingId}
      className="tick-warn space-y-3.5 rounded-r-md bg-warn/5 py-4 pl-4 pr-4"
    >
      <div className="flex items-start gap-3">
        <Clock aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-warn" />
        <div className="min-w-0 space-y-1">
          <h3
            id={headingId}
            className="text-base font-semibold leading-snug text-warn-ink"
          >
            Awaiting approval from an authorised officer
          </h3>
          <p className="text-sm leading-relaxed text-ink">
            This message will not send until a named human approves it.
          </p>
          {refusal === null ? null : (
            <>
              <p className="text-sm leading-relaxed text-ink">
                The server refused to send it and recorded the refusal in the audit log.
              </p>
              {refusal.detail === null ? null : (
                <p className="max-w-[72ch] text-label leading-snug text-slate-700">
                  {refusal.detail}
                </p>
              )}
            </>
          )}
        </div>
      </div>

      <div className="space-y-1 pl-8">
        <p className="text-label text-slate-700">Who can approve</p>
        {approvers.length === 0 ? (
          // Said, not left blank: an empty list is a real answer and a silent gap would read
          // as a rendering fault.
          <p className="text-sm text-ink">
            No officer is currently both permitted and cleared to approve this follow-up.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {approvers.map((person) => (
              <li key={person.user_id} className="leading-snug">
                <span className="block text-sm font-medium text-ink">{person.full_name}</span>
                {person.title == null ? null : (
                  <span className="block text-label text-slate-700">{person.title}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {submittedBy === null ? null : (
        <p className="pl-8 text-label leading-snug text-slate-700">
          Submitted by{' '}
          <span className="font-medium text-ink">
            {submittedBy.full_name}
            {submittedBy.title == null ? '' : `, ${submittedBy.title}`}
          </span>
          {submittedAt === null ? null : (
            <>
              {' '}
              on{' '}
              <time dateTime={submittedAt} className="tabular">
                {formatDateTime(submittedAt)}
              </time>
            </>
          )}
        </p>
      )}

      <div className="pl-8">
        <Button
          variant="outline"
          size="sm"
          disabled
          className="border-warn bg-transparent text-warn-ink disabled:opacity-100"
        >
          <Lock aria-hidden="true" className="text-warn" />
          Send
          <span className="sr-only">, locked until an authorised officer approves it</span>
        </Button>
      </div>
    </section>
  );
}
