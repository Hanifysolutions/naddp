import type { OutcomeFigure } from '@naddp/contracts';

/**
 * Formatting for the Unified Outcomes board, and nothing else.
 *
 * Every figure, tone and count on the board is decided by `GET /v1/outcomes`. These helpers turn
 * a server value into text or a class: they never derive one value from another.
 */

export type OutcomeTone = OutcomeFigure['tone'];

/** Figure colour by the tone the server decided. `warn` uses --warn-ink, as on the command tiles. */
export const TONE_TEXT: Readonly<Record<OutcomeTone, string>> = {
  neutral: 'text-ink',
  ok: 'text-ok',
  warn: 'text-warn-ink',
  risk: 'text-risk',
  proposed: 'text-proposed',
};

/**
 * The left border-tick, which marks state that needs a human or an honesty label. A resolved or
 * healthy state is not ticked: a calm board should look calm (DESIGN_SYSTEM.md).
 */
export const TONE_TICK: Readonly<Record<OutcomeTone, string | null>> = {
  neutral: null,
  ok: null,
  warn: 'tick-warn',
  risk: 'tick-risk',
  proposed: 'tick-proposed',
};

/** Bounded contexts, as the mission names them. */
const CONTEXT_LABELS: Readonly<Record<string, string>> = {
  opportunities: 'Opportunities',
  consular: 'Consular',
  diaspora: 'Diaspora',
  stakeholders: 'Stakeholders',
  meetings: 'Meetings',
};

export function contextLabel(context: string): string {
  return CONTEXT_LABELS[context] ?? context;
}

const NUMBER_FORMAT = new Intl.NumberFormat('en-AU');

/** A count. Zero formats as "0": a zero is a fact, and never a dash. */
export function formatCount(value: number): string {
  return NUMBER_FORMAT.format(value);
}

/** "a", "a and b", "a, b and c". */
export function joinWords(parts: readonly string[]): string {
  if (parts.length <= 1) return parts[0] ?? '';
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1] ?? ''}`;
}

/** Narrow an optional wire field that may also be null. */
export function present<T>(value: T | null | undefined): value is T {
  return value !== null && value !== undefined;
}
