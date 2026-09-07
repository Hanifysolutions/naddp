import * as React from 'react';

import type { Distribution } from '@/lib/command-view';
import { formatCount } from '@/lib/command-view';
import { cn } from '@/lib/utils';

/**
 * A one-series distribution: counts per stage, status, strength or consent state.
 *
 * Design decisions, and why each one is not a preference:
 *
 *  - **One hue, not a categorical palette.** These bars encode magnitude within a single
 *    series, so colour carries no identity - the row label does. A palette here would
 *    invite the reader to compare hues that mean nothing.
 *  - **Every value is written out.** The bar is a secondary encoding; the number beside it
 *    is the primary one. That makes the chart legible to a screen reader, in forced-colours
 *    mode, and in a photograph of a projector screen, which is the actual viewing
 *    condition for a demo.
 *  - **A zero draws no bar and still shows "0".** An empty track next to a printed zero
 *    cannot be misread as a missing bucket - and a bucket the API did not report at all
 *    prints an em dash instead, never a zero.
 *  - **The bars are `aria-hidden`.** They restate what the labels and values already say,
 *    so announcing them would add noise, not information.
 *
 * Bars are scaled against the largest bucket in the set, and the caption says so: a full
 * bar means "the biggest bucket here", never "100% of anything".
 */
export function DistributionBars({
  distribution,
  className,
}: {
  distribution: Distribution;
  className?: string;
}): React.JSX.Element {
  const { caption, entries, peak } = distribution;

  return (
    <section className={cn('min-w-0', className)} aria-label={caption}>
      <p className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
        {caption}
        <span className="sr-only">
          {peak > 0
            ? `. Bars are scaled against the largest bucket, ${formatCount(peak)}.`
            : '. Every bucket is zero.'}
        </span>
      </p>

      <dl className="space-y-1">
        {entries.map((entry) => {
          const width =
            entry.value === null || peak === 0 ? 0 : Math.round((entry.value / peak) * 100);

          return (
            <div key={entry.key} className="flex items-center gap-2">
              <dt className="w-[6rem] shrink-0 truncate text-2xs text-muted-foreground desk:w-[7.5rem]">
                {entry.label}
              </dt>

              <div
                aria-hidden="true"
                className="h-2 min-w-0 flex-1 overflow-hidden rounded-[3px] bg-muted"
              >
                {width > 0 ? (
                  <div
                    className="h-full rounded-[3px] bg-[hsl(var(--chart-1))]"
                    style={{ width: `${width}%` }}
                  />
                ) : null}
              </div>

              <dd className="tabular w-9 shrink-0 text-right font-mono text-2xs text-foreground">
                {entry.value === null ? (
                  <>
                    <span aria-hidden="true" className="text-muted-foreground">
                      &mdash;
                    </span>
                    <span className="sr-only">not reported</span>
                  </>
                ) : (
                  formatCount(entry.value)
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
