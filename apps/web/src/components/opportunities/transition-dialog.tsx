'use client';

import * as React from 'react';
import { AlertTriangle } from 'lucide-react';
import type { BoardCard } from '@naddp/contracts';

import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';

/**
 * Confirm a stage transition, with the reason that will reach the audit row.
 *
 * **The reason is required by the UI even where the API would accept none.** Only some
 * events demand one (`docs/workflows.md` marks them), but a pipeline whose history reads
 * "advanced" with no explanation is a pipeline nobody can review six months later. Asking
 * every time costs a sentence; not asking costs the audit trail its meaning.
 *
 * The dialog states plainly that the act is recorded. That is not a warning - it is the
 * product working - and an officer who is surprised by it afterwards was not told clearly
 * enough beforehand.
 */
export interface TransitionDialogProps {
  card: BoardCard;
  event: string;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}

const EVENT_SENTENCE: Readonly<Record<string, string>> = {
  qualify: 'Qualify this opportunity and move it to QUALIFIED.',
  plan_contact: 'Record that an approach is planned, moving it to CONTACT_PLANNED.',
  record_contact: 'Record that contact was made, moving it to CONTACTED.',
  schedule_meeting: 'Record a scheduled meeting, moving it to MEETING.',
  enter_negotiation:
    'Open negotiation. The classification is raised to at least CONFIDENTIAL on entry, which may remove this opportunity from the view of colleagues who can see it now.',
  partner: 'Commit the mission to a partnership. This is terminal and cannot be reopened.',
  close: 'Close this opportunity. CLOSED is terminal and cannot be reopened.',
  dismiss: 'Dismiss this opportunity. CLOSED is terminal and cannot be reopened.',
  revert: 'Step this opportunity back one stage to correct a mis-advance.',
};

export function TransitionDialog({
  card,
  event,
  submitting,
  onCancel,
  onConfirm,
}: TransitionDialogProps): React.JSX.Element {
  const [reason, setReason] = React.useState('');
  const trimmed = reason.trim();
  const consequential = event === 'partner' || event === 'close' || event === 'dismiss';

  return (
    <Sheet open onOpenChange={(open) => (open ? undefined : onCancel())}>
      <SheetContent side="right" className="w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{EVENT_SENTENCE[event] ?? `Fire "${event}" on this opportunity.`}</SheetTitle>
          <SheetDescription>
            {card.title} — currently at {card.stage}.
          </SheetDescription>
        </SheetHeader>

        <form
          className="space-y-4 px-4"
          onSubmit={(submitEvent) => {
            submitEvent.preventDefault();
            if (trimmed.length > 0 && !submitting) onConfirm(trimmed);
          }}
        >
          {consequential ? (
            <p className="flex gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm">
              <AlertTriangle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              <span>
                This is a terminal transition. It cannot be undone from this screen — a
                revived opportunity is a new row, not a reopened one.
              </span>
            </p>
          ) : null}

          <div className="space-y-1.5">
            <label htmlFor="transition-reason" className="text-sm font-medium">
              Reason
            </label>
            <textarea
              id="transition-reason"
              value={reason}
              onChange={(changeEvent) => setReason(changeEvent.target.value)}
              rows={4}
              required
              autoFocus
              className="w-full rounded-md border border-input bg-background p-2 text-sm shadow-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              placeholder="What changed, and what is it based on?"
            />
            <p className="text-xs text-muted-foreground">
              Written to the append-only audit log against your name, with the stage before
              and after. Every consequential transition is recorded (BUILD_BIBLE §6).
            </p>
          </div>

          <div className="flex justify-end gap-2 pb-4">
            <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={trimmed.length === 0 || submitting}>
              {submitting ? 'Applying…' : 'Confirm and record'}
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
