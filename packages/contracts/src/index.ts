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

import type { components, paths } from './generated/openapi';

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
 * The exactly-one shape every AI endpoint returns (BUILD_BIBLE §4).
 *
 * RECONCILED WITH THE GENERATED SCHEMA (`make gen-client`, Phase 6). These three types
 * were hand-written so the web app could compile before the API existed, and the original
 * note said the generated `components['schemas']` version supersedes them once the API is
 * up. It is up, so they are now *derived* rather than mirrored: the API is the authority
 * and a future field change lands here automatically instead of drifting silently.
 *
 * What the reconciliation found:
 *  - `ApprovalStatus` — identical, all five members including `BLOCKED`. No change but the
 *    derivation.
 *  - `EvidenceRef` — same five fields; the API marks `url`, `source` and `citation_id`
 *    *optional as well as nullable* (Pydantic defaults), which the hand-written interface
 *    required to be present. That was the one real drift: a wire object omitting `url`
 *    would not have satisfied the local type.
 *  - `AiEnvelope` — the API's `GatewayResult` makes `evidence` optional (defaults to `[]`)
 *    and `explanation` optional-and-nullable. Same drift, same fix. `result` stays generic
 *    here because the API types it as an open `BaseModel` — the purpose schema decides the
 *    shape, so the caller supplies it.
 */
export type ApprovalStatus = components['schemas']['ApprovalStatus'];

/**
 * One item of provenance behind an AI answer. Never render an AI claim without these.
 *
 * `Readonly` and not a re-declaration: mutating a response object is never correct, and
 * the field list stays the API's.
 */
export type EvidenceRef = Readonly<components['schemas']['EvidenceRef']>;

/**
 * Envelope for every AI response. Raw prose is never a valid AI payload.
 *
 * `result` is nullable because a `BLOCKED` envelope carries no result. Callers must narrow
 * on `approval_status` before reading it; `strict` + `strictNullChecks` make forgetting a
 * compile error rather than a blank panel in front of the Ambassador. `evidence` is
 * optional on the wire — read it as `envelope.evidence ?? []`, never assume the array.
 */
