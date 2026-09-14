import * as React from 'react';
import { ShieldCheck } from 'lucide-react';
import type { SessionSummary } from '@naddp/contracts';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

/**
 * Who the mission thinks you are, beside the control that changes it.
 *
 * Shows the persona's name and title, because "TRADE_OFFICER" is a role code and a person
 * on stage is a person. The clearance rank and any need-to-know compartments sit in the
 * tooltip and in the accessible description: they are the reason two officers see
 * different numbers, and a viewer who cannot see them reads a narrow clearance as a quiet
 * week.
 *
 * The email is synthetic and on a non-routable demo domain, so it is safe to display -
 * but it is not displayed, because nothing on this screen needs it.
 *
 * Hidden below the laptop breakpoint: at 1280px and under, the top bar's job is the badge
 * and the picker.
 *
 * This block sits ON the --slate-900 command bar, so its two lines take the dark-surface
 * pair: white at 14.29:1 for the name, --slate-300 at 4.61:1 for the title. --slate-400
 * would have been the muted token on paper and is NOT used here - 4.11:1 is below AA for
 * text. The tooltip itself is a portalled popover on --surface and keeps the light
 * palette, where --slate-700 labels measure 9.61:1.
 */
export function IdentitySummary({
  summary,
}: {
  summary: SessionSummary | null;
}): React.JSX.Element | null {
  if (summary === null) return null;

  // Read aloud as part of a sentence, so it is phrased as a clause.
  const compartments =
    summary.compartments.length === 0
      ? 'no need-to-know compartments'
      : `compartments: ${summary.compartments.join(', ')}`;

  // Read as a value in a labelled field, so it is phrased as a value.
  const compartmentValue =
    summary.compartments.length === 0 ? 'None held' : summary.compartments.join(', ');

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="hidden min-w-0 cursor-default flex-col items-end leading-none laptop:flex">
          <span className="truncate text-xs font-medium text-white">
            {summary.full_name}
          </span>
          <span className="mt-0.5 flex items-center gap-1 truncate text-2xs text-slate-300">
            <ShieldCheck aria-hidden="true" className="h-3 w-3" />
            {summary.title}
          </span>
          <span className="sr-only">
            Acting as {summary.full_name}, {summary.title}, {summary.mission}. Clearance
            rank {summary.clearance_rank}, {compartments}. This is a demonstration
            identity; authorisation is enforced by the API.
          </span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom" align="end" className="max-w-[18rem]">
        <p className="font-medium">{summary.mission}</p>
        {/*
          Two discrete labelled fields rather than a `rank · compartments` meta string.
          The clearance rank and the compartment list are different kinds of fact and a
          middle dot implies they are one; naming each is also what lets the compartment
          list be empty without reading as a formatting accident.
        */}
        <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-2xs">
          <dt className="text-slate-700">Clearance rank</dt>
          <dd className="tabular">{summary.clearance_rank}</dd>
          <dt className="text-slate-700">Compartments</dt>
          <dd>{compartmentValue}</dd>
        </dl>
        <p className="mt-1.5 text-2xs">
          Identity is faked by the role picker. Permissions, clearance and the audit trail
          are real.
        </p>
      </TooltipContent>
    </Tooltip>
  );
}
