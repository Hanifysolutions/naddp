import * as React from 'react';
import type { Metadata } from 'next';

import { MorningBrief } from '@/components/intelligence/morning-brief';

export const metadata: Metadata = {
  title: 'Morning brief',
  description:
    'The mission morning brief: every claim carries its sources as resolving public citations, and the routing decision behind the draft is on the page. Synthetic demonstration data.',
};

/**
 * The morning brief.
 *
 * A thin server component over a client view, for the same reason the command centre and the
 * pipeline are: switching demo identity has to re-query through TanStack Query - the cache the
 * role picker invalidates - rather than depend on a server render noticing a new cookie.
 *
 * The page padding lives here and only here. `MorningBrief` adds none of its own, so the
 * route-level skeleton in `loading.tsx` and the real view sit on exactly the same margin and
 * nothing shifts when the data lands.
 */
export default function IntelligencePage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <MorningBrief />
    </div>
  );
}
