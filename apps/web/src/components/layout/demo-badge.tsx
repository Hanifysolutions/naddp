import * as React from 'react';
import { FlaskConical } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/**
 * The DEMO / SYNTHETIC DATA marker (BUILD_BIBLE §11).
 *
 * Non-negotiable properties, all of which are enforced structurally rather than by
 * convention:
 *
 *  - Always present. It is rendered by the app shell, not by any page, so no route can
 *    ship without it.
 *  - Never dismissible. There is no close affordance and no state; there is nothing to
 *    click and nothing to persist.
 *  - Announced, not just drawn. The visible text reads "DEMO", which is terse enough to
 *    be ambiguous out of context, so the accessible name is the full sentence. Screen
 *    reader users get the same warning as sighted users, not a weaker one.
 *  - The icon is decorative (`aria-hidden`); the words carry the meaning, satisfying
 *    WCAG 1.4.1 (colour is not the only cue).
 *
 * Contrast: demo-foreground on demo measures 7.45:1 in light and 8.55:1 in dark, and the
 * light-mode badge carries a 5.76:1 border so its boundary is perceivable against the
 * card surface behind it (SC 1.4.11).
 */
export function DemoBadge({ className }: { className?: string }): React.JSX.Element {
  return (
    <Badge
      variant="demo"
      className={cn('gap-1.5 px-2 py-1 text-2xs font-bold uppercase tracking-wider', className)}
    >
      <FlaskConical aria-hidden="true" />
      <span aria-hidden="true">Demo</span>
      <span aria-hidden="true" className="hidden font-semibold opacity-90 laptop:inline">
        {'·'} Synthetic data
      </span>
      <span className="sr-only">
        Demonstration environment. All data shown here is synthetic and does not describe
        any real person, case or organisation.
      </span>
    </Badge>
  );
}
