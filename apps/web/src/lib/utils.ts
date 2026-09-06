import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge conditional class names and resolve Tailwind conflicts, last-wins.
 * The standard shadcn/ui helper: `clsx` handles conditionals, `tailwind-merge`
 * ensures a caller-supplied `className` can actually override a component default.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
