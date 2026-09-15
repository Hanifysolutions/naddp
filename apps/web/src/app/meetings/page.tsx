import * as React from 'react';
import type { Metadata } from 'next';

import { MeetingIndex } from '@/components/meetings/meeting-index';

export const metadata: Metadata = {
  title: 'Meetings',
  description:
    'The mission diary: pre-reads before each meeting and follow-ups after, none sent without a named human approval. Synthetic demonstration data.',
};

/**
 * The meeting diary.
 *
 * A thin server component over a client view, for the same reason as every staff screen:
 * switching demo identity has to re-query through TanStack Query - the cache the role picker
 * clears - rather than depend on a server render noticing a new cookie. The page padding lives
 * here only, so `loading.tsx` and the view share one margin.
 */
export default function MeetingsPage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <MeetingIndex />
    </div>
  );
}
