import * as React from 'react';
import { Bot, TriangleAlert } from 'lucide-react';
import type { ApiError, OpportunityPage } from '@naddp/contracts';

import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { formatCount } from '@/lib/command-view';
import { OPPORTUNITY_STAGE_LABELS } from '@/lib/enum-labels';

/**
 * The rows behind the pipeline counts.
 *
 * This strip exists for one reason: `is_proposed_by_ai`. The hero opportunity of this demo
 * - the Australian lithium value-chain partnership with a Nigerian skilled-migration
 * training corridor - is **AI-proposed, not reported** (BUILD_BIBLE §2, OPEN_QUESTIONS
 * Q-17). No public source connects an Australian lithium operator to Nigeria. An
 * AI-proposed opportunity sitting unmarked in a list of detected ones would quietly claim
 * a provenance it does not have, so the flag the API returns is rendered every time it is
 * true, in words, with an icon, and never by colour alone.
 *
 * Nothing here is fetched: the parent owns the query, and this component renders whatever
 * state it was handed. An empty page renders nothing at all - the tile's own empty state
 * has already said that every count is zero, and repeating it would be noise.
 */
export interface OpportunityStripProps {
  /** Whether this session holds `read:opportunity`. When false, nothing is rendered. */
  canRead: boolean;
  data: OpportunityPage | null;
  error: ApiError | null;
  isPending: boolean;
}

export function OpportunityStrip({
  canRead,
  data,
  error,
  isPending,
}: OpportunityStripProps): React.JSX.Element | null {
  // The tile itself is already in its `forbidden` state for a role without the permission.
  // Saying so twice on one card is nagging, not clarity.
  if (!canRead) return null;

  if (error !== null) {
    return (
      <p className="flex items-start gap-1.5 text-2xs leading-snug text-destructive">
        <TriangleAlert aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          The pipeline rows could not be loaded ({error.message}). The counts above came
          from a separate request and are unaffected.
        </span>
      </p>
    );
  }

  if (isPending) {
    return (
      <div className="space-y-1.5" aria-busy="true">
        <Skeleton className="h-3 w-2/3" />
        <Skeleton className="h-3 w-1/2" />
        <span className="sr-only">Loading recent opportunities</span>
      </div>
    );
  }

  if (data === null || data.items.length === 0) return null;

  return (
    <section aria-label="Most recently updated opportunities" className="min-w-0">
      <p className="mb-1.5 text-2xs font-medium uppercase tracking-wide text-muted-foreground">
        Most recently updated
        <span className="ml-1 font-normal normal-case tracking-normal">
          ({formatCount(data.items.length)} of {formatCount(data.total)})
        </span>
      </p>

      <ul className="space-y-1.5">
        {data.items.map((item) => (
          <li key={item.id} className="flex items-start justify-between gap-2">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs leading-snug text-foreground">
                {item.title}
              </span>
              <span className="mt-0.5 flex flex-wrap items-center gap-1">
                <Badge variant="outline" className="text-2xs font-medium">
                  {OPPORTUNITY_STAGE_LABELS[item.stage]}
                </Badge>
                {item.is_proposed_by_ai ? <AiProposedBadge /> : null}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The provenance marker for an AI-proposed opportunity.
 *
 * Deliberately worded as a limitation rather than a feature. "Pending officer
 * qualification" is the state the record is actually in: a machine drew a line between two
 * facts, and no human has yet agreed with it.
 */
function AiProposedBadge(): React.JSX.Element {
  return (
    <Badge variant="warning" className="gap-1 text-2xs font-medium">
      <Bot aria-hidden="true" className="h-3 w-3" />
      AI-proposed
      <span className="sr-only">
        , not reported by any source. Pending officer qualification.
      </span>
    </Badge>
  );
}
