import * as React from 'react';

import { ApprovalQueueSkeleton } from '@/components/meetings/approval-queue';

/** Route-level loading UI: the same skeleton the queue shows while its query runs. */
export default function ApprovalQueueLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <ApprovalQueueSkeleton />
    </div>
  );
}
