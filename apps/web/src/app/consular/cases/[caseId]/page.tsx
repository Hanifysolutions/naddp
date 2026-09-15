import * as React from 'react';
import type { Metadata } from 'next';

import { CaseWorkspaceView } from '@/components/consular/case-workspace';

export const metadata: Metadata = {
  title: 'Consular case',
  description:
    'One consular case: its service-level clock, evidence metadata, timeline, and an AI triage recommendation that a named officer confirms. Synthetic demonstration data.',
};

/**
 * One consular case.
 *
 * The id is handed to a client component rather than fetched here: the case and the events on it
 * are authorisation-scoped, so they must re-query when the demo identity changes. Follows the
 * `meetings/[meetingId]` params pattern.
 */
export default async function ConsularCasePage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}): Promise<React.JSX.Element> {
  const { caseId } = await params;
  return (
    <div className="p-4 laptop:p-6">
      <CaseWorkspaceView caseId={caseId} />
    </div>
  );
}
