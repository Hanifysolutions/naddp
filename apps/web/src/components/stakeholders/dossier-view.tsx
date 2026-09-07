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
import { CLASSIFICATION_LABELS, RELATIONSHIP_STRENGTH_LABELS } from '@/lib/enum-labels';
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
 */

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

export function DossierView({ organisationId }: { organisationId: string }): React.JSX.Element {
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
    <Link
      href="/stakeholders"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground underline underline-offset-2 hover:text-foreground"
    >
      <ArrowLeft aria-hidden="true" className="size-4" /> All stakeholders
    </Link>
  );
}

function Dossier360({ dossier }: { dossier: Dossier }): React.JSX.Element {
  const withheld = dossier.withheld_interactions + dossier.withheld_opportunities;
  return (
    <div className="space-y-4">
      <BackLink />

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold tracking-tight">{dossier.name}</h1>
          <Badge variant="outline" className="text-[10px]">
            {CLASSIFICATION_LABELS[dossier.classification]}
          </Badge>
          {dossier.dormant ? (
            <Badge variant="warning" className="text-[10px]">
              Dormant 90 days
            </Badge>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          {dossier.subtitle} · {dossier.country}
          {dossier.sectors.length === 0 ? '' : ` · ${dossier.sectors.join(' · ')}`}
        </p>
        <p className="max-w-3xl text-sm">{dossier.description}</p>
        <p className="text-sm text-muted-foreground">
          {dossier.interaction_count} interaction
          {dossier.interaction_count === 1 ? '' : 's'} · last contact{' '}
          {formatDate(dossier.last_contact_at)}
          {dossier.strongest_relationship === null
            ? ''
            : ` · strongest relationship ${RELATIONSHIP_STRENGTH_LABELS[
                dossier.strongest_relationship
              ].toLowerCase()}`}
        </p>
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
            {dossier.withheld_interactions > 0 && dossier.withheld_opportunities > 0 ? 'and ' : ''}
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
              <h2 className="text-sm font-semibold">Interaction timeline</h2>
            </CardHeader>
            <CardContent>
              {dossier.timeline.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
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
                <p className="pt-3 text-xs text-muted-foreground">
                  Older interactions exist beyond this page.
                </p>
              ) : null}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-sm font-semibold">Linked opportunities</h2>
            </CardHeader>
            <CardContent className="space-y-2">
              {dossier.opportunities.length === 0 ? (
                <p className="py-4 text-sm text-muted-foreground">
                  Nothing in the pipeline is attached to this counterpart.
                </p>
              ) : (
                dossier.opportunities.map((opportunity) => (
                  <div key={opportunity.id} className="rounded-md border border-border p-2">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium leading-snug">{opportunity.title}</p>
                      {opportunity.score === null ? null : (
                        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs font-semibold tabular-nums">
                          {Math.round(opportunity.score)}
                        </span>
                      )}
                    </div>
                    <div className="flex flex-wrap items-center gap-1 pt-1">
                      <Badge variant="outline" className="text-[10px]">
                        {opportunity.stage}
                      </Badge>
                      <Badge variant="outline" className="text-[10px]">
                        linked as {opportunity.link}
                      </Badge>
                      {opportunity.is_proposed_by_ai ? (
                        <Badge variant="warning" className="text-[10px]">
                          <Sparkles aria-hidden="true" className="size-3" /> AI-proposed
                        </Badge>
                      ) : null}
                    </div>
                    <p className="pt-1 text-xs text-muted-foreground">
                      {opportunity.value_estimate_aud === null
                        ? 'No value estimate'
                        : formatAud(opportunity.value_estimate_aud)}
                      {opportunity.next_action_at === null
                        ? ''
                        : ` · next action ${formatDate(opportunity.next_action_at)}`}
                    </p>
                  </div>
                ))
              )}
              <Link
                href="/opportunities"
                className="inline-block pt-1 text-xs underline underline-offset-2"
              >
                Open the pipeline
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-sm font-semibold">People</h2>
            </CardHeader>
            <CardContent className="space-y-2">
              {dossier.people.length === 0 ? (
                <p className="py-4 text-sm text-muted-foreground">
                  No contacts you are cleared to read.
                </p>
              ) : (
                dossier.people.map((person) => (
                  <div key={person.id} className="text-sm">
                    <p className="font-medium">{person.full_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {person.role_title} ·{' '}
                      {RELATIONSHIP_STRENGTH_LABELS[person.relationship_strength].toLowerCase()} ·
                      last contact {formatDate(person.last_contact_at)}
                    </p>
                    {person.owner_name === null ? null : (
                      <p className="text-xs text-muted-foreground">
                        Owned by {person.owner_name}
                      </p>
                    )}
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <h2 className="text-sm font-semibold">Sources</h2>
            </CardHeader>
            <CardContent className="space-y-2">
              {dossier.sources.length === 0 ? (
                <p className="py-4 text-sm text-muted-foreground">No sources cited.</p>
              ) : (
                dossier.sources.map((source) => (
                  <p key={source.citation_id} className="text-xs">
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-start gap-1 font-medium underline underline-offset-2"
                    >
                      {source.title}
                      <ExternalLink aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
                    </a>
                    <span className="block text-muted-foreground">
                      {source.publisher}
                      {source.verified ? '' : ' · not yet verified by eye'}
                    </span>
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
    <li className="border-l-2 border-border pl-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <time className="text-xs tabular-nums text-muted-foreground">
          {formatDate(entry.occurred_at)}
        </time>
        <Badge variant="outline" className="text-[10px]">
          {entry.interaction_type.toLowerCase()}
        </Badge>
        <Badge variant="outline" className="text-[10px]">
          {entry.direction.toLowerCase()}
        </Badge>
        {entry.classification === 'PUBLIC' ? null : (
          <Badge variant="outline" className="text-[10px]">
            {CLASSIFICATION_LABELS[entry.classification]}
          </Badge>
        )}
      </div>
      <p className="pt-0.5 text-sm font-medium">{entry.subject}</p>
      <p className="text-xs text-muted-foreground">{entry.body}</p>
      <p className="pt-0.5 text-xs text-muted-foreground">
        {entry.stakeholder_name ?? 'No named contact'}
        {entry.recorded_by === null ? '' : ` · recorded by ${entry.recorded_by}`}
        {entry.opportunity_id === null
          ? ''
          : // The tie is shown even when the title is withheld: the caller may read this
            // interaction but not the opportunity it names, and hiding the tie entirely
            // would misrepresent the record.
            ` · ${entry.opportunity_title ?? 'linked to an opportunity above your clearance'}`}
      </p>
    </li>
  );
}
