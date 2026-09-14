'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  Ban,
  ExternalLink,
  EyeOff,
  Lock,
  Sparkles,
} from 'lucide-react';
import { isApiError, type Dossier, type TimelineEntry } from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchOrganisationDossier } from '@/lib/api-queries';
import { formatAud } from '@/lib/command-view';
import {
  CLASSIFICATION_LABELS,
  OPPORTUNITY_STAGE_LABELS,
  RELATIONSHIP_STRENGTH_LABELS,
} from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * Stakeholder 360: one counterpart, everything the mission knows, and where it came from.
 *
 * **The withheld notice is the most important element on this page.** A dossier assembles
 * four independently-classified collections, and when clearance removes rows from any of
 * them, what remains still looks like a complete history - a reader would conclude nothing
 * happened in the gap. The API returns a count of what it withheld; this view renders it
 * prominently rather than in a footnote, because "you are not seeing all of this" is a
 * stronger statement than anything else on the screen.
 *
 * Sources are rendered as real outbound links. An unverified one says so: `TODO_VERIFY`
 * means live but not yet confirmed by eye, and a reader deserves to know which is which
 * before quoting it to an Ambassador.
 *
 * **This page owns the app's external-link treatment** - `target="_blank"`, a
 * `rel="noreferrer noopener"` that denies the opened tab any handle on this one, a trailing
 * ExternalLink icon, and the --accent underline. The Intelligence citations copy it, so a
 * change here is a change to what "this leaves the mission's system" looks like everywhere.
 */

/**
 * The dossier's opportunity rows carry `stage` as a plain string rather than as the pipeline
 * enum, so the shared label map is read through a widened alias. An unrecognised value falls
 * back to itself: showing the server's own word is honest, showing `undefined` is not.
 */
const STAGE_LABELS: Readonly<Record<string, string | undefined>> =
  OPPORTUNITY_STAGE_LABELS;

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

