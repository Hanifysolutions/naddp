# ADR-0004: Append-only `audit_events`

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Solutions Architect, Lead Engineer
- **Consulted:** Consular stakeholder (demo buyer)
- **Informed:** All contributors
- **Tags:** `governance` `data` `security`

## Context

`CLAUDE.md` §2.3 is unconditional: *every consequential state transition writes an append-only
`audit_events` row. No exceptions.* `BUILD_BIBLE.md` §6 requires that the controls which may never be
autonomous — diplomatic communications, commitments, consular determinations, case closure, external
outreach — each leave an authenticated human plus an audit log behind them.

The forces:

- **An audit log that can be edited is not evidence.** Its entire value is that it constrains the
  people who operate the system, including the people who built it. If a developer, an administrator,
  or a compromised application account can rewrite history, the log answers no question that matters.
- **Consular decisions carry real consequences for real people.** "Who decided this, on what basis,
  and when" must be answerable months later, by someone who does not trust the current operator.
- **The demo has to make this visible.** A believable, lived-in audit timeline is part of the
  Week 1 seed (`PROMPT_W1_foundation.md` Phase 5), and the trace drawer surfaces it.
- **Append-only is cheap to adopt now and expensive to retrofit.** Once code exists that updates audit
  rows — even to "fix a typo" or backfill a column — the property is gone and cannot be recovered for
  historical rows.

## Decision

`audit_events` is **append-only**: `INSERT` only. No `UPDATE`, no `DELETE`, no `TRUNCATE`, from any
code path, at any privilege level available to the application.

### What is recorded

One row per event, with at minimum:

| Column | Notes |
|---|---|
| `id` | ULID-in-UUID (ADR-0007). Time-sortable, so the log has a total order that does not depend on a clock column. |
| `occurred_at` | `timestamptz`, server clock, set by the database default — not by the caller. |
| `actor_id` | The `Principal` the request resolved to. Nullable **only** for system/seed actors, which use a reserved sentinel actor rather than `NULL`. |
| `actor_role` | Denormalised deliberately. Roles change; the log must say what the actor's role *was at the time*, and must not become wrong when a role assignment is later edited. |
| `action` | Verb from a closed vocabulary, `snake_case`, past-tense-neutral (`opportunity.transitioned`, `case.closed`, `export.performed`, `session.role_assumed`, `ai.generated`). Enumerated in `app/audit/actions.py`. |
| `object_type` | Bounded-context-qualified entity name (`opportunities.opportunity`, `consular.case`). |
| `object_id` | The affected row's ULID. |
| `policy_result` | `ALLOW` \| `DENY`. Denials are recorded, not only successes (ADR-0003). |
| `request_id` | Correlates every row emitted by one HTTP request, and links to the structlog request log and to `ai_traces.trace_id` where an AI call was involved. |
| `detail` | `jsonb`. Structured, bounded, non-sensitive context: the from/to states of a transition, the row count and filter of an export, the reason for a denial. |

Explicitly **not** recorded: request bodies, document contents, consular case narrative, personal
identifiers beyond the internal IDs. The audit log must not become a second, less protected copy of
the sensitive data it describes. `detail` carries references, not payloads.

### What triggers a row

Middleware in `app/audit/` records automatically:

- authentication events (`session.role_assumed`),
- read access to privileged objects (CONFIDENTIAL and CONSULAR_SENSITIVE),
- every export,
- every denial.

Services record explicitly, in the same transaction as the state change:

- every workflow transition in the three state machines (`docs/workflows.md`),
- every approval and rejection of a consequential action,
- every AI generation that produces a persisted artefact.

**The audit write is inside the business transaction.** If the transition commits, the audit row
commits; if the audit write fails, the transition rolls back. An audit log written on a best-effort
basis after commit is exactly as reliable as the code path that was supposed to write it — which is
to say, not.

### How append-only is enforced

Defence in depth, weakest layer last:

1. **Database privileges (primary).** The application connects as a role with
   `INSERT, SELECT` on `audit_events` and nothing else:

   ```sql
   REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM naddp_app;
   GRANT  INSERT, SELECT           ON audit_events TO   naddp_app;
   ```

   Granted in the Alembic migration that creates the table, so it is part of the schema and cannot be
   forgotten in a fresh environment.

2. **A `BEFORE UPDATE OR DELETE` trigger (belt and braces).** Because the demo runs locally as an
   owner-privileged user for convenience, and because a superuser bypasses grants, a trigger raises
   unconditionally:

   ```sql
   CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
   BEGIN
     RAISE EXCEPTION 'audit_events is append-only (attempted %)', TG_OP;
   END;
   $$ LANGUAGE plpgsql;

   CREATE TRIGGER audit_events_no_mutate
     BEFORE UPDATE OR DELETE ON audit_events
     FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
   ```

   This is the layer that actually fires in local development and in CI, and therefore the one that
   catches a mistake before it ships.

