import * as React from 'react';

import { ConsularDashboardSkeleton } from '@/components/consular/consular-dashboard';

/** Route-level loading UI: the same skeleton the dashboard shows while its query runs. */
export default function ConsularLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <ConsularDashboardSkeleton />
    </div>
  );
}
