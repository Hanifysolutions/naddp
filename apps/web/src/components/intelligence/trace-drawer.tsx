'use client';

import * as React from 'react';
import type { BriefTrace } from '@naddp/contracts';

import {
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * The routing decision behind this brief, written out for a person.
 *
 * BUILD_BIBLE section 4a says the trace drawer is "a plain badge, not JSON (must be legible
 * to a non-technical Ambassador)", and this component is the whole of that requirement. A
 * security-minded buyer does not take "it was handled appropriately" on trust; they want to
 * read which zone the call was declared in, which model was asked, which one answered, and
 * what happened when one did not.
 *
 * Two structural choices, both load-bearing:
 *
 *  - **Discrete labelled rows, not a parsed badge.** `route_badge` is an opaque string with
 *    five shapes and three or four segments, and the first segment is not always a
 *    classification display name (`packages/contracts/src/generated/openapi.d.ts`). Splitting
 *    it to build this list would mislabel four of the five shapes. Every row below reads its
 *    own field; the badge is shown once, verbatim, as itself - middle dots and all, in mono,
 *    because it is an identifier the API produced rather than prose this page composed.
 *  - **No second fetch.** Everything here arrives embedded in `BriefResponse.trace`, which
 *    the API populates only for a caller who holds `read:ai_trace` AND clears
 *    `dominant(data_class, result_class)`. Re-fetching `GET /v1/ai/traces/{id}` from the
 *    client would ask the same question a second time and give a reader a second chance to
 *    see a partially-rendered answer.
 *
 * `model_used` is null on a fallback and is rendered as a sentence rather than a dash: no
 * model authored a deterministic snapshot, and naming one - or leaving the row ambiguous -
 * would be the exact fabrication CLAUDE.md rule 2.5 exists to prevent.
 */
export function TraceDrawer({
  trace,
  subject = 'brief',
}: {
  trace: BriefTrace;
  /** What was routed ("pre-read", "draft"). Defaults to `brief`; wording only. */
  subject?: string;
}): React.JSX.Element {
  return (
    <SheetContent side="right" className="w-full sm:max-w-md">
      <SheetHeader>
        <SheetTitle>How this {subject} was routed</SheetTitle>
        <SheetDescription>
          Every AI call declares the sensitivity of the data it is about, and that
          declaration - not convenience - picks the route. This is the decision the
          Gateway made for this {subject}, recorded before the call was attempted.
        </SheetDescription>
      </SheetHeader>

      {/* Hairline-separated rows rather than floating blocks: an instrument panel reads as
          a single table of facts, and the rules do the separating that a shadow would. */}
      <dl className="mt-5 divide-y divide-line border-y border-line">
        <TraceRow label="Badge" valueClassName="font-mono text-2xs">
          {/* Shown as the Gateway rendered it. Empty means the row predates the column,
              which is a fact about the record rather than a licence to rebuild the string. */}
          {trace.route_badge === '' ? 'routing not recorded' : trace.route_badge}
        </TraceRow>

        <TraceRow label="Declared zone">
          {CLASSIFICATION_LABELS[trace.data_class]}
        </TraceRow>

        <TraceRow label="Answer zone">
          {CLASSIFICATION_LABELS[trace.result_class]}
        </TraceRow>

        <TraceRow label="Route">{trace.model_route}</TraceRow>

        <TraceRow label="Why">{trace.route_reason}</TraceRow>

        <TraceRow label="Model requested">{trace.model_requested ?? '—'}</TraceRow>

        <TraceRow label="Model used">
          {trace.model_used ?? 'none — the deterministic snapshot answered'}
        </TraceRow>

        <TraceRow label="Fallback">
          {trace.fallback
            ? `Yes — ${trace.fallback_reason ?? 'reason not recorded'}`
            : 'No'}
        </TraceRow>

        <TraceRow label="Trace id" valueClassName="font-mono text-2xs">
          {trace.trace_id}
        </TraceRow>
      </dl>
    </SheetContent>
  );
}

/**
 * One labelled fact.
 *
 * The `dt`/`dd` pair is the metric idiom from `components/command/command-tile.tsx`, so a
 * reader who has already read a tile knows how to read this without being taught twice. The
 * label is quiet and sentence-case - DESIGN_SYSTEM.md rules out the tracked-out caps eyebrow
 * this drawer used to carry, and a label is not an identifier, so it is not set in mono.
 */
function TraceRow({
  label,
  valueClassName,
  children,
}: {
  label: string;
  valueClassName?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="min-w-0 py-3">
      <dt className="text-label text-slate-700">{label}</dt>
      <dd className={cn('mt-0.5 text-sm leading-snug text-ink', valueClassName)}>
        {children}
      </dd>
    </div>
  );
}
