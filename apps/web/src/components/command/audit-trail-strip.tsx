import * as React from 'react';
import { TriangleAlert } from 'lucide-react';
import type { ApiError, AuditEventPage } from '@naddp/contracts';

import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { auditActionLabel } from '@/lib/audit-labels';
import { cn } from '@/lib/utils';

/**
 * The newest rows of the append-only audit log, for the roles that may read it.
 *
 * `read:audit` is held by AMBASSADOR, DEPUTY and ADMIN only, so on most screens this
 * renders nothing. That asymmetry is deliberate and is part of the demonstration: the log
 * is not a widget everyone gets, it is a control with its own permission, and ADMIN can
 * read it while being able to read none of the content it describes.
 *
 * Denials appear here alongside successes, because the API records both. A trail that only
 * showed what worked would be evidence of nothing.
 */
export interface AuditTrailStripProps {
  /** Whether this session holds `read:audit`. When false, nothing is rendered. */
  canRead: boolean;
  data: AuditEventPage | null;
  error: ApiError | null;
  isPending: boolean;
}

export function AuditTrailStrip({
  canRead,
  data,
  error,
  isPending,
}: AuditTrailStripProps): React.JSX.Element | null {
  if (!canRead) return null;

  if (error !== null) {
    return (
      <p className="flex items-start gap-1.5 text-2xs leading-snug text-risk">
        <TriangleAlert aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
        <span>The audit trail could not be loaded ({error.message}).</span>
      </p>
    );
  }

  if (isPending) {
    return (
      <div className="space-y-1.5" aria-busy="true">
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-2/3" />
        <span className="sr-only">Loading the audit trail</span>
      </div>
    );
  }

  if (data === null || data.events.length === 0) return null;

  return (
    <section aria-label="Most recent governed actions" className="min-w-0">
      <p className="mb-1.5 text-label text-slate-700">Most recent governed actions</p>
      {/*
       * Every row carries a semantic left tick, because on this strip the allow/deny
       * outcome IS the content - the tick is the structural carrier of the one fact the
       * log exists to record, not an ornament. It is never the only carrier: the badge
       * says the same word and the action names itself underneath.
       */}
      <ul className="space-y-1">
        {data.events.map((event) => (
          <li
            key={event.id}
            className={cn(
              'flex items-start gap-2 pl-2',
              event.policy_result === 'DENY' ? 'tick-risk' : 'tick-ok',
            )}
          >
            {event.policy_result === 'DENY' ? (
              <Badge
                variant="destructive"
                className="mt-px shrink-0 text-2xs font-medium"
              >
                Denied
              </Badge>
            ) : (
              <Badge variant="outline" className="mt-px shrink-0 text-2xs font-medium">
                Allowed
              </Badge>
            )}
            <span className="min-w-0 flex-1">
              {/* The action in plain language, from the shared audit label map. The
                  closed-vocabulary token stays on the title for an auditor who needs it. */}
              <span className="block truncate text-2xs text-slate-700" title={event.action}>
                {auditActionLabel(event.action, event.policy_result)}
              </span>
              <span className="block truncate text-xs leading-snug text-ink">
                {event.summary}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