function formatDate(value: string | null): string {
  if (value === null) return '—';
  return new Date(value).toLocaleDateString('en-AU', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function DossierView({
  organisationId,
}: {
  organisationId: string;
}): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const dossier = useQuery({
    queryKey: queryKeys.organisationDossier(role, organisationId),
    queryFn: ({ signal }) => fetchOrganisationDossier(organisationId, signal),
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle>No demo identity resolved</AlertTitle>
        <AlertDescription>Choose a role to open this dossier.</AlertDescription>
      </Alert>
    );
  }

  if (dossier.isPending) return <Skeleton className="h-[32rem]" />;

  if (dossier.isError) {
    const error = dossier.error;
    const refused = isApiError(error) && (error.status === 403 || error.status === 404);
    return (
      <div className="space-y-4">
        <BackLink />
        <Alert variant={refused ? 'warning' : 'destructive'} role="status">
          {refused ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <AlertTitle>
            {refused ? 'Not available to this role' : 'The dossier could not be loaded'}
          </AlertTitle>
          <AlertDescription>
            {isApiError(error) ? error.message : 'The API did not answer.'}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <Dossier360 dossier={dossier.data} />;
}

function BackLink(): React.JSX.Element {
  return (
    // The leading arrow is a back affordance, not the trailing "→" tell: it points at where
    // the link goes, and removing it would leave the control without one.
    <Link
      href="/stakeholders"
      className="inline-flex items-center gap-1 text-sm text-accent underline underline-offset-2"
    >
      <ArrowLeft aria-hidden="true" className="size-4" /> All stakeholders
    </Link>
  );
}

/** One labelled field. The replacement for the old `A · B · C` meta line. */
function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="text-label text-slate-700">{label}</dt>
      <dd className="text-label font-medium text-ink">{children}</dd>
    </div>
  );
}

function Dossier360({ dossier }: { dossier: Dossier }): React.JSX.Element {
  const withheld = dossier.withheld_interactions + dossier.withheld_opportunities;
  return (
    <div className="space-y-4">
      <BackLink />

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold text-ink">{dossier.name}</h1>
          <Badge variant="outline" className="text-2xs font-medium">
            {CLASSIFICATION_LABELS[dossier.classification]}
          </Badge>
          {dossier.dormant ? (
            // Dormancy is a state, so it keeps a semantic colour - with the words beside it.
            <Badge variant="warning" className="text-2xs font-medium">
              Dormant 90 days
            </Badge>
          ) : null}
        </div>
        <p className="max-w-[72ch] text-sm text-slate-700">{dossier.subtitle}</p>
        <p className="max-w-[72ch] text-sm text-ink">{dossier.description}</p>
        {/* Discrete labelled fields rather than one middle-dot string: each of these is a
            separate fact about the counterpart and a reader should be able to find one
            without parsing a sentence. */}
        <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <Field label="Country">{dossier.country}</Field>
          {dossier.sectors.length === 0 ? null : (
            // Sector codes are the taxonomy's own identifiers and stay verbatim.
            <Field label="Sectors">{dossier.sectors.join(', ')}</Field>
          )}
          <Field label="Interactions">
            <span className="tabular">{dossier.interaction_count}</span>
          </Field>
          <Field label="Last contact">
            <span className="tabular">{formatDate(dossier.last_contact_at)}</span>
          </Field>
          {dossier.strongest_relationship === null ? null : (
            <Field label="Strongest relationship">
              {RELATIONSHIP_STRENGTH_LABELS[dossier.strongest_relationship]}
            </Field>
          )}
        </dl>
      </header>

      {withheld === 0 ? null : (
        <Alert variant="warning" role="status">
          <EyeOff aria-hidden="true" />
          <AlertTitle>This dossier is not complete for your clearance</AlertTitle>
          <AlertDescription>
            {dossier.withheld_interactions > 0
              ? `${dossier.withheld_interactions} interaction${
                  dossier.withheld_interactions === 1 ? '' : 's'
                } `
              : ''}
            {dossier.withheld_interactions > 0 && dossier.withheld_opportunities > 0
              ? 'and '
              : ''}
            {dossier.withheld_opportunities > 0
              ? `${dossier.withheld_opportunities} linked opportunit${
                  dossier.withheld_opportunities === 1 ? 'y' : 'ies'
                } `
              : ''}
            sit above your data zone and are not shown. The count is shown so a shorter
            history is never mistaken for a quieter relationship.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-sm font-semibold text-ink">Interaction timeline</h2>
            </CardHeader>
            <CardContent>
              {dossier.timeline.length === 0 ? (
                <p className="py-6 text-center text-sm text-slate-700">
                  No interactions recorded that you are cleared to read.
                </p>
              ) : (
                <ol className="space-y-3">
                  {dossier.timeline.map((entry) => (
                    <TimelineRow key={entry.id} entry={entry} />
                  ))}
                </ol>
              )}
              {dossier.timeline_truncated ? (
                <p className="pt-3 text-label text-slate-700">
                  Older interactions exist beyond this page.
                </p>
              ) : null}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-sm font-semibold text-ink">Linked opportunities</h2>
            </CardHeader>
            <CardContent className="space-y-2">
              {dossier.opportunities.length === 0 ? (
                <p className="py-4 text-sm text-slate-700">
                  Nothing in the pipeline is attached to this counterpart.
                </p>
              ) : (
                dossier.opportunities.map((opportunity) => (
                  <div
                    key={opportunity.id}
                    // Q-17 again: proposed is a state, so it takes the --proposed tick here
                    // exactly as it does on the pipeline board.
                    className={
                      opportunity.is_proposed_by_ai
                        ? 'tick-proposed rounded-r-md bg-proposed-weak/40 p-2'
                        : 'rounded-lg border border-line p-2'
                    }
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium leading-snug text-ink">
                        {opportunity.title}
                      </p>
                      {opportunity.score === null ? null : (
                        <span className="tabular shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-label font-semibold text-ink">
                          <span className="sr-only">Score </span>
                          {Math.round(opportunity.score)}
                        </span>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-1 pt-1">
                      <Badge variant="outline" className="text-2xs font-medium">
                        {stageLabel(opportunity.stage)}
                      </Badge>
                      <Badge variant="outline" className="text-2xs font-medium">
                        linked as {opportunity.link}
                      </Badge>
                      {opportunity.is_proposed_by_ai ? (
                        <Badge variant="proposed" className="text-2xs font-medium">
                          <Sparkles aria-hidden="true" className="size-3" /> AI-proposed
                        </Badge>
                      ) : null}
                    </div>
                    <p className="tabular pt-1 text-label text-slate-700">
                      {opportunity.value_estimate_aud === null
                        ? 'No value estimate'
                        : `Estimated value ${formatAud(opportunity.value_estimate_aud)}`}
                    </p>
                    {opportunity.next_action_at === null ? null : (
                      <p className="tabular text-label text-slate-700">
                        Next action {formatDate(opportunity.next_action_at)}
                      </p>
                    )}
                  </div>
                ))
              )}
              <Link
                href="/opportunities"
                className="inline-block pt-1 text-label text-accent underline underline-offset-2"
              >
                Open the pipeline
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-sm font-semibold text-ink">People</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              {dossier.people.length === 0 ? (
                <p className="py-4 text-sm text-slate-700">
                  No contacts you are cleared to read.
                </p>
              ) : (
                dossier.people.map((person) => (
                  <div key={person.id} className="text-sm">
                    <p className="font-medium text-ink">{person.full_name}</p>
                    <p className="text-label text-slate-700">{person.role_title}</p>
                    <dl className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 pt-0.5">
                      <Field label="Relationship">
                        {RELATIONSHIP_STRENGTH_LABELS[person.relationship_strength]}
                      </Field>
                      <Field label="Last contact">
                        <span className="tabular">
                          {formatDate(person.last_contact_at)}
                        </span>
                      </Field>
                      {person.owner_name === null ? null : (
                        <Field label="Owned by">{person.owner_name}</Field>
                      )}
                    </dl>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-sm font-semibold text-ink">Sources</h2>
            </CardHeader>
            <CardContent className="space-y-2">
              {dossier.sources.length === 0 ? (
                <p className="py-4 text-sm text-slate-700">No sources cited.</p>
              ) : (
                dossier.sources.map((source) => (
                  <p key={source.citation_id} className="text-label">
                    {/* The external-link treatment the rest of the app copies: a new tab,
                        a rel that denies it any handle on this one, the trailing icon, and
                        the --accent underline that says "this leaves the system". */}
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-start gap-1 font-medium text-accent underline underline-offset-2"
                    >
                      {source.title}
                      <ExternalLink
                        aria-hidden="true"
                        className="mt-0.5 size-3 shrink-0"
                      />
                    </a>
                    <span className="block text-slate-700">{source.publisher}</span>
                    {source.verified ? null : (
                      // TODO_VERIFY: live, but no one has read it yet. Said in words as well
                      // as in colour, in --warn-ink because --warn is border and icon only.
                      <span className="flex items-center gap-1 text-warn-ink">
                        <AlertTriangle
                          aria-hidden="true"
                          className="size-3 shrink-0 text-warn"
                        />
                        Not yet verified by eye
                      </span>
                    )}
                  </p>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function TimelineRow({ entry }: { entry: TimelineEntry }): React.JSX.Element {
  return (
    <li className="border-l-2 border-line pl-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <time className="tabular text-label text-slate-700">
          {formatDate(entry.occurred_at)}
        </time>
        <Badge variant="outline" className="text-2xs font-medium">
          {entry.interaction_type.toLowerCase()}
        </Badge>
        <Badge variant="outline" className="text-2xs font-medium">
          {entry.direction.toLowerCase()}
        </Badge>
        {entry.classification === 'PUBLIC' ? null : (
          <Badge variant="outline" className="text-2xs font-medium">
            {CLASSIFICATION_LABELS[entry.classification]}
          </Badge>
        )}
      </div>
      <p className="pt-0.5 text-sm font-medium text-ink">{entry.subject}</p>
      <p className="max-w-[72ch] text-label text-slate-700">{entry.body}</p>
      <dl className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 pt-0.5">
        <Field label="Contact">{entry.stakeholder_name ?? 'none named'}</Field>
        {entry.recorded_by === null ? null : (
          <Field label="Recorded by">{entry.recorded_by}</Field>
        )}
        {entry.opportunity_id === null ? null : (
          // The tie is shown even when the title is withheld: the caller may read this
          // interaction but not the opportunity it names, and hiding the tie entirely
          // would misrepresent the record.
          <Field label="Opportunity">
            {entry.opportunity_title ?? 'linked to an opportunity above your clearance'}
          </Field>
        )}
      </dl>
    </li>
  );
}
