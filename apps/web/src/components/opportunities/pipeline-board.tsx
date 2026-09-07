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
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';
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
 */

type ViewMode = 'board' | 'table';

/** Where a stage sits on the "is this progressing" scale, for the column accent only. */
const COLUMN_ACCENT: Readonly<Record<string, string>> = {
  DETECTED: 'border-t-muted-foreground/40',
  QUALIFIED: 'border-t-info/60',
  CONTACT_PLANNED: 'border-t-info/60',
  CONTACTED: 'border-t-info/60',
  MEETING: 'border-t-warning/60',
  NEGOTIATION: 'border-t-warning/60',
  PARTNERED: 'border-t-success/70',
  CLOSED: 'border-t-muted-foreground/30',
};

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
  const [pending, setPending] = React.useState<{ card: BoardCard; event: string } | null>(null);
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
          {forbidden ? 'This role may not read the pipeline' : 'The pipeline could not be loaded'}
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
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Opportunity pipeline</h1>
          <p className="text-sm text-muted-foreground">
            {data.total} opportunit{data.total === 1 ? 'y' : 'ies'} you are cleared to read,{' '}
            {data.open_total} open, {formatAud(data.pipeline_value_aud)} estimated (
            {formatAud(data.weighted_pipeline_value_aud)} probability-weighted).
            {data.overdue_next_action > 0
              ? ` ${data.overdue_next_action} overdue next action${
                  data.overdue_next_action === 1 ? '' : 's'
                }.`
              : ''}
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-md border border-border p-1">
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
        <BoardView columns={data.columns} onFire={(card, event) => setPending({ card, event })} />
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
        {columns.map((column) => (
          <section
            key={column.stage}
            aria-label={`${column.label}, ${column.count} opportunities`}
            className={cn(
              'w-72 shrink-0 rounded-md border border-t-4 border-border bg-muted/30 p-2',
              COLUMN_ACCENT[column.stage] ?? 'border-t-border',
            )}
          >
            <header className="flex items-baseline justify-between px-1 pb-2">
              <h2 className="text-sm font-semibold">
                {column.label}
                {column.is_terminal ? (
                  <span className="ml-1 text-xs font-normal text-muted-foreground">terminal</span>
                ) : null}
              </h2>
              <span className="text-xs tabular-nums text-muted-foreground">
                {column.count}
                {column.value_estimate_aud > 0 ? ` · ${formatAud(column.value_estimate_aud)}` : ''}
              </span>
            </header>
            <div className="space-y-2">
              {column.cards.length === 0 ? (
                <p className="px-1 py-6 text-center text-xs text-muted-foreground">
                  Nothing at this stage.
                </p>
              ) : (
                column.cards.map((card) => (
                  <OpportunityCard key={card.id} card={card} onFire={onFire} />
                ))
              )}
            </div>
          </section>
        ))}
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
    <Card className="gap-0 py-3">
      <CardHeader className="px-3 pb-2">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-medium leading-snug">{card.title}</h3>
          {card.score === null ? null : (
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs font-semibold tabular-nums">
              {Math.round(card.score)}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1 pt-1">
          <Badge variant="outline" className="text-[10px]">
            {CLASSIFICATION_LABELS[card.classification]}
          </Badge>
          {card.is_proposed_by_ai ? (
            // Q-17: the hero opportunity is the platform's own proposition, not a reported
            // fact. It has to read that way everywhere it appears, not only on its detail.
            <Badge variant="warning" className="text-[10px]">
              <Sparkles aria-hidden="true" className="size-3" /> AI-proposed
            </Badge>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-1.5 px-3 text-xs text-muted-foreground">
        <p className="flex items-center gap-1.5">
          <User aria-hidden="true" className="size-3.5 shrink-0" />
          <span className="truncate">{card.owner_name ?? 'Unowned'}</span>
          {card.counterpart_name === null ? null : (
            <span className="truncate">· {card.counterpart_name}</span>
          )}
        </p>
        {card.organisation_id === null || card.organisation_name === null ? null : (
          <p className="truncate">
            <Link
              href={`/stakeholders/organisations/${card.organisation_id}`}
              className="underline underline-offset-2 hover:text-foreground"
            >
              {card.organisation_name}
            </Link>
          </p>
        )}
        <p
          className={cn(
            'flex items-center gap-1.5',
            card.next_action_overdue && 'font-medium text-destructive',
          )}
        >
          <CalendarClock aria-hidden="true" className="size-3.5 shrink-0" />
          {card.next_action_at === null
            ? 'No next action scheduled'
            : `Next action ${formatDate(card.next_action_at)}`}
          {card.next_action_overdue ? ' · overdue' : ''}
        </p>
        <p className="flex items-center gap-1.5">
          <FileText aria-hidden="true" className="size-3.5 shrink-0" />
          {card.evidence_count} citation{card.evidence_count === 1 ? '' : 's'}
          {card.value_estimate_aud === null
            ? ''
            : ` · ${formatAud(card.value_estimate_aud)}${
                card.probability === null ? '' : ` at ${Math.round(card.probability)}%`
              }`}
        </p>
      </CardContent>
      {card.available_events.length === 0 && commitmentGate === undefined ? null : (
        <div className="flex flex-wrap gap-1 px-3 pt-2">
          {card.available_events.map((event) => (
            <Button key={event} size="sm" variant="outline" onClick={() => onFire(card, event)}>
              {EVENT_LABELS[event] ?? event}
            </Button>
          ))}
          {commitmentGate === undefined ? null : (
            // Shown, disabled, with the reason - never hidden. BUILD_BIBLE section 6
            // requires the demo to SHOW a never-autonomous control refusing, and an
            // absent button is indistinguishable from an absent feature.
            <Button size="sm" variant="outline" disabled title={commitmentGate.reason}>
              <Lock aria-hidden="true" className="size-3.5" /> {commitmentGate.label}
            </Button>
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
      <p className="py-10 text-center text-sm text-muted-foreground">
        No opportunities you are cleared to read.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-md border border-border">
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
                <span className="font-medium">{card.title}</span>
                {card.is_proposed_by_ai ? (
                  <Badge variant="warning" className="ml-2 text-[10px]">
                    AI-proposed
                  </Badge>
                ) : null}
                <span className="block text-xs text-muted-foreground">
                  {CLASSIFICATION_LABELS[card.classification]} · {card.sector_code}
                </span>
              </TableCell>
              <TableCell className="whitespace-nowrap text-sm">{card.stage}</TableCell>
              <TableCell className="text-sm">{card.owner_name ?? '—'}</TableCell>
              <TableCell className="text-sm">
                {card.organisation_id !== null && card.organisation_name !== null ? (
                  <Link
                    href={`/stakeholders/organisations/${card.organisation_id}`}
                    className="underline underline-offset-2"
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
                {card.value_estimate_aud === null ? '—' : formatAud(card.value_estimate_aud)}
              </TableCell>
              <TableCell
                className={cn(
                  'whitespace-nowrap text-sm',
                  card.next_action_overdue && 'font-medium text-destructive',
                )}
              >
                {formatDate(card.next_action_at)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{card.evidence_count}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
