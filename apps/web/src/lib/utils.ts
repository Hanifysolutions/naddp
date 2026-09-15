import { type ClassValue, clsx } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/**
 * tailwind-merge, taught this project's type ramp.
 *
 * `tailwind.config.ts` adds role-named font sizes (`text-label`, `text-figure-sm`,
 * `text-figure`, `text-figure-lg`). tailwind-merge only recognises the stock t-shirt sizes as
 * font sizes, so it filed `text-label` in the text-COLOUR group - and whenever a component
 * merged `text-label` with a colour such as `text-slate-700` or `text-warn-ink`, it dropped
 * the size as a "conflict", and the element silently inherited its parent's size. The status
 * chips were the visible casualty. Declaring the names here puts them in the font-size group,
 * where they conflict only with other sizes.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['2xs', 'label', 'figure-sm', 'figure', 'figure-lg'] }],
    },
  },
});

/**
 * Merge conditional class names and resolve Tailwind conflicts, last-wins.
 * The standard shadcn/ui helper: `clsx` handles conditionals, `tailwind-merge`
 * ensures a caller-supplied `className` can actually override a component default.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
