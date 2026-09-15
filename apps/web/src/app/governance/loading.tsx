import * as React from 'react';

import { GovernanceConsoleSkeleton } from '@/components/governance/governance-console';

/** Route-level loading UI: the same skeleton the console shows while its first page loads. */
export default function GovernanceLoading(): React.JSX.Element {
  return <GovernanceConsoleSkeleton />;
}
