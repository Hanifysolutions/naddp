'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { isApiError, type KnowledgeArticle } from '@naddp/contracts';

import { useDemoSession } from '@/components/layout/session-provider';
import { formatDate } from '@/components/meetings/meeting-format';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchKnowledgeArticle } from '@/lib/api-queries';
import {
  CLASSIFICATION_LABELS,
  KNOWLEDGE_AUDIENCE_LABELS,
  KNOWLEDGE_STATUS_LABELS,
} from '@/lib/enum-labels';
import { queryKeys } from '@/lib/query-keys';
import { cn } from '@/lib/utils';

/**
 * The approved article a citation resolves to: its text, who approved it, and whether it is in
 * force.
 *
 * This is what makes a knowledge answer checkable rather than asserted. The quotation on the
 * answer can be read in its full context here, beside the named human who approved the article,
 * its version and its validity, and the public page it restates. An expired or retired article
 * still opens - an answer given while it was current keeps a working citation - and says plainly
 * that it no longer grounds new answers.
 */
export function ArticleSheet({
  slug,
  onClose,
}: {
  slug: string | null;
  onClose: () => void;
}): React.JSX.Element {
  const session = useDemoSession();
  const article = useQuery({
    queryKey: queryKeys.knowledgeArticle(session.role, slug ?? ''),
    queryFn: ({ signal }) => fetchKnowledgeArticle(slug ?? '', signal),
    enabled: slug !== null && session.role !== null,
    retry: false,
    staleTime: 60_000,
  });

  return (
    <Sheet open={slug !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent side="right" className="w-full overflow-y-auto shadow-none sm:max-w-lg">
        {article.isPending ? (
          <>
            <SheetHeader>
              <SheetTitle>Knowledge article</SheetTitle>
              <SheetDescription>Loading the approved article.</SheetDescription>
            </SheetHeader>
            <div className="mt-5 space-y-3" aria-busy="true">
              <Skeleton className="h-6 w-3/4" />
              <Skeleton className="h-40 w-full" />
            </div>
          </>
        ) : article.isError ? (
          <SheetHeader>
            <SheetTitle>Article not available</SheetTitle>
            <SheetDescription>
              {isApiError(article.error) ? article.error.message : 'The article could not be loaded.'}
            </SheetDescription>
          </SheetHeader>
        ) : (
          <ArticleBody article={article.data} />
        )}
      </SheetContent>
    </Sheet>
  );
}

function standing(article: KnowledgeArticle): string {
  if (article.in_force) return 'Approved and in force';
  if (article.status === 'RETIRED') return 'Retired: kept so earlier citations still resolve';
  return `${KNOWLEDGE_STATUS_LABELS[article.status]}, not in force`;
}

function categoryLabel(category: string): string {
  const spaced = category.replaceAll('-', ' ').replaceAll('_', ' ').toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function ArticleBody({ article }: { article: KnowledgeArticle }): React.JSX.Element {
  return (
    <>
      <SheetHeader>
        <SheetTitle className="leading-snug">{article.title}</SheetTitle>
        <SheetDescription className="text-slate-700">{article.summary}</SheetDescription>
      </SheetHeader>

      <p
        className={cn(
          'mt-4 inline-flex rounded border px-2 py-0.5 text-label font-medium',
          article.in_force ? 'border-ok/60 bg-ok/5 text-ok' : 'border-warn bg-warn/5 text-warn-ink',
        )}
      >
        {standing(article)}
      </p>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5 rounded-md border border-line p-3">
        <Fact label="Version">{article.version}</Fact>
        <Fact label="Approved by">{article.approved_by_name ?? 'Not recorded'}</Fact>
        <Fact label="Approved">
          {article.approved_at === null ? 'Not recorded' : formatDate(article.approved_at)}
        </Fact>
        <Fact label="Owner">{article.owner_name ?? 'No current owner'}</Fact>
        <Fact label="Written for">{KNOWLEDGE_AUDIENCE_LABELS[article.audience]}</Fact>
        <Fact label="Valid until">
          {article.valid_until === null ? 'No expiry set' : formatDate(article.valid_until)}
        </Fact>
        <Fact label="Zone">{CLASSIFICATION_LABELS[article.classification]}</Fact>
        <Fact label="Category">{categoryLabel(article.category)}</Fact>
      </dl>

      <div className="mt-4 max-w-[72ch] whitespace-pre-line text-sm leading-relaxed text-ink">
        {article.body}
      </div>

      {article.source === null ? (
        <p className="mt-4 text-label leading-snug text-slate-700">
          Mission-authored guidance with no external source. It cannot ground an answer, because
          an answer must cite a page a reader can open.
        </p>
      ) : (
        <div className="mt-4 text-sm">
          <p className="text-label text-slate-700">Restates</p>
          <a
            href={article.source.url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-start gap-1 font-medium text-accent underline underline-offset-2"
          >
            {article.source.title}
            <ExternalLink aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
          <p className="text-label text-slate-700">{article.source.publisher}</p>
        </div>
      )}
    </>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <div className="min-w-0">
      <dt className="text-label text-slate-700">{label}</dt>
      <dd className="tabular text-sm text-ink">{children}</dd>
    </div>
  );
}
