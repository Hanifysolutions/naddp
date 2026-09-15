import * as React from 'react';
import type { Metadata } from 'next';

import { ConsularDashboardView } from '@/components/consular/consular-dashboard';

export const metadata: Metadata = {
  title: 'Consular',
  description:
    'Consular caseload, ageing and service-level risk over the cases this role is cleared to read. Synthetic demonstration data.',
};

/**
 * The consular command dashboard.
 *
 * A thin server component over a client view, as on every staff screen: switching demo identity
 * has to re-query through TanStack Query - the cache the role picker clears - rather than rely
 * on a server render noticing a new cookie. The page padding lives here only, so `loading.tsx`
 * and the view share one margin.
 */
export default function ConsularPage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <ConsularDashboardView />
    </div>
  );
}
