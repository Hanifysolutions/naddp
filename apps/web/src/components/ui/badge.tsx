import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

/**
 * Status vocabulary for the demo. Colour never carries meaning alone (WCAG 1.4.1) -
 * every caller pairs a variant with a text label, and usually an icon.
 *
 *   success  a completed / approved outcome
 *   warning  awaiting human approval - the trust signal of the whole product
 *   destructive  genuine risk or a denial
 *   demo     reserved exclusively for the DEMO / SYNTHETIC DATA marker
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 [&_svg]:size-3 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        destructive: 'border-transparent bg-destructive text-destructive-foreground',
        success: 'border-transparent bg-success text-success-foreground',
        warning: 'border-transparent bg-warning text-warning-foreground',
        demo: 'border-demo-border bg-demo text-demo-foreground',
        outline: 'border-line text-slate-700',
        /*
         * AI-proposed. The Q-17 honesty colour, and deliberately the QUIETEST
         * variant here: a tint rather than a fill, so a proposed item reads as
         * cooler and more subordinate than the evidenced items beside it. The
         * gap is carried chromatically as well as numerically
         * (DESIGN_SYSTEM.md --proposed).
         */
        proposed: 'border-proposed/35 bg-proposed-weak text-proposed',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps): React.JSX.Element {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
