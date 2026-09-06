import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * A loading placeholder.
 *
 * Accessibility: a skeleton is decoration for a region that is already announced as
 * busy by its container (`aria-busy` / `role="status"`), so it is hidden from assistive
 * technology here rather than read out as a stream of empty boxes.
 */
function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>): React.JSX.Element {
  return (
    <div
      aria-hidden="true"
      className={cn('animate-pulse rounded-md bg-muted', className)}
      {...props}
    />
  );
}

export { Skeleton };
