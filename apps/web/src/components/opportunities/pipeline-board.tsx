'use client';

import * as React from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Ban,
  CalendarClock,
  Columns3,
  FileText,
  Lock,
  Rows3,
  Sparkles,
  User,
} from 'lucide-react';
import { isApiError, type BoardCard, type BoardColumn } from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { TransitionDialog } from '@/components/opportunities/transition-dialog';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { fetchPipelineBoard, transitionOpportunity } from '@/lib/api-queries';
import { formatAud } from '@/lib/command-view';
import { CLASSIFICATION_LABELS, OPPORTUNITY_STAGE_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * The opportunity pipeline: kanban and table over the same server-built board.
 *
 * **The server decides everything that matters.** Columns, counts, card contents and - the
 * part that would be tempting to get wrong - which events each card may offer all come from
 * `GET /v1/opportunities/board`. The client never derives a button from a role, because a
 * client that could do that is a client that can disagree with the server, and the one it
 * would be believed over is the wrong one.
 *
 * **A transition is a request, not a drag.** There is no drag-and-drop: every stage change
 * goes through a dialog that requires a reason and posts an explicit event with the stage it
 * expected to find. Dragging a card would imply the board is the authority and would make an
 * audited, reasoned act feel like rearranging furniture.
 *
 * Both views render the same `BoardCard[]`. The table exists because a kanban is a poor way
 * to compare eleven opportunities on value or next action, and the demo audience will want
 * to do exactly that.
 *
 * **Colour is spent only on state** (DESIGN_SYSTEM.md). A stage is a position in a workflow,
 * not a condition, so no column is tinted for being a column; the semantic tokens appear only
 * where something is actually true of a row - overdue in `--risk`, a refused commitment in
 * `--warn`, an AI-proposed opportunity in `--proposed`.
 */

type ViewMode = 'board' | 'table';

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

export function PipelineBoard(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;
  const queryClient = useQueryClient();
  const [view, setView] = React.useState<ViewMode>('board');
  const [pending, setPending] = React.useState<{ card: BoardCard; event: string } | null>(
    null,
  );
  const [refusal, setRefusal] = React.useState<string | null>(null);

  const board = useQuery({
    queryKey: queryKeys.pipelineBoard(role),
    queryFn: ({ signal }) => fetchPipelineBoard(signal),
    enabled: role !== null,
    retry: retryUnlessRefused,
  });

  const transition = useMutation({
    mutationFn: (input: { card: BoardCard; event: string; reason: string }) =>
      transitionOpportunity(input.card.id, input.event, input.reason, input.card.stage),
    onSuccess: async () => {
      setPending(null);
      setRefusal(null);
      // Refetch rather than patch the cache: a transition can change the row's
      // classification (entering NEGOTIATION raises it to CONFIDENTIAL), which can remove
      // the card from this caller's board entirely. A local patch would leave a card on
      // screen that the server would no longer return.
      await queryClient.invalidateQueries({ queryKey: queryKeys.pipelineBoard(role) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.commandToday(role) });
    },
    onError: (error: unknown) => {
      // A refusal is shown, not swallowed. A 403 here is a control working (BUILD_BIBLE
      // section 6) and the audience needs to read the server's own words for it.
      setRefusal(
        isApiError(error)
          ? // The server's own words, plus the permission it named when it named one. A
            // paraphrase here would be the UI editing an authorisation decision.
            [
              error.message,
              error.missingPermissions.length > 0
                ? `Missing: ${error.missingPermissions.join(', ')}.`
                : null,
            ]
              .filter((part): part is string => part !== null)
              .join(' ')
          : 'The transition could not be applied.',
      );
    },
  });

  if (role === null) {
    return (
      <Alert variant="warning" role="status">
        <Lock aria-hidden="true" />
        <AlertTitle>No demo identity resolved</AlertTitle>
        <AlertDescription>
          Choose a role to see the pipeline. Nothing is shown without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (board.isPending) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-64" />
        ))}
      </div>
    );
  }

  if (board.isError) {
    const error = board.error;
    const forbidden = isApiError(error) && error.status === 403;
    return (
      <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
        {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
        <AlertTitle>
          {forbidden
            ? 'This role may not read the pipeline'
            : 'The pipeline could not be loaded'}
        </AlertTitle>
        <AlertDescription>
          {isApiError(error) ? error.message : 'The API did not answer.'}
        </AlertDescription>
      </Alert>
    );
  }

  const data = board.data;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-ink">Opportunity pipeline</h1>
          <p className="tabular max-w-[72ch] text-sm text-slate-700">
            {data.total} opportunit{data.total === 1 ? 'y' : 'ies'} you are cleared to
            read, {data.open_total} open, {formatAud(data.pipeline_value_aud)} estimated (
            {formatAud(data.weighted_pipeline_value_aud)} probability-weighted).
            {data.overdue_next_action > 0
              ? ` ${data.overdue_next_action} overdue next action${
                  data.overdue_next_action === 1 ? '' : 's'
                }.`
              : ''}
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-line p-1">
          <Button
            variant={view === 'board' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setView('board')}
            aria-pressed={view === 'board'}
          >
            <Columns3 aria-hidden="true" className="size-4" /> Board
          </Button>
          <Button
            variant={view === 'table' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setView('table')}
            aria-pressed={view === 'table'}
          >
            <Rows3 aria-hidden="true" className="size-4" /> Table
          </Button>
        </div>
      </header>

      {refusal === null ? null : (
        <Alert variant="warning" role="status">
          <Lock aria-hidden="true" />
          <AlertTitle>Refused by the server</AlertTitle>
          <AlertDescription>
            {refusal} The attempt was written to the audit log.
          </AlertDescription>
        </Alert>
      )}

      {view === 'board' ? (
        <BoardView
          columns={data.columns}
          onFire={(card, event) => setPending({ card, event })}
        />
      ) : (
        <TableView columns={data.columns} />
      )}

      {pending === null ? null : (
        <TransitionDialog
          card={pending.card}
          event={pending.event}
          submitting={transition.isPending}
          onCancel={() => setPending(null)}
          onConfirm={(reason) =>
            transition.mutate({ card: pending.card, event: pending.event, reason })
          }
        />
      )}
    </div>
  );
}

