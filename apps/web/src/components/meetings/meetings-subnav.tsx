'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { useDemoSession } from '@/components/layout/session-provider';
import { cn } from '@/lib/utils';

/**
 * The two views of the meetings context: the diary, and the approval queue.
 *
 * **The queue link is a courtesy, not a control.** It appears when the session holds
 * `approve:meeting_followup` so that four of the six roles are not offered a page that will
 * refuse them; the API is still the gate, and a Trade Officer who types the URL is refused and
 * audited by the server, not by this component. Same rule as the rail (`lib/nav.ts`).
 *
 * The current view is marked by an underline rule, a heavier weight and `aria-current` - a
 * shape and an attribute, never colour alone.
 */
export function MeetingsSubnav(): React.JSX.Element {
  const session = useDemoSession();
  const pathname = usePathname();

  const items: readonly { href: string; label: string }[] = [
    { href: '/meetings', label: 'Meetings' },
    ...(session.can('approve:meeting_followup')
      ? [{ href: '/meetings/approvals', label: 'Approval queue' }]
      : []),
  ];

  return (
    <nav aria-label="Meetings views" className="border-b border-line">
      <ul className="-mb-px flex flex-wrap gap-x-5">
        {items.map((item) => {
          const active = pathname === item.href;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'inline-flex border-b-2 px-0.5 pb-2 pt-1 text-sm transition-colors',
                  active
                    ? 'border-accent font-semibold text-ink'
                    : 'border-transparent font-medium text-slate-700 hover:border-slate-400 hover:text-ink',
                )}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
