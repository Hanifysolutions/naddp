import * as React from 'react';
import type { Metadata } from 'next';

import { OutcomesBoard } from '@/components/outcomes/outcomes-board';

export const metadata: Metadata = {
  title: 'Unified outcomes',
  description:
    'Mission outcomes across partnerships, citizen service, diaspora capability, relationships and meetings in one frame, each counted in its own domain under its own authorisation. Synthetic demonstration data.',
};

/**
 * The Unified Outcomes board: the demo's closing screen.
 *
 * A thin server component over a client board, as on every staff screen: switching demo identity
 * re-queries through TanStack Query - the cache the role picker clears - and the board recomposes,
 * because what each domain returns is decided by the API for the role asking.
 */
export default function OutcomesPage(): React.JSX.Element {
  return <OutcomesBoard />;
}
