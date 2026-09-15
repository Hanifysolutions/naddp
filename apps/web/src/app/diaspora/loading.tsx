import * as React from 'react';

import { DiasporaDeskSkeleton } from '@/components/diaspora/diaspora-desk';

/** Route-level loading UI: the same skeleton the desk shows while its query runs. */
export default function DiasporaLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <DiasporaDeskSkeleton />
    </div>
  );
}
