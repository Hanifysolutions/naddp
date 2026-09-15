'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Ban,
  Check,
  CircleDashed,
  Clock,
  FileText,
  Lock,
  type LucideIcon,
} from 'lucide-react';
import {
  isApiError,
  type CaseChecklistItem,
  type CaseEvidenceItem,
  type CaseWorkspace,
} from '@naddp/contracts';

import { CaseActionsPanel } from '@/components/consular/case-actions-panel';
import { CaseStatusChip, SlaChip } from '@/components/consular/case-chips';
import { CaseTimeline } from '@/components/consular/case-timeline';
import {
  formatBusinessDays,
  sentenceCase,
  slaPhrase,
  slaTick,
} from '@/components/consular/consular-format';
import { TriagePanel } from '@/components/consular/triage-panel';
import { useCaseActions } from '@/components/consular/use-case-actions';
import { useDemoSession } from '@/components/layout/session-provider';
import { MeetingField } from '@/components/meetings/meeting-field';
import { formatDate, formatDateTime } from '@/components/meetings/meeting-format';
import { RefusalAlert } from '@/components/meetings/refusal-alert';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchCaseWorkspace } from '@/lib/api-queries';
import {
  CLASSIFICATION_LABELS,
  EVIDENCE_TYPE_LABELS,
  PRIORITY_LABELS,
} from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * One consular case: the clock, what is on file, what happened, and what may happen next.
 *
 * **Layout serves "AI recommends, a human decides".** On a laptop the right column holds the
 * triage panel and the officer actions, sticky under the top bar, so the recommendation, its
 * routing badge and the officer's confirmation are on screen together at 1440x900 while the
 * clock, checklist, evidence and timeline scroll beside them. The column comes first in the
 * DOM, so below the laptop breakpoint - and in keyboard order - the governed act is reached
 * first rather than beneath a long timeline.
 *
 * **What is not here is deliberate.** No subject name: the case is identified by its opaque
 * reference and a synthetic file token. No documents: evidence is metadata, a label, a type and
 * a verification state. The API never sends either, so this page cannot leak them.
 *
 * The case is one `GET /v1/consular/cases/{id}`, identity-scoped (its events are statements
 * about this caller), so the query key carries the role and a 403 for a role or zone the caller
 * does not hold is the API's to make and to audit.
 */

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && [403, 404, 422].includes(error.status)) return false;
  return failureCount < 2;
}

export function CaseWorkspaceSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-5" aria-busy="true">
      <Skeleton className="h-4 w-36" />
      <Skeleton className="h-8 w-[36rem] max-w-full" />
      <Skeleton className="h-4 w-[48rem] max-w-full" />
      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1fr)_30rem] laptop:items-start">
        <Skeleton className="h-[34rem] rounded-lg laptop:col-start-2 laptop:row-start-1" />
        <Skeleton className="h-[44rem] rounded-lg laptop:col-start-1 laptop:row-start-1" />
      </div>
      <span className="sr-only" role="status">
        Loading the case
      </span>
    </div>
  );
}

