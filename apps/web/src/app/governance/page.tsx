import * as React from 'react';
import type { Metadata } from 'next';

import { GovernanceConsole } from '@/components/governance/governance-console';

export const metadata: Metadata = {
  title: 'Governance',
  description:
    'The append-only audit log, with every approval and refusal, and on-demand verification that its hash chain is intact. Synthetic demonstration data.',
};

/**
 * The Governance page: the platform's tamper-evidence screen.
 *
 * A thin server component over a client console, as on every staff screen: switching demo
 * identity re-queries through TanStack Query, because what the log shows is scoped by the reader's
 * clearance and whether it shows anything at all is decided by `read:audit`.
 */
export default function GovernancePage(): React.JSX.Element {
  return <GovernanceConsole />;
}
