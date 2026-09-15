import * as React from 'react';

import { KnowledgeDeskSkeleton } from '@/components/knowledge/knowledge-desk';

/** Route-level loading UI: the same skeleton the desk shows while its query runs. */
export default function KnowledgeLoading(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <KnowledgeDeskSkeleton />
    </div>
  );
}
