import * as React from 'react';
import type { Metadata } from 'next';

import { MeetingDetailView } from '@/components/meetings/meeting-detail';

export const metadata: Metadata = {
  title: 'Meeting',
  description:
    'One meeting: its sourced pre-read, agenda and attendees, and the follow-up that cannot be sent until a named human approves it. Synthetic demonstration data.',
};

/**
 * One meeting.
 *
 * The id is handed to a client component rather than fetched here: the meeting and the
 * actions on its follow-up are authorisation-scoped, so they must re-query when the demo
 * identity changes, which is TanStack Query's job rather than a server render's. Follows the
 * `stakeholders/organisations/[organisationId]` params pattern.
 */
export default async function MeetingPage({
  params,
}: {
  params: Promise<{ meetingId: string }>;
}): Promise<React.JSX.Element> {
  const { meetingId } = await params;
  return (
    <div className="p-4 laptop:p-6">
      <MeetingDetailView meetingId={meetingId} />
    </div>
  );
}
