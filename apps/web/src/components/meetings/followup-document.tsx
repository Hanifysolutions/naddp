'use client';

import * as React from 'react';
import { PenLine, Sparkles } from 'lucide-react';
import type { Followup } from '@naddp/contracts';

import { TraceBadge } from '@/components/intelligence/trace-badge';
import { formatDateTime } from '@/components/meetings/meeting-format';
import { Badge } from '@/components/ui/badge';
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * The follow-up itself: the artefact an approver sees is the artefact that gets sent.
 *
 * Rendered verbatim and never reflowed: the subject, the recipients as the labels the server
 * stores (they are organisation and role labels, never addresses - a database CHECK refuses
 * an `@`), and the body with its own paragraph breaks. Nothing here abbreviates or
 * summarises the message, because an approval given against a summary is an approval of
 * something other than what leaves.
 *
 * **Provenance sits on the document, not beside it.** "AI draft" with the routing chip when
 * the Gateway wrote it, "Written by an officer" when a person did, and the drafter's name
 * and post in a `dl`. A routing decision that exists but is withheld from this role says so
 * rather than disappearing.
 *
 * `bounded` caps the body's height on a laptop so the panel's actions stay on screen at
 * 1440x900; the region is keyboard-focusable and labelled so the rest of the message is
 * reachable without a mouse. Below the laptop breakpoint the body is never clipped.
 */
export function FollowupDocument({
  followup,
  bounded = false,
}: {
  followup: Followup;
  bounded?: boolean;
}): React.JSX.Element {
  const bodyLabelId = React.useId();
  const trace = followup.trace ?? null;
  const traceId = followup.trace_id ?? null;

  return (
    <div className="min-w-0 space-y-3">
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
        <Badge variant="outline" className="text-2xs font-medium">
          {CLASSIFICATION_LABELS[followup.classification]}
        </Badge>
        <TraceBadge trace={trace} subject="draft" />
      </div>

      {traceId !== null && trace === null ? (
        <p className="text-label text-slate-700">Routing decision withheld for this role.</p>
      ) : null}

      <dl className="space-y-2.5">
        <div>
          <dt className="text-label text-slate-700">Subject</dt>
          <dd className="mt-0.5 text-sm font-semibold leading-snug text-ink">
            {followup.subject}
          </dd>
        </div>
        <div>
          <dt className="text-label text-slate-700">To</dt>
          <dd className="mt-1">
            <RecipientList recipients={followup.recipients} />
          </dd>
        </div>
      </dl>

      <div>
        <p id={bodyLabelId} className="text-label text-slate-700">
          Message
        </p>
        <div
          role="region"
          aria-labelledby={bodyLabelId}
          tabIndex={bounded ? 0 : undefined}
          className={cn(
            'mt-1 rounded-md border border-line bg-paper px-3 py-2.5',
            bounded && 'laptop:max-h-44 laptop:overflow-y-auto',
          )}
        >
          <p className="max-w-[72ch] whitespace-pre-line text-sm leading-relaxed text-ink">
            {followup.body}
          </p>
        </div>
      </div>

      <FollowupProvenance followup={followup} />
    </div>
  );
}

/** Recipients as a list of labels. A list, not a joined string: each is a separate party. */
export function RecipientList({
  recipients,
}: {
  recipients: readonly string[];
}): React.JSX.Element {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {recipients.map((recipient, index) => (
        <li
          // The index is in the key because two recipients may carry the same label.
          key={`${recipient}#${index}`}
          className="rounded border border-line bg-surface px-1.5 py-0.5 text-label text-ink"
        >
          {recipient}
        </li>
      ))}
    </ul>
  );
}

/**
 * Who wrote it and when, and - when asked - who submitted it. Labelled rows rather than a
 * "Drafted by A · 3 Sep" meta string, so each fact can be found without parsing a sentence.
 */
export function FollowupProvenance({
  followup,
  withSubmission = false,
}: {
  followup: Followup;
  withSubmission?: boolean;
}): React.JSX.Element {
  const submittedBy = followup.submitted_by ?? null;
  const submittedAt = followup.submitted_at ?? null;

  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-label">
      <dt className="text-slate-700">Drafted by</dt>
      <dd className="text-ink">
        {followup.drafted_by.full_name}
        {followup.drafted_by.title == null ? null : (
          <span className="text-slate-700">, {followup.drafted_by.title}</span>
        )}
      </dd>
      <dt className="text-slate-700">Drafted</dt>
      <dd className="tabular text-ink">
        <time dateTime={followup.drafted_at}>{formatDateTime(followup.drafted_at)}</time>
      </dd>
      {withSubmission && submittedBy !== null ? (
        <>
          <dt className="text-slate-700">Submitted by</dt>
          <dd className="text-ink">
            {submittedBy.full_name}
            {submittedBy.title == null ? null : (
              <span className="text-slate-700">, {submittedBy.title}</span>
            )}
          </dd>
        </>
      ) : null}
      {withSubmission && submittedAt !== null ? (
        <>
          <dt className="text-slate-700">Submitted</dt>
          <dd className="tabular text-ink">
            <time dateTime={submittedAt}>{formatDateTime(submittedAt)}</time>
          </dd>
        </>
      ) : null}
    </dl>
  );
}