export interface AiEnvelope<TResult>
  extends Omit<Readonly<components['schemas']['GatewayResult']>, 'result'> {
  readonly result: TResult | null;
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
 * A normalised, renderable API failure.
 *
 * Shaped against what this API actually emits. Every error leaving it is an RFC 9457
 * `application/problem+json` document (`apps/api/app/core/errors.py`) carrying `detail`,
 * `code`, `request_id` and — for an RBAC refusal — `missing_permissions`. Those last two
 * are the difference between "something went wrong" and "your role does not hold
 * read:opportunity, and here is the request id that proves it was refused server-side",
 * which is the sentence this demo needs to be able to say on stage.
 */
export interface ApiError {
  readonly status: number;
  readonly message: string;
  /** The `ai_traces` id, when the failure came from an AI endpoint. Usually null. */
  readonly traceId: string | null;
  /** Correlates this failure with the API log line and any audit row it wrote. */
  readonly requestId: string | null;
  /** The API's machine-readable problem code, e.g. `permission_denied`. */
  readonly code: string | null;
  /** Permissions the caller was missing, when the refusal named them. Often empty. */
  readonly missingPermissions: readonly string[];
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

/** True when the API refused this call under deny-by-default RBAC or ADR-0006 clearance. */
export function isDeniedError(error: ApiError): boolean {
  return (
    error.status === 401 ||
    error.status === 403 ||
    error.code === 'permission_denied' ||
    error.code === 'classification_denied'
  );
}

/** Read a string field, treating an empty string as absent. */
function readString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/**
 * Turn a non-OK response body into an {@link ApiError} without ever inventing a message.
 * If the body is unreadable we say so rather than pretending the call succeeded.
 */
export function toApiError(status: number, body: unknown): ApiError {
  let message = `Request failed with status ${status}.`;
  let traceId: string | null = null;
  let requestId: string | null = null;
  let code: string | null = null;
  let missingPermissions: readonly string[] = [];

  if (typeof body === 'object' && body !== null) {
    const record = body as Record<string, unknown>;

    traceId = readString(record, 'trace_id');
    requestId = readString(record, 'request_id');
    code = readString(record, 'code');

    const missing = record['missing_permissions'];
    if (Array.isArray(missing)) {
      missingPermissions = missing.filter(
        (entry): entry is string => typeof entry === 'string',
      );
    }

    const detail = record['detail'];
    if (typeof detail === 'string' && detail.length > 0) {
      message = detail;
    } else if (Array.isArray(detail)) {
      // FastAPI's 422: a list of validation problems rather than one sentence.
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

  return { status, message, traceId, requestId, code, missingPermissions };
}

/**
 * The failure that never reached the API at all: DNS, a refused connection, a CORS block.
 *
 * Given its own constructor so callers cannot accidentally report a network failure as an
 * HTTP status the server never sent. Status 0 means "no response", which is exactly what
 * happened.
 */
export function toNetworkError(message: string): ApiError {
  return {
    status: 0,
    message,
    traceId: null,
    requestId: null,
    code: 'network_unreachable',
    missingPermissions: [],
  };
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
] as const satisfies readonly components['schemas']['RoleCode'][];

/**
 * A role identifier. Aliased to the generated `RoleCode` rather than inferred from the
 * array above, so the *type* can never be narrower than what the API may send.
 */
export type NaddpRole = components['schemas']['RoleCode'];

/**
 * Compile-time proof that the picker offers every role the API defines.
 *
 * `satisfies` above catches an invented role; this catches a forgotten one. If the API
 * adds a seventh `RoleCode`, this alias resolves to `never` and `typecheck` fails with the
 * missing member named — rather than the picker quietly omitting an identity the demo
 * needs. Exported because an unused local type alias is itself a lint error.
 */
export type RolesCoverApi =
  Exclude<NaddpRole, (typeof NADDP_ROLES)[number]> extends never ? true : never;

/** Data zones from BUILD_BIBLE §5. Enforced server-side; shown in the UI trace drawer. */
export const DATA_CLASSIFICATIONS = [
  'PUBLIC',
  'MISSION_INTERNAL',
  'CONFIDENTIAL',
  'CONSULAR_SENSITIVE',
] as const satisfies readonly components['schemas']['Classification'][];

/** Aliased to the generated enum for the same reason as {@link NaddpRole}. */
export type DataClassification = components['schemas']['Classification'];

/** The classification counterpart of {@link RolesCoverApi}. */
export type ClassificationsCoverApi =
  Exclude<DataClassification, (typeof DATA_CLASSIFICATIONS)[number]> extends never
    ? true
    : never;

/* ==========================================================================
 * Response aliases
 *
 * One place to see the seam. Every alias below resolves to the generated schema, so a
 * change to the API's wire shape becomes a compile error in the web app rather than a
 * runtime surprise on stage. Nothing here declares a field of its own.
 * ========================================================================== */

/** `GET /v1/session/me` and `POST /v1/session/assume-role`: who am I, and what may I do. */
export type SessionSummary = components['schemas']['SessionResponse'];

/** `GET /v1/command/today`: the six tiles, each null when its permission is not held. */
export type CommandTodayResponse = components['schemas']['CommandTodayResponse'];

export type OpportunityTile = components['schemas']['OpportunityTileResponse'];
export type ConsularTile = components['schemas']['ConsularTileResponse'];
export type StakeholderTile = components['schemas']['StakeholderTileResponse'];
export type DiasporaTile = components['schemas']['DiasporaTileResponse'];
export type IntelligenceTile = components['schemas']['IntelligenceTileResponse'];
export type MeetingTile = components['schemas']['MeetingTileResponse'];

/** `GET /v1/opportunities`: one page of the pipeline the caller is cleared to read. */
export type OpportunityPage = components['schemas']['OpportunityPageResponse'];
export type OpportunitySummary = components['schemas']['OpportunitySummary'];
export type OpportunityStage = components['schemas']['OpportunityStage'];

/** `GET /v1/audit/events`: one page of the append-only log, newest first. */
export type AuditEventPage = components['schemas']['AuditEventPageResponse'];
export type AuditEvent = components['schemas']['AuditEventResponse'];
export type PolicyResult = components['schemas']['PolicyResult'];

/* --- Domain enums, aliased so an exhaustive label map is a compile-time guarantee --- */
export type CaseStatus = components['schemas']['CaseStatus'];
export type ConsentStatus = components['schemas']['ConsentStatus'];
export type RelationshipStrength = components['schemas']['RelationshipStrength'];
export type SignalStatus = components['schemas']['SignalStatus'];

/**
 * Name of the signed demo-session cookie the API issues.
 *
 * MIRRORED from `apps/api/app/security/session.py::SESSION_COOKIE_NAME`. The web app never
 * reads the cookie's *value* - it is `httponly`, and only the API holds the signing secret.
 * It reads only whether the cookie is present, so a visitor who has never assumed a role
 * can be answered without a round trip. See `apps/web/src/lib/session.ts` for why that
 * matters: the API records an `access.denied` audit row for every unauthenticated probe,
 * and a page load is not worth an audit row.
 */
export const DEMO_SESSION_COOKIE = 'naddp_demo_session';
