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
import { OPPORTUNITY_STAGE_LABELS } from '@/lib/enum-labels';

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
 *
 * The sentences below name stages and data zones by their display labels rather than by
 * their enum members. The wire values are unchanged and still travel with the request; what
 * changed is that a diplomat reads "Contact planned", not `CONTACT_PLANNED`.
 */
export interface TransitionDialogProps {
  card: BoardCard;
  event: string;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}

const EVENT_SENTENCE: Readonly<Record<string, string>> = {
  qualify: 'Qualify this opportunity and move it to Qualified.',
  plan_contact: 'Record that an approach is planned, moving it to Contact planned.',
  record_contact: 'Record that contact was made, moving it to Contacted.',
  schedule_meeting: 'Record a scheduled meeting, moving it to Meeting.',
  enter_negotiation:
    'Open negotiation. The classification is raised to at least Confidential on entry, which may remove this opportunity from the view of colleagues who can see it now.',
  partner:
    'Commit the mission to a partnership. This is terminal and cannot be reopened.',
  close: 'Close this opportunity. Closed is terminal and cannot be reopened.',
  dismiss: 'Dismiss this opportunity. Closed is terminal and cannot be reopened.',
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
          <SheetTitle>
            {EVENT_SENTENCE[event] ?? `Fire "${event}" on this opportunity.`}
          </SheetTitle>
          <SheetDescription>
            {card.title} — currently at {OPPORTUNITY_STAGE_LABELS[card.stage]}.
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
            // A terminal transition is a state worth marking structurally, so it carries the
            // --warn tick. The icon is --warn (borders and icons only, SC 1.4.11) and the
            // words are --warn-ink, the AA-safe warn for text.
            <p className="tick-warn flex gap-2 rounded-r-md bg-warn/5 p-3 text-sm text-warn-ink">
              <AlertTriangle
                aria-hidden="true"
                className="mt-0.5 size-4 shrink-0 text-warn"
              />
              <span>
                This is a terminal transition. It cannot be undone from this screen — a
                revived opportunity is a new row, not a reopened one.
              </span>
            </p>
          ) : null}

          <div className="space-y-1.5">
            <label htmlFor="transition-reason" className="text-sm font-medium text-ink">
              Reason
            </label>
            <textarea
              id="transition-reason"
              value={reason}
              onChange={(changeEvent) => setReason(changeEvent.target.value)}
              rows={4}
              required
              autoFocus
              // No local focus ring: globals.css declares one for every :focus-visible so no
              // control in the app can ship without a visible indicator.
              className="w-full rounded-md border border-input bg-background p-2 text-sm"
              placeholder="What changed, and what is it based on?"
            />
            <p className="text-label text-slate-700">
              Written to the append-only audit log against your name, with the stage
              before and after. Every consequential transition is recorded (BUILD_BIBLE
              §6).
            </p>
          </div>

          <div className="flex justify-end gap-2 pb-4">
            <Button
              type="button"
              variant="ghost"
              onClick={onCancel}
              disabled={submitting}
            >
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