3. **ORM level.** The `AuditEvent` model is mapped read-only for mutation purposes: all columns
   `Mapped[...]` without setters exposed by the writer API, and the only supported write path is
   `app/audit/writer.py::record(...)`, which issues an `INSERT`. A SQLAlchemy `before_flush` event
   listener inspects `session.dirty` and `session.deleted` and raises if any `AuditEvent` appears in
   either. This gives a clear Python-level error at the point of the bug rather than a database error
   at flush time.

4. **`demo-reset` is the sanctioned exception, and it is total.** Resetting the demo drops and
   recreates the schema; it does not delete audit rows selectively. There is no "clean up some audit
   entries" capability, because the existence of one would defeat the property. `demo-reset` is
   guarded to refuse to run when `DEMO_MODE` is not true.

### Retention and correction

Corrections are made by **appending**, never by editing. A mistaken event is followed by a
compensating event (`*.corrected`) whose `detail` references the earlier row's `id`. The reader — the
audit query service in `app/audit/` — is responsible for presenting a corrected timeline; the storage
layer stays dumb and immutable.

## Consequences

### Positive

- The log is evidence, not a report. It answers questions asked by someone who does not trust us.
- The seeded audit history can be presented honestly as "this is the same table the live system
  writes to", because it is.
- Denials being recorded makes the RBAC story demonstrable rather than assertable (ADR-0003).
- Same-transaction writes mean the log cannot silently diverge from application state.

### Negative / costs

- The table only grows. In production this needs partitioning by month and an archival policy to cold
  storage; for the demo, volume is trivial and this is deferred. Deferring it is a known debt, noted
  here so it is not a surprise.
- No fixups. A wrong `action` name or a badly shaped `detail` is permanent, so the action vocabulary
  and `detail` shapes deserve review attention before they are used.
- Audit-in-transaction couples the audit table's availability to the write path: if `audit_events` is
  unwritable, consequential writes fail. That is the correct trade — a state change we cannot record
  is a state change we should not make — but it must be a conscious one.
- The trigger fires per row, adding a small cost to bulk seeding. Negligible at demo volumes.

### Neutral / follow-on work

- Pilot backlog: monthly partitioning, an off-box append-only sink (WORM object storage or a log
  service), and periodic hash-chaining if tamper-*evidence* beyond DB privileges is required.
- The audit query service powers both the trace drawer and any future compliance export — which is
  itself gated by `bulk_export`.

## Alternatives considered

### A mutable table with an `updated_at` column and discipline

The default in most codebases, and it degrades immediately. Discipline is not a control, and the
absence of a mechanism is invisible until the moment it matters.

### Soft delete (`deleted_at`) on audit rows

Rejected outright. A soft-deleted audit row is a hidden audit row. "Deleted by whom, and why" would
itself need auditing, recursively.

### Event-sourcing the whole domain

Genuinely appealing, and far too large. Event sourcing every aggregate would restructure the entire
data model and blow the four-week budget, to obtain immutability where we need it only for the audit
log. Append-only for one table gets the property at a fraction of the cost.

### Application-level logging only (structlog to stdout / a log aggregator)

Useful, and complementary — we do this too, keyed by `request_id`. Insufficient alone: log shipping is
lossy, logs are not transactional with the database, and a log line cannot be joined to the row it
describes. The audit table is the record of decisions; the log stream is the record of execution.

### Write-ahead / trigger-based shadow audit of every table

Captures more, and captures the wrong thing. A row-diff trigger records *what bytes changed*, not
*what a person decided*. `opportunity.transitioned QUALIFIED → CONTACT_PLANNED by DEPUTY` is the
answerable unit; a diff of four columns is not.

## Enforcement

| Mechanism | Status |
|---|---|
| DB grants in the creating migration: `REVOKE UPDATE, DELETE, TRUNCATE`. | Week 1 |
| `BEFORE UPDATE OR DELETE` trigger raising unconditionally. | Week 1 |
| SQLAlchemy `before_flush` listener rejecting dirty/deleted `AuditEvent` instances. | Week 1 |
| Test: attempting an `UPDATE` and a `DELETE` on `audit_events` both raise. | Week 1 |
| Test: an opportunity stage transition writes exactly one audit row with the correct `action`, `object_type`, `object_id` and `policy_result` (VERIFY item 6). | Week 1 |
| Test: a rolled-back transition leaves no audit row (same-transaction property). | Week 1 |
| Test: `action` values used by services are all members of the closed vocabulary. | Week 1 |

## References

- `BUILD_BIBLE.md` §6 (non-negotiable controls), §10 (believable audit history)
- `CLAUDE.md` §2.3
- `PROMPT_W1_foundation.md` Phase 3, VERIFY item 6
- `docs/workflows.md` (the transitions that must emit audit rows)
- ADR-0003 (RBAC, `policy_result`), ADR-0007 (ULID ordering)
