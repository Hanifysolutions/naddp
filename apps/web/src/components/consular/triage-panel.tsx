'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Bot, ExternalLink, Lock, ShieldCheck, UserRound } from 'lucide-react';
import type {
  AiTrace,
  BriefTrace,
  CaseWorkspace,
  EvidenceRef,
  GatedCaseEvent,
  Priority,
} from '@naddp/contracts';

import { PriorityChip } from '@/components/consular/case-chips';
import type { CaseActions } from '@/components/consular/use-case-actions';
import { TraceBadge } from '@/components/intelligence/trace-badge';
import { useDemoSession } from '@/components/layout/session-provider';
import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchAiTrace } from '@/lib/api-queries';
import { PRIORITY_LABELS, PRIORITY_ORDER } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * Triage: the AI recommends, a named officer decides. Two blocks, visibly separate.
 *
 * **The recommendation** comes from `POST /v1/ai/consular/cases/{id}/triage`. The call is
 * declared CONSULAR-SENSITIVE, so the Gateway routes it to no external model and answers with
 * mission-local rules over six metadata fields; the case narrative, the subject and the
 * documents are never read. That is not asserted by this page: the routing strip reads the
 * trace the call wrote and prints its badge verbatim - "CONSULAR-SENSITIVE · no external route ·
 * metadata-only · generation withheld" - and says "no model was asked" only when the trace's own
 * fields say so. The block carries the `--proposed` tick, the product's mark for machine output.
 *
 * **The decision** is a separate act. The officer picks a priority themselves - nothing is
 * pre-selected, and the AI's choice is only marked beside its option - and confirms. The trace
 * id travels with the event so the server can record that a recommendation informed the
 * decision; the server verifies it is a triage trace about THIS case and records it as
 * provenance, never as the value. A role without `triage:consular_case` sees the refusal the
 * server would give, in the server's words.
 */

interface TriageProposal {
  readonly priority: Priority;
  readonly slaDays: number;
  readonly rationale: string;
  readonly nextSteps: readonly string[];
  readonly inputsUsed: readonly string[];
  readonly narrativeWithheld: boolean;
  readonly confidence: number | null;
}

function stringList(value: unknown): readonly string[] | null {
  if (!Array.isArray(value)) return null;
  const strings = value.filter((entry): entry is string => typeof entry === 'string');
  return strings.length === value.length ? strings : null;
}

function isPriority(value: unknown): value is Priority {
  return typeof value === 'string' && (PRIORITY_ORDER as readonly string[]).includes(value);
}

/**
 * Narrow the envelope's open `result` to the fields this panel renders, or refuse.
 *
 * Defensive on purpose: a result that does not parse renders as "could not be read" rather
 * than as a half-filled recommendation an officer might act on.
 */
function readTriageProposal(result: unknown): TriageProposal | null {
  if (typeof result !== 'object' || result === null) return null;
  const record = result as Record<string, unknown>;
  const priority = record['proposed_priority'];
  const slaDays = record['proposed_sla_days'];
  const rationale = record['rationale'];
  const nextSteps = stringList(record['next_steps']);
  const inputsUsed = stringList(record['inputs_used']);
  const confidence = record['confidence'];
  if (
    !isPriority(priority) ||
    typeof slaDays !== 'number' ||
    typeof rationale !== 'string' ||
    nextSteps === null ||
    inputsUsed === null
  ) {
    return null;
  }
  return {
    priority,
    slaDays,
    rationale,
    nextSteps,
    inputsUsed,
    narrativeWithheld: record['narrative_withheld'] === true,
    confidence: typeof confidence === 'number' ? confidence : null,
  };
}

/** The trace row, in the shape `TraceBadge` and its drawer already render. */
function asBriefTrace(trace: AiTrace): BriefTrace {
  return {
    trace_id: trace.id,
    route_badge: trace.route_badge,
    data_class: trace.data_class,
    result_class: trace.result_class,
    model_route: trace.model_route,
    route_reason: trace.route_reason,
    model_requested: trace.model_requested,
    model_used: trace.model_used,
    fallback: trace.fallback,
    fallback_reason: trace.fallback_reason,
  };
}

