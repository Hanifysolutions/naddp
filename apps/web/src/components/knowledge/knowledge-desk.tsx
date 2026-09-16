'use client';

import * as React from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { AlertTriangle, Ban, BookOpen, Lock } from 'lucide-react';
import { isApiError, type Jurisdiction, type KnowledgeOverview } from '@naddp/contracts';

import { ArticleSheet } from '@/components/knowledge/article-sheet';
import {
  KnowledgeExchange,
  type Exchange,
} from '@/components/knowledge/knowledge-answer';
import { useDemoSession } from '@/components/layout/session-provider';
import { PRIMARY_ACTION } from '@/components/meetings/action-styles';
import { formatDate } from '@/components/meetings/meeting-format';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { askKnowledge, fetchKnowledgeOverview } from '@/lib/api-queries';
import {
  JURISDICTION_LABELS,
  JURISDICTION_ORDER,
  KNOWLEDGE_AUDIENCE_LABELS,
} from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * The Knowledge desk: ask the approved knowledge base, and see exactly what it may answer from.
 *
 * **Grounded-or-refuse is the whole screen.** The right column lists every article an answer for
 * this role could come from - approved, in force, written for this role - so the boundary of what
 * the answerer knows is on the page rather than implied. The left column asks and answers. An
 * answer quotes an article from that list; a refusal says that none of them supports the
 * question and who to take it to. Neither is rendered as the other, and a refusal is never styled
 * as an error.
 *
 * The answers asked in this sitting are kept newest first, so a grounded answer and a refusal can
 * be read one above the other. They belong to the identity that asked them: the body is keyed by
 * role, so switching demo identity starts an empty desk rather than showing another role's answers.
 */

const QUESTION_MIN = 3;
const QUESTION_MAX = 1000;

function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  if (isApiError(error) && (error.status === 403 || error.status === 404)) return false;
  return failureCount < 2;
}

export function KnowledgeDeskSkeleton(): React.JSX.Element {
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
        Loading the knowledge base
      </span>
    </div>
  );
}

export function KnowledgeDesk(): React.JSX.Element {
  const session = useDemoSession();
  const role = session.role;

  const overview = useQuery({
    queryKey: queryKeys.knowledgeOverview(role),
    queryFn: ({ signal }) => fetchKnowledgeOverview(signal),
    // Gated on identity only: a role without read:knowledge_article is refused by the API, on
    // the record, rather than by this page.
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
          Choose a role to ask the knowledge base. Nothing is answered without one.
        </AlertDescription>
      </Alert>
    );
  }

  if (overview.isPending) return <KnowledgeDeskSkeleton />;

  if (overview.isError) {
    const apiError = isApiError(overview.error) ? overview.error : null;
    const forbidden = apiError !== null && apiError.status === 403;
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-ink">Knowledge</h1>
        <Alert variant={forbidden ? 'warning' : 'destructive'} role="status">
          {forbidden ? <Ban aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <AlertTitle
            className={forbidden ? 'leading-snug text-warn-ink' : 'leading-snug'}
          >
            {forbidden
              ? 'This role may not read the knowledge base'
              : 'The knowledge base could not be loaded'}
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
                the audit log.
              </p>
            ) : null}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return <DeskBody key={role} overview={overview.data} />;
}

