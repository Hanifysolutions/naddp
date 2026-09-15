import type { CaseSla, SlaState } from '@naddp/contracts';

/**
 * Words and ticks for the consular service-level clock.
 *
 * The API measures in business days on the mission calendar and pauses the clock while a case
 * waits on the citizen (OPEN_QUESTIONS Q-15, `apps/api/app/domain/sla.py`). These helpers only
 * phrase what it measured: they never recompute a figure, and a value the API left null is
 * said as "not set" rather than turned into a zero.
 */

const DAYS_FORMAT = new Intl.NumberFormat('en-AU', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});

/** "2.5 business days", "1 business day". One decimal, because the clock is fractional. */
export function formatBusinessDays(days: number): string {
  const text = DAYS_FORMAT.format(Math.abs(days));
  return `${text} business day${text === '1' ? '' : 's'}`;
}

/**
 * What the clock means for the reader, in one phrase.
 *
 * Under one business day the hours are given as well: "0.4 business days" is exact, and
 * "about 10 hours" is what an officer holding a lost-passport case actually plans around. A
 * business day on this clock is a whole weekday, so the hours are weekday hours.
 */
export function slaPhrase(sla: CaseSla): string {
  const remaining = sla.remaining_business_days;
  switch (sla.state) {
    case 'NOT_SET':
      return 'No service standard is set for this case type';
    case 'STOPPED':
      if (sla.met === true) return 'Resolved within the service standard';
      if (sla.met === false) return 'Resolved outside the service standard';
      return 'The clock has stopped';
    case 'PAUSED':
      return remaining === null
        ? 'Paused while the case waits on the citizen'
        : `Paused with ${formatBusinessDays(remaining)} left`;
    case 'BREACHED':
      return remaining === null
        ? 'Past its service standard'
        : `${formatBusinessDays(remaining)} past the standard`;
    case 'DUE_SOON':
    case 'ON_TRACK': {
      if (remaining === null) return 'Remaining time not reported';
      if (remaining >= 1) return `${formatBusinessDays(remaining)} left`;
      const hours = Math.max(1, Math.round(remaining * 24));
      return `${formatBusinessDays(remaining)} left, about ${hours} hour${hours === 1 ? '' : 's'}`;
    }
  }
}

/**
 * The state tick a row or panel carries. Only two states earn one: a breach (`--risk`) and a
 * near-breach (`--warn`). Everything else stays quiet, so a queue reads as a scale with the
 * cases that need a human marked, rather than as a column of alarms.
 */
export function slaTick(state: SlaState): string | null {
  if (state === 'BREACHED') return 'tick-risk';
  if (state === 'DUE_SOON') return 'tick-warn';
  return null;
}

/** "ONLINE_PORTAL" as "Online portal". The server's word, re-cased, never replaced. */
export function sentenceCase(value: string): string {
  const spaced = value.replaceAll('_', ' ').toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** A label from a server-supplied map, falling back to the identifier the server sent. */
export function labelFrom(
  labels: Readonly<Partial<Record<string, string>>>,
  key: string,
): string {
  return labels[key] ?? key;
}
