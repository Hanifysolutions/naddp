import { createApiClient, type ApiClient } from '@naddp/contracts';

/**
 * The single place this app learns where the API lives.
 *
 * ============================ SECURITY INVARIANT ============================
 * Only `NEXT_PUBLIC_*` variables may ever be read in this file, and by extension
 * anywhere under `apps/web/src`. Next inlines `process.env.NEXT_PUBLIC_*` into the
 * client bundle at build time, so reading a server-only secret here would ship it to
 * every browser that loads the demo. BUILD_BIBLE §11 forbids that outright.
 *
 * In particular: ANTHROPIC_API_KEY, DEMO_SESSION_SECRET and DATABASE_URL are API-side
 * concerns. The web app never sees them. All model access goes through the API's AI
 * Gateway (`apps/api/app/ai/gateway.py`), which is the only module permitted to touch
 * the Anthropic SDK at all.
 *
 * The literal `process.env.NEXT_PUBLIC_API_URL` below is deliberately not read through
 * a variable or a helper - Next's build-time substitution only works on the static
 * member expression.
 * ===========================================================================
 */
export const API_BASE_URL: string =
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

/**
 * Typed API client. Carries the signed `naddp_demo_session` cookie on every request
 * (`credentials: 'include'`), which is how the API resolves the current demo identity
 * and applies deny-by-default RBAC.
 *
 * Until `make gen-client` has generated `packages/contracts/src/generated/openapi.d.ts`
 * against a running API, the path map is empty and every call is a compile error. That
 * is the intended, loud failure mode - it points at the missing generation step rather
 * than silently degrading to `any`.
 */
export const api: ApiClient = createApiClient(API_BASE_URL);
