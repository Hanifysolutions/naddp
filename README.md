# NADDP — Nigeria-Australia Digital Diplomacy Platform

NADDP is the Ambassador demonstration of a governed diplomatic-intelligence platform for the
Nigeria-Australia bilateral relationship: signals to opportunities to stakeholders to meetings,
consular cases and diaspora capability, all under real RBAC and an append-only audit trail.
This repository is the **demo vertical slice** — it shares production's domain model and API shape,
with production-only complexity deferred. Nothing here is throwaway.

> [!WARNING]
> ## DEMO / SYNTHETIC DATA ONLY
> Every record in this system is **synthetic**. There is **no real citizen data** here — no real
> passport numbers, no private email addresses, no genuine consular cases. Identities are faked by a
> role picker; authorisation and audit are real. Every screen carries a visible DEMO / SYNTHETIC
> badge. Do not load real personal data into this repository or any environment built from it.

## Prime directive

**The demo must be physically incapable of dead-ending in front of the Ambassador.** Every AI call
has a deterministic cached fallback behind a 4-second timeout, every citation resolves to a real
public source, and every screen has seed data. `make demo-reset` restores a clean seeded state.

Three moments carry the demo:

1. **"Not a chatbot"** — a morning brief whose citations open real public pages.
2. **"AI drafts, humans decide"** — a consequential action visibly **blocks** on human approval.
3. **"One governed picture"** — opportunity, stakeholder, diaspora expert and case metric in one frame.

## Prerequisites

| Tool | Version |
| --- | --- |
| Docker Desktop (or Docker Engine + Compose v2) | current |
| Node | 22 |
| pnpm | 9.15.4 |
| uv | current |
| Python | 3.12 |

## Quickstart

```bash
cp .env.example .env      # then fill in ANTHROPIC_API_KEY and DEMO_SESSION_SECRET
```

**Linux / macOS / CI** — GNU make:

```bash
make install      # pnpm workspace + uv project
make dev          # db (5433) + api (8000) + web (3000)
make seed         # load the synthetic dataset
make demo-reset   # drop schema, migrate, re-seed
make test         # pytest + web lint/typecheck
make help         # every target, self-documented
```

**Windows** — `make.ps1`, a PowerShell 7 shim with the identical targets (GNU make is not installed
on Windows; the shim delegates to the same underlying commands as the Makefile):

```powershell
.\make.ps1 install
.\make.ps1 dev
.\make.ps1 seed
.\make.ps1 demo-reset
.\make.ps1 test
.\make.ps1 help
```

Targets are identical in both: `help`, `install`, `db-up`, `db-down`, `migrate`, `dev`, `seed`,
`demo-reset`, `test`, `lint`, `typecheck`, `gen-client`, `clean`. If you change a recipe in the
`Makefile`, change `make.ps1` in the same commit.

## Ports

| Service | URL / port | Notes |
| --- | --- | --- |
| Web (Next.js 14) | http://localhost:3000 | staff + citizen surfaces |
| API (FastAPI) | http://localhost:8000 | routes under `/v1`; OpenAPI at `/openapi.json` |
| Postgres (pgvector) | localhost:**5433** | container listens on 5432; 5433 avoids a host conflict |

## Layout

```
apps/web/            Next.js 14 App Router (staff + citizen)
apps/api/            FastAPI — core, domain, models, schemas, services, api/v1, security, audit, ai
packages/contracts/  Generated OpenAPI types + typed client (make gen-client)
data/demo-seed/      Synthetic seed + citation registry
data/taxonomy/       Controlled vocabularies
infra/               Postgres init SQL, Railway/Vercel config, IaC stubs
evals/               Grounding + security eval stubs
docs/architecture/   ADRs and architecture notes
storage/             Local object storage for demo documents (git-ignored)
```

## Security posture

- The **AI Gateway is a security boundary**: only `apps/api/app/ai/gateway.py` may import the
  Anthropic SDK. Every AI response is `{ result, evidence, trace_id, approval_status }` — never raw prose.
- **RBAC is deny-by-default** from day one across the six roles (AMBASSADOR, DEPUTY, TRADE_OFFICER,
  CONSULAR_OFFICER, DIASPORA_OFFICER, ADMIN) and four classifications (PUBLIC, MISSION_INTERNAL,
  CONFIDENTIAL, CONSULAR_SENSITIVE).
- **Every consequential state transition writes an append-only `audit_events` row.**
- **No secrets in the client bundle.** Only `NEXT_PUBLIC_*` variables reach the browser;
  `ANTHROPIC_API_KEY` and `DEMO_SESSION_SECRET` are server-side only. See `.env.example`.

## Further reading

- **[BUILD_BIBLE.md](./BUILD_BIBLE.md)** — the binding contract: prime directive, config, AI Gateway
  contract, data classification, repo structure, state machines, seed targets, definition of done.
- **[ROADMAP.md](./ROADMAP.md)** — where we are and what is next, week by week.
- **[CLAUDE.md](./CLAUDE.md)** — operating rules for anyone (human or agent) working in this repo.
