import * as React from 'react';
import type { Metadata } from 'next';

import { CommandBoard } from '@/components/command/command-board';

export const metadata: Metadata = {
  title: 'Command centre',
  description:
    'Executive overview of mission intelligence, opportunities, citizen services, relationships, diaspora capability and outcomes. Synthetic demonstration data.',
};

/**
 * The executive command centre.
 *
 * A thin server component: the metadata above, and a client board that reads the API. The
 * fetching lives on the client so that switching demo identity re-queries through TanStack
 * Query - the same cache the role picker invalidates - rather than depending on a server
 * render to notice a new cookie.
 *
 * Every figure the board shows comes from `GET /v1/command/today` and is counted by the API
 * under the caller's permissions and clearance. Nothing on this screen is invented, and a
 * tile the role may not read renders as an explicit refusal rather than as a zero.
 */
export default function CommandPage(): React.JSX.Element {
  return <CommandBoard />;
}
