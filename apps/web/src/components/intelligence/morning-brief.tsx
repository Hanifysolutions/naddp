'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, FileText, Lock } from 'lucide-react';
import { isApiError, type MorningBriefItem } from '@naddp/contracts';

import { BriefHistory } from '@/components/intelligence/brief-history';
import { BriefItemCard } from '@/components/intelligence/brief-item-card';
import { BriefStatusChip } from '@/components/intelligence/brief-status-chip';
import { TraceBadge } from '@/components/intelligence/trace-badge';
import { useDemoSession } from '@/components/layout/session-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchBriefHistory, fetchMorningBrief } from '@/lib/api-queries';
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * The morning brief: winning moment #1, "not a chatbot".
 *
 * One `GET /v1/intelligence/brief` supplies the whole document - the brief, its items, their
 * evidence and the routing decision behind it - because that endpoint is already
 * identity-scoped. It takes no parameters and still answers differently per caller: an
 * AMBASSADOR or TRADE_OFFICER receives their own desk's brief, a DEPUTY or DIASPORA_OFFICER
 * the mission-wide one, and a CONSULAR_OFFICER or ADMIN receives a 403.
 *
 * **The primary query is deliberately not gated on the permission.** Everywhere else in this
 * app a read is asked for only when the session holds the grant, to keep expected 403s out of
 * the console. Here the refusal *is* the demonstration: the API must be the thing that says
 * no, and it must write the audit row that proves it said no. A client that checked
 * `can('read:intelligence')` first and rendered its own denial would be a client making an
 * authorisation decision - the one thing CLAUDE.md rule 2.4 does not allow - and it would
 * leave the audit log silent about a refusal the audience just watched happen.
 *
 * The *history* query is gated, because it demonstrates nothing: two refusals for one role
 * would put a second expected error in the console beside the one that matters, and a console
 * full of expected errors is how a real one gets missed.
 *
 * Five states, kept strictly apart, because four of them are routinely blurred in exactly the
 * direction that flatters the product:
 *
 *   no identity  nobody has assumed a demo role, so nothing was asked
 *   loading      a request is in flight
 *   403          this reader may not read intelligence at all - a fact about the reader
 *   404          the mission has assembled no brief - a fact about the day
 *   success      the brief, with every item's provenance and evidence on screen
 *
 * The approval state of the document sits beside the title rather than in the metadata
 * cluster (OPEN_QUESTIONS Q-25). A brief the mission has not yet signed off should say so
 * where the reader's eye lands first: "AI drafts, humans decide" is only a control if the
 * gate is visible on the artefact the gate applies to.
 */

/** How many history rows the rail asks for. Small: it is context, not an archive. */
const HISTORY_LIMIT = 10;

/**
 * Retry transient failures only.
 *
 * A 403 or 404 is a settled answer. Retrying a 403 wastes the opening seconds of the demo and
 * writes a second denial into the audit log for no reason; retrying a 404 cannot conjure a
 * brief. Anything else gets two attempts, which absorbs a cold API container.
 */
function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

/**
 * The highest confidence among the *evidenced* items of this brief, or null.
 *
 * This is the number the AI-proposed item is measured against, and it is computed from the
 * brief on screen rather than stored anywhere: the honesty contrast of winning moment #1 has
 * to be a demonstrated property of the document a viewer is looking at, not a claim that
 * would go stale the moment the seed changed.
 *
 * Proposed items are skipped - comparing the proposal against itself would say nothing - and
 * so are unassessed ones, because `confidence` null means "not assessed" and treating it as a
 * zero would invent an assessment the API declined to make. `for...of` rather than an indexed
 * loop, so `noUncheckedIndexedAccess` holds without a cast.
 */
export function evidencedPeak(items: readonly MorningBriefItem[]): number | null {
  let peak: number | null = null;
  for (const item of items) {
    if (item.is_proposed_by_ai) continue;
    const confidence = item.confidence ?? null;
    if (confidence === null) continue;
    if (peak === null || confidence > peak) peak = confidence;
  }
  return peak;
}

/**
 * The repo formats dates per file rather than through a shared helper; this matches
 * `components/opportunities/pipeline-board.tsx`. `brief_date` is a date-only string, so it is
 * read as UTC midnight - the same calendar day in Abuja and in Canberra.
 */
