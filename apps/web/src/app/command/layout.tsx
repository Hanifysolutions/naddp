import * as React from 'react';
import { Lock } from 'lucide-react';

import { AppShell } from '@/components/layout/app-shell';
import { RolePicker } from '@/components/layout/role-picker';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { navItemsForPermissions } from '@/lib/nav';
import { getDemoSession } from '@/lib/session';

/**
 * The command-centre chrome lives in a layout rather than in the page so that it
 * persists across navigation and, critically, so `loading.tsx` renders *inside* it. A
 * route transition must never blank the DEMO badge or the identity control, even for a
 * frame.
 */
export default async function CommandLayout({
  children,
}: Readonly<{ children: React.ReactNode }>): Promise<React.JSX.Element> {
  const session = await getDemoSession();

  // Navigation is the intersection of the catalogue and what the API says this session
  // holds. No permissions resolved means no destinations - deny by default.
  const navItems = navItemsForPermissions(session.permissions);

  return (
    <AppShell navItems={navItems} rolePicker={<RolePicker activeRole={session.role} />}>
      {session.unavailableReason === null ? null : (
        <div className="px-4 pt-4 laptop:px-6">
          {/*
            A failed or absent session is stated plainly. It is never allowed to look
            like a legitimately empty account, and it is never papered over with
            placeholder content.
          */}
          <Alert variant="warning" role="status">
            <Lock aria-hidden="true" />
            <AlertTitle>No demo identity resolved</AlertTitle>
            <AlertDescription>
              {session.unavailableReason} Every figure below is therefore shown as
              unavailable rather than as a number.
            </AlertDescription>
          </Alert>
        </div>
      )}
      {children}
    </AppShell>
  );
}
