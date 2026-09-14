'use client';

import * as React from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import type { BriefList, NaddpRole } from '@naddp/contracts';

import { BriefStatusChip } from '@/components/intelligence/brief-status-chip';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { cn } from '@/lib/utils';

/**
 * The readable brief history, as a quiet rail beneath this morning's brief.
 *
 * **It never displaces the brief.** The query is handed in already running, and this
 * component returns null while it is pending and again if it fails. A secondary read that
 * could paint an error banner over winning moment #1 would let a slow or refused history
 * call cost the demo its opening screen; the brief itself is authoritative and is already on
 * the page. The failure is visible in the network panel, where a developer will look, and
 * nowhere the audience will.
 *
 * **The rows are not links.** There is no per-brief route in this slice, and BUILD_BIBLE
 * section 0 forbids the demo dead-ending: a row that looks openable and goes nowhere is
 * precisely that. When a `/intelligence/briefs/{id}` route exists, these become links and
 * nothing else about the rail changes.
 *
 * `total` is the number of rows returned, never a count of what exists. The difference
 * between the two is how many briefs this caller is not cleared for, which is the leak the
 * API's clearance predicate exists to prevent - so the caption says "you are cleared to
 * read" and never "of N".
 */

/**
 * Presentation labels for the desk a brief was assembled for.
 *
 * A local total map rather than an import: the one in `components/layout/role-picker.tsx` is
 * private to that component, and typing this `Record<NaddpRole, string>` means a new role in
 * the API is a compile error here rather than an undefined caption on a row.
 */
const ROLE_LABELS: Readonly<Record<NaddpRole, string>> = {
  AMBASSADOR: 'Ambassador',
  DEPUTY: 'Deputy Head of Mission',
  TRADE_OFFICER: 'Trade Officer',
  CONSULAR_OFFICER: 'Consular Officer',
  DIASPORA_OFFICER: 'Diaspora Officer',
  ADMIN: 'Administrator',
};

/**
 * The repo formats dates per file rather than through a shared helper; this matches
 * `components/opportunities/pipeline-board.tsx` exactly so two screens cannot drift apart.
 *
 * `brief_date` is a date-only string, so it is read as UTC midnight - the same calendar day
 * in Abuja and Canberra, which are the only clocks this demo is shown on.
 */
function formatDate(value: string | null): string {
  if (value === null) return '—';
  return new Date(value).toLocaleDateString('en-AU', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function BriefHistory({
  query,
}: {
  query: UseQueryResult<BriefList, unknown>;
}): React.JSX.Element | null {
  if (query.isPending) return null;
  if (query.isError) return null;

  const data = query.data;

  // Nothing to add: the brief on screen above is already the whole of what this caller may
  // read, and an empty rail would read as a rendering fault rather than as that fact.
  if (data.items.length === 0) return null;

  return (
    <Card>
      <CardHeader className="gap-1 pb-3">
        <h2 className="text-base font-semibold leading-tight">Recent briefs</h2>
        <p className="max-w-[72ch] text-label leading-snug text-slate-700">
          The {data.total} most recent brief{data.total === 1 ? '' : 's'} you are cleared
          to read, newest first.
        </p>
      </CardHeader>

      <CardContent className="pt-0">
        <ul className="divide-y divide-line">
          {data.items.map((summary) => (
            <li
              key={summary.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2.5 first:pt-0"
            >
              <span
                className={cn(
                  // Tabular figures so the date column cannot jitter between rows; the
                  // date is a number, which is not the same thing as an identifier.
                  'tabular w-28 shrink-0 text-label text-slate-700',
                  summary.is_today && 'font-semibold text-ink',
                )}
              >
                {formatDate(summary.brief_date)}
              </span>

              <span className="min-w-0 flex-1 truncate text-sm">{summary.title}</span>

              {summary.is_today ? (
                <Badge variant="outline" className="shrink-0 text-2xs font-medium">
                  Today
                </Badge>
              ) : null}

              <BriefStatusChip status={summary.status} />

              {/* `is_mission_wide` is exactly `role_scope === null`; branching on the field
                  that carries the label keeps the caption and the fact in one place. */}
              <span className="shrink-0 text-label text-slate-700">
                {summary.role_scope === null
                  ? 'Mission-wide'
                  : ROLE_LABELS[summary.role_scope]}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
