import {
  toApiError,
  toNetworkError,
  type ApiError,
  type AuditEventPage,
  type CommandTodayResponse,
  type Dossier,
  type OpportunityPage,
  type OrganisationList,
  type PipelineBoard,
  type SessionSummary,
  type TransitionResponse,
} from '@naddp/contracts';

import { api, API_BASE_URL } from '@/lib/api';

/**
 * Every read this app performs, in one file.
 *
 * Three rules hold for all of them:
 *
 *  1. **The generated client makes the call.** Paths, query parameters and response types
 *     all come from `packages/contracts/src/generated/openapi.d.ts`. A path this API does
 *     not serve, or a field it does not return, is a compile error rather than an
 *     `undefined` on screen.
 *
 *  2. **A failure is thrown, never swallowed.** TanStack Query needs a rejected promise to
 *     enter its error state; a resolved `undefined` would render as an empty tile, which
 *     is exactly the lie this product cannot tell. Every rejection is a normalised
 *     `ApiError`, so a 403 arrives with the permission the API said was missing.
 *
 *  3. **Nothing here invents a value.** No default counts, no `?? 0`, no placeholder rows.
 *     If the API did not say it, the caller does not get it.
 */

/**
 * Run a client call, converting a transport failure into a reportable `ApiError`.
 *
 * openapi-fetch rejects with a `TypeError` when the request never reached the server -
 * the API is down, DNS failed, or CORS refused the response. That is not an HTTP status
 * and must not be reported as one, so it becomes status 0 with a message that names the
 * origin we tried. An `AbortError` is re-thrown untouched: TanStack Query cancels
 * in-flight queries on unmount and on invalidation, and a cancellation is not a failure.
 */
async function guard<T>(call: Promise<T>): Promise<T> {
  try {
    return await call;
  } catch (failure) {
    if (failure instanceof DOMException && failure.name === 'AbortError') throw failure;
    throw toNetworkError(
      `Could not reach the API at ${API_BASE_URL}. ${
        failure instanceof Error ? failure.message : 'Unknown network failure.'
      }`,
    );
  }
}

/**
 * `GET /v1/command/today` - the six tiles, scoped to the caller.
 *
 * A tile the caller may not read comes back `null` and its tables were never queried
 * (`apps/api/app/services/command.py`). The UI must render that null as a denial, never
 * as a zero.
 *
 * Throws {@link ApiError} with status 403 when there is no session, or when the role does
 * not hold `read:command`.
 */
export async function fetchCommandToday(
  signal?: AbortSignal,
): Promise<CommandTodayResponse> {
  const { data, error, response } = await guard(
    api.GET('/v1/command/today', { signal }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/** `GET /v1/session/me` - the caller's identity, permissions and readable zones. */
export async function fetchSession(signal?: AbortSignal): Promise<SessionSummary> {
  const { data, error, response } = await guard(api.GET('/v1/session/me', { signal }));
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/**
 * `GET /v1/opportunities` - one page of the pipeline the caller is cleared to read.
 *
 * Used by the opportunity tile to show *which* opportunities sit behind its counts, and
 * in particular to badge the ones carrying `is_proposed_by_ai` - the hero opportunity is
 * AI-proposed rather than reported (BUILD_BIBLE §2 / OPEN_QUESTIONS Q-17) and has to read
 * as lower-confidence than the signals beneath it.
 */
export async function fetchOpportunities(
  limit: number,
  signal?: AbortSignal,
): Promise<OpportunityPage> {
  const { data, error, response } = await guard(
    api.GET('/v1/opportunities', { params: { query: { limit } }, signal }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/**
 * `GET /v1/audit/events` - the newest rows of the append-only log.
 *
 * Held by AMBASSADOR, DEPUTY and ADMIN only, so the caller must check `read:audit` before
 * enabling this query. Asking without it would put a 403 in the console on every load for
 * four of the six roles, and a console full of expected errors is how a real one gets
 * missed.
 */
export async function fetchAuditEvents(
  limit: number,
  signal?: AbortSignal,
): Promise<AuditEventPage> {
  const { data, error, response } = await guard(
    api.GET('/v1/audit/events', { params: { query: { limit } }, signal }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/**
 * `POST /v1/session/assume-role` - become one of the six demo identities.
 *
 * Returns the session the API actually issued. The caller must adopt *this* role, not the
 * one it asked for: they are the same in every expected case, and trusting the response is
 * what makes an unexpected case visible instead of silently wrong.
 */
export async function assumeRole(
  role: SessionSummary['role'],
  signal?: AbortSignal,
): Promise<SessionSummary> {
  const { data, error, response } = await guard(
    api.POST('/v1/session/assume-role', { body: { role }, signal }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/**
 * `GET /v1/opportunities/board` - the pipeline, grouped by stage.
 *
 * One call rather than a list plus a fan-out: the cards show owner and counterpart *names*,
 * and resolving those from the browser would be an N+1 across the wire on the one screen an
 * audience is watching. The server also decides which events each card may offer, so the
 * client never derives a button from a role.
 */
export async function fetchPipelineBoard(signal?: AbortSignal): Promise<PipelineBoard> {
  const { data, error, response } = await guard(
    api.GET('/v1/opportunities/board', { signal }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/**
 * `POST /v1/opportunities/{id}/transition` - fire a workflow event.
 *
 * `expectedStage` is sent as a precondition, not as a courtesy: two officers with the board
 * open are the normal case, and without it the second click would silently apply to a stage
 * that had already moved. The API answers 409 instead, and the UI can say so.
 *
 * A refusal is thrown, never swallowed. A 403 here is a control working - BUILD_BIBLE
 * section 6 requires it to be visible - so the caller renders the reason rather than a
 * generic failure.
 */
export async function transitionOpportunity(
  opportunityId: string,
  event: string,
  reason: string,
  expectedStage: string,
  signal?: AbortSignal,
): Promise<TransitionResponse> {
  const { data, error, response } = await guard(
    api.POST('/v1/opportunities/{opportunity_id}/transition', {
      params: { path: { opportunity_id: opportunityId } },
      body: {
        event,
        reason,
        expected_stage: expectedStage as TransitionResponse['from_stage'],
      },
      signal,
    }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/** `GET /v1/stakeholders/organisations` - the organisation index. */
export async function fetchOrganisations(
  signal?: AbortSignal,
): Promise<OrganisationList> {
  const { data, error, response } = await guard(
    api.GET('/v1/stakeholders/organisations', { signal }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/** `GET /v1/stakeholders/organisations/{id}` - Stakeholder 360 for an organisation. */
export async function fetchOrganisationDossier(
  organisationId: string,
  signal?: AbortSignal,
): Promise<Dossier> {
  const { data, error, response } = await guard(
    api.GET('/v1/stakeholders/organisations/{organisation_id}', {
      params: { path: { organisation_id: organisationId } },
      signal,
    }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

/** `GET /v1/stakeholders/people/{id}` - Stakeholder 360 for one named contact. */
export async function fetchPersonDossier(
  stakeholderId: string,
  signal?: AbortSignal,
): Promise<Dossier> {
  const { data, error, response } = await guard(
    api.GET('/v1/stakeholders/people/{stakeholder_id}', {
      params: { path: { stakeholder_id: stakeholderId } },
      signal,
    }),
  );
  if (data === undefined) throw toApiError(response.status, error);
  return data;
}

export type { ApiError };
