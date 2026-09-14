'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, Lock, Search } from 'lucide-react';
import { isApiError, type OrganisationRow } from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { fetchOrganisations } from '@/lib/api-queries';
import { RELATIONSHIP_STRENGTH_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * The organisation index: the way into a Stakeholder 360 dossier.
 *
 * The filter box is **client-side over the page the server returned**, and that is a
 * deliberate limit rather than an oversight. The API's list is already narrowed to what this
 * caller may read, so filtering locally cannot widen it; typing a name the server withheld
 * finds nothing, which is the correct answer. If the corpus outgrows one page this becomes a
 * server-side query parameter (the endpoint already accepts `q`), not a bigger client filter.
 */

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

function formatDate(value: string | null): string {
  if (value === null) return 'never';
  return new Date(value).toLocaleDateString('en-AU', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function OrganisationIndex(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;
  const [filter, setFilter] = React.useState('');

  const organisations = useQuery({
    queryKey: queryKeys.organisations(role),
    queryFn: ({ signal }) => fetchOrganisations(signal),
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle>No demo identity resolved</AlertTitle>
        <AlertDescription>Choose a role to see the relationship map.</AlertDescription>
      </Alert>
    );
  }

  if (organisations.isPending) return <Skeleton className="h-96" />;

  if (organisations.isError) {
    const error = organisations.error;
    const forbidden = isApiError(error) && error.status === 403;
    return (
      <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
        {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
        <AlertTitle>
          {forbidden
            ? 'This role may not read the relationship map'
            : 'The relationship map could not be loaded'}
        </AlertTitle>
        <AlertDescription>
          {isApiError(error) ? error.message : 'The API did not answer.'}
        </AlertDescription>
      </Alert>
    );
  }

  const needle = filter.trim().toLowerCase();
  const rows: readonly OrganisationRow[] =
    needle.length === 0
      ? organisations.data.items
      : organisations.data.items.filter(
          (row) =>
            row.name.toLowerCase().includes(needle) ||
            row.sectors.some((sector) => sector.toLowerCase().includes(needle)),
        );

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-ink">Stakeholders</h1>
          <p className="tabular max-w-[72ch] text-sm text-slate-700">
            {organisations.data.total} organisation
            {organisations.data.total === 1 ? '' : 's'} you are cleared to read. Open one
            for its dossier: people, contact history, linked opportunities and sources.
          </p>
        </div>
        <label className="relative">
          <span className="sr-only">Filter organisations by name or sector</span>
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-slate-400"
          />
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter by name or sector"
            // No local focus ring: globals.css declares one for every :focus-visible, so no
            // control in the app can ship without a visible indicator.
            className="w-64 rounded-md border border-input bg-background py-1.5 pl-8 pr-2 text-sm"
          />
        </label>
      </header>

      {rows.length === 0 ? (
        <p className="py-10 text-center text-sm text-slate-700">
          {needle.length === 0
            ? 'No organisations you are cleared to read.'
            : `Nothing matches "${filter.trim()}".`}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Organisation</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Country</TableHead>
                <TableHead>Relationship</TableHead>
                <TableHead className="text-right">Contacts</TableHead>
                <TableHead className="text-right">Interactions</TableHead>
                <TableHead className="text-right">Opportunities</TableHead>
                <TableHead>Last contact</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="max-w-[20rem]">
                    <Link
                      href={`/stakeholders/organisations/${row.id}`}
                      className="font-medium text-accent underline underline-offset-2"
                    >
                      {row.name}
                    </Link>
                    {row.sectors.length === 0 ? null : (
                      // A list, joined as a list. The middle dot was a structural device
                      // pretending to be punctuation; sector codes are the taxonomy's own
                      // identifiers and stay verbatim.
                      <span className="block truncate text-label text-slate-700">
                        {row.sectors.join(', ')}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-sm">
                    {row.org_type.replaceAll('_', ' ').toLowerCase()}
                  </TableCell>
                  <TableCell className="text-sm">{row.country}</TableCell>
                  <TableCell className="text-sm">
                    {row.strongest_relationship === null ? (
                      '—'
                    ) : (
                      <Badge variant="outline" className="text-2xs font-medium">
                        {RELATIONSHIP_STRENGTH_LABELS[row.strongest_relationship]}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.people_count}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.interaction_count}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.opportunity_count}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-sm tabular-nums text-slate-700">
                    {formatDate(row.last_contact_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