function DeskBody({ overview }: { overview: KnowledgeOverview }): React.JSX.Element {
  const [question, setQuestion] = React.useState('');
  const [jurisdiction, setJurisdiction] = React.useState<Jurisdiction | ''>('');
  const [exchange, setExchange] = React.useState<Exchange | null>(null);
  const [openSlug, setOpenSlug] = React.useState<string | null>(null);
  const nextId = React.useRef(1);
  const ask = useMutation({ mutationFn: askKnowledge });

  const fieldId = React.useId();
  const countId = React.useId();
  const jurisdictionId = React.useId();
  const suggestionsId = React.useId();

  const busy = exchange?.status === 'pending';
  const articleCount = overview.articles.length;

  function submit(text: string): void {
    const asked = text.trim();
    if (asked.length < QUESTION_MIN || busy) return;
    const id = nextId.current;
    nextId.current += 1;
    const chosen = jurisdiction === '' ? null : jurisdiction;
    // The desk shows this question and its answer. A new ask replaces the last rather
    // than stacking under it: two answers on screen invite reading the wrong one, and the
    // refusal is only unmistakable when it is the only thing there.
    setExchange({
      id,
      question: asked,
      jurisdiction: chosen,
      status: 'pending',
      envelope: null,
      error: null,
    });
    ask.mutate(
      { question: asked, jurisdictions: chosen === null ? [] : [chosen] },
      {
        onSuccess: (envelope) =>
          setExchange((current) =>
            current?.id === id ? { ...current, status: 'done', envelope } : current,
          ),
        onError: (failure) =>
          setExchange((current) =>
            current?.id === id
              ? {
                  ...current,
                  status: 'failed',
                  error: isApiError(failure)
                    ? failure.message
                    : failure instanceof Error
                      ? failure.message
                      : 'The API did not answer.',
                }
              : current,
          ),
      },
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-ink">Knowledge</h1>
        <p className="tabular max-w-[80ch] text-sm leading-relaxed text-slate-700">
          Answers come only from the {articleCount} approved, in-date{' '}
          {articleCount === 1 ? 'article' : 'articles'} written for your role, quoted and
          cited. When none of them supports a question, the answer is a refusal that says
          so and names who to ask - never an improvised answer.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-6 laptop:grid-cols-[minmax(0,1fr)_22rem] laptop:items-start">
        <div className="min-w-0 space-y-5">
          <form
            className="space-y-3 rounded-lg border border-line bg-card p-4"
            onSubmit={(event) => {
              event.preventDefault();
              submit(question);
            }}
          >
            <label htmlFor={fieldId} className="text-sm font-medium text-ink">
              Ask the approved knowledge base
            </label>
            <textarea
              id={fieldId}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  submit(question);
                }
              }}
              rows={3}
              maxLength={QUESTION_MAX}
              aria-describedby={countId}
              placeholder="For example: what evidence is needed for an emergency travel document?"
              className="w-full rounded-md border border-input bg-background p-2 text-sm"
            />
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="space-y-1">
                <label
                  htmlFor={jurisdictionId}
                  className="block text-label text-slate-700"
                >
                  Jurisdiction of the source
                </label>
                <select
                  id={jurisdictionId}
                  value={jurisdiction}
                  onChange={(event) =>
                    setJurisdiction(event.target.value as Jurisdiction | '')
                  }
                  className="block rounded-md border border-input bg-background p-1.5 text-sm"
                >
                  <option value="">Any jurisdiction</option>
                  {JURISDICTION_ORDER.map((code) => (
                    <option key={code} value={code}>
                      {JURISDICTION_LABELS[code]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-center gap-3">
                <span id={countId} className="tabular text-label text-slate-700">
                  {question.length} of {QUESTION_MAX} characters
                </span>
                <Button
                  type="submit"
                  disabled={question.trim().length < QUESTION_MIN || busy}
                  className={PRIMARY_ACTION}
                >
                  {busy ? 'Checking…' : 'Ask'}
                </Button>
              </div>
            </div>
          </form>

          {overview.suggested_questions.length === 0 ? null : (
            <section aria-labelledby={suggestionsId} className="space-y-2">
              <h2 id={suggestionsId} className="text-label font-medium text-slate-700">
                Try asking
              </h2>
              <ul className="flex flex-wrap gap-2">
                {overview.suggested_questions.map((suggestion) => (
                  <li key={suggestion.id}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setQuestion(suggestion.question);
                        submit(suggestion.question);
                      }}
                      className="rounded-md border border-line bg-card px-2.5 py-1.5 text-left text-label text-ink hover:border-slate-400 disabled:opacity-60"
                    >
                      {suggestion.question}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section aria-label="Answers" aria-live="polite" className="space-y-4">
            {exchange === null ? (
              <p className="rounded-lg border border-dashed border-input px-4 py-6 text-center text-sm text-slate-700">
                Ask a question, or try one above. An answer shows the approved article it
                quotes; a refusal says why none applies.
              </p>
            ) : (
              <KnowledgeExchange exchange={exchange} onOpenArticle={setOpenSlug} />
            )}
          </section>
        </div>

        <aside className="min-w-0 laptop:sticky laptop:top-20">
          <CorpusCard overview={overview} onOpen={setOpenSlug} />
        </aside>
      </div>

      <ArticleSheet slug={openSlug} onClose={() => setOpenSlug(null)} />
    </div>
  );
}

function CorpusCard({
  overview,
  onOpen,
}: {
  overview: KnowledgeOverview;
  onOpen: (slug: string) => void;
}): React.JSX.Element {
  const headingId = React.useId();
  const { articles, audiences, support_threshold: threshold } = overview;
  const audienceNames = audiences
    .map((audience) => KNOWLEDGE_AUDIENCE_LABELS[audience])
    .join(', ');

  return (
    <Card aria-labelledby={headingId}>
      <CardHeader className="gap-1 pb-2">
        <h2
          id={headingId}
          className="flex items-center gap-2 text-base font-semibold text-ink"
        >
          <BookOpen aria-hidden="true" className="h-4 w-4 text-slate-700" />
          What answers can come from
        </h2>
        <p className="text-label leading-snug text-slate-700">
          {articles.length} approved, in-date{' '}
          {articles.length === 1 ? 'article' : 'articles'} written for {audienceNames}. An
          article grounds an answer only if it holds at least{' '}
          {Math.round(threshold.min_weighted_coverage * 100)}% of a question&apos;s key
          terms, weighted by rarity, and at least {threshold.min_matched_terms} of them.
        </p>
      </CardHeader>
      <CardContent>
        {articles.length === 0 ? (
          <p className="text-sm text-slate-700">
            No approved article is written for your role, so every question will be
            refused.
          </p>
        ) : (
          <ul className="divide-y divide-line">
            {articles.map((article) => (
              <li key={article.slug} className="py-2.5 first:pt-0 last:pb-0">
                <button
                  type="button"
                  onClick={() => onOpen(article.slug)}
                  className="text-left text-sm font-medium leading-snug text-accent underline underline-offset-2"
                >
                  {article.title}
                </button>
                <p className="mt-0.5 flex flex-wrap gap-x-3 text-label text-slate-700">
                  <span>{KNOWLEDGE_AUDIENCE_LABELS[article.audience]}</span>
                  <span className="tabular">Version {article.version}</span>
                  <span>
                    {article.approved_by_name === null
                      ? 'Approver not recorded'
                      : `Approved by ${article.approved_by_name}`}
                  </span>
                  {article.valid_until === null ? null : (
                    <span className="tabular">
                      Until {formatDate(article.valid_until)}
                    </span>
                  )}
                  {article.citation_id === null ? (
                    <span>No cited source: cannot ground</span>
                  ) : null}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
