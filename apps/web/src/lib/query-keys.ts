import type { NaddpRole } from '@naddp/contracts';

/**
 * TanStack Query keys, in one place.
 *
 * **Every key carries the role.** The cache is keyed by identity, not only by endpoint,
 * because every read in this product is authorisation-scoped: the same URL returns a
 * different, smaller answer for a CONSULAR_OFFICER than for an AMBASSADOR. If the role
 * were not in the key, switching identity could paint the previous role's numbers under
 * the new role's name for the frame before the refetch lands - which on stage is the worst
 * failure this app could have.
 *
 * The role picker also calls `invalidateQueries()` with no filter on success, so the cache
 * is emptied as well. This is the belt to that pair of braces: even a cache entry that
 * escaped invalidation cannot be read by the wrong identity, because it is filed under a
 * key nobody will ask for.
 *
 * `null` stands for "no session resolved", so an unauthenticated view has its own cache
 * bucket rather than sharing one with whichever role happened to load first.
 */
export const queryKeys = {
  commandToday: (role: NaddpRole | null) => ['command', 'today', role] as const,
  session: () => ['session', 'me'] as const,
  opportunities: (role: NaddpRole | null, limit: number) =>
    ['opportunities', 'list', role, limit] as const,
  auditEvents: (role: NaddpRole | null, limit: number) =>
    ['audit', 'events', role, limit] as const,
} as const;
