'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  BookCheck,
  ExternalLink,
  LoaderCircle,
  SearchX,
  UserRound,
} from 'lucide-react';
import type { AiEnvelope, AiTrace, EvidenceRef, Jurisdiction } from '@naddp/contracts';

import { TraceBadge } from '@/components/intelligence/trace-badge';
import { useDemoSession } from '@/components/layout/session-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { asBriefTrace } from '@/lib/ai-trace';
import { fetchAiTrace } from '@/lib/api-queries';
import { JURISDICTION_LABELS } from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';

/**
 * One question and what the approved knowledge base said about it: an answer, or a refusal.
 *
 * **The two outcomes must never look alike.** A grounded answer carries the `--ok` tick, quotes
 * the approved article's own sentences as quotations, names the article and its version, and
 * lists the published source behind it. A refusal carries the `--warn` tick - not `--risk`: it
 * is the answerer working exactly as designed, not an alarm - quotes nothing, cites nothing, says
 * why in the server's words, and names the officer to take the question to. Nothing is rendered
 * that the API did not return, and a result that does not parse renders as "no answer was
 * given" rather than as a half-filled answer an officer might repeat.
 *
 * Both outcomes carry the routing record: the section 4a badge as the Gateway wrote it, and a
 * sentence derived from the trace's own fields saying whether any model was asked.
 */

export interface Exchange {
  readonly id: number;
  readonly question: string;
  readonly jurisdiction: Jurisdiction | null;
  readonly status: 'pending' | 'done' | 'failed';
  readonly envelope: AiEnvelope<unknown> | null;
  readonly error: string | null;
}

interface QuotedPassage {
  readonly slug: string;
  readonly title: string;
  readonly version: number;
  readonly text: string;
}

type KnowledgeResult =
  | {
      readonly kind: 'answered';
      readonly answer: string;
      readonly passages: readonly QuotedPassage[];
      readonly caveats: readonly string[];
    }
  | {
      readonly kind: 'refused';
      readonly answer: string;
      readonly reason: string;
      readonly guidance: string;
      readonly referToName: string;
      readonly referToTitle: string;
    };

function readPassage(value: unknown): QuotedPassage | null {
  if (typeof value !== 'object' || value === null) return null;
  const record = value as Record<string, unknown>;
  const slug = record['article_slug'];
  const title = record['article_title'];
  const version = record['article_version'];
  const text = record['text'];
  if (
    typeof slug !== 'string' ||
    typeof title !== 'string' ||
    typeof version !== 'number' ||
    typeof text !== 'string'
  ) {
    return null;
  }
  return { slug, title, version, text };
}

/** Narrow the envelope's open result to an answer or a refusal, or refuse to render it. */
function readKnowledgeResult(result: unknown): KnowledgeResult | null {
  if (typeof result !== 'object' || result === null) return null;
  const record = result as Record<string, unknown>;
  const answer = record['answer'];
  if (typeof answer !== 'string') return null;

  if (record['answered_from_approved_sources'] === true) {
    const rawPassages = record['passages'];
    const rawCaveats = record['caveats'];
    const passages = Array.isArray(rawPassages) ? rawPassages.map(readPassage) : [];
    if (passages.some((passage) => passage === null)) return null;
    const caveats = Array.isArray(rawCaveats)
      ? rawCaveats.filter((caveat): caveat is string => typeof caveat === 'string')
      : [];
    return {
      kind: 'answered',
      answer,
      passages: passages.filter((passage): passage is QuotedPassage => passage !== null),
      caveats,
    };
  }

  if (record['answered_from_approved_sources'] === false) {
    const refusal = record['refusal'];
    if (typeof refusal !== 'object' || refusal === null) return null;
    const detail = refusal as Record<string, unknown>;
    const reason = detail['reason'];
    const guidance = detail['guidance'];
    const referToName = detail['refer_to_name'];
    const referToTitle = detail['refer_to_title'];
    if (
      typeof reason !== 'string' ||
      typeof guidance !== 'string' ||
      typeof referToName !== 'string' ||
      typeof referToTitle !== 'string'
    ) {
      return null;
    }
    return { kind: 'refused', answer, reason, guidance, referToName, referToTitle };
  }

  return null;
}

