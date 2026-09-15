'use client';

import * as React from 'react';
import {
  AlertTriangle,
  Check,
  Clock,
  OctagonAlert,
  Pause,
  Timer,
  type LucideIcon,
} from 'lucide-react';
import type { CaseStatus, Priority, SlaState } from '@naddp/contracts';

import { CASE_STATUS_LABELS, PRIORITY_LABELS, SLA_STATE_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * The three facts a consular reader scans a case for: its clock, its status, its priority.
 *
 * The outlined chip silhouette shared with `FollowupStatusChip`, so "what state is this in"
 * reads as the same kind of fact on every screen.
 *
 * **Only the clock earns colour.** Mission Slate reserves the semantic colours for state that
 * needs a human, and on a consular queue that is exactly two clock states: BREACHED takes
 * `--risk` and DUE_SOON takes the warn border with `warn-ink` words (`--warn` clears 3:1 as a
 * border and icon, not 4.5:1 as text). Status and priority stay neutral - an urgent priority is
 * a confirmed attribute of the case, not an alarm about it - so a column of chips reads as a
 * scale with the at-risk cases marked.
 *
 * Never colour alone (WCAG 1.4.1): an icon and a word on every coloured chip, and a spoken
 * clause for the states whose meaning the word does not finish.
 */

const CHIP =
  'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded border px-2 py-0.5 text-label font-medium';

const SLA_TONE: Readonly<Record<SlaState, string>> = {
  BREACHED: 'border-risk/60 bg-risk/5 text-risk',
  DUE_SOON: 'border-warn bg-warn/5 text-warn-ink',
  ON_TRACK: 'border-slate-400/60 text-ink',
  PAUSED: 'border-slate-400/60 text-slate-700',
  STOPPED: 'border-slate-400/60 text-slate-700',
  NOT_SET: 'border-slate-400/60 text-slate-700',
};

const SLA_ICON: Readonly<Record<SlaState, LucideIcon>> = {
  BREACHED: OctagonAlert,
  DUE_SOON: Timer,
  ON_TRACK: Clock,
  PAUSED: Pause,
  STOPPED: Check,
  NOT_SET: Clock,
};

const SLA_SPOKEN: Readonly<Partial<Record<SlaState, string>>> = {
  BREACHED: '. This case is past its service standard.',
  DUE_SOON: '. This case is close to breaching its service standard.',
  PAUSED: '. The clock does not run while the mission waits on the citizen.',
};

export function SlaChip({
  state,
  className,
}: {
  state: SlaState;
  className?: string;
}): React.JSX.Element {
  const Icon = SLA_ICON[state];
  const spoken = SLA_SPOKEN[state];
  return (
    <span className={cn(CHIP, SLA_TONE[state], className)}>
      <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
      {SLA_STATE_LABELS[state]}
      {spoken === undefined ? null : <span className="sr-only">{spoken}</span>}
    </span>
  );
}

export function CaseStatusChip({
  status,
  className,
}: {
  status: CaseStatus;
  className?: string;
}): React.JSX.Element {
  const waiting = status === 'AWAITING_CITIZEN';
  return (
    <span
      className={cn(
        CHIP,
        status === 'CLOSED' ? 'border-slate-400/60 text-slate-700' : 'border-slate-400/60 text-ink',
        className,
      )}
    >
      {waiting ? <Pause aria-hidden="true" className="h-3.5 w-3.5 shrink-0" /> : null}
      {CASE_STATUS_LABELS[status]}
    </span>
  );
}

export function PriorityChip({
  priority,
  className,
}: {
  priority: Priority;
  className?: string;
}): React.JSX.Element {
  const urgent = priority === 'URGENT';
  return (
    <span
      className={cn(
        CHIP,
        urgent ? 'border-slate-400/60 font-semibold text-ink' : 'border-slate-400/60 text-ink',
        className,
      )}
    >
      {urgent ? <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5 shrink-0" /> : null}
      {PRIORITY_LABELS[priority]}
    </span>
  );
}
