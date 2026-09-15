'use client';

import * as React from 'react';
import { Ban, Check, Inbox, LoaderCircle } from 'lucide-react';
import type {
  AuditEvent,
  DataClassification,
  NaddpRole,
  PolicyResult,
} from '@naddp/contracts';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  AUDIT_ACTION_GROUPS,
  AUDIT_ACTION_LABELS,
  POLICY_RESULT_LABELS,
  actionsInGroup,
  auditActionLabel,
  auditObjectLabel,
} from '@/lib/audit-labels';
import { CLASSIFICATION_LABELS, ROLE_LABELS } from '@/lib/enum-labels';
import { cn } from '@/lib/utils';

/**
 * The append-only audit log, in plain language.
 *
 * Every row is one consequential event exactly as it was recorded: who acted and in what role at
 * the time, what they did, what it was about, whether the policy allowed or refused it, the zone of
 * the content it describes, and when. Refusals sit beside successes - a log that only showed what
 * worked would be evidence of nothing - and carry the --risk tick; allowed actions carry --ok.
 *
 * Filtering and paging happen in the API, inside its SQL and under the reader's clearance; this
 * view sends the filters, lays the rows out and formats their words and times. Paging is by keyset
 * cursor, so rows appended while the log is being read - including the privileged-read row a page
 * of sensitive entries writes about its reader - neither shift nor repeat.
 */

export interface AuditLogFilters {
  readonly actorRole: NaddpRole | null;
  readonly action: string | null;
  readonly policyResult: PolicyResult | null;
}

export const NO_AUDIT_FILTERS: AuditLogFilters = {
  actorRole: null,
  action: null,
  policyResult: null,
};

const ROLE_FILTER_ORDER: readonly NaddpRole[] = [
  'AMBASSADOR',
  'DEPUTY',
  'TRADE_OFFICER',
  'CONSULAR_OFFICER',
  'DIASPORA_OFFICER',
  'ADMIN',
];

const NUMBER_FORMAT = new Intl.NumberFormat('en-AU');
const TIME_FORMAT = new Intl.DateTimeFormat('en-AU', {
  dateStyle: 'medium',
  timeStyle: 'short',
});

export function AuditLogSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-2" aria-busy="true">
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
      <span className="sr-only" role="status">
        Loading the audit log
      </span>
    </div>
  );
}

