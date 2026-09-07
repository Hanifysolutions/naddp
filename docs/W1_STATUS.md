# Week 1 — Foundation · Status

**Date:** 2026-09-07 · **Branch:** `week1-foundation` · **Scope:** `PROMPT_W1_foundation.md` P1–P6
**Verdict:** All six phases complete. All eight VERIFY items pass. Week 2 not started.

---

## 1. Phase status

| Phase | Status | What landed |
|---|---|---|
| P1 Monorepo & tooling | ✅ | pnpm workspace + uv, Postgres 16/pgvector on host **5433**, CI, `Makefile` **and** `make.ps1` |
| P2 DB schema & migration | ✅ | 26 tables, one Alembic migration, pgvector + HNSW, append-only enforced in the database |
| P3 Governance core | ✅ | 40 permissions, deny-by-default RBAC, role-picker session, append-only audit + middleware |
| P4 AI Gateway | ✅ | 9-stage pipeline, generation stubbed, deterministic fallback harness, 16 snapshots |
| P5a Citation registry | ✅ | 163 citations, **138 independently HTTP-verified** |
| P5b Seed | ✅ | All BUILD_BIBLE §10 volumes met, 450 audit rows, deterministic and re-runnable |
| P6 Command centre | ✅ | Six role-scoped tiles on real seed reads, DEMO badge, generated OpenAPI client |

---

## 2. Row counts vs BUILD_BIBLE §10

Observed by direct query after `make demo-reset`.

| Table | Count | §10 target | |
|---|---:|---|:--:|
| `signals` | 29 | 20–30 | ✅ |
| `opportunities` | 13 | 12–15 | ✅ |
| `stakeholders` | 30 | 30 | ✅ |
| `cases` | 15 | 15 | ✅ |
| `diaspora_profiles` | 40 | 40 | ✅ |
| `knowledge_articles` | 20 | 20 | ✅ |
| `meetings` | 10 | 10 | ✅ |
| `audit_events` | 450 | "believable history" (Q-13) | ✅ |

Supporting tables: `sources` 64 · `documents` 138 · `organisations` 30 · `interactions` 66 ·
`actions` 20 · `case_events` 54 · `case_evidence` 12 · `briefs` 5 · `brief_items` 20 ·
`expertise_tags` 44 · `diaspora_expertise` 49 · `meeting_attendees` 18 · `ai_traces` 10 ·
`users` 6 · `roles` 6 · `permissions` 40 · `role_permissions` 134 · `user_roles` 6.

**Q-13 verified by query:** 450 rows over a **56-day** trailing window anchored to `now()`,
**5.3% denials** (24/456 including live writes), **all six roles** represented, chain intact.

---

## 3. VERIFY block results

| # | Item | Result |
|---|---|---|
| 1 | `make dev` boots web + api + db | ✅ via `.\make.ps1 dev` (GNU make absent on Windows). **One real blocker found and fixed** — see §5. |
| 2 | `make seed` populates all tables | ✅ all 7 §10 targets met; loader exits non-zero if one is missed |
| 3 | `make demo-reset` restores clean state | ✅ run twice, byte-identical counts after genuine mutation |
| 4 | `/v1/command/today` role-scoped | ✅ TRADE_OFFICER sees 5 tiles, consular `null`; CONSULAR_OFFICER sees consular + meetings only |
| 5 | Gateway fallback with live path disabled | ✅ `fallback=true`, `reason=LIVE_DISABLED`, 9 stages traced, byte-identical across two calls |
| 6 | Stage transition writes an audit row | ✅ `opportunity.contact_planned`, actor TRADE_OFFICER, `ALLOW`; a guard refusal also wrote a `DENY` row |
| 7 | `make test` green, CI valid | ✅ **838 tests pass**; ruff, ruff-format and mypy-strict all clean |
| 8 | `/command` at 1080p | ✅ HTTP 200, DEMO + SYNTHETIC badges and all six tile titles present; production build clean |

**Standing invariants re-checked:** `import anthropic` appears **only** in `app/ai/gateway.py:348`
(lazy, inside the live branch) · no secrets in `apps/web` beyond prohibition comments ·
anonymous `GET /v1/opportunities` → **403** · every seeded citation is `VERIFIED` ·
no seeded text or snapshot claims an Australian lithium **refinery** is expanding.

---

## 4. `TODO_VERIFY` — needs a human before rehearsal

### 4.1 Citations (25 of 163 not usable as-is)

**None of these is referenced by any seeded row.** Sources and documents are built only from
`VERIFIED` entries, so the demo cannot reach one. They are research output, not demo content.

**23 × `TODO_VERIFY` — unreachable from the build network, *not* dead links.** TCP 443 opens but
the TLS handshake is silently dropped, for every HTTP version and user-agent tried, while a sibling
origin (`abs.gov.au`) verifies normally. Almost certainly live. **Open each in a browser on the
demo machine before rehearsal.**

| Count | Host |
|---:|---|
| 9 | `www.industry.gov.au` |
| 8 | `www.jobsandskills.gov.au` |
| 2 | `www.dfat.gov.au` |
| 1 | `www.education.gov.au` |
| 1 | `www.migrationdataportal.org` |
| 2 | `nipc.gov.ng` / `www.nipc.gov.ng` (403 to automated clients; likely fine in a browser) |

