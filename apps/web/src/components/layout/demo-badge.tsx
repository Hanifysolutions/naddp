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
 *  - Announced, not just drawn. The visible text reads "Demo", which is terse enough to
 *    be ambiguous out of context, so the accessible name is the full sentence. Screen
 *    reader users get the same warning as sighted users, not a weaker one.
 *  - The icon is decorative (`aria-hidden`); the words carry the meaning, satisfying
 *    WCAG 1.4.1 (colour is not the only cue).
 *
 * This badge keeps the `demo` tokens deliberately: the amber is the one loud non-semantic
 * colour in an otherwise disciplined palette, and it is the thing that must be unmissable
 * in a screenshot or a projected photo. Against the --slate-900 command bar the fill
 * measures 7.16:1, so it separates from the bar on its own; demo-foreground on it is
 * 7.48:1. The demo-border is only 2.48:1 there, which is fine - with a 7.16:1 fill the
 * badge's boundary is carried by the fill, not the hairline (SC 1.4.11).
 *
 * What it does NOT keep is the tracked-out uppercase. DESIGN_SYSTEM.md asks for sentence
 * case everywhere and no tracked-out caps anywhere in the app; loudness here is carried by
 * the amber, the weight and the flask, which is more than enough.
 */
export function DemoBadge({ className }: { className?: string }): React.JSX.Element {
  return (
    <Badge
      variant="demo"
      className={cn('gap-1.5 px-2 py-1 text-2xs font-bold', className)}
    >
      <FlaskConical aria-hidden="true" />
      <span aria-hidden="true">Demo</span>
      {/*
        Two discrete fields separated by a hairline rule, not by a middle dot. The dot
        would be the `A · B` meta-string device DESIGN_SYSTEM.md lists as an anti-goal;
        a rule says the same thing structurally and survives being read aloud, because
        the accessible name below is a real sentence either way.
      */}
      <span
        aria-hidden="true"
        className="hidden h-3 w-px bg-demo-foreground/40 laptop:inline-block"
      />
      <span aria-hidden="true" className="hidden font-semibold laptop:inline">
        Synthetic data
      </span>
      <span className="sr-only">
        Demonstration environment. All data shown here is synthetic and does not describe
        any real person, case or organisation.
      </span>
    </Badge>
  );
}