export function CaseWorkspaceView({ caseId }: { caseId: string }): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const workspace = useQuery({
    queryKey: queryKeys.consularCase(role, caseId),
    queryFn: ({ signal }) => fetchCaseWorkspace(caseId, signal),
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">No demo identity resolved</AlertTitle>
        <AlertDescription>Choose a role to open this case. Nothing is shown without one.</AlertDescription>
      </Alert>
    );
  }

  if (workspace.isPending) return <CaseWorkspaceSkeleton />;

  if (workspace.isError) {
    const apiError = isApiError(workspace.error) ? workspace.error : null;

    if (apiError !== null && apiError.status === 403) {
      return (
        <div className="space-y-4">
          <BackLink />
          <Alert variant="warning" role="status">
            <Ban aria-hidden="true" />
            <AlertTitle className="leading-snug text-warn-ink">
              This case is not available to this role
            </AlertTitle>
            <AlertDescription className="max-w-[72ch]">
              <p>
                {apiError.message}
                {apiError.missingPermissions.length > 0
                  ? ` Missing: ${apiError.missingPermissions.join(', ')}.`
                  : null}
              </p>
              <p className="mt-2">
                The refusal was made by the API, not by this page, and it was written to the
                audit log.
              </p>
            </AlertDescription>
          </Alert>
        </div>
      );
    }

    if (apiError !== null && (apiError.status === 404 || apiError.status === 422)) {
      return (
        <div className="space-y-4">
          <BackLink />
          <Alert variant="default" role="status">
            <FileText aria-hidden="true" />
            <AlertTitle className="leading-snug">No such case</AlertTitle>
            <AlertDescription className="max-w-[72ch]">{apiError.message}</AlertDescription>
          </Alert>
        </div>
      );
    }

    return (
      <div className="space-y-4">
        <BackLink />
        <Alert variant="destructive" role="status">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle className="leading-snug">The case could not be loaded</AlertTitle>
          <AlertDescription className="max-w-[72ch]">
            {apiError === null ? 'The API did not answer.' : apiError.message}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <CaseWorkspaceBody workspace={workspace.data} />;
}

function BackLink(): React.JSX.Element {
  return (
    <Link href="/consular" className="inline-block text-sm text-accent underline underline-offset-2">
      Consular dashboard
    </Link>
  );
}

function CaseWorkspaceBody({ workspace }: { workspace: CaseWorkspace }): React.JSX.Element {
  const session = useDemoSession();
  const actions = useCaseActions(session.role, workspace.id);

  return (
    <div className="space-y-5">
      <BackLink />

      <header className="space-y-2.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-xl font-semibold leading-snug text-ink">
            {workspace.case_type_label}
          </h1>
          <span className="font-mono text-label text-slate-700">{workspace.public_ref}</span>
          <Badge variant="outline" className="text-2xs font-medium">
            {CLASSIFICATION_LABELS[workspace.classification]}
          </Badge>
          <CaseStatusChip status={workspace.status} />
          <SlaChip state={workspace.sla.state} />
        </div>

        <dl className="flex flex-wrap items-baseline gap-x-6 gap-y-1.5">
          <MeetingField label="Priority">
            {workspace.status === 'NEW'
              ? `${PRIORITY_LABELS[workspace.priority]}, not yet confirmed at triage`
              : PRIORITY_LABELS[workspace.priority]}
          </MeetingField>
          <MeetingField label="File">
            <span className="font-mono">{workspace.subject_reference ?? 'Not recorded'}</span>
          </MeetingField>
          <MeetingField label="Opened">
            <span className="tabular">{formatDateTime(workspace.opened_at)}</span>
          </MeetingField>
          <MeetingField label="Channel">{sentenceCase(workspace.channel)}</MeetingField>
          <MeetingField label="Officer">
            {workspace.assigned_officer_name ?? 'Not assigned'}
          </MeetingField>
        </dl>

        <p className="max-w-[80ch] text-label text-slate-700">
          Synthetic demonstration case. The person is identified only by a file token: no name,
          passport number or document is shown here, and none is sent to the AI.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1fr)_30rem] laptop:items-start">
        <div className="min-w-0 space-y-4 laptop:sticky laptop:top-20 laptop:col-start-2 laptop:row-start-1 laptop:max-h-[calc(100vh-6rem)] laptop:overflow-y-auto">
          {actions.refusal === null ? null : <RefusalAlert refusal={actions.refusal} />}
          <TriagePanel workspace={workspace} actions={actions} />
          <CaseActionsPanel workspace={workspace} actions={actions} />
        </div>

        <div className="min-w-0 space-y-4 laptop:col-start-1 laptop:row-start-1">
          <SlaPanel workspace={workspace} />
          <OutcomePanel workspace={workspace} />
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <ChecklistCard items={workspace.checklist} />
            <EvidenceCard evidence={workspace.evidence} />
          </div>
          <PrecisCard summary={workspace.summary} />
          <CaseTimeline entries={workspace.timeline} />
        </div>
      </div>
    </div>
  );
}

function SlaPanel({ workspace }: { workspace: CaseWorkspace }): React.JSX.Element {
  const { sla } = workspace;
  const headingId = React.useId();

  return (
    <Card aria-labelledby={headingId} className="overflow-hidden">
      {/* The tick rides an inner wrapper, as on the command tiles: on the Card itself it would
          lose its colour to the Card's own hairline border utility. */}
      <div className={cn('flex flex-col', slaTick(sla.state))}>
        <CardHeader className="gap-1 pb-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id={headingId} className="text-base font-semibold text-ink">
              Service level
            </h2>
            <SlaChip state={sla.state} />
          </div>
          <p className="text-label leading-snug text-slate-700">
            Counted in business days on the mission calendar. The clock pauses while the case
            waits on the citizen, and restarts with a fresh budget if the case is reopened.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <p
            className={cn(
              'tabular font-display text-figure-sm font-semibold leading-tight',
              sla.state === 'BREACHED'
                ? 'text-risk'
                : sla.state === 'DUE_SOON'
                  ? 'text-warn-ink'
                  : 'text-ink',
            )}
          >
            {slaPhrase(sla)}
          </p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 md:grid-cols-4">
            <PanelFact label="Service standard">
              {sla.budget_business_days === null
                ? 'None set'
                : formatBusinessDays(sla.budget_business_days)}
            </PanelFact>
            <PanelFact label="Chargeable time">
              {formatBusinessDays(sla.elapsed_business_days)}
            </PanelFact>
            <PanelFact label="Paused on the citizen">
              {formatBusinessDays(sla.paused_business_days)}
            </PanelFact>
            <PanelFact label="Due">
              {sla.due_at === null ? 'No due date' : formatDateTime(sla.due_at)}
            </PanelFact>
          </dl>
        </CardContent>
      </div>
    </Card>
  );
}

