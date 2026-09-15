'use client';

import * as React from 'react';
import { ArrowRight, Bot, History } from 'lucide-react';
import type { CaseTimelineEntry } from '@naddp/contracts';

import { formatDateTime } from '@/components/meetings/meeting-format';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { CASE_EVENT_TYPE_LABELS, CASE_STATUS_LABELS, ROLE_LABELS } from '@/lib/enum-labels';

/**
 * The case timeline: the immutable `case_events` rows, newest first.
 *
 * The API returns them oldest first, as the record is written; this view reverses them so the
 * step an officer has just taken lands at the top, where they are looking. Each entry says when,
 * what moved, who acted in which role, and - when an AI recommendation informed the step - says
 * that too, without ever naming the AI as the actor. The note is the product's own plain
 * sentence: reasons and determinations live on the audit row and the case, not here.
 */
export function CaseTimeline({
  entries,
}: {
  entries: readonly CaseTimelineEntry[];
}): React.JSX.Element {
  const headingId = React.useId();
  const newestFirst = [...entries].reverse();

  return (
    <Card aria-labelledby={headingId}>
      <CardHeader className="gap-1 pb-2">
        <h2 id={headingId} className="flex items-center gap-2 text-base font-semibold text-ink">
          <History aria-hidden="true" className="h-4 w-4 text-slate-700" />
          Timeline
        </h2>
        <p className="text-label text-slate-700">
          Newest first. Append-only: each entry is written in the same transaction as its audit
          row and is never edited or removed.
        </p>
      </CardHeader>
      <CardContent>
        {newestFirst.length === 0 ? (
          <p className="text-sm text-slate-700">No entries recorded yet.</p>
        ) : (
          <ol className="divide-y divide-line">
            {newestFirst.map((entry) => (
              <TimelineRow key={entry.id} entry={entry} />
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function TimelineRow({ entry }: { entry: CaseTimelineEntry }): React.JSX.Element {
  const from = entry.from_status;
  const to = entry.to_status;
  const actorRole = entry.actor_role;
  const actor = entry.is_system ? 'System' : (entry.actor_name ?? 'Officer not recorded');

  return (
    <li className="space-y-1 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <time dateTime={entry.occurred_at} className="tabular text-label text-slate-700">
          {formatDateTime(entry.occurred_at)}
        </time>
        <span className="text-label font-medium text-ink">
          {CASE_EVENT_TYPE_LABELS[entry.event_type]}
        </span>
        {from !== null && to !== null && from !== to ? (
          <span className="inline-flex items-center gap-1 text-label text-ink">
            {CASE_STATUS_LABELS[from]}
            <ArrowRight aria-hidden="true" className="h-3 w-3 text-slate-700" />
            <span className="sr-only"> to </span>
            {CASE_STATUS_LABELS[to]}
          </span>
        ) : to !== null ? (
          <span className="text-label text-ink">{CASE_STATUS_LABELS[to]}</span>
        ) : null}
        {entry.ai_informed ? (
          <Badge variant="proposed" className="gap-1 text-2xs font-medium">
            <Bot aria-hidden="true" className="h-3 w-3" />
            AI-informed
            <span className="sr-only">
              : an AI recommendation informed this step; the decision was the officer&apos;s.
            </span>
          </Badge>
        ) : null}
      </div>
      <p className="max-w-[72ch] text-sm leading-snug text-ink">{entry.note}</p>
      <p className="text-label text-slate-700">
        {actor}
        {actorRole !== null && !entry.is_system ? `, ${ROLE_LABELS[actorRole]}` : ''}
      </p>
    </li>
  );
}
