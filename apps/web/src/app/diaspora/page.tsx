import * as React from 'react';
import type { Metadata } from 'next';

import { DiasporaDesk } from '@/components/diaspora/diaspora-desk';

export const metadata: Metadata = {
  title: 'Diaspora',
  description:
    'Capability search across the consented diaspora directory. Returns candidates only - profiles without consent are never loaded, and the platform contacts no one. Synthetic demonstration data.',
};

/**
 * The Diaspora desk.
 *
 * A thin server component over a client view, as on every staff screen: switching demo identity
 * has to re-query through TanStack Query - the cache the role picker clears - because the
 * directory a search may reach is scoped by role. The page padding lives here only, so
 * `loading.tsx` and the view share one margin.
 */
export default function DiasporaPage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <DiasporaDesk />
    </div>
  );
}