export function AuditLog({
  filters,
  onFiltersChange,
  events,
  totalMatching,
  appliedClassifications,
  isLoading,
  hasMore,
  isLoadingMore,
  onLoadMore,
}: {
  filters: AuditLogFilters;
  onFiltersChange: (next: AuditLogFilters) => void;
  events: readonly AuditEvent[];
  totalMatching: number | null;
  appliedClassifications: readonly DataClassification[];
  isLoading: boolean;
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
}): React.JSX.Element {
  const headingId = React.useId();
  const actorId = React.useId();
  const actionId = React.useId();
  const resultId = React.useId();
  const filtered =
    filters.actorRole !== null ||
    filters.action !== null ||
    filters.policyResult !== null;
  const sensitiveHidden =
    appliedClassifications.length > 0 &&
    !appliedClassifications.includes('CONSULAR_SENSITIVE');

  return (
    <section
      aria-labelledby={headingId}
      className="mt-6 rounded-lg border border-line bg-card"
    >
      <header className="space-y-3 border-b border-line p-4">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
          <div className="min-w-0">
            <h2 id={headingId} className="text-base font-semibold leading-tight text-ink">
              Audit log
            </h2>
            <p className="mt-1 max-w-[80ch] text-label leading-snug text-slate-700">
              Newest first. Every approval and every refusal is recorded - the refusals
              are the deny-by-default controls working, on the record.
            </p>
          </div>
          {totalMatching === null ? null : (
            <p className="tabular text-sm text-slate-700">
              <span className="font-semibold text-ink">
                {NUMBER_FORMAT.format(totalMatching)}
              </span>{' '}
              {totalMatching === 1 ? 'event matches' : 'events match'} in the zones you
              are cleared to read
            </p>
          )}
        </div>

        {sensitiveHidden ? (
          <p className="max-w-[80ch] rounded-md border border-line bg-paper px-3 py-2 text-label leading-snug text-slate-700">
            Your clearance does not admit consular-sensitive content, so events describing
            it are not selected for you - not redacted, never loaded. You can see that
            governed actions happened without reading what they were about: administration
            is not clearance.
          </p>
        ) : null}

        <div className="flex flex-wrap items-end gap-3">
          <FilterField id={actorId} label="Actor">
            <select
              id={actorId}
              value={filters.actorRole ?? ''}
              onChange={(event) =>
                onFiltersChange({
                  ...filters,
                  actorRole:
                    event.target.value === '' ? null : (event.target.value as NaddpRole),
                })
              }
              className="block rounded-md border border-input bg-background p-1.5 text-sm"
            >
              <option value="">Any actor</option>
              {ROLE_FILTER_ORDER.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </FilterField>

          <FilterField id={actionId} label="Action">
            <select
              id={actionId}
              value={filters.action ?? ''}
              onChange={(event) =>
                onFiltersChange({
                  ...filters,
                  action: event.target.value === '' ? null : event.target.value,
                })
              }
              className="block max-w-[18rem] rounded-md border border-input bg-background p-1.5 text-sm"
            >
              <option value="">Any action</option>
              {AUDIT_ACTION_GROUPS.map((group) => (
                <optgroup key={group.label} label={group.label}>
                  {actionsInGroup(group.prefixes).map((action) => (
                    <option key={action} value={action}>
                      {AUDIT_ACTION_LABELS[action]?.allowed ?? action}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </FilterField>

          <FilterField id={resultId} label="Result">
            <select
              id={resultId}
              value={filters.policyResult ?? ''}
              onChange={(event) =>
                onFiltersChange({
                  ...filters,
                  policyResult:
                    event.target.value === ''
                      ? null
                      : (event.target.value as PolicyResult),
                })
              }
              className="block rounded-md border border-input bg-background p-1.5 text-sm"
            >
              <option value="">Allowed and denied</option>
              <option value="ALLOW">Allowed only</option>
              <option value="DENY">Denied only</option>
            </select>
          </FilterField>

          {filtered ? (
            <Button
              type="button"
              variant="ghost"
              onClick={() => onFiltersChange(NO_AUDIT_FILTERS)}
              className="text-label"
            >
              Clear filters
            </Button>
          ) : null}
        </div>
      </header>

      <div className="p-4">
        {isLoading ? (
          <AuditLogSkeleton />
        ) : events.length === 0 ? (
          <p className="flex items-start gap-2 rounded-md border border-dashed border-input px-4 py-6 text-sm text-slate-700">
            <Inbox aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              No events match these filters in the zones you are cleared to read. This is
              the API&apos;s answer, not a placeholder.
            </span>
          </p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-3">Actor</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Object</TableHead>
                    <TableHead>Result</TableHead>
                    <TableHead>Classification</TableHead>
                    <TableHead className="whitespace-nowrap">When</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {events.map((event) => (
                    <AuditRow key={event.id} event={event} />
                  ))}
                </TableBody>
              </Table>
            </div>
            {hasMore ? (
              <div className="mt-4 flex justify-center">
                <Button
                  type="button"
                  variant="outline"
                  onClick={onLoadMore}
                  disabled={isLoadingMore}
                >
                  {isLoadingMore ? (
                    <>
                      <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin" />
                      Loading older events…
                    </>
                  ) : (
                    'Load older events'
                  )}
                </Button>
              </div>
            ) : (
              <p className="mt-4 text-center text-label text-slate-700">
                The start of the log in the zones you are cleared to read.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function FilterField({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-label text-slate-700">
        {label}
      </label>
      {children}
    </div>
  );
}

function AuditRow({ event }: { event: AuditEvent }): React.JSX.Element {
  const denied = event.policy_result === 'DENY';
  return (
    <TableRow>
      <TableCell
        className={cn(
          'whitespace-nowrap pl-3 align-top',
          denied ? 'tick-risk' : 'tick-ok',
        )}
      >
        {event.actor_role === null || event.actor_role === undefined ? (
          <span className="block text-sm text-ink">System</span>
        ) : (
          <span className="block text-sm text-ink">{ROLE_LABELS[event.actor_role]}</span>
        )}
        <span className="block text-2xs text-slate-700">
          {event.actor_role === null || event.actor_role === undefined
            ? 'Seed or scheduled job'
            : 'Role held at the time'}
        </span>
      </TableCell>
      <TableCell className="min-w-[16rem] align-top">
        <span
          className="block text-sm font-medium leading-snug text-ink"
          title={event.action}
        >
          {auditActionLabel(event.action, event.policy_result)}
        </span>
        <span className="mt-0.5 block max-w-[60ch] text-xs leading-snug text-slate-700">
          {event.summary}
        </span>
      </TableCell>
      <TableCell className="align-top">
        <span className="block whitespace-nowrap text-sm text-ink">
          {auditObjectLabel(event.object_type)}
        </span>
        {event.object_public_ref === null ||
        event.object_public_ref === undefined ? null : (
          <span className="block font-mono text-2xs text-slate-700">
            {event.object_public_ref}
          </span>
        )}
      </TableCell>
      <TableCell className="align-top">
        {denied ? (
          <Badge variant="destructive" className="whitespace-nowrap text-2xs font-medium">
            <Ban aria-hidden="true" />
            {POLICY_RESULT_LABELS.DENY}
          </Badge>
        ) : (
          <Badge
            variant="outline"
            className="whitespace-nowrap text-2xs font-medium text-ok"
          >
            <Check aria-hidden="true" />
            {POLICY_RESULT_LABELS.ALLOW}
          </Badge>
        )}
      </TableCell>
      <TableCell className="align-top">
        <Badge variant="outline" className="whitespace-nowrap text-2xs font-medium">
          {CLASSIFICATION_LABELS[event.classification]}
        </Badge>
      </TableCell>
      <TableCell className="tabular whitespace-nowrap align-top text-xs text-slate-700">
        {TIME_FORMAT.format(new Date(event.occurred_at))}
      </TableCell>
    </TableRow>
  );
}
