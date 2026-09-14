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
  pipelineBoard: (role: NaddpRole | null) => ['opportunities', 'board', role] as const,
  organisations: (role: NaddpRole | null) => ['stakeholders', 'organisations', role] as const,
  organisationDossier: (role: NaddpRole | null, id: string) =>
    ['stakeholders', 'organisation', role, id] as const,
  personDossier: (role: NaddpRole | null, id: string) =>
    ['stakeholders', 'person', role, id] as const,
  /**
   * The morning brief. The role is not a convenience here, it is the whole address:
   * `GET /v1/intelligence/brief` takes no parameters and still returns a different
   * document per identity - an AMBASSADOR gets their own brief, a DEPUTY gets the
   * mission-wide one, and a CONSULAR_OFFICER gets a 403. Keyed without the role, a cached
   * Ambassador brief would paint under a Deputy's name, which is the exact failure the
   * rule at the top of this file exists to prevent.
   */
  morningBrief: (role: NaddpRole | null) => ['intelligence', 'brief', role] as const,
  /**
   * The readable brief history. Same reasoning, plus `limit`: the API returns the newest
   * `limit` rows *the caller may read*, so two roles asking for the same count get
   * different lists, and one role asking for two counts gets two different lists.
   */
  briefHistory: (role: NaddpRole | null, limit: number) =>
    ['intelligence', 'briefs', role, limit] as const,
} as const;
