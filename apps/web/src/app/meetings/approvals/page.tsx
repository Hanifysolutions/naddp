import * as React from 'react';
import type { Metadata } from 'next';

import { ApprovalQueueView } from '@/components/meetings/approval-queue';

export const metadata: Metadata = {
  title: 'Approval queue',
  description:
    'Outbound follow-ups waiting for a named human decision. Nothing here is sent without an approval recorded against a name. Synthetic demonstration data.',
};

/**
 * The approval queue.
 *
 * Served to every role and refused by the API for the roles that may not approve: the
 * refusal, and its audit row, are the server's to produce. See `ApprovalQueueView`.
 */
export default function ApprovalQueuePage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <ApprovalQueueView />
    </div>
  );
}
