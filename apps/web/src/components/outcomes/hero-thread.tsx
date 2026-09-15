'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Ban, SearchX } from 'lucide-react';
import type { HeroThread, ThreadStep } from '@naddp/contracts';

import { TraceBadge } from '@/components/intelligence/trace-badge';
import { useDemoSession } from '@/components/layout/session-provider';
import {
  TONE_TICK,
  contextLabel,
  joinWords,
  present,
} from '@/components/outcomes/outcome-format';
import { Skeleton } from '@/components/ui/skeleton';
import { asBriefTrace } from '@/lib/ai-trace';
import { fetchAiTrace } from '@/lib/api-queries';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * The hero thread: one corridor, followed across the mission's functions.
 *
 * The lithium processing skills and skilled-migration corridor runs from the bilateral
 * opportunity, through the counterpart and the meeting, to the diaspora experts who fit it and the
 * citizen-service case beside it. It is one story, and it is governed five times: every step is
 * read by its own domain under its own authorisation, so a role that may not read a step sees it
 * withheld - in place, on the line - rather than a thread with a gap nobody explains.
 *
 * A numbered line because the steps are a sequence (DESIGN_SYSTEM.md numbers only sequences).
 * The AI-proposed opportunity carries the --proposed tick: the corridor is the platform's
 * inference and must read as quieter than the evidenced steps after it (Q-17).
 */
export function HeroThreadBand({ thread }: { thread: HeroThread }): React.JSX.Element {
  const headingId = React.useId();
  return (
    <section
      aria-labelledby={headingId}
      className="mt-4 rounded-lg border border-line bg-card"
    >
      <header className="border-b border-line px-4 py-3">
        <h2 id={headingId} className="text-base font-semibold leading-tight text-ink">
          {thread.title}
        </h2>
        <p className="mt-1 max-w-[80ch] text-label leading-snug text-slate-700">
          One story across the mission&apos;s functions: the bilateral opportunity, the
          counterpart, the meeting, the diaspora experts who fit it and the citizen
          service beside it. Each step is read by its own domain, under its own
          authorisation.
        </p>
      </header>
      <ol className="px-4 pt-4">
        {thread.steps.map((step, index) => (
          <StepItem
            key={step.key}
            step={step}
            position={index + 1}
            last={index === thread.steps.length - 1}
          />
        ))}
      </ol>
    </section>
  );
}

function StepItem({
  step,
  position,
  last,
}: {
  step: ThreadStep;
  position: number;
  last: boolean;
}): React.JSX.Element {
  const withheld = !step.authorisation.granted;
  return (
    <li className="flex gap-3">
      <div className="flex flex-col items-center" aria-hidden="true">
        <span
          className={cn(
            'tabular flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-2xs font-semibold',
            withheld
              ? 'border-dashed border-line text-slate-700'
              : 'border-slate-400 bg-card text-ink',
          )}
        >
          {position}
        </span>
        {last ? null : <span className="mt-1 w-px flex-1 bg-line" />}
      </div>

      <div
        className={cn(
          'mb-4 grid min-w-0 flex-1 grid-cols-1 gap-x-6 gap-y-2 pl-3 laptop:grid-cols-[13rem_minmax(0,1fr)_auto]',
          step.found === true ? TONE_TICK[step.tone] : null,
        )}
      >
        <div className="min-w-0">
          <h3 className="text-sm font-semibold leading-tight text-ink">{step.title}</h3>
          <p className="mt-0.5 text-2xs leading-snug text-slate-700">
            {`${contextLabel(step.bounded_context)} domain, under ${joinWords(step.authorisation.required_permissions)}`}
          </p>
        </div>

        <StepBody step={step} />

        <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5 laptop:flex-col laptop:items-end">
          {present(step.trace_id) ? <StepTrace traceId={step.trace_id} /> : null}
          {present(step.href) ? (
            <Link
              href={step.href}
              className="text-label font-medium text-accent underline underline-offset-4 hover:text-ink"
            >
              Open
              <span className="sr-only">{` ${step.title}`}</span>
            </Link>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function StepBody({ step }: { step: ThreadStep }): React.JSX.Element {
  if (step.found === null || step.found === undefined) {
    return (
      <p className="flex min-w-0 items-start gap-2 text-xs leading-snug text-slate-700">
        <Ban aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          {`Withheld. This step requires ${joinWords(step.authorisation.missing_permissions)}, which your current demo role does not hold. It was never queried: a refusal, not an empty step.`}
        </span>
      </p>
    );
  }

  if (!step.found) {
    return (
      <p className="flex min-w-0 items-start gap-2 text-xs leading-snug text-slate-700">
        <SearchX aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{step.note ?? 'Not found in the records you are cleared to read.'}</span>
      </p>
    );
  }

  return (
    <div className="min-w-0 space-y-1.5">
      {present(step.headline) ? (
        <p className="text-sm font-medium leading-snug text-ink">{step.headline}</p>
      ) : null}
      {step.facts.length === 0 ? null : (
        <dl className="flex flex-wrap gap-x-6 gap-y-1.5">
          {step.facts.map((fact) => (
            <div key={fact.label} className="min-w-0">
              <dt className="text-2xs text-slate-700">{fact.label}</dt>
              <dd className="text-xs leading-snug text-ink">{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {present(step.note) ? (
        <p className="max-w-[72ch] text-2xs leading-snug text-slate-700">{step.note}</p>
      ) : null}
    </div>
  );
}

/** The section 4a routing badge for the AI call behind a step, for roles that may read traces. */
function StepTrace({ traceId }: { traceId: string }): React.JSX.Element | null {
  const session = useDemoSession();
  const mayRead = session.can('read:ai_trace');
  const trace = useQuery({
    queryKey: queryKeys.aiTrace(session.role, traceId),
    queryFn: ({ signal }) => fetchAiTrace(traceId, signal),
    enabled: mayRead,
    staleTime: Infinity,
    retry: false,
  });

  if (!mayRead) return null;
  if (trace.isPending) return <Skeleton className="h-5 w-40" />;
  if (trace.isError) {
    return (
      <span className="text-2xs text-slate-700">
        Routing record unavailable (trace <span className="font-mono">{traceId}</span>).
      </span>
    );
  }
  return <TraceBadge trace={asBriefTrace(trace.data)} subject="step" />;
}
