'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Building2,
  GraduationCap,
  List,
  LoaderCircle,
  MapPin,
  SearchX,
  ShieldCheck,
  UserCheck,
  Users,
  type LucideIcon,
} from 'lucide-react';
import type { AiEnvelope, AiTrace } from '@naddp/contracts';

import { TraceBadge } from '@/components/intelligence/trace-badge';
import { useDemoSession } from '@/components/layout/session-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { asBriefTrace } from '@/lib/ai-trace';
import { fetchAiTrace } from '@/lib/api-queries';
import { CONSENT_STATUS_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * One capability search and what the consented directory returned: candidates, or nobody.
 *
 * **Only what the API returned, and only consented people.** A candidate is rendered only when its
 * consent is one of the two states a search may return - contactable or directory only - and its
 * `contactable` flag agrees; anything else is not shown at all, rather than shown with a caveat.
 * Each card carries expertise, sector, institution, coarse location and consent state, because
 * those are the only things a result holds: there is no city, no language and no contact detail
 * to render, and no contact control, because no contact action exists.
 *
 * **Nobody fitting is a calm result, not an error.** It carries the `--warn` tick, names nobody,
 * and says why in the server's words. Both outcomes carry the routing record: the section 4a badge
 * as the Gateway wrote it, and a sentence derived from the trace saying whether any model was asked.
 */

export interface Search {
  readonly id: number;
  readonly requirement: string;
  readonly status: 'pending' | 'done' | 'failed';
  readonly envelope: AiEnvelope<unknown> | null;
  readonly error: string | null;
}

type SearchableConsent = 'GIVEN_CONTACTABLE' | 'GIVEN_DIRECTORY_ONLY';

interface CandidateExpertise {
  readonly code: string;
  readonly label: string;
}

interface Candidate {
  readonly profileRef: string;
  readonly name: string;
  readonly headline: string | null;
  readonly expertise: readonly CandidateExpertise[];
  readonly sector: string | null;
  readonly institution: string | null;
  readonly organisation: string | null;
  readonly location: string | null;
  readonly availability: string | null;
  readonly matchedTerms: readonly string[];
  readonly consent: SearchableConsent;
}

type DiasporaResult =
  | {
      readonly kind: 'candidates';
      readonly rationale: string;
      readonly candidates: readonly Candidate[];
    }
  | { readonly kind: 'none'; readonly rationale: string; readonly reason: string };

/** A string, `null` when absent, or `undefined` when the value is of the wrong type. */
function optionalText(value: unknown): string | null | undefined {
  if (value === undefined || value === null) return null;
  return typeof value === 'string' ? value : undefined;
}

function readExpertise(value: unknown): CandidateExpertise | null {
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  const code = record['code'];
  const label = record['label'];
  return typeof code === 'string' && typeof label === 'string' ? { code, label } : null;
}

function readCandidate(value: unknown): Candidate | null {
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  const profileRef = record['profile_ref'];
  const name = record['display_name'];
  const consent = record['consent_status'];
  if (typeof profileRef !== 'string' || typeof name !== 'string') return null;
  if (consent !== 'GIVEN_CONTACTABLE' && consent !== 'GIVEN_DIRECTORY_ONLY') return null;
  if (record['contactable'] !== (consent === 'GIVEN_CONTACTABLE')) return null;

  const headline = optionalText(record['headline']);
  const sector = optionalText(record['sector_label']);
  const institution = optionalText(record['institution']);
  const organisation = optionalText(record['organisation']);
  const location = optionalText(record['coarse_location']);
  const availability = optionalText(record['availability']);
  if (
    headline === undefined ||
    sector === undefined ||
    institution === undefined ||
    organisation === undefined ||
    location === undefined ||
    availability === undefined
  ) {
    return null;
  }

  const rawExpertise = record['expertise'];
  const expertise = Array.isArray(rawExpertise) ? rawExpertise.map(readExpertise) : [];
  if (expertise.some((item) => item === null)) return null;
  const rawTerms = record['matched_terms'];
  const matchedTerms = Array.isArray(rawTerms)
    ? rawTerms.filter((term): term is string => typeof term === 'string')
    : [];

  return {
    profileRef,
    name,
    headline,
    expertise: expertise.filter((item): item is CandidateExpertise => item !== null),
    sector,
    institution,
    organisation,
    location,
    availability,
    matchedTerms,
    consent,
  };
}

/** Narrow the envelope's open result to candidates or nobody, or refuse to render it. */
function readDiasporaResult(result: unknown): DiasporaResult | null {
  if (typeof result !== 'object' || result === null) return null;
  const record = result as Record<string, unknown>;
  const rationale = record['rationale'];
  const matches = record['matches'];
  if (typeof rationale !== 'string' || !Array.isArray(matches)) return null;
  if (record['candidates_only'] !== true) return null;

  if (matches.length === 0) {
    const noMatch = record['no_match'];
    if (typeof noMatch !== 'object' || noMatch === null) return null;
    const reason = (noMatch as Record<string, unknown>)['reason'];
    return typeof reason === 'string' ? { kind: 'none', rationale, reason } : null;
  }

  const candidates = matches.map(readCandidate);
  if (candidates.some((candidate) => candidate === null)) return null;
  return {
    kind: 'candidates',
    rationale,
    candidates: candidates.filter(
      (candidate): candidate is Candidate => candidate !== null,
    ),
  };
}

export function DiasporaSearchOutcome({ search }: { search: Search }): React.JSX.Element {
  if (search.status === 'pending') {
    return (
      <div className="rounded-lg border border-line bg-card p-4" aria-busy="true">
        <RequirementLine requirement={search.requirement} />
        <p
          className="mt-2 flex items-center gap-1.5 text-label text-slate-700"
          role="status"
        >
          <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
          Searching the consented directory…
        </p>
        <Skeleton className="mt-3 h-24 w-full" />
      </div>
    );
  }

  const envelope = search.envelope;
  if (search.status === 'failed' || envelope === null) {
    return (
      <Alert variant="destructive" role="status">
        <AlertTriangle aria-hidden="true" />
        <AlertTitle className="leading-snug">The search could not be run</AlertTitle>
        <AlertDescription className="max-w-[72ch] space-y-1">
          <p className="font-medium">{search.requirement}</p>
          <p>
            {search.error ?? 'The API did not answer.'} The directory was not reached, so
            nobody was returned.
          </p>
        </AlertDescription>
      </Alert>
    );
  }

  const parsed =
    envelope.approval_status === 'BLOCKED' ? null : readDiasporaResult(envelope.result);
  if (parsed === null) {
    return (
      <Alert variant="warning" role="status">
        <AlertTriangle aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">
          No result was given
        </AlertTitle>
        <AlertDescription className="max-w-[72ch] space-y-1">
          <p className="font-medium">{search.requirement}</p>
          <p>
            {envelope.explanation ??
              'The result could not be read, so nobody is shown rather than part of a list.'}
          </p>
        </AlertDescription>
      </Alert>
    );
  }

  return parsed.kind === 'candidates' ? (
    <CandidateSet search={search} envelope={envelope} result={parsed} />
  ) : (
    <NoCandidates search={search} envelope={envelope} result={parsed} />
  );
}

function RequirementLine({ requirement }: { requirement: string }): React.JSX.Element {
  return (
    <p className="max-w-[72ch] text-base font-semibold leading-snug text-ink">
      {requirement}
    </p>
  );
}

function CandidateSet({
  search,
  envelope,
  result,
}: {
  search: Search;
  envelope: AiEnvelope<unknown>;
  result: Extract<DiasporaResult, { kind: 'candidates' }>;
}): React.JSX.Element {
  const count = result.candidates.length;
  const contactable = result.candidates.filter(
    (candidate) => candidate.consent === 'GIVEN_CONTACTABLE',
  ).length;
  return (
    <article
      aria-label="Consented candidates"
      className="overflow-hidden rounded-lg border border-line bg-card"
    >
      <div className="tick-ok space-y-4 p-4">
        <header className="space-y-1.5">
          <p className="tabular flex items-center gap-1.5 text-label font-medium text-ok">
            <Users aria-hidden="true" className="h-4 w-4" />
            {count} consented {count === 1 ? 'candidate' : 'candidates'}, {contactable}{' '}
            contactable
          </p>
          <RequirementLine requirement={search.requirement} />
        </header>

        <p className="max-w-[72ch] text-sm leading-relaxed text-slate-700">
          {result.rationale}
        </p>

        <ul aria-label="Candidates" className="space-y-3">
          {result.candidates.map((candidate) => (
            <CandidateCard key={candidate.profileRef} candidate={candidate} />
          ))}
        </ul>

        <CandidatesOnlyNote />
        <RoutingLine traceId={envelope.trace_id} kind="candidates" />
      </div>
    </article>
  );
}

function CandidateCard({ candidate }: { candidate: Candidate }): React.JSX.Element {
  return (
    <li className="rounded-md border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-0.5">
          <p className="text-sm font-semibold leading-snug text-ink">{candidate.name}</p>
          {candidate.headline === null ? null : (
            <p className="text-label leading-snug text-slate-700">{candidate.headline}</p>
          )}
        </div>
        <ConsentChip consent={candidate.consent} />
      </div>

      {candidate.expertise.length === 0 ? null : (
        <ul aria-label="Consented expertise" className="mt-2.5 flex flex-wrap gap-1.5">
          {candidate.expertise.map((item) => (
            <li key={item.code}>
              <Badge variant="secondary">{item.label}</Badge>
            </li>
          ))}
        </ul>
      )}

      <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-2 laptop:grid-cols-2">
        <Fact label="Sector" value={candidate.sector} />
        <Fact label="Institution" value={candidate.institution} icon={GraduationCap} />
        <Fact label="Organisation" value={candidate.organisation} icon={Building2} />
        <Fact
          label="Location (state and country)"
          value={candidate.location}
          icon={MapPin}
        />
        <Fact label="Availability" value={candidate.availability} />
      </dl>

      {candidate.matchedTerms.length === 0 ? null : (
        <p className="mt-2.5 text-label text-slate-700">
          Matched on key terms:{' '}
          <span className="font-mono text-2xs">{candidate.matchedTerms.join(', ')}</span>
        </p>
      )}
    </li>
  );
}

function Fact({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: string | null;
  icon?: LucideIcon;
}): React.JSX.Element | null {
  if (value === null) return null;
  return (
    <div className="min-w-0">
      <dt className="text-label text-slate-700">{label}</dt>
      <dd className="flex items-start gap-1.5 text-sm leading-snug text-ink">
        {Icon === undefined ? null : (
          <Icon
            aria-hidden="true"
            className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-700"
          />
        )}
        <span>{value}</span>
      </dd>
    </div>
  );
}

function ConsentChip({ consent }: { consent: SearchableConsent }): React.JSX.Element {
  const contactable = consent === 'GIVEN_CONTACTABLE';
  const meaning = contactable
    ? 'consented to be approached through the mission’s own process'
    : 'consented to be listed only, not to be approached';
  return (
    <Badge variant="outline" className={contactable ? 'text-ok' : ''} title={meaning}>
      {contactable ? <UserCheck aria-hidden="true" /> : <List aria-hidden="true" />}
      {CONSENT_STATUS_LABELS[consent]}
      <span className="sr-only">: {meaning}</span>
    </Badge>
  );
}

function CandidatesOnlyNote(): React.JSX.Element {
  return (
    <p className="flex max-w-[72ch] items-start gap-1.5 text-label leading-snug text-slate-700">
      <ShieldCheck aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        <span className="font-medium text-ink">Candidates only.</span> Neither the
        platform nor its AI contacts anyone, and there is no contact action here. The
        mission approaches people through its own process - and only those marked
        contactable.
      </span>
    </p>
  );
}

function NoCandidates({
  search,
  envelope,
  result,
}: {
  search: Search;
  envelope: AiEnvelope<unknown>;
  result: Extract<DiasporaResult, { kind: 'none' }>;
}): React.JSX.Element {
  return (
    <article
      aria-label="No consented profile fits this requirement"
      className="overflow-hidden rounded-lg border border-line bg-card"
    >
      <div className="tick-warn space-y-3 p-4">
        <header className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-label font-medium text-warn-ink">
            <SearchX aria-hidden="true" className="h-4 w-4" />
            No consented profile fits this requirement
          </p>
          <RequirementLine requirement={search.requirement} />
        </header>

        <dl className="space-y-2.5 rounded-md border border-line p-3">
          <div>
            <dt className="text-label text-slate-700">Why</dt>
            <dd className="max-w-[72ch] text-sm leading-snug text-ink">
              {result.reason}
            </dd>
          </div>
          <div>
            <dt className="text-label text-slate-700">What to do</dt>
            <dd className="max-w-[72ch] text-sm leading-snug text-ink">
              Rephrase or broaden the requirement, or pursue it through the mission&apos;s
              own networks. Profiles without consent were not searched and are never a
              fallback.
            </dd>
          </div>
        </dl>

        <p className="max-w-[72ch] text-label leading-snug text-slate-700">
          {result.rationale} Returning nobody is the search working as designed: a weak
          match is not offered to fill the gap.
        </p>

        <RoutingLine traceId={envelope.trace_id} kind="none" />
      </div>
    </article>
  );
}

/** Derived from the trace's own fields; says nothing the trace does not. */
function routingSentence(trace: AiTrace, kind: 'candidates' | 'none'): string {
  if (trace.model_used !== null) {
    return `Ranked by ${trace.model_used} from the consented candidates only.`;
  }
  if (kind === 'none') {
    return 'No model was asked: no consented profile fits, so there was no one to rank.';
  }
  return trace.fallback
    ? 'No model was asked: candidates were selected by rule from consented profiles only.'
    : 'No model ranked this search.';
}

function RoutingLine({
  traceId,
  kind,
}: {
  traceId: string;
  kind: 'candidates' | 'none';
}): React.JSX.Element {
  const session = useDemoSession();
  const mayRead = session.can('read:ai_trace');
  const trace = useQuery({
    queryKey: queryKeys.aiTrace(session.role, traceId),
    queryFn: ({ signal }) => fetchAiTrace(traceId, signal),
    enabled: mayRead,
    staleTime: Infinity,
    retry: false,
  });

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line pt-3">
      <span className="text-label text-slate-700">Routing</span>
      {!mayRead ? (
        <span className="text-label text-slate-700">
          The routing record is shown to roles holding read:ai_trace.
        </span>
      ) : trace.isPending ? (
        <Skeleton className="h-5 w-56" />
      ) : trace.isError ? (
        <span className="text-label text-slate-700">
          The routing record could not be loaded (trace{' '}
          <span className="font-mono text-2xs">{traceId}</span>).
        </span>
      ) : (
        <>
          <TraceBadge trace={asBriefTrace(trace.data)} subject="search" />
          <span className="text-label text-slate-700">
            {routingSentence(trace.data, kind)}
          </span>
        </>
      )}
    </div>
  );
}
