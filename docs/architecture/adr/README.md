# Architecture Decision Records

Decisions that shape NADDP's structure, security posture, or operational envelope are recorded here.
An ADR is written when a decision is **hard to reverse**, **surprising to a newcomer**, or **load-bearing
for the demo's trust story**. Routine choices (a helper's name, a component's layout) do not get one.

Format: MADR-flavoured — see [`0000-adr-template.md`](0000-adr-template.md). Copy it, take the next
free number, and never renumber or rewrite an accepted ADR. Decisions change by **superseding**:
the old ADR's Status becomes `Superseded by ADR-XXXX` and the new one explains what changed and why.

Every ADR carries an **Enforcement** section. A decision that cannot be detected when violated is a
preference, not an architecture decision.

## Index

| # | Title | Status | Date | Tags |
|---|---|---|---|---|
| [0000](0000-adr-template.md) | ADR template | — | — | `meta` |
| [0001](0001-ai-gateway-as-security-boundary.md) | AI Gateway as the single security boundary for model access | Accepted | 2026-09-06 | `ai` `security` |
| [0002](0002-deterministic-fallback-harness.md) | Deterministic fallback harness for every AI call | Accepted | 2026-09-06 | `ai` `ops` |
| [0003](0003-rbac-real-identity-faked.md) | Real RBAC, faked identity | Accepted | 2026-09-06 | `security` `governance` |
| [0004](0004-append-only-audit-events.md) | Append-only `audit_events` | Accepted | 2026-09-06 | `governance` `data` |
| [0005](0005-sync-sqlalchemy-and-uv.md) | Synchronous SQLAlchemy + psycopg 3, and uv over Poetry | Accepted | 2026-09-06 | `platform` |
| [0006](0006-classification-zones.md) | Four classification zones with propagation to derived content | Accepted | 2026-09-06 | `security` `data` `ai` |
| [0007](0007-ulid-primary-keys-and-public-refs.md) | ULID primary keys, independent random public references | Accepted | 2026-09-06 | `data` `security` |

## Reading order for a newcomer

1. **0003** and **0006** — who may see what. Everything else assumes these.
2. **0001** — where AI is allowed to touch the system at all.
3. **0002** — why the demo cannot dead-end.
4. **0004** — how we prove any of it happened.
5. **0005** and **0007** — the platform and identifier choices you will meet in every file.
