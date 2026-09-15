'use client';

import * as React from 'react';
import { Gavel, Lock } from 'lucide-react';
import type { CaseTransitionRequest, CaseWorkspace } from '@naddp/contracts';

import { labelFrom } from '@/components/consular/consular-format';
import type { CaseActions } from '@/components/consular/use-case-actions';
import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';

/**
 * Every officer action on a case other than triage, each confirmed in a sheet.
 *
 * **What is offered is the server's decision.** The buttons are `available_events`; which of
 * them needs a reason is `reason_required_events`; which is a human determination is
 * `control_events`; and the officers a case may be assigned to are `assignable_officers`. The
 * client adds wording and nothing else, and the server re-checks every request - a button
 * shown here is a courtesy, never a control.
 *
 * **What is refused is shown, not hidden.** `gated_events` lists the actions legal at this
 * status that this role may not take, with the permission the server would ask for. An
 * Ambassador reading a case sees that triage and determination exist and are not theirs, which
 * is the separation of oversight from casework made visible.
 *
 * The reason limit is the API's (500 characters), enforced by `maxLength` and counted beside
 * the field so an officer is never refused by a 422 after writing it.
 */

const REASON_LIMIT = 500;

const ASSIGNMENT_EVENTS: ReadonlySet<string> = new Set(['assign', 'reassign']);

const EVENT_NOTE: Readonly<Partial<Record<string, string>>> = {
  assign: 'The officer you name becomes accountable for this case.',
  reassign:
    'Accountability moves to the officer you name. The earlier assignment stays on the timeline.',
  begin_review: 'You start working the case as its assigned officer.',
  request_information:
    'The service-level clock pauses while the case waits on the citizen, and resumes when the information arrives.',
  information_received:
    'The service-level clock resumes. The paused time stays on the record and does not count against the mission.',
  escalate: 'The case is raised for a decision above the assigned officer.',
  return_to_officer: 'The assigned officer resumes the case with your guidance.',
  resolve: 'Record the determination made and communicated to the applicant.',
  reopen:
    'The determination is set aside - the original stays on the timeline - and the clock restarts with a fresh budget.',
  close: 'A closed case is never reopened. A later matter for the same person is a new case.',
};

const REASON_LABEL: Readonly<Partial<Record<string, string>>> = {
  resolve: 'Determination',
  close: 'Reason for closing',
};

