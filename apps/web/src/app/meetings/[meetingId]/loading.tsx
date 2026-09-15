import * as React from 'react';

import { MeetingDetailSkeleton } from '@/components/meetings/meeting-detail';

/** Route-level loading UI: the same skeleton the detail view shows while its query runs. */
export default function MeetingLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <MeetingDetailSkeleton />
    </div>
  );
}
