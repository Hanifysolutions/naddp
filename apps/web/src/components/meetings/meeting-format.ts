/**
 * Date and time captions for the meetings screens.
 *
 * The rest of the app formats dates per file; meetings need the same three shapes in six
 * components (a date, a moment, a slot), and six private copies of a time-range formatter is
 * six chances for the index and the detail to disagree about when a meeting is. One module,
 * one locale, one set of options.
 *
 * **Times are shown in the reader's own clock, and the slot says which clock that is.** The
 * API sends UTC instants; the mission sits in one timezone and its counterparts in another,
 * so a bare "10:00" on a diplomatic diary is an ambiguity that has cost people meetings. The
 * detail header carries the zone name; the dense index rows do not repeat it on every line.
 *
 * Every function here renders from the value it was given. None supplies a default time, and
 * none invents a date when the input is missing - the callers branch on null first.
 */

const LOCALE = 'en-AU';

const DATE_FORMAT = new Intl.DateTimeFormat(LOCALE, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});

const WEEKDAY_DATE_FORMAT = new Intl.DateTimeFormat(LOCALE, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});

const TIME_FORMAT = new Intl.DateTimeFormat(LOCALE, {
  hour: 'numeric',
  minute: '2-digit',
});

const ZONE_FORMAT = new Intl.DateTimeFormat(LOCALE, {
  hour: 'numeric',
  timeZoneName: 'short',
});

/** "11 Sep 2026". */
export function formatDate(value: string): string {
  return DATE_FORMAT.format(new Date(value));
}

/** "11 Sep 2026, 4:00 pm". */
export function formatDateTime(value: string): string {
  const date = new Date(value);
  return `${DATE_FORMAT.format(date)}, ${TIME_FORMAT.format(date)}`;
}

/** "Fri 11 Sep 2026". */
export function formatMeetingDay(value: string): string {
  return WEEKDAY_DATE_FORMAT.format(new Date(value));
}

/**
 * "11:00 am – 12:00 pm", or both full moments when the slot crosses midnight in the reader's
 * clock. The en dash is range punctuation, not a structural separator.
 */
export function formatTimeRange(start: string, end: string): string {
  const from = new Date(start);
  const to = new Date(end);
  if (DATE_FORMAT.format(from) !== DATE_FORMAT.format(to)) {
    return `${formatDateTime(start)} – ${formatDateTime(end)}`;
  }
  return `${TIME_FORMAT.format(from)} – ${TIME_FORMAT.format(to)}`;
}

/** The short name of the reader's timezone at that instant, e.g. "GMT+1" or "AEST". */
export function zoneName(value: string): string | null {
  const part = ZONE_FORMAT.formatToParts(new Date(value)).find(
    (entry) => entry.type === 'timeZoneName',
  );
  return part === undefined ? null : part.value;
}
