import * as React from 'react';

import { Skeleton } from '@/components/ui/skeleton';

/**
 * Route-level loading UI, shown while the layout resolves the demo identity server-side.
 *
 * It mirrors the pending state inside `components/intelligence/morning-brief.tsx` block for
 * block - one heading line, one standfirst line, four item cards, on the same rhythm and at
 * the same heights as the restyled cards - so the transition from the route skeleton to the
 * query skeleton to the brief itself is invisible rather than two different placeholders
 * swapping places. Change a height here and change it there in the same commit.
 *
 * This sits inside `intelligence/layout.tsx`, so the DEMO badge, the rail and the identity
 * control stay on screen throughout.
 */
export default function IntelligenceLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
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
    </div>
  );
}
