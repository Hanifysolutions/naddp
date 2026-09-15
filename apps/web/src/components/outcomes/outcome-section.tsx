import * as React from 'react';
import Link from 'next/link';
import { Ban, Lock } from 'lucide-react';
import type { OutcomeFigure, OutcomeSection } from '@naddp/contracts';

import {
  TONE_TEXT,
  TONE_TICK,
  contextLabel,
  formatCount,
  joinWords,
  present,
} from '@/components/outcomes/outcome-format';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * One domain's outcomes on the Unified Outcomes board.
 *
 * **Two states, never confused.** A section the role may read shows its figures, each with the
 * domain and authorisation it was counted under. A section the role may not read shows a refusal
 * that names the permission the API wanted - never a panel of zeros, because the domain was never
 * queried and a zero would be a claim about the mission rather than about the reader.
 *
 * Instrument-panel tile (DESIGN_SYSTEM.md): hairline edge, no shadow, one loud figure. The state
 * tick comes from the section tone the server decided.
 */
export function OutcomeSectionCard({
  section,
  className,
  figureGridClass,
}: {
  section: OutcomeSection;
  className: string;
  /** Column count for the secondary figures, as Tailwind classes: tiles differ in width. */
  figureGridClass: string;
}): React.JSX.Element {
  const headingId = `outcome-${section.key}`;
  const granted = section.authorisation.granted;
  const [primary, ...rest] = section.figures;

  return (
    <Card
      aria-labelledby={headingId}
      className={cn('flex h-full flex-col overflow-hidden', className)}
    >
      <div
        className={cn('flex h-full flex-col', granted ? TONE_TICK[section.tone] : null)}
      >
        <CardHeader className="gap-1 pb-3">
          <div className="flex items-start justify-between gap-2">
            <h3 id={headingId} className="text-sm font-semibold leading-tight text-ink">
              {section.title}
            </h3>
            {granted ? null : (
              <span className="shrink-0 rounded border border-line px-1.5 py-0.5 text-2xs font-medium text-slate-700">
                <Lock aria-hidden="true" className="mr-1 inline h-3 w-3" />
                Restricted
              </span>
            )}
          </div>
          <p className="text-xs leading-snug text-slate-700">{section.summary}</p>
          {granted && present(section.href) ? (
            <Link
              href={section.href}
              className="w-fit text-label font-medium text-accent underline underline-offset-4 hover:text-ink"
            >
              Open
              <span className="sr-only">{` ${section.title}`}</span>
            </Link>
          ) : null}
        </CardHeader>

        <CardContent className="flex flex-1 flex-col gap-4">
          {granted ? (
            <>
              {primary === undefined ? null : (
                <PrimaryFigure figure={primary} context={section.bounded_context} />
              )}
              {rest.length === 0 ? null : (
                <dl className={cn('grid gap-x-5 gap-y-3', figureGridClass)}>
                  {rest.map((figure) => (
                    <SecondaryFigure
                      key={figure.key}
                      figure={figure}
                      context={section.bounded_context}
                    />
                  ))}
                </dl>
              )}
            </>
          ) : (
            <Refusal section={section} />
          )}
          <SectionFootnote section={section} />
        </CardContent>
      </div>
    </Card>
  );
}

function PrimaryFigure({
  figure,
  context,
}: {
  figure: OutcomeFigure;
  context: string;
}): React.JSX.Element {
  return (
    <div className="flex min-w-0 flex-col-reverse gap-1.5">
      <div className="min-w-0 space-y-0.5">
        <p className="text-label font-medium text-ink">{figure.label}</p>
        <p className="max-w-[62ch] text-2xs leading-snug text-slate-700">
          {figure.detail}
        </p>
        <FigureProvenance figure={figure} context={context} />
      </div>
      <FigureValue figure={figure} loud />
    </div>
  );
}

function SecondaryFigure({
  figure,
  context,
}: {
  figure: OutcomeFigure;
  context: string;
}): React.JSX.Element {
  return (
    <div className="flex min-w-0 flex-col-reverse gap-1">
      <dt className="min-w-0 space-y-0.5">
        <span className="block text-label text-slate-700">{figure.label}</span>
        <span className="block text-2xs leading-snug text-slate-700">
          {figure.detail}
        </span>
        <FigureProvenance figure={figure} context={context} />
      </dt>
      <dd>
        <FigureValue figure={figure} loud={false} />
      </dd>
    </div>
  );
}

/**
 * A figure, or the reason there is none. A withheld figure keeps the quiet size and a lock: it
 * never shouts an em dash across the room, and it is never a zero.
 */
function FigureValue({
  figure,
  loud,
}: {
  figure: OutcomeFigure;
  loud: boolean;
}): React.JSX.Element {
  if (!present(figure.value)) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xl text-slate-700">
        <Lock aria-hidden="true" className="h-3.5 w-3.5" />
        <span aria-hidden="true">&mdash;</span>
        <span className="sr-only">
          {`Withheld: this figure requires ${joinWords(figure.authorisation.missing_permissions)}, which your role does not hold.`}
        </span>
      </span>
    );
  }
  return (
    <span
      className={cn(
        'tabular block font-display font-semibold leading-none',
        loud ? 'figure-settle text-figure' : 'text-xl',
        TONE_TEXT[figure.tone],
      )}
    >
      {formatCount(figure.value)}
      {present(figure.of) ? (
        <span className="ml-1.5 text-sm font-medium text-slate-700">
          of {formatCount(figure.of)}
        </span>
      ) : null}
    </span>
  );
}

/** Which domain counted this figure, and under which authorisation. One line. */
function FigureProvenance({
  figure,
  context,
}: {
  figure: OutcomeFigure;
  context: string;
}): React.JSX.Element {
  const {
    granted,
    required_permissions: required,
    missing_permissions: missing,
  } = figure.authorisation;
  return (
    <span className="block text-2xs leading-snug text-slate-700">
      {granted
        ? `Counted in ${contextLabel(context)} under ${joinWords(required)}.`
        : `Withheld: requires ${joinWords(missing)}, which your role does not hold.`}
    </span>
  );
}

/**
 * The refusal. It names the permission the API wanted, because a refusal a viewer can trace is a
 * demonstrable control, and it says plainly that this is not a zero.
 */
function Refusal({ section }: { section: OutcomeSection }): React.JSX.Element {
  return (
    <div className="flex items-start gap-2.5 rounded-md border border-dashed border-line px-3 py-3">
      <Ban aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-slate-700" />
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium text-ink">Your role does not have access</p>
        <p className="text-xs leading-snug text-slate-700">
          {`Reading these outcomes requires ${joinWords(section.authorisation.missing_permissions)}, which your current demo role does not hold. The ${contextLabel(section.bounded_context)} domain was never queried, so this is not a count of zero - it is a refusal. Nothing from it reaches any other figure on this board.`}
        </p>
      </div>
    </div>
  );
}

function SectionFootnote({ section }: { section: OutcomeSection }): React.JSX.Element {
  const { granted, required_permissions: required } = section.authorisation;
  const zones = section.counted_across.map((zone) => CLASSIFICATION_LABELS[zone]);
  return (
    <p className="mt-auto border-t border-line pt-2.5 text-2xs leading-snug text-slate-700">
      <span className="font-medium text-ink">
        {contextLabel(section.bounded_context)}
      </span>
      {granted
        ? ` domain, authorised separately under ${joinWords(required)} and counted across ${joinWords(zones)}.`
        : ` domain, authorised separately under ${joinWords(required)}. Withheld for your role.`}
    </p>
  );
}
