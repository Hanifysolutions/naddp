'use client';

import * as React from 'react';
import { Archive, Check, Clock, PencilLine, Send, type LucideIcon } from 'lucide-react';
import type { FollowupStatus } from '@naddp/contracts';

import { FOLLOWUP_STATUS_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * Where a follow-up sits in its approval lifecycle.
 *
 * The outlined `StateChip` silhouette shared with `BriefStatusChip`, so "has a human signed
 * this" reads as the same kind of fact on the brief and on a meeting.
 *
 * **Only two states earn a colour.** OFFICER_REVIEW is the block - a communication that will
 * not leave until a named human decides - and takes the warn border with `warn-ink` words
 * (`--warn` clears 3:1 as a border and icon, not 4.5:1 as text; see `globals.css`). SENT is
 * the resolved end and takes --ok. Draft, approved-not-sent and discarded stay neutral, so a
 * column of chips reads as a scale with one thing waiting rather than as five alarms.
 *
 * Never colour alone (WCAG 1.4.1): an icon and a word on every chip, and a spoken clause for
 * the two states whose meaning the word does not finish.
 */
const STATUS_TONE: Readonly<Record<FollowupStatus, string>> = {
  DRAFTED: 'border-slate-400/60 text-slate-700',
  OFFICER_REVIEW: 'border-warn bg-warn/5 text-warn-ink',
  APPROVED: 'border-slate-400/60 text-ink',
  SENT: 'border-ok/60 bg-ok/5 text-ok',
  DISCARDED: 'border-slate-400/60 text-slate-700',
};

const STATUS_ICON: Readonly<Record<FollowupStatus, LucideIcon>> = {
  DRAFTED: PencilLine,
  OFFICER_REVIEW: Clock,
  APPROVED: Check,
  SENT: Send,
  DISCARDED: Archive,
};

const STATUS_SPOKEN: Readonly<Partial<Record<FollowupStatus, string>>> = {
  OFFICER_REVIEW: '. It will not send until a named human approves it.',
  DISCARDED: '. Kept on record, never deleted.',
};

export function FollowupStatusChip({
  status,
  className,
}: {
  status: FollowupStatus;
  className?: string;
}): React.JSX.Element {
  const Icon = STATUS_ICON[status];
  const spoken = STATUS_SPOKEN[status];

  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded border px-2 py-0.5 text-label font-medium',
        STATUS_TONE[status],
        className,
      )}
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
      {FOLLOWUP_STATUS_LABELS[status]}
      {spoken === undefined ? null : <span className="sr-only">{spoken}</span>}
    </span>
  );
}
