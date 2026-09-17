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
 *
 * TWO BASES, because this app calls the API from two places that need different answers:
 *
 *  - The BROWSER calls `/api`, a same-origin path that `next.config.mjs` rewrites to the
 *    API. Same-origin is what keeps `naddp_demo_session` same-site. Split across two
 *    registrable domains (Vercel and Railway) the cookie was cross-site, and a cross-site
 *    cookie is at the mercy of each browser's third-party cookie policy - Safari drops it
 *    outright. The proxy ends the question rather than negotiating with it.
 *
 *  - The SERVER calls the API directly. `lib/session.ts` resolves identity during SSR,
 *    where a relative URL has no origin to resolve against and Node's fetch throws
 *    `Failed to parse URL`. Nothing about SameSite applies to a server-to-server call,
 *    and hairpinning back out through our own edge would only add a hop.
 */
export const API_BASE_URL: string = '/api';

/**
 * The API's absolute origin: the rewrite target, and the base for server-side calls.
 *
 * Never reach for this from code that runs in the browser. Doing so leaves same-origin
 * and reintroduces the cross-site cookie problem the proxy exists to end.
 */
export const SERVER_API_BASE_URL: string =
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