function BoardView({
  columns,
  onFire,
}: {
  columns: readonly BoardColumn[];
  onFire: (card: BoardCard, event: string) => void;
}): React.JSX.Element {
  return (
    // Horizontal scroll on the container, never on the page body: eight columns do not fit
    // a laptop, and a page that scrolls sideways loses the rail and the DEMO badge.
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-max gap-3">
        {columns.map((column) => {
          // The stage enum is the server's; the caption is ours. `column.label` arrives
          // Title Cased ("Contact Planned") and DESIGN_SYSTEM.md asks for sentence case
          // everywhere, so the shared label map captions both the column and its
          // accessible name, which keeps the two from drifting apart.
          const label = OPPORTUNITY_STAGE_LABELS[column.stage];
          return (
            <section
              key={column.stage}
              aria-label={`${label}, ${column.count} opportunities`}
              className="w-72 shrink-0 rounded-lg border border-line bg-muted/40 p-2"
            >
              <header className="space-y-0.5 px-1 pb-2">
                <div className="flex items-baseline justify-between gap-2">
                  <h2 className="text-sm font-semibold text-ink">
                    {label}
                    {column.is_terminal ? (
                      <span className="ml-1.5 text-label font-normal text-slate-700">
                        terminal
                      </span>
                    ) : null}
                  </h2>
                  <span className="tabular text-label font-medium text-slate-700">
                    {column.count}
                  </span>
                </div>
                {column.value_estimate_aud > 0 ? (
                  <p className="tabular text-label text-slate-700">
                    Estimated {formatAud(column.value_estimate_aud)}
                  </p>
                ) : null}
              </header>
              <div className="space-y-2">
                {column.cards.length === 0 ? (
                  <p className="px-1 py-6 text-center text-label text-slate-700">
                    Nothing at this stage.
                  </p>
                ) : (
                  column.cards.map((card) => (
                    <OpportunityCard key={card.id} card={card} onFire={onFire} />
                  ))
                )}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function OpportunityCard({
  card,
  onFire,
}: {
  card: BoardCard;
  onFire: (card: BoardCard, event: string) => void;
}): React.JSX.Element {
  const commitmentGate = card.gated_events.find((gate) => gate.is_commitment);
  return (
    // Q-17: an AI-proposed opportunity carries the --proposed state tick as well as its
    // badge, so the honesty gap is structural and survives a glance down the column.
    //
    // Spelled as border utilities rather than as the `.tick-proposed` component class:
    // Card already sets `border border-line`, and a utility beats a components-layer rule,
    // so the named tick would be painted over in hairline grey and silently do nothing.
    <Card className={cn('p-3', card.is_proposed_by_ai && 'border-l-2 border-l-proposed')}>
      <CardHeader className="space-y-1 p-0">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-medium leading-snug text-ink">{card.title}</h3>
          {card.score === null ? null : (
            <span className="tabular shrink-0 rounded-md bg-muted px-1.5 py-0.5 text-label font-semibold text-ink">
              <span className="sr-only">Score </span>
              {Math.round(card.score)}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <Badge variant="outline" className="text-2xs font-medium">
            {CLASSIFICATION_LABELS[card.classification]}
          </Badge>
          {card.is_proposed_by_ai ? (
            // Q-17: the hero opportunity is the platform's own proposition, not a reported
            // fact. It has to read that way everywhere it appears, not only on its detail.
            <Badge variant="proposed" className="text-2xs font-medium">
              <Sparkles aria-hidden="true" className="size-3" /> AI-proposed
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-1.5 p-0 pt-2 text-xs text-slate-700">
        <p className="flex items-center gap-1.5">
          <User aria-hidden="true" className="size-3.5 shrink-0 text-slate-400" />
          <span className="truncate">Owner: {card.owner_name ?? 'unassigned'}</span>
        </p>
        {card.counterpart_name === null ? null : (
          <p className="truncate pl-5">Counterpart: {card.counterpart_name}</p>
        )}
        {card.organisation_id === null || card.organisation_name === null ? null : (
          <p className="truncate pl-5">
            <Link
              href={`/stakeholders/organisations/${card.organisation_id}`}
              className="text-accent underline underline-offset-2"
            >
              {card.organisation_name}
            </Link>
          </p>
        )}
        <p
          className={cn(
            'flex items-center gap-1.5',
            card.next_action_overdue && 'font-medium text-risk',
          )}
        >
          <CalendarClock
            aria-hidden="true"
            className={cn(
              'size-3.5 shrink-0',
              card.next_action_overdue ? 'text-risk' : 'text-slate-400',
            )}
          />
          <span className="tabular">
            Next action:{' '}
            {card.next_action_at === null
              ? 'none scheduled'
              : formatDate(card.next_action_at)}
            {/* The word carries the state as well as the colour does (WCAG 1.4.1). */}
            {card.next_action_overdue ? ', overdue' : ''}
          </span>
        </p>
        <p className="flex items-center gap-1.5">
          <FileText aria-hidden="true" className="size-3.5 shrink-0 text-slate-400" />
          <span className="tabular">
            Evidence: {card.evidence_count} citation{card.evidence_count === 1 ? '' : 's'}
          </span>
        </p>
        {card.value_estimate_aud === null ? null : (
          <p className="tabular pl-5">
            Estimated value: {formatAud(card.value_estimate_aud)}
            {card.probability === null
              ? ''
              : ` at ${Math.round(card.probability)}% probability`}
          </p>
        )}
      </CardContent>
      {card.available_events.length === 0 && commitmentGate === undefined ? null : (
        <div className="space-y-2 pt-3">
          {card.available_events.length === 0 ? null : (
            <div className="flex flex-wrap gap-1">
              {card.available_events.map((event) => (
                <Button
                  key={event}
                  size="sm"
                  variant="outline"
                  onClick={() => onFire(card, event)}
                >
                  {EVENT_LABELS[event] ?? event}
                </Button>
              ))}
            </div>
          )}
          {commitmentGate === undefined ? null : (
            // Shown, disabled, with the reason - never hidden. BUILD_BIBLE section 6
            // requires the demo to SHOW a never-autonomous control refusing, and an
            // absent button is indistinguishable from an absent feature.
            //
            // The reason is now rendered as text rather than left in a `title`: a disabled
            // control takes no pointer events, so that tooltip could never be read, and a
            // refusal nobody can read is not a refusal anybody can trust. The --warn tick
            // and the lock icon mark the state; the words are --warn-ink, which is the
            // AA-safe warn (--warn itself is border/icon only), and the kit's disabled
            // opacity is overridden so the contrast that was measured is the one shipped.
            <div className="tick-warn space-y-1.5 rounded-r-md bg-warn/5 p-2">
              <Button
                size="sm"
                variant="outline"
                disabled
                className="border-warn/60 text-warn-ink disabled:opacity-100"
              >
                <Lock aria-hidden="true" className="text-warn" /> {commitmentGate.label}
              </Button>
              <p className="text-2xs leading-snug text-warn-ink">
                {commitmentGate.reason}
              </p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

/** Client-side labels for the events. The server sends the same words for gated ones. */
const EVENT_LABELS: Readonly<Record<string, string>> = {
  qualify: 'Qualify',
  plan_contact: 'Plan approach',
  record_contact: 'Record contact',
  schedule_meeting: 'Schedule meeting',
  enter_negotiation: 'Open negotiation',
  partner: 'Commit to partnership',
  close: 'Close',
  dismiss: 'Dismiss',
  revert: 'Revert one stage',
};

function TableView({ columns }: { columns: readonly BoardColumn[] }): React.JSX.Element {
  const rows = columns.flatMap((column) => column.cards);
  if (rows.length === 0) {
    return (
      <p className="py-10 text-center text-sm text-slate-700">
        No opportunities you are cleared to read.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Opportunity</TableHead>
            <TableHead>Stage</TableHead>
            <TableHead>Owner</TableHead>
            <TableHead>Counterpart</TableHead>
            <TableHead className="text-right">Score</TableHead>
            <TableHead className="text-right">Value</TableHead>
            <TableHead>Next action</TableHead>
            <TableHead className="text-right">Evidence</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((card) => (
            <TableRow key={card.id}>
              <TableCell className="max-w-[22rem]">
                <span className="font-medium text-ink">{card.title}</span>
                {card.is_proposed_by_ai ? (
                  <Badge variant="proposed" className="ml-2 text-2xs font-medium">
                    AI-proposed
                  </Badge>
                ) : null}
                {/* Two discrete fields separated by space rather than by a middle dot: the
                    data zone the row sits in, and the sector it is filed under. The sector
                    code is the taxonomy's own identifier and stays verbatim. */}
                <span className="mt-0.5 flex flex-wrap items-center gap-x-3 text-label text-slate-700">
                  <span>{CLASSIFICATION_LABELS[card.classification]}</span>
                  <span>Sector {card.sector_code}</span>
                </span>
              </TableCell>
              <TableCell className="whitespace-nowrap text-sm">
                {OPPORTUNITY_STAGE_LABELS[card.stage]}
              </TableCell>
              <TableCell className="text-sm">{card.owner_name ?? '—'}</TableCell>
              <TableCell className="text-sm">
                {card.organisation_id !== null && card.organisation_name !== null ? (
                  <Link
                    href={`/stakeholders/organisations/${card.organisation_id}`}
                    className="text-accent underline underline-offset-2"
                  >
                    {card.counterpart_name ?? card.organisation_name}
                  </Link>
                ) : (
                  (card.counterpart_name ?? '—')
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {card.score === null ? '—' : Math.round(card.score)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {card.value_estimate_aud === null
                  ? '—'
                  : formatAud(card.value_estimate_aud)}
              </TableCell>
              <TableCell
                className={cn(
                  'whitespace-nowrap text-sm tabular-nums',
                  card.next_action_overdue && 'font-medium text-risk',
                )}
              >
                {formatDate(card.next_action_at)}
                {/* Overdue is stated, not only coloured (WCAG 1.4.1). */}
                {card.next_action_overdue ? (
                  <span className="block text-label font-medium text-risk">Overdue</span>
                ) : null}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {card.evidence_count}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