function formatDate(value: string): string {
  return new Date(value).toLocaleDateString('en-AU', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function MorningBrief(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const briefQuery = useQuery({
    queryKey: queryKeys.morningBrief(role),
    queryFn: ({ signal }) => fetchMorningBrief(signal),
    // Gated on identity only. Without a session there is nobody for the API to authorise. With
    // one, the request always goes - including for the roles that will be refused, because
    // that refusal is the demonstration and only the server may make it. See the header.
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  const historyQuery = useQuery({
    queryKey: queryKeys.briefHistory(role, HISTORY_LIMIT),
    queryFn: ({ signal }) => fetchBriefHistory(HISTORY_LIMIT, signal),
    // Gated on the permission as well: this read demonstrates nothing, so a role that will be
    // refused should not spend a second console error on it.
    enabled: role !== null && session.can('read:intelligence'),
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle className="text-warn-ink">No demo identity resolved</AlertTitle>
        <AlertDescription>
          Choose a role to see the morning brief. Nothing is shown without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (briefQuery.isPending) {
    return (
      <div className="space-y-4" aria-busy="true">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-[28rem] max-w-full" />
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-52 rounded-lg" />
        ))}
        {/* Skeletons are aria-hidden by design, so the region has to announce itself. */}
        <span className="sr-only" role="status">
          Loading the morning brief
        </span>
      </div>
    );
  }

  if (briefQuery.isError) {
    const error = briefQuery.error;
    const apiError = isApiError(error) ? error : null;

    // The state CONSULAR_OFFICER and ADMIN see. It is a control working, not a fault, and it
    // has to read that way: a warning tone rather than a destructive one, and the server's own
    // words before the page's, because a paraphrase here would be the UI editing an
    // authorisation decision it did not make. The title takes `warn-ink` rather than `warn`,
    // because `--warn` clears AA as a border and an icon but not as text (see `globals.css`).
    if (apiError !== null && apiError.status === 403) {
      return (
        <Alert variant="warning" role="status">
          <Ban aria-hidden="true" />
          <AlertTitle className="text-warn-ink">
            This role may not read the morning brief
          </AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            <p>{apiError.message}</p>
            <p className="mt-2">
              The morning brief is intelligence, and this role holds no intelligence read.
              The refusal was made by the API, not by this page, and it was written to the
              audit log.
            </p>
          </AlertDescription>
        </Alert>
      );
    }

    // A 404 is a statement about the day, not about the reader, and it does not get an error
    // tone. Merging it into the denial above would tell a cleared officer they were refused.
    if (apiError !== null && apiError.status === 404) {
      return (
        <Alert variant="default" role="status">
          <FileText aria-hidden="true" />
          <AlertTitle>No brief available</AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            The mission has not assembled a brief yet. Nothing is invented to fill the
            space.
          </AlertDescription>
        </Alert>
      );
    }

    return (
      <Alert variant="destructive" role="status">
        <AlertTriangle aria-hidden="true" />
        <AlertTitle>The morning brief could not be loaded</AlertTitle>
        <AlertDescription className="max-w-[72ch]">
          {apiError === null ? 'The API did not answer.' : apiError.message}
        </AlertDescription>
      </Alert>
    );
  }

  const brief = briefQuery.data;
  // Computed once for the whole document: every card compares against the same peak.
  const peak = evidencedPeak(brief.items);
  const trace = brief.trace ?? null;
  const traceId = brief.trace_id ?? null;

  const standfirst: string[] = [
    `${formatDate(brief.brief_date)}. ${brief.items.length} item${
      brief.items.length === 1 ? '' : 's'
    } you are cleared to read.`,
  ];
  if (brief.is_mission_wide) {
    standfirst.push(
      'This is the mission-wide brief: no brief was assembled for your desk, so you are reading the mission-wide one.',
    );
  }
  if (!brief.is_today) {
    // A stale brief is labelled rather than passed off as this morning's. An officer acting on
    // yesterday's assessment believing it is today's is the failure this sentence prevents.
    standfirst.push('Not today’s brief — this is the most recent one.');
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          {/* The approval state rides with the title, not with the metadata below it. */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className="text-figure-sm font-semibold text-ink">Morning brief</h1>
            <BriefStatusChip status={brief.status} />
          </div>
          <p className="mt-2 max-w-[72ch] text-sm leading-relaxed text-slate-700">
            {standfirst.join(' ')}
          </p>
        </div>

        <div className="flex flex-col items-start gap-1.5 laptop:items-end">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="text-2xs font-medium">
              {CLASSIFICATION_LABELS[brief.classification]}
            </Badge>
            <TraceBadge trace={trace} />
          </div>

          {/* The routing decision exists and this role may not inspect it. Said plainly: a
              silently absent badge would read as a brief that was never routed at all. */}
          {traceId !== null && trace === null ? (
            <p className="text-label text-slate-700">
              Routing decision withheld for this role.
            </p>
          ) : null}
        </div>
      </header>

      <section aria-label="Standfirst" className="min-w-0">
        <h2 className="max-w-[72ch] text-xl font-semibold leading-snug text-ink">
          {brief.title}
        </h2>
        <p className="mt-1.5 max-w-[72ch] text-sm leading-relaxed">{brief.summary}</p>
      </section>

      {brief.items.length === 0 ? (
        // Never "0 items". The count is a statement about this reader's clearance, and wording
        // it as a zero would describe the mission's morning instead.
        <p className="py-10 text-center text-sm text-slate-700">
          No items in this brief that you are cleared to read.
        </p>
      ) : (
        <div className="space-y-3">
          {brief.items.map((item) => (
            <BriefItemCard key={item.id} item={item} evidencedPeak={peak} />
          ))}
        </div>
      )}

      <BriefHistory query={historyQuery} />
    </div>
  );
}