export function KnowledgeExchange({
  exchange,
  onOpenArticle,
}: {
  exchange: Exchange;
  onOpenArticle: (slug: string) => void;
}): React.JSX.Element {
  if (exchange.status === 'pending') {
    return (
      <div className="rounded-lg border border-line bg-card p-4" aria-busy="true">
        <QuestionLine exchange={exchange} />
        <p className="mt-2 flex items-center gap-1.5 text-label text-slate-700" role="status">
          <LoaderCircle aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
          Checking the approved knowledge base…
        </p>
        <Skeleton className="mt-3 h-16 w-full" />
      </div>
    );
  }

  const envelope = exchange.envelope;
  if (exchange.status === 'failed' || envelope === null) {
    return (
      <Alert variant="destructive" role="status">
        <AlertTriangle aria-hidden="true" />
        <AlertTitle className="leading-snug">The question could not be asked</AlertTitle>
        <AlertDescription className="max-w-[72ch] space-y-1">
          <p className="font-medium">{exchange.question}</p>
          <p>
            {exchange.error ?? 'The API did not answer.'} The knowledge base was not reached, so
            nothing was answered and nothing was refused.
          </p>
        </AlertDescription>
      </Alert>
    );
  }

  const parsed =
    envelope.approval_status === 'BLOCKED' ? null : readKnowledgeResult(envelope.result);
  if (parsed === null) {
    return (
      <Alert variant="warning" role="status">
        <AlertTriangle aria-hidden="true" />
        <AlertTitle className="leading-snug text-warn-ink">No answer was given</AlertTitle>
        <AlertDescription className="max-w-[72ch] space-y-1">
          <p className="font-medium">{exchange.question}</p>
          <p>
            {envelope.explanation ??
              'The result could not be read, so nothing is shown rather than part of an answer.'}
          </p>
        </AlertDescription>
      </Alert>
    );
  }

  return parsed.kind === 'answered' ? (
    <GroundedAnswer
      exchange={exchange}
      envelope={envelope}
      result={parsed}
      onOpenArticle={onOpenArticle}
    />
  ) : (
    <NoApprovedSource exchange={exchange} envelope={envelope} result={parsed} />
  );
}

function QuestionLine({ exchange }: { exchange: Exchange }): React.JSX.Element {
  return (
    <div className="space-y-0.5">
      <p className="max-w-[72ch] text-base font-semibold leading-snug text-ink">
        {exchange.question}
      </p>
      {exchange.jurisdiction === null ? null : (
        <p className="text-label text-slate-700">
          Grounded only in {JURISDICTION_LABELS[exchange.jurisdiction]} sources and mission-authored
          guidance
        </p>
      )}
    </div>
  );
}