/** What the trace says happened at generation, derived from its fields and nothing else. */
function routingSentence(trace: AiTrace): string {
  if (trace.model_used !== null) return `Answered by ${trace.model_used}.`;
  if (trace.fallback) return 'No model answered; a stored deterministic snapshot was served.';
  if (trace.model_requested === null) {
    return 'No model was asked. Mission-local rules answered from case metadata alone, and nothing left the mission.';
  }
  return 'No model answered this call.';
}

export function TriagePanel({
  workspace,
  actions,
}: {
  workspace: CaseWorkspace;
  actions: CaseActions;
}): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;
  const mayPropose = session.can('triage:consular_case');
  const mayReadTrace = session.can('read:ai_trace');
  const headingId = React.useId();

  const envelope = actions.triage.data ?? null;
  const traceId = envelope === null ? null : envelope.trace_id;
  const trace = useQuery({
    queryKey: queryKeys.aiTrace(role, traceId ?? 'none'),
    queryFn: ({ signal }) => fetchAiTrace(traceId ?? '', signal),
    enabled: traceId !== null && mayReadTrace,
    staleTime: Infinity,
    retry: false,
  });

  const proposal = envelope === null ? null : readTriageProposal(envelope.result);
  const triageOpen = workspace.available_events.includes('triage');
  const triageGate = workspace.gated_events.find((gate) => gate.event === 'triage') ?? null;
  const closed = workspace.status === 'CLOSED';

  return (
    <Card aria-labelledby={headingId}>
      <CardHeader className="gap-1 pb-3">
        <h2 id={headingId} className="text-base font-semibold text-ink">
          Triage
        </h2>
        <p className="text-label leading-snug text-slate-700">
          The AI recommends. A named consular officer decides, and only the officer&apos;s
          decision changes the case.
        </p>
      </CardHeader>

      <CardContent className="space-y-4">
        <section
          aria-label="AI recommendation"
          className="tick-proposed space-y-3 rounded-md border border-line py-3 pl-3.5 pr-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-sm font-semibold text-ink">
              <Bot aria-hidden="true" className="h-4 w-4 text-slate-700" />
              AI recommends
            </p>
            {envelope !== null && envelope.approval_status === 'PENDING_APPROVAL' ? (
              <Badge variant="proposed" className="text-2xs font-medium">
                Pending a named officer
              </Badge>
            ) : null}
          </div>

          {envelope === null ? (
            <IdleRecommendation
              mayPropose={mayPropose && !closed}
              closed={closed}
              pending={actions.triage.isPending}
              onPropose={() => actions.triage.mutate()}
            />
          ) : proposal === null ? (
            <p className="text-sm leading-relaxed text-ink">
              {envelope.explanation ??
                'The recommendation could not be read, so none is shown. Nothing about the case has changed.'}
            </p>
          ) : (
            <div className="space-y-3">
              <RoutingStrip
                trace={trace.data ?? null}
                loading={trace.isPending && mayReadTrace}
                failed={trace.isError}
                mayReadTrace={mayReadTrace}
                traceId={envelope.trace_id}
              />

              <dl className="grid grid-cols-3 gap-3">
                <Fact label="Priority">
                  <PriorityChip priority={proposal.priority} />
                </Fact>
                <Fact label="Standard">
                  <span className="tabular">{proposal.slaDays} business days</span>
                </Fact>
                <Fact label="Confidence">
                  <span className="tabular">
                    {proposal.confidence === null
                      ? 'Not assessed'
                      : `${Math.round(proposal.confidence * 100)}%`}
                  </span>
                </Fact>
              </dl>

              <p className="max-w-[72ch] text-sm leading-relaxed text-ink">{proposal.rationale}</p>

              <div>
                <p className="text-label text-slate-700">Suggested next steps</p>
                <ol className="mt-1 list-decimal space-y-1 pl-5 text-sm leading-snug text-ink">
                  {proposal.nextSteps.map((step, index) => (
                    <li key={`${index}#${step}`}>{step}</li>
                  ))}
                </ol>
              </div>

              <p className="text-label leading-relaxed text-slate-700">
                Read from{' '}
                {proposal.inputsUsed.map((field, index) => (
                  <React.Fragment key={field}>
                    {index === 0 ? null : ' '}
                    <code className="rounded border border-line px-1 font-mono text-2xs text-ink">
                      {field}
                    </code>
                  </React.Fragment>
                ))}
                {proposal.narrativeWithheld ? '. The case narrative was withheld.' : null}
              </p>

              <SourceList evidence={envelope.evidence ?? []} />
            </div>
          )}
        </section>

        <section aria-label="Officer decision" className="space-y-3 rounded-md border border-line p-3">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-ink">
            <UserRound aria-hidden="true" className="h-4 w-4 text-slate-700" />
            You decide
          </p>
          {triageOpen ? (
            <TriageDecision
              workspace={workspace}
              actions={actions}
              recommended={proposal === null ? null : proposal.priority}
              traceId={proposal === null ? null : traceId}
            />
          ) : triageGate !== null ? (
            <GatedNote gate={triageGate} />
          ) : (
            <p className="text-sm leading-relaxed text-ink">
              {workspace.status === 'NEW'
                ? 'Triage is not open to your role for this case.'
                : 'Triage was confirmed by a named officer; the timeline records who and when.'}{' '}
              Priority on record: <PriorityChip priority={workspace.priority} className="ml-1" />
            </p>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function IdleRecommendation({
  mayPropose,
  closed,
  pending,
  onPropose,
}: {
  mayPropose: boolean;
  closed: boolean;
  pending: boolean;
  onPropose: () => void;
}): React.JSX.Element {
  return (
    <div className="space-y-2.5">
      <p className="max-w-[72ch] text-sm leading-relaxed text-ink">
        A recommendation reads six metadata fields only: case type, chargeable age, service-level
        state, business days remaining, days paused and status. The narrative, the subject and the
        documents are never read, and a consular-sensitive call is routed to no external model.
      </p>
      {mayPropose ? (
        <Button variant="outline" size="sm" onClick={onPropose} disabled={pending}>
          <Bot aria-hidden="true" className="mr-1.5 h-3.5 w-3.5" />
          {pending ? 'Asking…' : 'Get AI recommendation'}
        </Button>
      ) : (
        <p className="flex items-start gap-1.5 text-label text-slate-700">
          <Lock aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
          {closed
            ? 'A closed case is not triaged.'
            : 'Requesting a recommendation needs triage:consular_case, which this role does not hold.'}
        </p>
      )}
    </div>
  );
}

function RoutingStrip({
  trace,
  loading,
  failed,
  mayReadTrace,
  traceId,
}: {
  trace: AiTrace | null;
  loading: boolean;
  failed: boolean;
  mayReadTrace: boolean;
  traceId: string;
}): React.JSX.Element {
  return (
    <div className="space-y-1.5 rounded-md border border-slate-400/60 bg-background p-2.5">
      <p className="flex items-center gap-1.5 text-label font-medium text-ink">
        <ShieldCheck aria-hidden="true" className="h-3.5 w-3.5 text-slate-700" />
        How this recommendation was routed
      </p>
      {!mayReadTrace ? (
        <p className="text-label leading-snug text-slate-700">
          The routing record is shown to roles holding read:ai_trace. Trace{' '}
          <span className="font-mono text-2xs">{traceId}</span>.
        </p>
      ) : loading ? (
        <Skeleton className="h-6 w-full" />
      ) : failed || trace === null ? (
        <p className="text-label leading-snug text-slate-700">
          The routing record could not be loaded. Trace{' '}
          <span className="font-mono text-2xs">{traceId}</span>.
        </p>
      ) : (
        <>
          <TraceBadge trace={asBriefTrace(trace)} subject="recommendation" />
          <p className="text-label leading-snug text-slate-700">{routingSentence(trace)}</p>
        </>
      )}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <div className="flex min-w-0 flex-col-reverse items-start gap-1">
      <dt className="text-label text-slate-700">{label}</dt>
      <dd className="text-sm font-medium text-ink">{children}</dd>
    </div>
  );
}

function SourceList({ evidence }: { evidence: readonly EvidenceRef[] }): React.JSX.Element {
  if (evidence.length === 0) {
    return <p className="text-label text-slate-700">No published guidance was cited.</p>;
  }
  return (
    <section aria-label="Published guidance cited" className="min-w-0">
      <p className="text-label text-slate-700">Published guidance cited</p>
      <ul className="mt-1 space-y-1.5">
        {evidence.map((source, index) => {
          const url = source.url ?? null;
          const publisher = source.source ?? null;
          return (
            <li key={`${source.id}#${index}`} className="text-xs">
              {url === null ? (
                <span className="font-medium text-ink">{source.title}</span>
              ) : (
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-start gap-1 font-medium text-accent underline underline-offset-2"
                >
                  {source.title}
                  <ExternalLink aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              )}
              {publisher === null ? null : (
                <span className="block text-slate-700">{publisher}</span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function GatedNote({ gate }: { gate: GatedCaseEvent }): React.JSX.Element {
  return (
    <p className="flex items-start gap-1.5 text-sm leading-relaxed text-ink">
      <Lock aria-hidden="true" className="mt-1 h-3.5 w-3.5 shrink-0 text-slate-700" />
      <span>
        {gate.label} needs <code className="font-mono text-2xs">{gate.permission}</code>. {gate.reason}{' '}
        The server refuses it, on the record, if it is attempted.
      </span>
    </p>
  );
}

function TriageDecision({
  workspace,
  actions,
  recommended,
  traceId,
}: {
  workspace: CaseWorkspace;
  actions: CaseActions;
  recommended: Priority | null;
  traceId: string | null;
}): React.JSX.Element {
  const [choice, setChoice] = React.useState<Priority | null>(null);
  const groupName = React.useId();
  const legendId = React.useId();
  const submitting = actions.transition.isPending;

  return (
    <form
      className="space-y-3"
      onSubmit={(submitEvent) => {
        submitEvent.preventDefault();
        if (choice === null || submitting) return;
        actions.transition.mutate({
          event: 'triage',
          priority: choice,
          expected_status: workspace.status,
          triage_trace_id: traceId,
        });
      }}
    >
      <fieldset aria-labelledby={legendId}>
        <legend id={legendId} className="text-label text-slate-700">
          The priority you confirm
        </legend>
        <div className="mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {PRIORITY_ORDER.map((priority) => (
            <label
              key={priority}
              className={cn(
                'flex cursor-pointer items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 text-sm text-ink',
                choice === priority ? 'border-accent bg-accent/5' : 'border-line',
              )}
            >
              <span className="flex items-center gap-2">
                <input
                  type="radio"
                  name={groupName}
                  value={priority}
                  checked={choice === priority}
                  onChange={() => setChoice(priority)}
                />
                {PRIORITY_LABELS[priority]}
              </span>
              {recommended === priority ? (
                <Badge variant="proposed" className="gap-1 text-2xs font-medium">
                  <Bot aria-hidden="true" className="h-3 w-3" />
                  AI recommends
                </Badge>
              ) : null}
            </label>
          ))}
        </div>
      </fieldset>

      <p className="text-label leading-snug text-slate-700">
        Confirming triage is a human decision, recorded against your name on the case timeline
        and in the audit log.{' '}
        {traceId === null
          ? 'No AI recommendation will be recorded with it.'
          : 'The recommendation is recorded as having informed it; it does not set the priority.'}
      </p>

      <Button
        type="submit"
        disabled={choice === null || submitting}
        className={cn('w-full sm:w-auto', PRIMARY_ACTION)}
      >
        {submitting
          ? 'Recording…'
          : choice === null
            ? 'Choose a priority to confirm'
            : `Confirm triage as ${PRIORITY_LABELS[choice]}`}
      </Button>
    </form>
  );
}
