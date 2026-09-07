# NADDP Demo — Build Bible (v1.0)
**The single source of truth. Every Claude Code session reads this first.**

Ambassador Demonstration — Nigeria–Australia Digital Diplomacy Platform
Owner: [FDE/Solutions Architect] · Builder: Pascal · Target: 10-min live demo, consular approval

---

## 0. Prime Directive
We are building the **demo vertical slice** from the Architecture doc §19–20 — the *same domain model and API shape as production*, with production-only complexity deferred. Nothing we build gets thrown away. This is a production skeleton wearing demo clothes.

**The demo must be physically incapable of dead-ending in front of the Ambassador.** Every AI call has a deterministic cached fallback. Every citation resolves. Every screen has seed data.

## 1. The Three Winning Moments (weight quality here)
1. **"Not a chatbot"** — Morning brief, real source citations that open real public pages.
2. **"AI drafts, humans decide"** — A consequential action (meeting follow-up send / case transition) visibly BLOCKS on human approval. This is the #1 diplomatic trust signal.
3. **"One governed picture"** — Unified Outcomes ties opportunity → stakeholder → diaspora expert → case metric in one frame.

## 2. Hero Narrative Thread (drives ALL seed data)
Dual sector: **Critical Minerals (lithium)** + **Education / Skilled Migration**.

> **§2 REVISION (2026-09-07, per OPEN_QUESTIONS Q-16/Q-17):** The original "refinery expansion" framing was factually unsupportable — as of Sept 2026 WA lithium *refining* is contracting (Kemerton in care-and-maintenance; Kwinana Phase 2 halted). Reframed below to a citable midstream/downstream + skills-gap narrative. The Nigeria↔Australia corridor is an **AI-proposed synthesis, not a reported fact** (Q-17) — it must render at lower confidence than the signals beneath it.

- **Signal (EVIDENCED, PUBLIC):** Australia is scaling lithium midstream/downstream (e.g. Covalent Mt Holland–Kwinana: refinery ramping to nameplate, concentrator doubling 380→760 ktpa) against a *documented resources-sector processing-skills gap* (Critical Minerals Strategy 2023–2030 ch.6; National Reconstruction Fund resources value-adding). Do NOT conflate concentrator vs refinery stages.
- **→ Opportunity (AI-PROPOSED, UNCONFIRMED):** "AU lithium value-chain skills partnership + Nigerian skilled-migration training corridor." `is_proposed_by_ai = true`; card shows a confidence badge + "AI-proposed, pending officer qualification". This contrast (hard evidence underneath, labelled AI proposal on top) IS winning moment #1's payoff.
- → Stakeholder: the AU operator + a Nigerian counterpart institution.
- → Meeting: Trade Officer preps a pre-read; follow-up email BLOCKS on approval.
- → Consular: a synthetic passport-renewal case for a Nigerian student in AU (ties migration thread).
- → Diaspora: search returns BOTH a lithium-processing engineer AND a migration-pathway academic.
- → Outcomes: all four metrics coexist on one board.

## 3. Config (LOCKED)
| Decision | Value |
|---|---|
| Web | Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui |
| API | Python FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic |
| DB | PostgreSQL + pgvector |
| AI | **Live Anthropic API** wrapped in AI Gateway with deterministic cached fallback (≤4s timeout) |
| Server state | TanStack Query, OpenAPI-generated client |
| Workflow | State machines in-code for demo (Temporal interface stubbed, deferred) |
| Hosting | Web → Vercel · API+DB → Railway |
| Auth | Demo identities (role picker), but RBAC + audit events REAL from day 1 |

## 4. AI Gateway Contract (build the seam in Week 1)
The AI Gateway is a first-class boundary (Arch §9). Application code NEVER calls Anthropic directly.
Every AI call passes: `purpose → classify → retrieve(authorized only) → route → generate(structured JSON) → post-check(citations) → trace`.

```
POST (internal) gateway.generate(purpose, data_class, context, user)
  → { result: <schema-validated JSON>, evidence_ids: [], trace_id, approval_status, model_route }
```
- **Fallback rule:** if live call errors OR >4s → return cached deterministic snapshot keyed by purpose+scenario. Log fallback in trace. Demo never blocks.
- Every AI endpoint returns `{ result, evidence, trace_id, approval_status }` — NEVER raw prose (Arch §16).

## 4a. Model-Routing Table (Q-12 RESOLVED 2026-09-07) — Gateway enforces, trace drawer displays
Sensitivity is the PRIMARY routing key; capability tier is secondary within each band. Trace drawer is a plain badge, not JSON (must be legible to a non-technical Ambassador).

