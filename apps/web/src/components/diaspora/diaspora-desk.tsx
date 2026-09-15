'use client';

import * as React from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, Lock, ShieldCheck } from 'lucide-react';
import { isApiError, type DiasporaOverview } from '@naddp/contracts';

import {
  DiasporaSearchOutcome,
  type Search,
} from '@/components/diaspora/diaspora-results';
import { useDemoSession } from '@/components/layout/session-provider';
import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchDiasporaOverview, searchDiaspora } from '@/lib/api-queries';
import { queryKeys } from '@/lib/query-keys';

/**
 * The Diaspora desk: search the consented directory for a capability, and see the consent gate.
 *
 * **Consent is the gate, and the screen says so in numbers.** The header counts only what this
 * role may search - consented profiles, split into contactable and directory only - because the
 * API never loads anyone else, so there is nobody else to count. The right column states the rules
 * the results obey: profiles without consent are never loaded, the platform returns candidates
 * only, the mission approaches people through its own process, and nothing here contacts anyone.
 *
 * **Candidates, not an outreach list.** There is no contact, message or outreach control anywhere
 * on this screen, because no such action exists in the API. Each candidate states its consent;
 * that is information for the mission's own process, not a button.
 *
 * Searches run in this sitting are kept newest first. They belong to the identity that ran them:
 * the body is keyed by role, so switching demo identity starts an empty desk.
 */

const REQUIREMENT_MIN = 3;
const REQUIREMENT_MAX = 1000;

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

export function DiasporaDeskSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-5" aria-busy="true">
      <div className="space-y-2">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-4 w-[40rem] max-w-full" />
      </div>
      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1fr)_22rem]">
        <Skeleton className="h-64 rounded-lg" />
        <Skeleton className="h-96 rounded-lg" />
      </div>
      <span className="sr-only" role="status">
        Loading the diaspora directory
      </span>
    </div>
  );
}

