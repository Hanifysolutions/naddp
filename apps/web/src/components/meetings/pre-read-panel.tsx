'use client';

import * as React from 'react';
import { ExternalLink, Sparkles } from 'lucide-react';
import type { BriefEvidence, DataClassification, PreRead } from '@naddp/contracts';

import { EvidenceList } from '@/components/intelligence/evidence-list';
import { TraceBadge } from '@/components/intelligence/trace-badge';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { CLASSIFICATION_LABELS } from '@/lib/enum-labels';

/**
 * The meeting pre-read: what to achieve, what to say and on whose authority, what to ask,
 * and what to avoid.
 *
 * **Read from the database, never generated here.** `pre_read` is the stored, validated
 * MEETING_PREP result with its trace id as provenance. There is no "generate" button when it
 * is null: the prep route would, for any meeting without its own snapshot, fall back to
 * another meeting's content - a fabrication on stage. So null says plainly that none was
 * prepared.
 *
 * **Every talking point shows its sources inline**, resolved by `citation_id` from the
 * pre-read's own `evidence`, which the API has already narrowed to VERIFIED registry entries.
 * An id that does not resolve is skipped rather than rendered as a dead label. The full
 * evidence list closes the panel, so each source's verbatim quote is one glance away.
 *
 * **Sensitivities are guidance, not state**, so they take a plain slate rule rather than the
 * warn tick - a warning colour here would make "do not conflate the concentrator and the
 * refinery" look like an alarm about the meeting.
 *
 * Confidence arrives on the AI schema's 0-1 scale (unlike the brief's 0-100) and is shown as a
 * percentage figure; null means not assessed, never zero.
 */
export function PreReadPanel({
  preRead,
  classification,
}: {
  preRead: PreRead | null;
  classification: DataClassification;
}): React.JSX.Element {
  const headingId = React.useId();

  if (preRead === null) {
    return (
      <Card role="region" aria-labelledby={headingId}>
        <CardHeader className="pb-2">
          <h2 id={headingId} className="text-base font-semibold text-ink">
            Pre-read
          </h2>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-slate-700">
            No pre-read has been prepared for this meeting.
          </p>
        </CardContent>
      </Card>
    );
  }

  const result = preRead.result;
  const trace = preRead.trace ?? null;
  const traceId = preRead.trace_id ?? null;
  const confidence = result.confidence ?? null;
  const percent =
    confidence === null ? null : Math.round(Math.max(0, Math.min(1, confidence)) * 100);
  const sources = new Map(preRead.evidence.map((entry) => [entry.citation_id, entry]));

  return (
    <Card role="region" aria-labelledby={headingId}>
      <CardHeader className="gap-3 space-y-0">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <h2 id={headingId} className="text-base font-semibold text-ink">
              Pre-read
            </h2>
            <span className="inline-flex items-center gap-1 text-label text-slate-700">
              <Sparkles aria-hidden="true" className="size-3.5 text-slate-400" />
              AI-generated
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="text-2xs font-medium">
              {CLASSIFICATION_LABELS[classification]}
            </Badge>
            <TraceBadge trace={trace} subject="pre-read" />
          </div>
        </div>

        {traceId !== null && trace === null ? (
          <p className="text-label text-slate-700">Routing decision withheld for this role.</p>
        ) : null}

        <dl className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div className="min-w-0 max-w-[48ch]">
            <dt className="text-label text-slate-700">Counterpart</dt>
            <dd className="mt-0.5 text-sm leading-snug text-ink">{result.counterpart}</dd>
          </div>
          <div>
            <dt className="text-label text-slate-700">Confidence</dt>
            <dd className="tabular mt-0.5 font-display text-xl font-semibold leading-none text-ink">
              {percent === null ? (
                <>
                  <span aria-hidden="true" className="text-slate-700">
                    &mdash;
                  </span>
                  <span className="sr-only">not assessed</span>
                </>
              ) : (
                `${percent}%`
              )}
            </dd>
          </div>
        </dl>
      </CardHeader>

      <CardContent className="space-y-6">
        <PreReadSection title="Objectives">
          <PlainList items={result.objectives} empty="No objectives recorded." />
        </PreReadSection>

        <PreReadSection title="Talking points">
          {result.talking_points.length === 0 ? (
            <p className="text-sm text-slate-700">No talking points recorded.</p>
          ) : (
            <ul className="space-y-4">
              {result.talking_points.map((talkingPoint, index) => (
                <li key={`${talkingPoint.point}#${index}`} className="min-w-0">
                  <p className="max-w-[72ch] text-sm font-medium leading-snug text-ink">
                    {talkingPoint.point}
                  </p>
                  <p className="mt-1 max-w-[72ch] text-sm leading-relaxed text-ink">
                    {talkingPoint.detail}
                  </p>
                  <InlineSources ids={talkingPoint.citation_ids} sources={sources} />
                </li>
              ))}
            </ul>
          )}
        </PreReadSection>

        <PreReadSection title="Questions to ask">
          <PlainList items={result.questions_to_ask} empty="No questions recorded." />
        </PreReadSection>

        <div className="border-l-2 border-slate-400 py-0.5 pl-3">
          <h3 className="text-sm font-semibold text-ink">Sensitivities</h3>
          <div className="mt-1.5">
            <PlainList items={result.sensitivities} empty="No sensitivities recorded." />
          </div>
        </div>

        <div className="border-t border-line pt-4">
          <EvidenceList evidence={preRead.evidence} />
        </div>
      </CardContent>
    </Card>
  );
}

function PreReadSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="min-w-0">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

function PlainList({
  items,
  empty,
}: {
  items: readonly string[];
  empty: string;
}): React.JSX.Element {
  if (items.length === 0) return <p className="text-sm text-slate-700">{empty}</p>;
  return (
    <ul className="max-w-[72ch] list-disc space-y-1 pl-5 text-sm leading-relaxed text-ink marker:text-slate-400">
      {items.map((item, index) => (
        <li key={`${item}#${index}`}>{item}</li>
      ))}
    </ul>
  );
}

/** A talking point's own sources, as outbound links, using the app's external-link treatment. */
function InlineSources({
  ids,
  sources,
}: {
  ids: readonly string[];
  sources: ReadonlyMap<string, BriefEvidence>;
}): React.JSX.Element | null {
  const resolved = ids
    .map((id) => sources.get(id))
    .filter((entry): entry is BriefEvidence => entry !== undefined);
  if (resolved.length === 0) return null;

  return (
    <div className="mt-1.5 flex max-w-[72ch] flex-wrap items-baseline gap-x-2 gap-y-1">
      <span className="text-label text-slate-700">Sources</span>
      <ul className="flex min-w-0 flex-wrap gap-x-3 gap-y-1">
        {resolved.map((source, index) => {
          const url = source.url ?? null;
          const label = source.title ?? source.publisher ?? source.citation_id;
          return (
            <li key={`${source.citation_id}#${index}`} className="min-w-0 text-label">
              {url === null ? (
                <span className="text-ink">{label}</span>
              ) : (
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-start gap-1 text-accent underline underline-offset-2"
                >
                  {label}
                  <ExternalLink aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
