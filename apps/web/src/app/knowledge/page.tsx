import * as React from 'react';
import type { Metadata } from 'next';

import { KnowledgeDesk } from '@/components/knowledge/knowledge-desk';

export const metadata: Metadata = {
  title: 'Knowledge',
  description:
    'Answers grounded only in approved, in-date mission guidance, quoted and cited - or a clear refusal when no approved source covers the question. Synthetic demonstration data.',
};

/**
 * The Knowledge desk.
 *
 * A thin server component over a client view, as on every staff screen: switching demo identity
 * has to re-query through TanStack Query - the cache the role picker clears - because the corpus
 * an answer may come from is different for every role. The page padding lives here only, so
 * `loading.tsx` and the view share one margin.
 */
export default function KnowledgePage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <KnowledgeDesk />
    </div>
  );
}