export function DiasporaDesk(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const overview = useQuery({
    queryKey: queryKeys.diasporaOverview(role),
    queryFn: ({ signal }) => fetchDiasporaOverview(signal),
    // Gated on identity only: a role without read:diaspora_profile is refused by the API, on the
    // record, rather than by this page.
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">
          No demo identity resolved
        </AlertTitle>
        <AlertDescription>
          Choose a role to search the diaspora directory. Nobody is searched without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (overview.isPending) return <DiasporaDeskSkeleton />;

  if (overview.isError) {
    const apiError = isApiError(overview.error) ? overview.error : null;
    const forbidden = apiError !== null && apiError.status === 403;
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-ink">Diaspora</h1>
        <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
          {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <AlertTitle
            className={forbidden ? 'leading-snug text-warn-ink' : 'leading-snug'}
          >
            {forbidden
              ? 'This role may not search the diaspora directory'
              : 'The diaspora directory could not be loaded'}
          </AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            <p>
              {apiError === null ? 'The API did not answer.' : apiError.message}
              {apiError !== null && apiError.missingPermissions.length > 0
                ? ` Missing: ${apiError.missingPermissions.join(', ')}.`
                : null}
            </p>
            {forbidden ? (
              <p className="mt-2">
                The refusal was made by the API, not by this page, and it was written to
                the audit log. No profile was loaded.
              </p>
            ) : null}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <DeskBody
      key={role}
      overview={overview.data}
      maySearch={session.can('search:diaspora_profile')}
    />
  );
}

function DeskBody({
  overview,
  maySearch,
}: {
  overview: DiasporaOverview;
  maySearch: boolean;
}): React.JSX.Element {
  const [requirement, setRequirement] = React.useState('');
  const [searches, setSearches] = React.useState<readonly Search[]>([]);
  const nextId = React.useRef(1);
  const run = useMutation({ mutationFn: searchDiaspora });

  const fieldId = React.useId();
  const countId = React.useId();
  const suggestionsId = React.useId();

  const busy = searches.some((search) => search.status === 'pending');
  const {
    searchable_count: searchable,
    contactable_count: contactable,
    directory_only_count: directoryOnly,
  } = overview;

  function submit(text: string): void {
    const asked = text.trim();
    if (!maySearch || asked.length < REQUIREMENT_MIN || busy) return;
    const id = nextId.current;
    nextId.current += 1;
    setSearches((current) => [
      { id, requirement: asked, status: 'pending', envelope: null, error: null },
      ...current,
    ]);
    run.mutate(
      { requirement: asked, sector_codes: [] },
      {
        onSuccess: (envelope) =>
          setSearches((current) =>
            current.map((search) =>
              search.id === id ? { ...search, status: 'done', envelope } : search,
            ),
          ),
        onError: (failure) =>
          setSearches((current) =>
            current.map((search) =>
              search.id === id
                ? {
                    ...search,
                    status: 'failed',
                    error: isApiError(failure)
                      ? failure.message
                      : failure instanceof Error
                        ? failure.message
                        : 'The API did not answer.',
                  }
                : search,
            ),
          ),
      },
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-ink">Diaspora</h1>
        <p className="tabular max-w-[80ch] text-sm leading-relaxed text-slate-700">
          Search the consented diaspora directory for a capability. {searchable} consented{' '}
          {searchable === 1 ? 'profile is' : 'profiles are'} searchable in your scope:{' '}
          {contactable} contactable and {directoryOnly} directory only. Results are
          candidates for the mission to consider - never an outreach list.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1fr)_22rem] laptop:items-start">
        <div className="min-w-0 space-y-5">
          <form
            className="space-y-3 rounded-lg border border-line bg-card p-4"
            onSubmit={(event) => {
              event.preventDefault();
              submit(requirement);
            }}
          >
            <label htmlFor={fieldId} className="text-sm font-medium text-ink">
              Describe the capability the mission needs
            </label>
            <textarea
              id={fieldId}
              value={requirement}
              onChange={(event) => setRequirement(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  submit(requirement);
                }
              }}
              rows={3}
              maxLength={REQUIREMENT_MAX}
              disabled={!maySearch}
              aria-describedby={countId}
              placeholder="For example: lithium refinery process skills, and the migration pathway that would carry them"
              className="w-full rounded-md border border-input bg-background p-2 text-sm disabled:opacity-60"
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span id={countId} className="tabular text-label text-slate-700">
                {requirement.length} of {REQUIREMENT_MAX} characters
              </span>
              <Button
                type="submit"
                disabled={
                  !maySearch || requirement.trim().length < REQUIREMENT_MIN || busy
                }
                className={PRIMARY_ACTION}
              >
                {busy ? 'Searching…' : 'Search candidates'}
              </Button>
            </div>
            {maySearch ? null : (
              <p className="text-label text-warn-ink" role="status">
                This role may see the directory counts but may not search it
                (search:diaspora_profile).
              </p>
            )}
          </form>

          {!maySearch || overview.suggested_searches.length === 0 ? null : (
            <section aria-labelledby={suggestionsId} className="space-y-2">
              <h2 id={suggestionsId} className="text-label font-medium text-slate-700">
                Try searching
              </h2>
              <ul className="flex flex-wrap gap-2">
                {overview.suggested_searches.map((suggestion) => (
                  <li key={suggestion.id}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setRequirement(suggestion.requirement);
                        submit(suggestion.requirement);
                      }}
                      className="rounded-md border border-line bg-card px-2.5 py-1.5 text-left text-label text-ink hover:border-slate-400 disabled:opacity-60"
                    >
                      {suggestion.requirement}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section aria-label="Search results" aria-live="polite" className="space-y-4">
            {searches.length === 0 ? (
              <p className="rounded-lg border border-dashed border-input px-4 py-6 text-center text-sm text-slate-700">
                Search for a capability, or try one above. Results are consented
                candidates only - the platform contacts no one.
              </p>
            ) : (
              searches.map((search) => (
                <DiasporaSearchOutcome key={search.id} search={search} />
              ))
            )}
          </section>
        </div>

        <aside className="min-w-0 laptop:sticky laptop:top-20">
          <ConsentGateCard overview={overview} />
        </aside>
      </div>
    </div>
  );
}

function ConsentGateCard({
  overview,
}: {
  overview: DiasporaOverview;
}): React.JSX.Element {
  const headingId = React.useId();
  const { selection } = overview;
  const rules: readonly { readonly title: string; readonly body: string }[] = [
    {
      title: 'Profiles without consent are never loaded.',
      body: 'The consent filter runs inside the database query, before anything is ranked. A profile whose consent was not given or has been withdrawn cannot appear, however well it fits, and is not counted above.',
    },
    {
      title: 'Candidates only.',
      body: 'The platform returns candidates. Neither it nor its AI contacts anyone, and there is no contact or outreach action anywhere in it.',
    },
    {
      title: 'The mission approaches people through its own process.',
      body: 'Only a candidate marked contactable may be approached at all. Directory only means listed, not approachable.',
    },
    {
      title: 'Coarse location only.',
      body: 'A result shows state or territory and country, never the city. Languages and personal details are not part of a result.',
    },
    {
      title: 'How candidates are chosen.',
      body: `A profile must hold at least ${selection.min_matched_terms} of the requirement's key terms. Each part of the requirement is covered first, then the strongest others at ${Math.round(selection.relative_floor * 100)}% of the best score or more, up to ${selection.max_candidates} candidates.`,
    },
  ];

  return (
    <Card aria-labelledby={headingId}>
      <CardHeader className="gap-1 pb-2">
        <h2
          id={headingId}
          className="flex items-center gap-2 text-base font-semibold text-ink"
        >
          <ShieldCheck aria-hidden="true" className="h-4 w-4 text-slate-700" />
          How consent gates this search
        </h2>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-line">
          {rules.map((rule) => (
            <li key={rule.title} className="py-2.5 first:pt-0 last:pb-0">
              <p className="text-sm font-medium leading-snug text-ink">{rule.title}</p>
              <p className="mt-0.5 text-label leading-snug text-slate-700">{rule.body}</p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