function PanelFact({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flex min-w-0 flex-col-reverse gap-0.5">
      <dt className="text-label text-slate-700">{label}</dt>
      <dd className="tabular text-sm font-medium text-ink">{children}</dd>
    </div>
  );
}

/** The determination and the closure, each with the named human who made it. */
function OutcomePanel({ workspace }: { workspace: CaseWorkspace }): React.JSX.Element | null {
  if (workspace.determination === null && workspace.close_reason === null) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <h2 className="text-base font-semibold text-ink">Outcome</h2>
      </CardHeader>
      <CardContent className="space-y-3">
        {workspace.determination === null ? null : (
          <div>
            <p className="text-label text-slate-700">Determination</p>
            <p className="max-w-[72ch] text-sm leading-relaxed text-ink">
              {workspace.determination}
            </p>
            <p className="text-label text-slate-700">
              {workspace.determined_by_name ?? 'Officer not recorded'}
              {workspace.determined_at === null ? '' : `, ${formatDateTime(workspace.determined_at)}`}
            </p>
          </div>
        )}
        {workspace.close_reason === null ? null : (
          <div>
            <p className="text-label text-slate-700">Closed</p>
            <p className="max-w-[72ch] text-sm leading-relaxed text-ink">{workspace.close_reason}</p>
            <p className="text-label text-slate-700">
              {workspace.closed_by_name ?? 'Officer not recorded'}
              {workspace.closed_at === null ? '' : `, ${formatDateTime(workspace.closed_at)}`}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const CHECK_STATE: Readonly<
  Record<CaseChecklistItem['state'], { icon: LucideIcon; label: string; tone: string }>
> = {
  verified: { icon: Check, label: 'Verified', tone: 'text-ok' },
  done: { icon: Check, label: 'Done', tone: 'text-ok' },
  received: { icon: Clock, label: 'Received, not yet verified', tone: 'text-warn-ink' },
  missing: { icon: CircleDashed, label: 'Missing', tone: 'text-warn-ink' },
  pending: { icon: CircleDashed, label: 'Pending', tone: 'text-slate-700' },
};

function ChecklistCard({ items }: { items: readonly CaseChecklistItem[] }): React.JSX.Element {
  return (
    <Card>
      <CardHeader className="gap-1 pb-2">
        <h2 className="text-base font-semibold text-ink">Checklist</h2>
        <p className="text-label text-slate-700">
          What this case type needs on file, and the steps the workflow expects.
        </p>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-slate-700">No checklist for this case type.</p>
        ) : (
          <ul className="space-y-2">
            {items.map((item, index) => {
              const state = CHECK_STATE[item.state];
              const Icon = state.icon;
              return (
                <li key={`${item.kind}#${index}`} className="flex items-start gap-2">
                  <Icon aria-hidden="true" className={cn('mt-0.5 h-4 w-4 shrink-0', state.tone)} />
                  <div className="min-w-0">
                    <p className="text-sm leading-snug text-ink">{item.label}</p>
                    <p className={cn('text-label', state.tone)}>{state.label}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function EvidenceCard({ evidence }: { evidence: readonly CaseEvidenceItem[] }): React.JSX.Element {
  return (
    <Card>
      <CardHeader className="gap-1 pb-2">
        <h2 className="text-base font-semibold text-ink">Evidence on file</h2>
        <p className="text-label text-slate-700">
          Metadata only. The documents are never displayed here and never sent to the AI.
        </p>
      </CardHeader>
      <CardContent>
        {evidence.length === 0 ? (
          <p className="text-sm text-slate-700">No evidence received yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {evidence.map((item) => (
              <li key={item.id} className="space-y-0.5 py-2 first:pt-0 last:pb-0">
                <p className="text-sm font-medium leading-snug text-ink">{item.label}</p>
                <p className="text-label text-slate-700">
                  {EVIDENCE_TYPE_LABELS[item.evidence_type]}, received{' '}
                  <span className="tabular">{formatDate(item.received_at)}</span>
                </p>
                <p className={cn('text-label', item.verified ? 'text-ok' : 'text-warn-ink')}>
                  {item.verified
                    ? `Verified${item.verified_at === null ? '' : ` ${formatDate(item.verified_at)}`}`
                    : 'Not yet verified'}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function PrecisCard({ summary }: { summary: string }): React.JSX.Element {
  return (
    <Card>
      <CardHeader className="gap-1 pb-2">
        <h2 className="text-base font-semibold text-ink">Officer&apos;s precis</h2>
        <p className="text-label text-slate-700">
          Shown to cleared consular staff. Never sent to the AI triage, which reads metadata only.
        </p>
      </CardHeader>
      <CardContent>
        <p className="max-w-[72ch] whitespace-pre-line text-sm leading-relaxed text-ink">
          {summary}
        </p>
      </CardContent>
    </Card>
  );
}
