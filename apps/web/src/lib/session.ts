import { cookies } from 'next/headers';
import {
  DATA_CLASSIFICATIONS,
  DEMO_SESSION_COOKIE,
  NADDP_ROLES,
  type DataClassification,
  type NaddpRole,
  type SessionSummary,
} from '@naddp/contracts';

import { SERVER_API_BASE_URL } from '@/lib/api';

/**
 * Server-side resolution of the current demo identity.
 *
 * This module is server-only by construction: it imports `next/headers`, which throws if
 * evaluated in a client component. It never sees the signing secret - the demo session
 * cookie is signed by the API with `DEMO_SESSION_SECRET`, and only the API can validate
 * it. The web app asks; the API decides. That is the whole point of the boundary.
 *
 * Resolving identity on the server rather than in the browser is what lets the navigation
 * rail render permission-correct in the first paint, with no flash of a rail the role does
 * not hold. The same payload is handed to the client through `SessionProvider`, so there
 * is one identity in the tree and not two that can disagree.
 *
 * It fails closed. Every failure path returns no permissions and a stated reason, and the
 * reason is rendered - a lookup that failed must never be indistinguishable from an
 * account that legitimately holds nothing.
 */

const SESSION_ENDPOINT = '/v1/session/me';

export interface DemoSession {
  /** The full session payload the API returned, or `null` when there is none. */
  readonly summary: SessionSummary | null;
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
  summary: null,
  role: null,
  permissions: [],
  unavailableReason: reason,
});

export async function getDemoSession(): Promise<DemoSession> {
  const jar = cookies();

  /*
   * No session cookie, no question worth asking.
   *
   * The answer is a certain 403, and the API records an `access.denied` audit row for
   * every unauthenticated probe - correctly, since a denial is an event. But a first page
   * load by someone who has not yet chosen a role is not a security event, and letting
   * every anonymous render append a row would bury the denials that do matter under the
   * demo's own noise. So: answer locally, fail closed, and leave the log for real refusals.
   *
   * This reads only the cookie's presence. The value is `httponly` and signed by the API;
   * this app can neither read nor forge it, and would not be believed if it tried.
   */
  if (jar.get(DEMO_SESSION_COOKIE) === undefined) {
    return NO_SESSION('No demo identity has been assumed yet.');
  }

  // Forward the browser's cookies so the API can validate the signed demo session.
  const cookieHeader = jar
    .getAll()
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join('; ');

  let response: Response;
  try {
    response = await fetch(`${SERVER_API_BASE_URL}${SESSION_ENDPOINT}`, {
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
      `The API at ${SERVER_API_BASE_URL} could not be reached (${
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

  const summary = parseSessionSummary(body);
  if (summary === null) {
    return NO_SESSION(
      'The session response did not match the shape this build was generated against. Permissions are withheld rather than partially trusted.',
    );
  }

  return {
    summary,
    role: summary.role,
    permissions: summary.permissions,
    unavailableReason: null,
  };
}

/* -------------------------------------------------------------------------- */
/* Defensive parsing                                                          */
/* -------------------------------------------------------------------------- */

/*
 * The response type is generated from the API's own OpenAPI document, so at compile time
 * this shape is known. It is still checked at runtime, because the generated type is a
 * statement about the build the client was generated against, not a guarantee about the
 * server that answered. An unrecognised shape yields no session; it never yields a
 * partially-trusted one.
 */

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string');
}

function asRole(value: unknown): NaddpRole | null {
  return typeof value === 'string' && (NADDP_ROLES as readonly string[]).includes(value)
    ? (value as NaddpRole)
    : null;
}

function asClassifications(value: unknown): DataClassification[] | null {
  if (!isStringArray(value)) return null;
  const known = new Set<string>(DATA_CLASSIFICATIONS);
  // An unknown zone is not dropped silently: a classification this client cannot render
  // is a contract mismatch, and rendering the rest would understate what the API said.
  return value.every((entry) => known.has(entry)) ? (value as DataClassification[]) : null;
}

export function parseSessionSummary(body: unknown): SessionSummary | null {
  if (typeof body !== 'object' || body === null) return null;
  const record = body as Record<string, unknown>;

  const role = asRole(record['role']);
  const readableClassifications = asClassifications(record['readable_classifications']);
  const permissions = record['permissions'];
  const sensitivePermissions = record['sensitive_permissions'];
  const compartments = record['compartments'];

  if (
    role === null ||
    readableClassifications === null ||
    !isStringArray(permissions) ||
    !isStringArray(sensitivePermissions) ||
    !isStringArray(compartments) ||
    typeof record['user_id'] !== 'string' ||
    typeof record['email'] !== 'string' ||
    typeof record['full_name'] !== 'string' ||
    typeof record['title'] !== 'string' ||
    typeof record['mission'] !== 'string' ||
    typeof record['clearance_rank'] !== 'number' ||
    typeof record['is_demo_identity'] !== 'boolean' ||
    typeof record['expires_in_seconds'] !== 'number'
  ) {
    return null;
  }

  return {
    role,
    user_id: record['user_id'],
    email: record['email'],
    full_name: record['full_name'],
    title: record['title'],
    mission: record['mission'],
    clearance_rank: record['clearance_rank'],
    compartments,
    permissions,
    sensitive_permissions: sensitivePermissions,
    readable_classifications: readableClassifications,
    is_demo_identity: record['is_demo_identity'],
    expires_in_seconds: record['expires_in_seconds'],
  };
}
