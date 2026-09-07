'use client';

import * as React from 'react';
import type { NaddpRole, SessionSummary } from '@naddp/contracts';

/**
 * The identity resolved on the server, made available to client components.
 *
 * There is exactly one identity in the tree. `app/command/layout.tsx` (a server component)
 * calls `GET /v1/session/me` with the browser's cookie and passes the answer down through
 * this provider; no client component fetches identity a second time. Two lookups could
 * disagree, and a rail rendered from one permission set beside tiles rendered from another
 * is a demo that contradicts itself on screen.
 *
 * After the role picker succeeds it calls `router.refresh()`, which re-runs that server
 * component with the new cookie and pushes a new value through here.
 *
 * **This context is a convenience, never a control.** It decides whether to *ask* for
 * something the role cannot have (so the console is not full of expected 403s), and how to
 * label a denial. Authorisation itself is the API's, re-checked on every request.
 */
export interface DemoSessionContextValue {
  readonly summary: SessionSummary | null;
  readonly role: NaddpRole | null;
  readonly permissions: readonly string[];
  readonly unavailableReason: string | null;
  /** Whether this session holds a permission, in `verb:object` form. */
  readonly can: (permission: string) => boolean;
}

const SessionContext = React.createContext<DemoSessionContextValue | null>(null);

export interface SessionProviderProps {
  summary: SessionSummary | null;
  unavailableReason: string | null;
  children: React.ReactNode;
}

export function SessionProvider({
  summary,
  unavailableReason,
  children,
}: SessionProviderProps): React.JSX.Element {
  const value = React.useMemo<DemoSessionContextValue>(() => {
    const permissions: readonly string[] = summary?.permissions ?? [];
    const granted = new Set(permissions);
    return {
      summary,
      role: summary?.role ?? null,
      permissions,
      unavailableReason,
      can: (permission: string) => granted.has(permission),
    };
  }, [summary, unavailableReason]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/**
 * Read the current demo session.
 *
 * Throws outside the provider rather than returning a permissive default. A component
 * that renders authorisation-scoped data without an identity in scope is a bug, and it
 * should fail in development rather than quietly render as though nothing were granted -
 * or, worse, as though everything were.
 */
export function useDemoSession(): DemoSessionContextValue {
  const value = React.useContext(SessionContext);
  if (value === null) {
    throw new Error(
      'useDemoSession must be used inside <SessionProvider>. Identity is resolved server-side in app/command/layout.tsx.',
    );
  }
  return value;
}
