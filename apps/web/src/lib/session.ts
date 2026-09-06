import { cookies } from 'next/headers';
import { NADDP_ROLES, type NaddpRole } from '@naddp/contracts';

import { API_BASE_URL } from '@/lib/api';

/**
 * Server-side resolution of the current demo identity.
 *
 * This module is server-only by construction: it imports `next/headers`, which throws if
 * evaluated in a client component. It never sees the signing secret - the demo session
 * cookie is signed by the API with `DEMO_SESSION_SECRET`, and only the API can validate
 * it. The web app asks; the API decides. That is the whole point of the boundary.
 *
 * The endpoint below does not exist yet; the governance track builds it in Week 1
 * (`app/security/` + `POST /v1/session/assume-role`). This implementation is written
 * against the endpoint as specified, so it starts working the moment that lands, with no
 * change here. Until then it fails closed: no permissions, and a stated reason.
 */

const SESSION_ENDPOINT = '/v1/session/me';

export interface DemoSession {
  /** The role the API confirmed, or `null` when there is no valid session. */
  readonly role: NaddpRole | null;
  /**
   * Permission identifiers this session holds. Empty means "no permissions", which is
   * the correct deny-by-default answer whenever the API has not affirmatively granted
   * anything - including when the API is unreachable.
   */
  readonly permissions: readonly string[];
  /**
   * Why there is no session, when there is none. Surfaced in the UI rather than hidden,
   * so a failed lookup never looks like a legitimately empty account.
   */
  readonly unavailableReason: string | null;
}

const NO_SESSION = (reason: string): DemoSession => ({
  role: null,
  permissions: [],
  unavailableReason: reason,
});

export async function getDemoSession(): Promise<DemoSession> {
  // Forward the browser's cookies so the API can validate the signed demo session.
  const cookieHeader = cookies()
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join('; ');

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${SESSION_ENDPOINT}`, {
      method: 'GET',
      headers:
        cookieHeader.length > 0
          ? { Accept: 'application/json', cookie: cookieHeader }
          : { Accept: 'application/json' },
      // Identity must never be served from a cache. This also marks the route dynamic,
      // which is correct: the page differs per session.
      cache: 'no-store',
    });
  } catch (failure) {
    return NO_SESSION(
      `The API at ${API_BASE_URL} could not be reached (${
        failure instanceof Error ? failure.message : 'unknown network failure'
      }).`,
    );
  }

  if (response.status === 401 || response.status === 403) {
    return NO_SESSION('No demo identity has been assumed yet.');
  }

  if (response.status === 404) {
    return NO_SESSION(
      `The API does not yet expose ${SESSION_ENDPOINT}. Navigation stays empty until it does, because permissions cannot be assumed.`,
    );
  }

  if (!response.ok) {
    return NO_SESSION(
      `The session lookup failed with HTTP ${response.status}. Permissions are withheld rather than guessed.`,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch (parseFailure) {
    return NO_SESSION(
      `The session response was not valid JSON (${
        parseFailure instanceof Error ? parseFailure.message : 'unknown parse failure'
      }).`,
    );
  }

  return parseSession(body);
}

/**
 * Defensive parse. The response schema is owned by the API track and is not generated
 * yet, so every field is checked rather than asserted. An unrecognised shape yields no
 * session - it does not yield a partially-trusted one.
 */
function parseSession(body: unknown): DemoSession {
  if (typeof body !== 'object' || body === null) {
    return NO_SESSION('The session response was not an object.');
  }

  const record = body as Record<string, unknown>;

  const rawRole = record['role'];
  const role =
    typeof rawRole === 'string' && (NADDP_ROLES as readonly string[]).includes(rawRole)
      ? (rawRole as NaddpRole)
      : null;

  const rawPermissions = record['permissions'];
  const permissions = Array.isArray(rawPermissions)
    ? rawPermissions.filter((entry): entry is string => typeof entry === 'string')
    : [];

  if (role === null) {
    return NO_SESSION('The session response did not name a recognised role.');
  }

  return { role, permissions, unavailableReason: null };
}
