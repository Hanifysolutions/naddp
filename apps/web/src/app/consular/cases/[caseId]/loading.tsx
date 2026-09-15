import * as React from 'react';

import { CaseWorkspaceSkeleton } from '@/components/consular/case-workspace';

/** Route-level loading UI: the same skeleton the case workspace shows while its query runs. */
export default function ConsularCaseLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <CaseWorkspaceSkeleton />
    </div>
  );
}
