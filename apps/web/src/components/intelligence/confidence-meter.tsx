'use client';

import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * One item's confidence, and - when it matters - what it is lower than.
 *
 * The bar idiom is the one in `components/command/distribution-bars.tsx`, and it carries
 * the same three rules for the same reasons:
 *
 *  - **The number is the primary encoding, the bar is secondary.** The value is always
 *    printed, so the reading survives a screen reader, forced-colours mode, and a
 *    photograph of a projector screen - which is the actual viewing condition here.
 *  - **The track is `aria-hidden`.** It restates the printed value; announcing it twice
 *    would be noise.
 *  - **A missing value is an em dash, never a zero.** `confidence` is nullable and null
 *    means "not assessed". Rendering that as 0 would invent an assessment the API
 *    explicitly declined to make.
 *
 * `confidence` is 0-100, not 0-1 (`packages/contracts/src/index.ts`). Nothing here scales
 * it; the width *is* the percentage.
 *
 * The comparative caption is the honesty contrast of winning moment #1 in one sentence.
 * The hero corridor is a synthesis the platform proposed rather than a fact a source
 * reported (BUILD_BIBLE section 2, OPEN_QUESTIONS Q-17), and it must visibly score below
 * the evidenced items beside it. Both numbers in that sentence are computed from the brief
 * that is on screen - hard-coding either would turn a demonstrated property into a claim,
 * and a claim that would go stale the first time the seed changed. When the caller did not
 * supply a peak to compare against, the caption is omitted entirely rather than softened:
 * no comparison is honest, an invented one is not.
 *
 * **The fill is the accent for an evidenced item and `--proposed` for a proposed one**, so
 * the gap is legible as two different lengths in two different hues before anybody reads a
 * digit. The figure itself takes tabular figures rather than a monospace face: a number is
 * not an identifier, and DESIGN_SYSTEM.md keeps mono for identifiers alone.
 *
 * No `role="progressbar"`: nothing in this codebase uses one, and a bar whose value is
 * printed beside it plus a spoken sentence is already the house pattern for a measure.
 */
export function ConfidenceMeter({
  value,
  isProposed,
  evidencedPeak,
}: {
  value: number | null;
  isProposed: boolean;
  evidencedPeak: number | null;
}): React.JSX.Element {
  // Clamped rather than trusted: the API says 0-100, and a bar that overflows its track on
  // stage because one row came back at 101 is not a failure worth risking.
  const width = value === null ? 0 : Math.max(0, Math.min(100, value));

  // Computed inside the conditional so TypeScript narrows both operands - and so the two
  // numbers can only ever come from the props this render was handed.
  const comparison =
    isProposed && value !== null && evidencedPeak !== null && value < evidencedPeak
      ? `Scored ${Math.round(value)} against ${Math.round(evidencedPeak)} for the best-sourced item in this brief — the platform inferred this link, it did not read it.`
      : null;

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-3">
        <div
          aria-hidden="true"
          className="h-2 min-w-0 flex-1 overflow-hidden rounded-[2px] bg-line"
        >
          {value === null ? null : (
            <div
              className={cn(
                'h-full rounded-[2px]',
                // A second hue for the proposed item, so the contrast survives a glance.
                // It is never the only cue: the AI-proposed badge and the caption below
                // both say it in words.
                isProposed ? 'bg-proposed' : 'bg-accent',
              )}
              style={{ width: `${width}%` }}
            />
          )}
        </div>

        <span
          className={cn(
            'tabular shrink-0 font-display text-base font-semibold leading-none',
            isProposed ? 'text-proposed' : 'text-ink',
          )}
        >
          {value === null ? (
            <>
              <span aria-hidden="true" className="text-slate-700">
                &mdash;
              </span>
              <span className="sr-only">not assessed</span>
            </>
          ) : (
            <>
              {Math.round(value)}
              <span className="sr-only">{` Confidence ${Math.round(value)} out of 100.`}</span>
            </>
          )}
        </span>
      </div>

      {comparison === null ? null : (
        <p className="mt-1.5 max-w-[72ch] text-label leading-snug text-slate-700">
          {comparison}
        </p>
      )}
    </div>
  );
}
