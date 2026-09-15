import * as React from 'react';

import { MissionShell } from '@/components/layout/mission-shell';

/** Same chrome as every other staff route. See `components/layout/mission-shell`. */
export default async function KnowledgeLayout({
  children,
}: Readonly<{ children: React.ReactNode }>): Promise<React.JSX.Element> {
  return <MissionShell>{children}</MissionShell>;
}
