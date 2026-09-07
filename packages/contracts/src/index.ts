/**
 * @naddp/contracts — the single typed seam between the Next.js app and the FastAPI API.
 *
 * This package is deliberately source-only (no build step). Consumers pull it in via
 * Next's `transpilePackages`, so there is exactly one compilation of these files and no
 * stale `dist/` to get out of sync with the generated OpenAPI types.
 *
 * Nothing in here may contain a secret. It is imported by client components.
 */
import createClient from 'openapi-fetch';

import type { paths } from './generated/openapi';

export type { paths, webhooks, components, operations, $defs } from './generated/openapi';

/**
 * The concrete client type for the NADDP API, keyed by the generated path map.
 * Derived from `createClient` via an instantiation expression so we do not depend on
 * openapi-fetch's internal type export names staying stable across patch releases.
 */
export type ApiClient = ReturnType<typeof createClient<paths>>;

/**
 * Build a typed API client.
 *
 * `credentials: 'include'` is load-bearing: the demo identity is a signed
 * `naddp_demo_session` cookie issued by `POST /v1/session/assume-role`, and the browser
 * will not attach it to cross-origin requests (web on :3000, API on :8000) unless the
 * request opts in. Without this every request would arrive unauthenticated and RBAC —
 * which is deny-by-default — would correctly reject it.
 *
 * @param baseUrl Absolute origin of the API, e.g. `http://localhost:8000`. Trailing
 *                slashes are trimmed so path joins stay predictable.
 */
export function createApiClient(baseUrl: string): ApiClient {
  const trimmed = baseUrl.replace(/\/+$/, '');
  if (trimmed.length === 0) {
    throw new Error('createApiClient: baseUrl must be a non-empty absolute URL.');
  }
  return createClient<paths>({
    baseUrl: trimmed,
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });
}

/**
 * The exactly-one shape every AI endpoint returns (BUILD_BIBLE §4). Declared here as a
 * hand-written contract so the web app can depend on it before the API exists; once the
 * API is up, the generated `components['schemas']` version supersedes it and this stays
 * as the documented invariant.
 */
export type ApprovalStatus =
  /** The action carries no approval requirement. */
  | 'NOT_REQUIRED'
  /** Drafted and awaiting a human decision. The consequential action is blocked. */
  | 'PENDING_APPROVAL'
  /** A human with the required permission approved it. */
  | 'APPROVED'
  /** A human with the required permission rejected it. */
  | 'REJECTED'
  /**
   * The Gateway refused or could not answer, and no deterministic fallback covered the
   * request (ADR-0002). `result` is null and the UI must say so plainly rather than
   * render an empty success. This is the state the demo is most likely to hit on stage,
   * so it has to be representable — omitting it is how a dead-end becomes a blank panel.
   */
  | 'BLOCKED';

/** One item of provenance behind an AI answer. Never render an AI claim without these. */
export interface EvidenceRef {
  readonly id: string;
  readonly title: string;
  readonly url: string | null;
  readonly source: string | null;
  /**
   * Key of the entry in `data/demo-seed/citations.json` this evidence resolved to.
   *
   * Usually the same string as `id`; it differs only when an item was matched to the
   * registry under another key. Additive and nullable, so a consumer written before the
   * Gateway landed still compiles. Reconciled against
   * `components['schemas']['EvidenceRef']`, which is the authority.
   */
  readonly citation_id: string | null;
}

/**
 * Envelope for every AI response. Raw prose is never a valid AI payload.
 *
 * `result` is nullable because a `BLOCKED` envelope carries no result. Callers must
 * narrow on `approval_status` before reading it; `strict` + `strictNullChecks` make
 * forgetting a compile error rather than a blank panel in front of the Ambassador.
 */
export interface AiEnvelope<TResult> {
  readonly result: TResult | null;
  readonly evidence: readonly EvidenceRef[];
  readonly trace_id: string;
  readonly approval_status: ApprovalStatus;
  /**
   * Why the Gateway refused, in a sentence a person can read.
   *
   * Populated only when `approval_status` is `BLOCKED`, and null otherwise. This is the
   * text the UI must show instead of an empty panel: a refusal the audience cannot read
   * looks like a broken demo rather than a control working. Additive and nullable, so an
   * existing consumer still compiles. Reconciled against
   * `components['schemas']['GatewayResult']`, which is the authority.
   */
  readonly explanation: string | null;
}

/**
 * FastAPI's error body. A 422 carries a list of validation problems; everything else
 * carries a plain string. Modelled as a union so callers must handle both.
 */
export type FastApiDetail =
  | string
  | ReadonlyArray<{
      readonly loc: readonly (string | number)[];
      readonly msg: string;
      readonly type: string;
    }>;

/**
 * A normalised, renderable API failure. Deliberately small: a status, a human-readable
 * message, and the trace id when the API supplied one.
 */
export interface ApiError {
  readonly status: number;
  readonly message: string;
  readonly traceId: string | null;
}

/** Narrow an unknown thrown/returned value to an {@link ApiError}. */
export function isApiError(value: unknown): value is ApiError {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate['status'] === 'number' &&
    typeof candidate['message'] === 'string' &&
    (candidate['traceId'] === null || typeof candidate['traceId'] === 'string')
  );
}

/**
 * Turn a non-OK response body into an {@link ApiError} without ever inventing a message.
 * If the body is unreadable we say so rather than pretending the call succeeded.
 */
export function toApiError(status: number, body: unknown): ApiError {
  let message = `Request failed with status ${status}.`;
  let traceId: string | null = null;

  if (typeof body === 'object' && body !== null) {
    const record = body as Record<string, unknown>;

    const rawTrace = record['trace_id'];
    if (typeof rawTrace === 'string' && rawTrace.length > 0) traceId = rawTrace;

    const detail = record['detail'];
    if (typeof detail === 'string' && detail.length > 0) {
      message = detail;
    } else if (Array.isArray(detail)) {
      const parts = detail
        .map((entry) => {
          if (typeof entry !== 'object' || entry === null) return null;
          const msg = (entry as Record<string, unknown>)['msg'];
          return typeof msg === 'string' ? msg : null;
        })
        .filter((part): part is string => part !== null);
      if (parts.length > 0) message = parts.join('; ');
    }
  } else if (typeof body === 'string' && body.trim().length > 0) {
    message = body.trim();
  }

  return { status, message, traceId };
}

/**
 * The six demo identities (BUILD_BIBLE §3 / CLAUDE.md §2.4).
 *
 * Hand-written on purpose and mirrored from the API's `Role` enum. It lives here rather
 * than in the web app so there is one shared list for every workspace consumer. Once
 * `make gen-client` has run, `components['schemas']['Role']` is the authority and this
 * constant must be reconciled against it — the API, not the client, decides what a role
 * is. RBAC is enforced server-side; this list only drives the picker's options.
 */
export const NADDP_ROLES = [
  'AMBASSADOR',
  'DEPUTY',
  'TRADE_OFFICER',
  'CONSULAR_OFFICER',
  'DIASPORA_OFFICER',
  'ADMIN',
] as const;

export type NaddpRole = (typeof NADDP_ROLES)[number];

/** Data zones from BUILD_BIBLE §5. Enforced server-side; shown in the UI trace drawer. */
export const DATA_CLASSIFICATIONS = [
  'PUBLIC',
  'MISSION_INTERNAL',
  'CONFIDENTIAL',
  'CONSULAR_SENSITIVE',
] as const;

export type DataClassification = (typeof DATA_CLASSIFICATIONS)[number];
