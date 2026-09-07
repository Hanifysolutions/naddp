import * as React from 'react';
import { Lock } from 'lucide-react';

import { AppShell } from '@/components/layout/app-shell';
import { IdentitySummary } from '@/components/layout/identity-summary';
import { RolePicker } from '@/components/layout/role-picker';
import { SessionProvider } from '@/components/layout/session-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { navItemsForPermissions } from '@/lib/nav';
import { getDemoSession } from '@/lib/session';

/**
 * The staff application's chrome: rail, identity control, DEMO badge, session provider.
 *
 * Extracted from `app/command/layout.tsx` when the pipeline and stakeholder routes landed.
 * Every staff route needs identical chrome, and three copies of it is three places for the
 * navigation rail to drift out of step with the permission set it is supposed to be derived
 * from - which would quietly reintroduce the per-persona navigation CLAUDE.md section 2.4
 * forbids.
 *
 * It stays a **layout**, not a page wrapper, so it persists across navigation and so each
 * route's `loading.tsx` renders inside it. A route transition must never blank the DEMO
 * badge or the identity control, even for a frame.
 *
 * Identity is resolved here, once, on the server: `GET /v1/session/me` with the browser's
 * cookie. Both the rail and the client-side `SessionProvider` are built from that single
 * answer, so they cannot disagree about what this role holds.
 */
export async function MissionShell({
  children,
}: Readonly<{ children: React.ReactNode }>): Promise<React.JSX.Element> {
  const session = await getDemoSession();

  // Navigation is the intersection of the catalogue and what the API says this session
  // holds. No permissions resolved means no destinations - deny by default.
  const navItems = navItemsForPermissions(session.permissions);

  return (
    <SessionProvider summary={session.summary} unavailableReason={session.unavailableReason}>
      <AppShell
        navItems={navItems}
        rolePicker={
          <>
            <IdentitySummary summary={session.summary} />
            <RolePicker activeRole={session.role} />
          </>
        }
      >
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
    </SessionProvider>
  );
}