export function CaseActionsPanel({
  workspace,
  actions,
}: {
  workspace: CaseWorkspace;
  actions: CaseActions;
}): React.JSX.Element {
  const [open, setOpen] = React.useState<string | null>(null);
  const headingId = React.useId();
  const events = workspace.available_events.filter((event) => event !== 'triage');
  const gated = workspace.gated_events.filter((gate) => gate.event !== 'triage');
  const closed = workspace.status === 'CLOSED';

  return (
    <Card aria-labelledby={headingId}>
      <CardHeader className="gap-1 pb-3">
        <h2 id={headingId} className="text-base font-semibold text-ink">
          Officer actions
        </h2>
        <p className="text-label leading-snug text-slate-700">
          The server checks every action again and records it against your name, on the case
          timeline and in the audit log.
        </p>
      </CardHeader>

      <CardContent className="space-y-3">
        {closed ? (
          <p className="text-sm leading-relaxed text-ink">
            This case is closed. A closed case is never reopened; a later matter is a new case.
          </p>
        ) : events.length === 0 ? (
          <p className="text-sm text-slate-700">
            No further action is open to your role at this status.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {events.map((event) => (
              <Button
                key={event}
                variant="outline"
                size="sm"
                onClick={() => setOpen(event)}
                disabled={actions.transition.isPending}
              >
                {workspace.control_events.includes(event) ? (
                  <Gavel aria-hidden="true" className="mr-1.5 h-3.5 w-3.5" />
                ) : null}
                {labelFrom(workspace.event_labels, event)}
                {workspace.control_events.includes(event) ? (
                  <span className="sr-only">, a human determination</span>
                ) : null}
              </Button>
            ))}
          </div>
        )}

        {gated.length === 0 ? null : (
          <div>
            <p className="text-label text-slate-700">Not open to your role</p>
            <ul className="mt-1 space-y-1">
              {gated.map((gate) => (
                <li key={gate.event} className="flex items-start gap-1.5 text-label text-slate-700">
                  <Lock aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
                  <span>
                    <span className="font-medium text-ink">{gate.label}</span> needs{' '}
                    <code className="font-mono text-2xs">{gate.permission}</code>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>

      {open === null ? null : (
        <ActionSheet
          key={open}
          event={open}
          workspace={workspace}
          submitting={actions.transition.isPending}
          onCancel={() => setOpen(null)}
          onConfirm={(body) => actions.transition.mutate(body, { onSettled: () => setOpen(null) })}
        />
      )}
    </Card>
  );
}

function ActionSheet({
  event,
  workspace,
  submitting,
  onCancel,
  onConfirm,
}: {
  event: string;
  workspace: CaseWorkspace;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (body: CaseTransitionRequest) => void;
}): React.JSX.Element {
  const label = labelFrom(workspace.event_labels, event);
  const needsReason = workspace.reason_required_events.includes(event);
  const needsAssignee = ASSIGNMENT_EVENTS.has(event);
  const isControl = workspace.control_events.includes(event);
  const officers = workspace.assignable_officers.filter(
    (officer) => event !== 'reassign' || officer.user_id !== workspace.assigned_user_id,
  );
  const note = EVENT_NOTE[event] ?? null;

  const [reason, setReason] = React.useState('');
  const [assignee, setAssignee] = React.useState<string>(
    officers[0]?.user_id ?? '',
  );
  const reasonId = React.useId();
  const countId = React.useId();
  const assigneeId = React.useId();
  const trimmed = reason.trim();
  const ready = (!needsReason || trimmed.length > 0) && (!needsAssignee || assignee !== '');

  return (
    <Sheet open onOpenChange={(next) => (next ? undefined : onCancel())}>
      <SheetContent side="right" className="w-full shadow-none sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{label}</SheetTitle>
          <SheetDescription className="text-slate-700">
            {workspace.case_type_label}
            <span className="block font-mono text-2xs">{workspace.public_ref}</span>
          </SheetDescription>
        </SheetHeader>

        <form
          className="mt-5 space-y-4"
          onSubmit={(submitEvent) => {
            submitEvent.preventDefault();
            if (!ready || submitting) return;
            onConfirm({
              event,
              expected_status: workspace.status,
              reason: needsReason ? trimmed : null,
              assignee_user_id: needsAssignee ? assignee : null,
            });
          }}
        >
          {note === null ? null : <p className="text-sm leading-relaxed text-ink">{note}</p>}

          {isControl ? (
            <p className="flex items-start gap-2 rounded-md border border-line p-2.5 text-label leading-snug text-ink">
              <Gavel aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              A human determination. It is recorded against your name, and the server refuses it
              outright if it is attempted from inside an AI call.
            </p>
          ) : null}

          {needsAssignee ? (
            <div className="space-y-1.5">
              <label htmlFor={assigneeId} className="text-sm font-medium text-ink">
                Officer
              </label>
              {officers.length === 0 ? (
                <p className="text-sm text-slate-700">No other cleared officer is available.</p>
              ) : (
                <select
                  id={assigneeId}
                  value={assignee}
                  onChange={(changeEvent) => setAssignee(changeEvent.target.value)}
                  className="w-full rounded-md border border-input bg-background p-2 text-sm"
                >
                  {officers.map((officer) => (
                    <option key={officer.user_id} value={officer.user_id}>
                      {officer.full_name}, {officer.title}
                    </option>
                  ))}
                </select>
              )}
              <p className="text-label text-slate-700">
                Listed: officers who work consular cases and hold the consular compartment.
              </p>
            </div>
          ) : null}

          {needsReason ? (
            <div className="space-y-1.5">
              <label htmlFor={reasonId} className="text-sm font-medium text-ink">
                {REASON_LABEL[event] ?? 'Reason'}
              </label>
              <textarea
                id={reasonId}
                value={reason}
                onChange={(changeEvent) => setReason(changeEvent.target.value)}
                rows={5}
                required
                maxLength={REASON_LIMIT}
                autoFocus
                aria-describedby={countId}
                className="w-full rounded-md border border-input bg-background p-2 text-sm"
              />
              <p id={countId} className="tabular text-right text-label text-slate-700">
                {reason.length} of {REASON_LIMIT} characters
              </p>
            </div>
          ) : null}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ready || submitting} className={PRIMARY_ACTION}>
              {submitting ? 'Recording…' : label}
            </Button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}
