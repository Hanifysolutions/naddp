# CLAUDE.md — Operating Rules for This Repo
> Claude Code reads this file automatically at the start of every session. It is binding. It is short on purpose.

## 0. Read first
Before doing anything, read `BUILD_BIBLE.md` (the full contract) and `ROADMAP.md` (where we are). If a request conflicts with the Build Bible, stop and flag it — do not silently deviate.

## 1. What we're building
The **demo vertical slice** of NADDP — the Nigeria–Australia Digital Diplomacy Platform Ambassador demo. Same domain model and API shape as production; production-only complexity deferred. Nothing here is throwaway.

## 2. Non-negotiable engineering rules (enforce in EVERY file)
1. **AI Gateway is a security boundary.** Application code NEVER imports the Anthropic SDK directly. Only `apps/api/app/ai/gateway.py` may. All AI goes through `gateway.generate(...)`.
2. **Every AI response shape is `{ result, evidence, trace_id, approval_status }`** — never raw prose.
3. **Every consequential state transition writes an append-only `audit_events` row.** No exceptions.
4. **RBAC is real and deny-by-default** from day one. Only identity is faked (role-picker). Navigation is generated from permissions, never hard-coded per persona.
5. **The demo must never dead-end.** Every AI call has a deterministic cached fallback (≤4s timeout) logged as `fallback=true` in the trace.
6. **No fabricated data.** Every intelligence citation is a REAL public URL from `data/demo-seed/citations.json`. If unsure of a URL, flag `TODO_VERIFY` — never invent one.
7. **Data classification is enforced** (PUBLIC / MISSION-INTERNAL / CONFIDENTIAL / CONSULAR-SENSITIVE) and the routing decision is visible in a UI trace drawer.

## 3. The three winning moments (weight quality here)
1. "Not a chatbot" — morning brief with real, resolving citations.
2. "AI drafts, humans decide" — a consequential action visibly BLOCKS on human approval.
3. "One governed picture" — unified outcomes tie opportunity → stakeholder → diaspora → case together.

## 4. Hero thread (all seed data hangs off this)
Critical Minerals (lithium) + Education / Skilled Migration. See BUILD_BIBLE §2 for the full chain.

## 5. Repo conventions
- Monorepo: `apps/web` (Next.js 14 App Router, TS strict, Tailwind, shadcn/ui, TanStack Query) · `apps/api` (FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic) · `packages/contracts` (generated OpenAPI client) · `data/` · `infra/` · `evals/` · `docs/`.
- FastAPI bounded contexts: Intelligence, Opportunities, Stakeholders, Meetings, Consular, Diaspora, Knowledge, Governance.
- Domain rules live in `app/domain` / `app/services`, NOT in route handlers.
- All list/search endpoints apply server-side authorization filters BEFORE the query runs.
- Commit in small, verifiable chunks. End every work session in a runnable state.

## 6. How we work
- One ROADMAP phase at a time. Do not start the next week's scope early.
- Each prompt ends with a VERIFY block — run it and print results before stopping.
- Write a `docs/W{n}_STATUS.md` at the end of each week summarizing what's done + any `TODO_VERIFY`.
- Safety rails always on: visible DEMO/SYNTHETIC badge, no real citizen data, `make demo-reset` works, no secrets in client bundle, responsive at laptop + 1080p.

## 7. When stuck
If something in the spec is ambiguous or a real public source can't be verified, STOP and write the question into `docs/OPEN_QUESTIONS.md` rather than guessing. Pascal escalates it to the architect.
