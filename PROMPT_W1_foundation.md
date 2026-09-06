# CLAUDE CODE PROMPT — Week 1: Foundation
> Pascal: paste this whole block into Claude Code at the root of an empty `naddp/` repo. Keep `BUILD_BIBLE.md` in the repo root; Claude Code should read it first. Work through the phases IN ORDER. Run the verification block at the end before you stop.

---

You are the lead engineer on the NADDP Ambassador demo. Read `BUILD_BIBLE.md` in the repo root and treat it as binding. We are building the Week 1 foundation of a demo vertical slice that shares production's domain model and API shape. Nothing here is throwaway.

**Golden rules for this repo (enforce in every file you write):**
- The AI Gateway is a security boundary. Application code NEVER imports the Anthropic SDK directly — only `app/ai/gateway.py` may. Build the seam now even though responses are stubbed this week.
- Every consequential state transition writes an append-only audit event. No exceptions.
- Every AI response shape is `{ result, evidence, trace_id, approval_status }`. Never raw prose.
- RBAC is real from day one (deny-by-default); only the identity is faked (role-picker).

## Phase 1 — Monorepo & tooling
Scaffold the repo exactly per BUILD_BIBLE §7. pnpm workspace for `apps/web` + `packages/*`; Poetry (or uv) for `apps/api`. Add:
- `apps/web`: Next.js 14 App Router, TypeScript strict, Tailwind, shadcn/ui, TanStack Query.
- `apps/api`: FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, pytest.
- Root `Makefile`: `make dev`, `make seed`, `make demo-reset`, `make test`, `make gen-client`.
- `.github/workflows/ci.yml`: lint + typecheck + pytest on push.
- `.env.example` with `DATABASE_URL`, `ANTHROPIC_API_KEY`, `NEXT_PUBLIC_API_URL`, `DEMO_MODE=true`.
- Local `docker-compose.yml`: Postgres 16 + pgvector extension.

## Phase 2 — Database schema & migrations
Implement the core schema from Architecture §7 as SQLAlchemy models + one Alembic migration. Tables: `users, roles, permissions, user_roles, sources, documents, signals, briefs, brief_items, opportunities, stakeholders, organisations, interactions, meetings, actions, cases, case_events, case_evidence, knowledge_articles, diaspora_profiles, expertise_tags, audit_events, ai_traces`.
Rules: UUID/ULID internal PKs; `cases.public_ref` is a SEPARATE random token; document bytes go to object storage (for demo, a local `./storage` dir path in `object_uri`); enable `pgvector` and add an `embedding vector(1536)` column to `documents` and `knowledge_articles`. Classification enum on documents/cases per BUILD_BIBLE §5.

## Phase 3 — Governance core (auth stub + RBAC + audit)
- `app/security/`: role-permission matrix, deny-by-default `require(permission, scope)` dependency, data-classification checks, separate `bulk_export` permission.
- Demo identity: `POST /v1/session/assume-role` sets a signed demo cookie for one of `AMBASSADOR, DEPUTY, TRADE_OFFICER, CONSULAR_OFFICER, DIASPORA_OFFICER, ADMIN`. Navigation is generated from permissions (Arch §5), never hard-coded per persona.
- `app/audit/`: append-only `audit_events` writer + query service. Middleware auto-logs logins, data access on privileged objects, exports, and every workflow transition. Record `actor, action, object_type/id, policy_result, request_id, timestamp`.

## Phase 4 — AI Gateway interface + fallback harness (STUB this week)
Build `app/ai/gateway.py` implementing the 9-stage pipeline signature from Architecture §9, but with stubbed generation:
```
gateway.generate(purpose, data_class, context, user) -> GatewayResult
```
- Enforce: purpose allowlist, classification check, retrieval-authorization filter (stub returns evidence IDs from seed), model-route decision (record it), structured-output schema validation, citation post-check, trace write to `ai_traces`.
- **Fallback harness:** wrap the (this week: fake) model call in a `try/timeout(4s)` → on error/timeout return a deterministic snapshot from `data/demo-seed/ai_snapshots/{purpose}_{scenario}.json`. Log `fallback=true` in the trace. This harness is the thing that makes the demo un-killable — build it robustly.
- Purposes to register now (responses stubbed): `morning_brief, opportunity_score, meeting_prep, meeting_followup, consular_triage, knowledge_answer, diaspora_match`.

## Phase 5 — Seed dataset + citation registry
Create `data/demo-seed/` with a `seed.py` that loads BUILD_BIBLE §10 volumes, all synthetic, all built around the hero thread (§2: lithium + skilled-migration).
- **Citation registry** `data/demo-seed/citations.json`: every intelligence signal references a REAL public URL (Austrade, Geoscience Australia, Nigerian High Commission, university pages, ABS). Seed only uses URLs from this registry so nothing 404s. Do NOT fabricate URLs — leave a `TODO_VERIFY` flag on any you're unsure of; we verify before rehearsal.
- Believable `audit_events` history so the audit timeline looks lived-in.

## Phase 6 — Command-center shell (web)
- Global layout with a persistent **DEMO / SYNTHETIC** badge and role-picker.
- `/command` executive shell: tile grid (Today, Bilateral Opportunity Health, Citizen Service Health, Relationship Health, Diaspora Capability, Mission Outcomes) — wired to real (seeded) API reads, role-scoped. Empty-but-structured is fine this week; no lorem ipsum, pull real seed counts.
- Generate the OpenAPI client into `packages/contracts` via `make gen-client`.
- shadcn design system: calm, information-dense, WCAG 2.2 AA, works at 1080p + laptop.

## VERIFY before you stop (print results)
1. `make dev` boots web + api + db with zero errors.
2. `make seed` populates all tables; print row counts vs BUILD_BIBLE §10 targets.
3. `make demo-reset` restores clean seeded state.
4. Hit `/v1/command/today` as TRADE_OFFICER vs CONSULAR_OFFICER → different role-scoped payloads.
5. Call the gateway with the live path disabled → confirm it returns the deterministic fallback and writes a trace with `fallback=true`.
6. Trigger one opportunity stage transition → confirm an `audit_events` row is written.
7. `make test` green; CI green.
8. Load `/command` at 1080p → DEMO badge visible, tiles show real seed numbers, no console errors.

Output a short `docs/W1_STATUS.md` summarizing what's done, row counts, and anything flagged `TODO_VERIFY`. Do not start Week 2.