**2 × `DO_NOT_CITE` — genuinely defective.** `ngsa-geological-survey-agency` and
`ngsa-lithium-corridor-map` (Nigerian Geological Survey Agency) have an **expired TLS certificate**.
A browser shows a full-page security interstitial *before* the content — worse in front of an
Ambassador than a missing citation. Excluded permanently unless the site is fixed and re-verified.

### 4.2 Open questions

**12 remain open** in `docs/OPEN_QUESTIONS.md`; **7 resolved**. None blocks Week 1.

- **Q-12 (model-routing table)** — left open *by architect decision*; delivered in the Week 2→3
  handoff before the Gateway goes live.
- **Q-06 (may `CONSULAR_SENSITIVE` reach a third-party model?)** — still open. Week 1 implements the
  **most conservative reading**: `consular_triage` accepts case metadata only and refuses narrative
  in code, recording the refusal in the trace. Reversible if the architect rules otherwise.
- **Q-01 (exact tile metrics)** — the six tiles use obviously-defensible counts pending a ruling.
- **Q-02b (role→permission matrix)** — **RESOLVED 2026-09-07.** Architect confirmed the matrix with
  one change: `read:ai_trace` removed from `ADMIN`, because a trace discloses the substance of the
  call and would otherwise be a side-channel around ADMIN's lack of any content read. Now
  **40 permissions / 134 grants**; ADMIN holds four.

---

## 5. Defects found and fixed during Week 1

Recorded because each was a real failure, not a style preference.

1. **`make.ps1 dev` never started the web server** and then tore down the API it had just started
   (`%1 is not a valid Win32 application` — pnpm's extensionless shim wins PATH order for
   `Start-Process`). This is the *only* way to run VERIFY item 1 on Windows. Fixed and re-proven.
2. **`morning_brief` had no fallback snapshots at all.** Six of seven purposes had them; the seventh
   is winning moment #1. Four written, citing only `VERIFIED` sources.
3. **Case closure had no human attribution.** BUILD_BIBLE §6 names closure a never-autonomous act;
   four of the five §6 controls had database-level attribution and closure had none. Added
   `closed_by_user_id` + `close_reason` + a CHECK constraint, observed rejecting a violating row.
4. **The audit privilege layer did not exist.** ADR-0004 calls table grants the *primary* control and
   the migration had zero. Added, with a non-superuser `naddp_app` role holding only `SELECT, INSERT`;
   a denied `UPDATE` is now refused before the trigger runs.
5. **`app.core.config` raised `IndexError` at import in the container layout** (fixed `parents[4]`).
   Repo-root discovery now walks up for a marker directory.
6. **Three cross-track blockers from the P1 scaffold audit**: `.gitignore` excluded a load-bearing
   committed placeholder; `gen-client` wrote to a filename nothing imported; two incompatible
   permission-naming schemes shipped in parallel, which would have left every nav rail empty.
7. **ESLint's `@typescript-eslint` rules were silently not loading** — transitive under pnpm's strict
   layout, so every rule in the TS override was inert.

---

## 6. Known limitations — carried into Week 2+

1. **The audit hash chain can fork under concurrent writes.** *This is an enhancement beyond the
   prompt's requirement, not a required control.* Appends take a bounded, non-blocking advisory
   lock; a burst that exceeds the budget writes unlocked and forks, which `verify_chain` reports as
   a break. It held intact over 456 rows including live API writes in verification, so realistic
   demo usage is fine — but **run `make demo-reset` before rehearsal** if the trace drawer surfaces
   "chain intact" as a panel. *Proper fix:* a unique index on `prev_event_hash` plus retry, making a
   fork structurally impossible. The append-only guarantee itself is solid and independently proven
   (triggers reject UPDATE/DELETE/TRUNCATE; privileges deny the app role).
2. **The API still connects as the table owner locally.** The privilege layer therefore does not bind
   in the local demo — the trigger is what stops mutation there. Switching `DATABASE_URL` to
   `naddp_app` (Alembic continuing as owner) is a Week 4 deploy task.
3. **Two `net::ERR_ABORTED` fetches on `/command` in dev only** — React 18 StrictMode double-mounting
   aborts the first effect's request; the retry succeeds and every tile renders correctly. Absent in
   the production build.
4. **`make lint` does not cover `data/demo-seed`.** A matching `ruff.toml` exists there and ruff +
   mypy-strict were run over it by hand (clean); widening the Makefile target is a small follow-up.
5. **Embeddings have no provider yet (Q-07).** `vector(1536)` columns and HNSW indexes exist and are
   empty, which is correct for Week 1; Week 2 retrieval needs the provider decision.

---

## 7. How to run it

```bash
docker compose up -d db          # Postgres 16 + pgvector on host port 5433
.\make.ps1 install               # or: make install
.\make.ps1 demo-reset            # drop, migrate, seed  -> clean demo state
.\make.ps1 dev                   # api :8000 + web :3000
```

Then open <http://localhost:3000/command> and pick a role.
`make` targets and `make.ps1` targets are identical by design — GNU make is not installed on the
Windows build machine, and the demo has to run there.
