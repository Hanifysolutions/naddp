import * as React from 'react';

import { MeetingIndexSkeleton } from '@/components/meetings/meeting-index';

/**
 * Route-level loading UI, shown while the layout resolves the demo identity server-side.
 *
 * It renders the same skeleton the index shows while its query is pending, so the route
 * placeholder and the query placeholder are one component and cannot drift into two shapes
 * swapping places. It sits inside `meetings/layout.tsx`, so the rail and DEMO badge stay put.
 */
export default function MeetingsLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <MeetingIndexSkeleton />
    </div>
  );
}