function GroundedAnswer({
  exchange,
  envelope,
  result,
  onOpenArticle,
}: {
  exchange: Exchange;
  envelope: AiEnvelope<unknown>;
  result: Extract<KnowledgeResult, { kind: 'answered' }>;
  onOpenArticle: (slug: string) => void;
}): React.JSX.Element {
  return (
    <article
      aria-label="Answer from approved guidance"
      className="overflow-hidden rounded-lg border border-line bg-card"
    >
      <div className="tick-ok space-y-4 p-4">
        <header className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-label font-medium text-ok">
            <BookCheck aria-hidden="true" className="h-4 w-4" />
            Answered from approved guidance
          </p>
          <QuestionLine exchange={exchange} />
        </header>

        {result.passages.length === 0 ? (
          <p className="max-w-[72ch] text-sm leading-relaxed text-ink">{result.answer}</p>
        ) : (
          result.passages.map((passage) => (
            <figure key={`${passage.slug}#${passage.version}`} className="space-y-1.5">
              <blockquote className="max-w-[72ch] border-l-2 border-slate-400/60 pl-3 text-sm leading-relaxed text-ink">
                {passage.text}
              </blockquote>
              <figcaption className="text-label text-slate-700">
                Quoted verbatim from{' '}
                <button
                  type="button"
                  onClick={() => onOpenArticle(passage.slug)}
                  className="font-medium text-accent underline underline-offset-2"
                >
                  {passage.title}
                </button>
                , version {passage.version}
              </figcaption>
            </figure>
          ))
        )}

        <SourceList evidence={envelope.evidence ?? []} />

        {result.caveats.length === 0 ? null : (
          <ul className="space-y-1 text-label text-slate-700">
            {result.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        )}

        <RoutingLine traceId={envelope.trace_id} kind="answered" />
      </div>
    </article>
  );
}

function NoApprovedSource({
  exchange,
  envelope,
  result,
}: {
  exchange: Exchange;
  envelope: AiEnvelope<unknown>;
  result: Extract<KnowledgeResult, { kind: 'refused' }>;
}): React.JSX.Element {
  return (
    <article
      aria-label="No approved source covers this question"
      className="overflow-hidden rounded-lg border border-line bg-card"
    >
      <div className="tick-warn space-y-3 p-4">
        <header className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-label font-medium text-warn-ink">
            <SearchX aria-hidden="true" className="h-4 w-4" />
            No approved source covers this question
          </p>
          <QuestionLine exchange={exchange} />
        </header>

        <p className="max-w-[72ch] text-sm leading-relaxed text-ink">{result.answer}</p>

        <dl className="space-y-2.5 rounded-md border border-line p-3">
          <div>
            <dt className="text-label text-slate-700">Why</dt>
            <dd className="max-w-[72ch] text-sm leading-snug text-ink">{result.reason}</dd>
          </div>
          <div>
            <dt className="text-label text-slate-700">What to do</dt>
            <dd className="max-w-[72ch] text-sm leading-snug text-ink">{result.guidance}</dd>
          </div>
          <div>
            <dt className="text-label text-slate-700">Take it to</dt>
            <dd className="flex items-center gap-1.5 text-sm font-medium text-ink">
              <UserRound aria-hidden="true" className="h-4 w-4 text-slate-700" />
              {result.referToName}, {result.referToTitle}
            </dd>
          </div>
        </dl>

        <p className="max-w-[72ch] text-label leading-snug text-slate-700">
          Nothing was generated and nothing was cited. Declining is the answerer working as
          designed: an answer the mission cannot stand behind is not given.
        </p>

        <RoutingLine traceId={envelope.trace_id} kind="refused" />
      </div>
    </article>
  );
}

function SourceList({ evidence }: { evidence: readonly EvidenceRef[] }): React.JSX.Element | null {
  if (evidence.length === 0) return null;
  return (
    <section aria-label="Published sources behind the articles" className="min-w-0">
      <p className="text-label text-slate-700">
        Published {evidence.length === 1 ? 'source' : 'sources'} the{' '}
        {evidence.length === 1 ? 'article restates' : 'articles restate'}
      </p>
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

/** Derived from the trace's own fields; says nothing the trace does not. */
function routingSentence(trace: AiTrace, kind: 'answered' | 'refused'): string {
  if (trace.model_used !== null) return `Written by ${trace.model_used} from the approved text.`;
  if (kind === 'refused') {
    return 'No model was asked: nothing approved supports the question, so there was nothing to answer from.';
  }
  return trace.fallback
    ? 'No model answered: the approved text is quoted directly.'
    : 'No model answered this question.';
}

function RoutingLine({
  traceId,
  kind,
}: {
  traceId: string;
  kind: 'answered' | 'refused';
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
          <TraceBadge
            trace={asBriefTrace(trace.data)}
            subject={kind === 'answered' ? 'answer' : 'refusal'}
          />
          <span className="text-label text-slate-700">{routingSentence(trace.data, kind)}</span>
        </>
      )}
    </div>
  );
}