| Data class | Route | Model tier | Retention | Tools | Trace drawer badge |
|---|---|---|---|---|---|
| PUBLIC | Approved external (Anthropic API) | Fast: classify/score · Strong: briefs/meeting-prep | No training retention | Read-only allowlist | `PUBLIC · external · [model] · fast\|strong` |
| MISSION-INTERNAL | Approved external, no-retention | Strong | No retention | Read-only | `INTERNAL · external-noret · [model]` |
| CONFIDENTIAL | Restricted (flagged "private-in-prod") | Strong | No retention + enhanced logging | Restricted set | `CONFIDENTIAL · restricted · [model] · enhanced-logging` |
| CONSULAR-SENSITIVE | **Sovereign/private route — in demo: refuse external, metadata-only** | Metadata classification only; NO narrative generation | No external call at all | None (classify only) | `CONSULAR-SENSITIVE · no external route · metadata-only · ⛔ generation withheld` |
| SECURITY/RESTRICTED | Never routed to any LLM | — | — | — | `RESTRICTED · blocked · no model path` |

- CONSULAR-SENSITIVE never leaves to an external model in the demo — this is the sharpest trust demonstration and aligns with the Q-06 metadata-only triage ruling.
- "Sovereign/private route" is simulated HONESTLY: the system genuinely refuses the external call + does metadata-only work; production swaps the simulated route for real region-controlled inference with nothing above it changing.

## 5. Data Zones & Classification (Arch §12) — enforce even in demo
`PUBLIC` | `MISSION-INTERNAL` | `CONFIDENTIAL` | `CONSULAR-SENSITIVE`
The Gateway's routing decision + the audit trail must be *visible* in the UI (a "trace" drawer). This is what makes a security-minded consular buyer trust it.

## 6. Non-Negotiable Controls (Blueprint §8) — must be DEMONSTRABLE
No autonomous: diplomatic comms, commitments, consular determinations, case closure, external outreach.
Each requires authenticated human + audit log. **The demo must SHOW one of these being blocked.**

## 7. Repo Structure (Arch §15 — follow exactly)
```
naddp/
  apps/web/            # Next.js staff + citizen
  apps/api/            # FastAPI
  packages/contracts/  # generated OpenAPI types
  data/demo-seed/      # synthetic seed + citation registry
  data/taxonomy/
  infra/               # Railway/Vercel config, IaC stubs
  evals/               # grounding + security eval stubs
  docs/architecture/adr/
```

## 8. Bounded Contexts (Arch §4) — module boundaries in FastAPI
Intelligence · Opportunities · Stakeholders · Meetings · Consular · Diaspora · Knowledge · Governance(audit/roles)

## 9. Workflow State Machines (Arch §10) — server-validated, audit on transition
- Opportunity: DETECTED→QUALIFIED→CONTACT_PLANNED→CONTACTED→MEETING→NEGOTIATION→PARTNERED/CLOSED
- Meeting follow-up: DRAFTED→OFFICER_REVIEW→APPROVED→SENT
- Consular case: NEW→TRIAGED→ASSIGNED→AWAITING_CITIZEN/IN_REVIEW→ESCALATED→RESOLVED→CLOSED

## 10. Seed Dataset Targets (Blueprint §18)
20–30 signals · 12–15 opportunities · 30 stakeholders · 15 cases · 40 diaspora profiles · 20 knowledge articles · 10 meetings · believable audit history. Every intelligence item has a REAL public citation URL.

## 11. Demo Safety Rails (Blueprint §20 acceptance)
- Visible DEMO / SYNTHETIC badge on every screen.
- No real citizen data, passport numbers, private emails.
- `make demo-reset` restores clean state.
- All key flows ≤3 clicks from dashboard.
- No secrets in client bundle.
- Works on laptop + 1080p.

## 12. Week Plan (RE-SEQUENCED — Gateway pulled forward)
- **W1 Foundation:** repo, CI, DB schema+migrations, seed, design system, role auth stub, command-center shell, **audit framework**, **AI Gateway INTERFACE + fallback harness (stub responses)**.
- **W2 Intelligence:** briefs, signals, opportunities, stakeholder 360, pipeline, evidence retrieval + pgvector. Gateway → live for briefs.
- **W3 AI + Citizen:** meeting copilot (approval-blocked), consular dashboard + case state machine, grounded knowledge answers. Gateway → live for all.
- **W4 Unified + Harden:** diaspora search, outcomes board, demo-reset, cache/fallback pass, security pass, trace drawer, rehearsal.

## 13. Definition of Done (demo)
10-min narrative end-to-end · no real private data · every AI claim has evidence · consequential actions visibly require approval · demo resets · fallbacks exist · responsive at 1080p + laptop.
