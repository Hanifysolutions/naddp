'use client';

import * as React from 'react';
import { Check, Clock, PencilLine, ShieldCheck, type LucideIcon } from 'lucide-react';
import type { BriefStatus } from '@naddp/contracts';

import { BRIEF_STATUS_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * Where a brief sits in its approval lifecycle.
 *
 * **Its shape is chosen to be wrong for the other badge on this page.** The AI-proposed
 * marker is a tinted `Badge`; this is an outlined chip, the `StateChip` idiom from
 * `components/command/command-tile.tsx`. The two say completely different things - one is
 * about where a claim came from, the other about whether a human has signed the document -
 * and if they shared a silhouette a reader would start reading them as one axis with two
 * settings. A different form is cheaper than a legend.
 *
 * DRAFT carries an extra sentence in its accessible name, and it is the most important
 * string in this file: DRAFT is the state the demo's hero brief is genuinely in. "AI
 * drafts, humans decide" (CLAUDE.md section 3.2) is a control, not a slogan, and a control
 * that is only visible to sighted readers is not a control. Hence icon plus word plus,
 * where it matters, a spoken clause - never colour alone (WCAG 1.4.1).
 *
 * **DRAFT is given presence rather than played down** (OPEN_QUESTIONS Q-25): an unapproved
 * brief sitting beside the mission's own headline is the human-approval gate made visible on
 * the document itself, so it is sized at the label step and warn-toned rather than tucked
 * into the meta line.
 */

/**
 * Border and text tone per status.
 *
 * DRAFT and PUBLISHED are the two ends of the lifecycle and are the only ones that earn a
 * colour; the two middle states stay neutral so the chip reads as a scale rather than as
 * four competing alarms.
 *
 * `--warn` measures 3.47:1 on paper, which clears SC 1.4.11 for a BORDER but not the 4.5:1
 * that text needs, so the border is `warn` and the word is `warn-ink` (4.63:1). That split
 * is the documented departure in `globals.css`, not a local preference.
 */
const STATUS_TONE: Readonly<Record<BriefStatus, string>> = {
  DRAFT: 'border-warn bg-warn/5 text-warn-ink',
  IN_REVIEW: 'border-line text-slate-700',
  APPROVED: 'border-line text-ink',
  PUBLISHED: 'border-ok/60 bg-ok/5 text-ok',
};

/** The glyph that makes the status legible without colour, and in a projected photo. */
const STATUS_ICON: Readonly<Record<BriefStatus, LucideIcon>> = {
  DRAFT: PencilLine,
  IN_REVIEW: Clock,
  APPROVED: Check,
  PUBLISHED: ShieldCheck,
};

export function BriefStatusChip({ status }: { status: BriefStatus }): React.JSX.Element {
  const Icon = STATUS_ICON[status];

  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 text-label font-medium',
        STATUS_TONE[status],
      )}
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
      {BRIEF_STATUS_LABELS[status]}
      {status === 'DRAFT' ? (
        <span className="sr-only">. Not yet reviewed or approved by a named human.</span>
      ) : null}
    </span>
  );
}
