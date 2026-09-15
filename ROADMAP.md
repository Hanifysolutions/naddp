# ROADMAP — NADDP Ambassador Demo
> Where we are and what's next. Update the Status column as each phase completes.

**Goal:** a top-1% 10-minute live demo that earns consular/Ambassador approval, built as a production-grade vertical slice.
**Config:** Next.js + FastAPI + Postgres/pgvector · Live Anthropic API behind an AI Gateway with deterministic fallback · Web→Vercel, API+DB→Railway.

---

## Legend
`⬜ Not started` · `🟡 In progress` · `✅ Done` · `⚠️ Blocked / TODO_VERIFY`

---

## Week 1 — Foundation  ✅
Prompt: `PROMPT_W1_foundation.md`
- ✅ P1 Monorepo & tooling (pnpm + Poetry/uv, Makefile, CI, docker-compose w/ pgvector)
- ✅ P2 DB schema + Alembic migration (Arch §7 tables, UUID PKs, pgvector columns, classification enums)
- ✅ P3 Governance core (role-picker auth stub, deny-by-default RBAC, append-only audit)
- ✅ P4 AI Gateway interface + fallback harness (9-stage pipeline, stubbed generation)
- ✅ P5 Seed dataset + citation registry (hero thread, real public URLs, audit history)
- ✅ P6 Command-center shell (DEMO badge, role picker, 6 tiles wired to seed, OpenAPI client)
- ✅ VERIFY block passes · `docs/W1_STATUS.md` written

**Exit condition: MET (2026-09-07).** `make dev` boots clean (via `make.ps1` on Windows), `make seed` hits all 7 §10 targets, role-scoped `/command/today` returns materially different payloads per role, and both the gateway fallback (`fallback=true`) and the audit write on a stage transition are proven by query. 836 tests pass. See `docs/W1_STATUS.md`.

---

## Week 2 — Diplomatic Intelligence  ✅
- ✅ Morning brief (role-aware, source-backed, confidence) — Gateway goes LIVE for briefs
- ✅ Opportunity Radar: ingest → dedupe → classify → score (explainable)
- ✅ Opportunity Pipeline (kanban + state machine + audit on transition)
- ✅ Stakeholder 360 (dossier, timeline, linked opportunity)
- ✅ Evidence retrieval: hybrid (metadata + FTS + pgvector) with provenance
- ✅ `docs/W2_STATUS.md`

**Exit condition:** every intelligence item has resolving evidence; brief is grounded; scoring is explainable + editable. **Met** — see `docs/W2_STATUS.md`.

---

## Week 3 — AI + Citizen Operations  ✅
- ✅ Meeting Copilot: pre-read + follow-up draft — **follow-up SEND blocks on human approval** (winning moment #2)
- ✅ Consular dashboard (volumes, ageing, SLA-risk) — business-day clock that pauses on the citizen; the near-breach urgent case is surfaced first after the breaches
- ✅ Case workspace + full case state machine + immutable case_events timeline — metadata-only AI triage on the CONSULAR-SENSITIVE no-external-model route; a named officer confirms
- ✅ Grounded knowledge answers (approved-only; refuse/escalate when source absent) — filtered before retrieval, approved text quoted and cited, and a refusal that cites nothing and names an officer
- ✅ Gateway LIVE for all purposes; trace drawer shows classification + model route — live-capable wherever section 4a permits a model; CONSULAR-SENSITIVE is metadata-only by design; the demo runs with the live path off and says so on every trace
- ✅ `docs/W3_STATUS.md`

**Exit condition:** synthetic passport case runs NEW→RESOLVED with audit; a consequential action is visibly blocked; knowledge answerer refuses when no source. **Met** — see `docs/W3_STATUS.md`.

---

## Week 4 — Unified Story + Hardening  ⬜
- ✅ Diaspora talent search (consent-filtered) — returns lithium engineer AND migration academic (winning moment #3 setup)
- ✅ Unified Outcomes board (opportunity + stakeholder + diaspora + service metrics in one frame) — each domain counted under its own authorisation; the lithium/skilled-migration corridor traced across five contexts (winning moment #3)
- ✅ Governance page — append-only audit log in plain language, filterable and paged by the API, with on-demand hash-chain verification ("Chain intact over N events"); the Governance nav item is un-greyed, so every nav item is now live
- ✅ Audit hash-chain hardening — the chain cannot fork under concurrent writes (unique index on `prev_event_hash`, NULLS NOT DISTINCT, plus re-link on conflict); **resolves `docs/W1_STATUS.md` §6 item 1**
- ⬜ `make demo-reset` + cached-fallback pass across every script beat
- ⬜ Security pass (CSP, no secrets in bundle, RBAC audit, injection eval stubs)
- ⬜ Citation verification (clear all `TODO_VERIFY`)
- ⬜ 10-minute script dry-run — no dead ends, all flows ≤3 clicks
- ⬜ Deploy: web→Vercel, api+db→Railway · rehearsal
- ⬜ `docs/W4_STATUS.md` — started: progress through W4.3, completed at week end

**Exit condition:** Definition of Done (BUILD_BIBLE §13) fully met; live rehearsal clean on laptop + 1080p.

---

## After approval → Pilot backlog
Tracked separately once the Ambassador greenlights (Arch §21): IdP integration, approved knowledge import, mission-specific CRM stages, source registry + scheduled ingestion, 2–3 low-risk consular case types, threat model + pen test + DR, AI governance baseline, SLOs/on-call.
