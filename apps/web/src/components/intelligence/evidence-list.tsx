'use client';

import * as React from 'react';
import { ExternalLink } from 'lucide-react';
import type { BriefEvidence } from '@naddp/contracts';

/**
 * The sources a brief item rests on.
 *
 * This is the component that makes winning moment #1 - "not a chatbot" - checkable rather
 * than asserted. Every URL here is a real public page from `data/demo-seed/citations.json`
 * (CLAUDE.md section 2.6); none is synthesised, and the demo survives an Ambassador
 * clicking one.
 *
 * Three rules, each guarding a way a citation list can quietly lie:
 *
 *  - **A null `url` is rendered as plain text**, never as an anchor with an empty or `#`
 *    href. A link that looks openable and does nothing is worse than no link: it invites a
 *    reader to conclude the source was checked. The absence is then said in a sentence of
 *    its own rather than appended to the label behind a separator, because a reader
 *    skimming a list of links must be able to see which one is not a link.
 *  - **The label never comes out blank.** A seeded row carries `quote` and no `title`, so
 *    the label falls back title -> publisher -> citation_id. An empty link label is
 *    unreachable by keyboard and unreadable by anyone.
 *  - **The quote is shown verbatim when there is one.** It is the exact sentence on the
 *    public page that the item rests on, and seeing it beside the claim is what separates
 *    a citation from a decoration.
 *
 * The outbound-link treatment is copied from `components/stakeholders/dossier-view.tsx` so
 * every external source in the product looks and behaves identically: the accent is the
 * colour DESIGN_SYSTEM.md reserves for the live and the actionable, and a citation an
 * Ambassador can open is exactly that.
 */
export function EvidenceList({
  evidence,
}: {
  evidence: readonly BriefEvidence[];
}): React.JSX.Element {
  return (
    <section className="min-w-0" aria-label="Sources">
      <p className="text-label text-slate-700">
        {evidence.length} {evidence.length === 1 ? 'source' : 'sources'}
      </p>

      {evidence.length === 0 ? (
        // Said plainly rather than left blank. An item with no evidence is a real state,
        // and a silent gap would read as a rendering fault instead of a fact about the row.
        <p className="mt-1 max-w-[72ch] text-xs leading-snug text-slate-700">
          Nothing was cited for this item.
        </p>
      ) : (
        <ul className="mt-1.5 max-w-[72ch] space-y-2.5">
          {evidence.map((source, index) => {
            // Optional in the schema, so absent and explicitly null both arrive here.
            // Normalise once; branch on null below.
            const url = source.url ?? null;
            const quote = source.quote ?? null;
            const publisher = source.publisher ?? null;
            const label = source.title ?? source.publisher ?? source.citation_id;

            return (
              // The index is in the key because one item may legitimately rest on two
              // different sentences from the same citation.
              <li key={`${source.citation_id}#${index}`} className="text-xs">
                {url === null ? (
                  <>
                    <span className="font-medium text-ink">{label}</span>
                    <span className="block text-slate-700">No public URL recorded.</span>
                  </>
                ) : (
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-start gap-1 font-medium text-accent underline underline-offset-2"
                  >
                    {label}
                    <ExternalLink aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
                  </a>
                )}

                {publisher === null ? null : (
                  <span className="block text-slate-700">{publisher}</span>
                )}

                {quote === null ? null : (
                  <blockquote className="mt-1 border-l-2 border-line pl-2.5 text-2xs italic leading-snug text-slate-700">
                    {quote}
                  </blockquote>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
