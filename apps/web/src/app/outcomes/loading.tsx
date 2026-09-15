import * as React from 'react';

import { OutcomesBoardSkeleton } from '@/components/outcomes/outcomes-board';

/** Route-level loading UI: the same skeleton the board shows while its query runs. */
export default function OutcomesLoading(): React.JSX.Element {
  return <OutcomesBoardSkeleton />;
}
