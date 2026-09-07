import * as React from 'react';
import type { Metadata } from 'next';

import { OrganisationIndex } from '@/components/stakeholders/organisation-index';

export const metadata: Metadata = {
  title: 'Stakeholders',
  description:
    'Organisations, contacts and relationship history across the Nigeria-Australia corridor. Synthetic demonstration data.',
};

/** The organisation index. The way into a Stakeholder 360 dossier. */
export default function StakeholdersPage(): React.JSX.Element {
  return (
    <div className="p-4 laptop:p-6">
      <OrganisationIndex />
    </div>
  );
}
