'use client';

import * as React from 'react';
import type { MorningBriefItem } from '@naddp/contracts';

import { AiProposedBadge } from '@/components/intelligence/ai-proposed-badge';
import { ConfidenceMeter } from '@/components/intelligence/confidence-meter';
import { EvidenceList } from '@/components/intelligence/evidence-list';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { BRIEF_ITEM_TYPE_LABELS, CLASSIFICATION_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * One numbered entry on the morning brief.
 *
 * **`body` and `so_what` are rendered apart, and that separation is the product.** The API
 * keeps them as two columns for a reason the schema states outright: `body` is the sourced
 * account and is what `evidence` supports, `so_what` is the mission's own analytic judgement
 * and the evidence does not support it and does not claim to. Running them together is how a
 * brief quietly becomes a summary, and how a reader ends up citing an inference to an
 * Ambassador as though a source had said it. The judgement therefore gets its own block with
 * its own rule and its own label, so a reader can see where the sourcing stops.
 *
 * **Provenance comes from one field only.** `is_proposed_by_ai` decides the state tick, the
 * card's ground, the badge and the comparison drawn by the confidence meter. Nothing here
 * inspects the headline text, the item type or a confidence threshold: those correlate with
 * proposal in today's seed and would silently stop correlating the first time the seed
 * changed, at which point the screen would misattribute a machine's inference to a human's
 * report.
 *
 * **A proposed item is made QUIETER, never louder.** It reads at a lower contrast than the
 * evidenced items beside it - a muted violet tick, a cool tint under it, a tinted rather
 * than filled badge and a violet confidence fill that is shorter than its neighbours'. That
 * is the Q-17 honesty gap rendered chromatically: the least certain thing on the brief leads
 * it, and looks like the least certain thing on it. An alarm tone would say the opposite -
 * that this item is the one demanding attention - and would be the inversion the gap exists
 * to prevent.
 *
 * `evidencedPeak` is passed down rather than computed here, because the comparison is
 * "against the best-sourced item *in this brief*" - a fact about the whole document that a
 * single card cannot see.
 */
export function BriefItemCard({
  item,
  evidencedPeak,
}: {
  item: MorningBriefItem;
  evidencedPeak: number | null;
}): React.JSX.Element {
  return (
    <Card
      className={cn(
        item.is_proposed_by_ai &&
          // `.tick-proposed` is the named state tick and carries the intent. The two
          // `border-l-*` utilities restate it because `Card` sets `border border-line`
          // from the utilities layer, which outranks the components layer the tick is
          // declared in - without them the tick would be overpainted by the hairline.
          'tick-proposed border-l-2 border-l-proposed bg-proposed-weak/40',
      )}
    >
      <CardHeader className="gap-1.5">
        <div className="flex items-start gap-2.5">
          {/* `position` is a zero-based render order from the API; a reader counting entries
              on a page starts at one. The chip is a display convention over that ordering,
              never a figure the API supplied - and it is set in tabular figures rather than
              a monospace face, which DESIGN_SYSTEM.md keeps for identifiers alone. */}
          <span className="mt-0.5 shrink-0 rounded-sm border border-line px-1.5 py-0.5 text-label font-semibold tabular text-slate-700">
            {item.position + 1}
          </span>
          <h3 className="text-base font-semibold leading-snug text-ink">
            {item.headline}
          </h3>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
          <Badge variant="outline" className="text-2xs font-medium">
            {BRIEF_ITEM_TYPE_LABELS[item.item_type]}
          </Badge>
          {/* The item's own zone, not the brief's. An item you are not cleared for is absent
              from the list entirely, so this badge always describes something on screen. */}
          <Badge variant="outline" className="text-2xs font-medium">
            {CLASSIFICATION_LABELS[item.classification]}
          </Badge>
          {item.is_proposed_by_ai ? <AiProposedBadge /> : null}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Prose is held to a readable measure. A brief read at arm's length across a table
            is not helped by a 120-character line. */}
        <p className="max-w-[72ch] text-sm leading-relaxed">{item.body}</p>

        {/* A bare 2px rule, no tint and no radius: this is a structural mark showing where
            the sourcing stops and the mission's own reading starts, not a decorated panel. */}
        <div className="border-l-2 border-slate-400 py-0.5 pl-3">
          <p className="text-label text-slate-700">Mission judgement</p>
          <p className="mt-0.5 max-w-[72ch] text-sm leading-relaxed">{item.so_what}</p>
        </div>

        <ConfidenceMeter
          value={item.confidence ?? null}
          isProposed={item.is_proposed_by_ai}
          evidencedPeak={evidencedPeak}
        />

        <EvidenceList evidence={item.evidence} />
      </CardContent>
    </Card>
  );
}
