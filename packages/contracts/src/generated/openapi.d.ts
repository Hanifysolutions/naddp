/**
 * ============================================================================
 *  GENERATED FILE — DO NOT HAND-EDIT.
 * ============================================================================
 *
 *  This file is regenerated from the FastAPI OpenAPI document by:
 *
 *      make gen-client          (Linux / CI)
 *      .\make.ps1 gen-client    (Windows)
 *
 *  which runs `pnpm --filter @naddp/contracts generate`, i.e.
 *  `openapi-typescript ${API_URL:-http://localhost:8000}/openapi.json`.
 *
 *  Any manual change here will be silently destroyed on the next generation
 *  run. If a type is wrong, fix the Pydantic schema in `apps/api/app/schemas/`
 *  and regenerate.
 *
 * ----------------------------------------------------------------------------
 *  WHY IS THIS PLACEHOLDER COMMITTED?
 *
 *  The web app imports `paths` from this package at build time. Without a
 *  committed placeholder, a fresh clone (or CI on a machine with no API
 *  running) would fail `pnpm typecheck` before `make gen-client` had ever been
 *  executed. Committing an empty-but-valid shape means the workspace always
 *  typechecks; the real endpoints simply appear the moment the API is up and
 *  the generator has run.
 *
 *  Consequence, on purpose: until generation runs, `api.GET('/v1/...')` is a
 *  type error rather than a silent `any`. That is the correct failure mode —
 *  it is loud, and it points at the missing generation step.
 * ============================================================================
 */

/** Endpoint map keyed by URL path. Empty until `make gen-client` runs. */
export interface paths {}

/** OpenAPI webhooks map. FastAPI emits none today; the key is kept for shape parity. */
export interface webhooks {}

/** Reusable schema objects (`components.schemas.*`). Empty until generation. */
export interface components {
  schemas: Record<string, never>;
  responses: Record<string, never>;
  parameters: Record<string, never>;
  requestBodies: Record<string, never>;
  headers: Record<string, never>;
  pathItems: Record<string, never>;
}

/** JSON-Schema `$defs` bucket emitted by openapi-typescript v7. */
export type $defs = Record<string, never>;

/** Operation-id keyed map. Empty until generation. */
export interface operations {}
