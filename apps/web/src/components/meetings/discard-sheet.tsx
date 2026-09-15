'use client';

import * as React from 'react';
import type { Followup } from '@naddp/contracts';

import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import type { ReasonEvent } from '@/components/meetings/use-followup-actions';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';

/**
 * Discard a follow-up - or return it to draft - with the reason that reaches the audit row.
 *
 * **Discard is never deletion** (OPEN_QUESTIONS Q-05, ruled 2026-09-15). The row stays, the
 * reason is stored on it, and the database refuses a DELETE outright. The sheet says that
 * before the click, in the product's own sentence, so "discard" cannot be mistaken for
 * "erase".
 *
 * The same reasoned-act form serves the two other events that carry a reason and send a
 * follow-up back rather than forward - `request_changes` (an approver returning it) and
 * `revoke_approval` - because a reason form that differed per event would be three places for
 * the 500-character limit and the audit wording to drift apart. Which of them is offered is
 * the server's decision (`available_actions`), never this component's.
 *
 * The limit is the API's (`reason` is capped at 500 characters): enforced by `maxLength` and
 * counted beside the field, so the officer is not refused by a 422 after writing it.
 */

const REASON_LIMIT = 500;

const COPY: Readonly<
  Record<
    ReasonEvent,
    {
      title: string;
      note: string;
      confirm: string;
      pending: string;
      placeholder: string;
    }
  >
> = {
  discard: {
    title: 'Discard this follow-up',
    note: 'Discarding keeps the draft on record — it is never deleted. Your reason is stored with it and the act is written to the audit log.',
    confirm: 'Discard draft',
    pending: 'Discarding…',
    placeholder: 'Why is this follow-up not going out?',
  },
  request_changes: {
    title: 'Return this follow-up for changes',
    note: 'The follow-up goes back to draft so the drafter can revise it, and it will need approval again before it can be sent. Your reason is written to the audit log.',
    confirm: 'Request changes',
    pending: 'Returning…',
    placeholder: 'What should the drafter change?',
  },
  revoke_approval: {
    title: 'Revoke the approval on this follow-up',
    note: 'The follow-up goes back to draft and will need approval again before it can be sent. Your reason is written to the audit log.',
    confirm: 'Revoke approval',
    pending: 'Revoking…',
    placeholder: 'Why is the approval being withdrawn?',
  },
};

export function DiscardSheet({
  event = 'discard',
  followup,
  submitting,
  onCancel,
  onConfirm,
}: {
  event?: ReasonEvent;
  followup: Followup;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}): React.JSX.Element {
  const [reason, setReason] = React.useState('');
  const fieldId = React.useId();
  const noteId = React.useId();
  const countId = React.useId();
  const trimmed = reason.trim();
  const copy = COPY[event];

  return (
    <Sheet open onOpenChange={(open) => (open ? undefined : onCancel())}>
      <SheetContent side="right" className="w-full shadow-none sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{copy.title}</SheetTitle>
          <SheetDescription className="text-slate-700">{followup.subject}</SheetDescription>
        </SheetHeader>

        <form
          className="mt-5 space-y-4"
          onSubmit={(submitEvent) => {
            submitEvent.preventDefault();
            if (trimmed.length > 0 && !submitting) onConfirm(trimmed);
          }}
        >
          <p id={noteId} className="text-sm leading-relaxed text-ink">
            {copy.note}
          </p>

          <div className="space-y-1.5">
            <label htmlFor={fieldId} className="text-sm font-medium text-ink">
              Reason
            </label>
            <textarea
              id={fieldId}
              value={reason}
              onChange={(changeEvent) => setReason(changeEvent.target.value)}
              rows={5}
              required
              maxLength={REASON_LIMIT}
              autoFocus
              aria-describedby={`${noteId} ${countId}`}
              // No local focus ring: globals.css declares one for every :focus-visible.
              className="w-full rounded-md border border-input bg-background p-2 text-sm"
              placeholder={copy.placeholder}
            />
            <p id={countId} className="tabular text-right text-label text-slate-700">
              {reason.length} of {REASON_LIMIT} characters
            </p>
          </div>

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={trimmed.length === 0 || submitting}
              className={PRIMARY_ACTION}
            >
              {submitting ? copy.pending : copy.confirm}
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
