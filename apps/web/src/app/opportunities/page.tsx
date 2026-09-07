import * as React from 'react';
import type { Metadata } from 'next';

import { PipelineBoard } from '@/components/opportunities/pipeline-board';

export const metadata: Metadata = {
  title: 'Opportunity pipeline',
  description:
    'Bilateral opportunity pipeline across the critical-minerals and skilled-migration corridors, with server-validated stage transitions. Synthetic demonstration data.',
};

/**
 * The opportunity pipeline.
 *
 * A thin server component over a client board, for the same reason the command centre is:
 * switching demo identity has to re-query through TanStack Query - the cache the role picker
 * invalidates - rather than depend on a server render noticing a new cookie.
 *
 * Every column, count and button on this screen is decided by `GET /v1/opportunities/board`
 * under the caller's permissions and clearance. The client renders what the server says is
 * available and never infers an action from a role.
 */
export default function OpportunitiesPage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <PipelineBoard />
    </div>
  );
}
