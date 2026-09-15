'use client';

import * as React from 'react';
import type { Followup } from '@naddp/contracts';

import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { RecipientList } from '@/components/meetings/followup-document';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';

/**
 * Confirm an approval that dispatches.
 *
 * Approval here is approve-AND-send: two committed, audited transitions under the approver's
 * name. The sheet says both halves before the click, because an Ambassador who learns
 * afterwards that "approve" also sent the message was not told clearly enough. It restates
 * what is being approved - meeting, subject, recipients, drafter - so the confirmation is of
 * a specific artefact rather than of a button.
 */
export function ApproveSheet({
  followup,
  meetingTitle,
  submitting,
  onCancel,
  onConfirm,
}: {
  followup: Followup;
  meetingTitle: string;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}): React.JSX.Element {
  return (
    <Sheet open onOpenChange={(open) => (open ? undefined : onCancel())}>
      <SheetContent side="right" className="w-full shadow-none sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Approve and send this follow-up</SheetTitle>
          <SheetDescription className="leading-relaxed text-slate-700">
            Your approval is recorded against your name, and the follow-up is dispatched
            immediately. Demo: dispatch is recorded; no message leaves this system.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-5 space-y-4">
          <dl className="space-y-2.5 rounded-md border border-line p-3">
            <div>
              <dt className="text-label text-slate-700">Meeting</dt>
              <dd className="text-sm text-ink">{meetingTitle}</dd>
            </div>
            <div>
              <dt className="text-label text-slate-700">Subject</dt>
              <dd className="text-sm font-medium text-ink">{followup.subject}</dd>
            </div>
            <div>
              <dt className="text-label text-slate-700">To</dt>
              <dd className="mt-1">
                <RecipientList recipients={followup.recipients} />
              </dd>
            </div>
            <div>
              <dt className="text-label text-slate-700">Drafted by</dt>
              <dd className="text-sm text-ink">
                {followup.drafted_by.full_name}
                {followup.drafted_by.title == null ? '' : `, ${followup.drafted_by.title}`}
              </dd>
            </div>
          </dl>

          <p className="text-label leading-snug text-slate-700">
            You are approving the message exactly as it appears on the page. Its content was
            locked when it was submitted for approval.
          </p>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
            <Button onClick={onConfirm} disabled={submitting} className={PRIMARY_ACTION}>
              {submitting ? 'Approving…' : 'Approve and send'}
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
