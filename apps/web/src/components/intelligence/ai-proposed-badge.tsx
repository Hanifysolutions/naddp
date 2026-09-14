'use client';

import * as React from 'react';
import { Bot } from 'lucide-react';

import { Badge } from '@/components/ui/badge';

/**
 * The provenance marker for an AI-proposed record.
 *
 * Deliberately worded as a limitation rather than a feature. "Pending officer
 * qualification" is the state the record is actually in: a machine drew a line between two
 * facts, and no human has yet agreed with it.
 *
 * This is a verbatim twin of the badge in `components/command/opportunity-strip.tsx`, and
 * the duplication is the point rather than an oversight to be refactored away. The same
 * claim - "the platform proposed this, nobody reported it" (BUILD_BIBLE section 2,
 * OPEN_QUESTIONS Q-17) - reaches the reader on the command centre and in the morning brief,
 * and the two screens must say it in the same words. A reader who sees one sentence on the
 * tile and a softer one in the brief will trust whichever is kinder.
 *
 * **The variant is `proposed`, not `warning`, and that is a claim about severity.** An
 * AI-proposed item is not a hazard to be flagged; it is a weaker claim to be discounted, so
 * it takes the muted violet tint DESIGN_SYSTEM.md reserves for unconfirmed provenance and
 * reads QUIETER than the evidenced items beside it. A warning fill would make the least
 * certain item on the brief the loudest thing on the page - the exact inversion the Q-17
 * honesty gap exists to prevent.
 *
 * Colour never carries the meaning alone (WCAG 1.4.1): there is an icon, there are words,
 * and the sr-only clause completes the sentence for a screen reader.
 */
export function AiProposedBadge(): React.JSX.Element {
  return (
    <Badge variant="proposed" className="gap-1 text-2xs font-medium">
      <Bot aria-hidden="true" className="h-3 w-3" />
      AI-proposed
      <span className="sr-only">
        , not reported by any source. Pending officer qualification.
      </span>
    </Badge>
  );
}
