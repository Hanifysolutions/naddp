import * as React from 'react';
import type { Metadata } from 'next';

import { DossierView } from '@/components/stakeholders/dossier-view';

export const metadata: Metadata = {
  title: 'Stakeholder 360',
  description:
    'One counterpart: relationship history, interaction timeline, linked opportunities and the sources behind each claim. Synthetic demonstration data.',
};

/**
 * Stakeholder 360 for one organisation.
 *
 * The id is passed to a client component rather than fetched here: the dossier is
 * authorisation-scoped, so it must re-query when the demo identity changes, and that is
 * TanStack Query's job rather than a server render's.
 */
export default async function OrganisationDossierPage({
  params,
}: {
  params: Promise<{ organisationId: string }>;
}): Promise<React.JSX.Element> {
  const { organisationId } = await params;
  return (
    <div className="p-4 laptop:p-6">
      <DossierView organisationId={organisationId} />
    </div>
  );
}
