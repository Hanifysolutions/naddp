import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * One labelled fact, the replacement for an `A · B · C` meta line.
 *
 * The same `dt`/`dd` idiom as `components/stakeholders/dossier-view.tsx`: a quiet sentence-case
 * label in --slate-700 (the AA-safe muted text on paper; --slate-400 is not a text colour
 * there) and the value in ink. Must be rendered inside a `<dl>`.
 */
export function MeetingField({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}): React.JSX.Element {
  return (
    <div className={cn('flex min-w-0 items-baseline gap-1.5', className)}>
      <dt className="shrink-0 text-label text-slate-700">{label}</dt>
      <dd className="min-w-0 text-label font-medium text-ink">{children}</dd>
    </div>
  );
}
