# Week 2 — Diplomatic Intelligence · Status

**Date:** 2026-09-07 · **Branch:** `week2-intelligence` · **Scope:** `WEEK2_PROMPTS.md` 2.1–2.4
**Verdict:** All four prompts complete, plus two architect-ordered interim steps. Every VERIFY
item printed and passing. Week 3 not started.

**Exit condition (ROADMAP):** *every intelligence item has resolving evidence; brief is grounded;
scoring is explainable + editable.* **Met.** Evidence resolves through the citation registry on
every surface that cites; the morning brief cites only `VERIFIED` ids; the score reconstructs from
its own breakdown and an authorised officer can move a factor, with both numbers kept.

---

## 1. What landed

| Prompt | Commit | What it delivered |
|---|---|---|
| W2.1 Retrieval | `368468f` | Canonical ingestion (normalise → hash → chunk → embed), hybrid FTS + pgvector search with RRF, **authorisation composed into the ranking query itself**, separate logical collections, knowledge audience/validity/approval gates |
| W2.2.0 Audiences | `0858196` | Real knowledge audiences and a non-vacuous audience-filter test |
| W2.2 Brief + Gateway | `7d3e885` | Gateway LIVE for `PUBLIC` and `MISSION_INTERNAL` only, §4a routing recorded on every trace, Morning Brief with grounded items and DRAFT→REVIEW→APPROVED→PUBLISHED |
| W2.3.0 Budgets | `2a67e5a` | Per-purpose Gateway budgets (4s light / 25s heavy), streaming progress, `make demo-prewarm`, `docs/demo-runbook.md` |
| W2.3 Radar + scoring | `e378a85` | Opportunity Radar (proposes, never creates), seven-factor explainable score with two ceilings, audited officer overrides, Q-04 revert |
| W2.4 Pipeline + 360 | `3a95116` | Kanban + table over the state machine, Stakeholder 360 dossiers, both wired into the command tiles |

**Tests:** 950 API tests pass in fixed and randomised order. `ruff`, `mypy --strict`, `next lint`
and `next build` all clean. Web builds seven routes, three of them new.

---

## 2. VERIFY results, by prompt

### W2.1 — hybrid retrieval
Ingestion produced **325 chunks / 325 vectors** over 142 documents and 20 articles. Retrieval
authorisation is part of the `WHERE` clause that ranks, not a pass over the results, so a withheld
row cannot be counted or hinted at by a total.

### W2.2 — morning brief and live Gateway
Brief generated for three roles with every item citing resolving evidence. Routing recorded on
`ai_traces` as `PUBLIC · external · claude-sonnet-5 · strong`. `CONSULAR_SENSITIVE` and
`RESTRICTED` remain the Week-1 refuse/metadata-only stubs, deliberately.

### W2.3 — radar, scoring, revert
Radar sweep for a `TRADE_OFFICER`: 28 signals visible, 2 dismissed, 5 candidate clusters, nothing
written. Hero score **71/100 HIGH, confidence 0.67** against supporting signals of 0.72–0.97 — the
AI-proposal ceiling binds in both directions (Q-17). Officer override applied, audited and visible;
`revert` allowed for `DEPUTY`, refused and logged for `TRADE_OFFICER` (Q-04).

### W2.4 — pipeline and Stakeholder 360

All eight sections run over the HTTP API rather than the services underneath it, because the demo
does.

| # | Check | Result |
|---|---|---|
| 1 | Board, authorised in SQL | `TRADE_OFFICER` 12 opportunities / A$24.9m; `AMBASSADOR` 13. Negotiation column reads **0** for the officer rather than 1-with-no-card |
| 2 | Covalent dossier | Reaches the hero opportunity as `lead`; 9 interactions, 4 tied to the hero; 2 people; **12 sources, all VERIFIED, all real URLs** |
| 3 | Nigerian counterpart | NIMG reaches the same opportunity via `interaction`; both ends of the corridor on one opportunity |
| 4 | Illegal transition | `partner` from `MEETING` → **409 `invalid_transition`**, *"'partner' is not legal from MEETING"*, stage unmoved, `DENY` row written. Fired as `AMBASSADOR`, who **holds** `commit:opportunity` — so the refusal is the machine, not the permission |
| 5 | Advance one stage | `MEETING → NEGOTIATION`, **200**, `opportunity.negotiation_opened` `ALLOW` row carrying the reason; classification raised to `CONFIDENTIAL` on entry |
| 6 | The consequence | The officer's own advance removed the row from their board (12 → 11) and from the dossier (1 → 0) — and the dossier **says so**: `withheld_opportunities = 1`. The `DEPUTY` still sees it |
| 7 | `commit:opportunity` | `TRADE_OFFICER` → **403 `permission_denied`**, `missing_permissions: ['commit:opportunity']`, `DENY` row written. `AMBASSADOR` and `DEPUTY` are offered `partner` on the same card |
| 8 | Command tiles | Opportunity Health: 11 open of 13, 3 overdue, **A$31.3m**, 1 AI-proposed pending. Relationship Health: 30 contacts across 30 organisations, 4 never contacted, 1 dormant |

Section 6 is the one worth watching on stage. It is not a scripted denial — an officer takes a
legitimate action they are fully entitled to take, and the classification rule removes the result
from their own view.

---

## 3. Seed changes this week

| Change | Why |
|---|---|
| 4 mission-authored internal documents (2 `MISSION_INTERNAL`, 2 `CONSULAR_SENSITIVE`) | The retrieval leakage test was vacuous without a non-public corpus to leak |
| Real knowledge audiences | Same reason, for the audience filter |
| 7 hand-written hero interactions carrying the hero opportunity's id | The generated contact history cannot produce a *narrative*, and without one the Stakeholder 360 dossier opens on a timeline unrelated to the opportunity beside it |
| Interaction classification propagates from the stakeholder (ADR-0006) | Interactions with the `CONFIDENTIAL` counterpart were seeded one rank below the relationship they record. Fixing it also made the dossier's clearance filter non-vacuous — two tests had been skipping themselves |

Counts after `make demo-reset`: interactions **73** (was 66), everything else unchanged and still
inside the §10 targets.

---

## 4. Defects found and fixed

Only the ones a reader would want to know about; the full list is in the commit messages.

1. **ADR-0001 violation in my own W2.1 services** — they imported `app.ai`. Fixed the architecture,
   not the test: an `EmbedFn` port bound in `app/cli/ingest.py`.
2. **Alembic autogenerate silently DROPPED the three hand-written opclass indexes** (HNSW, FTS,
   trgm). Applied as generated, it degraded retrieval to sequential scans without failing anything.
3. **`brief_items.confidence` scale mismatch** — the column documents 0–100, the generator wrote
   0–1.
4. **Hero score 83 out-scored its weakest signal (72).** A synthesis may never read as more certain
   than its own evidence, so the AI-proposal ceiling now applies to the score, not only the
   confidence. 71 against 72.
5. **`rescore_all` silently erased officer overrides.** The override lived only in the in-memory
   call, so a routine recompute reverted the score to the machine's view *while the audit trail
   still said an officer had moved it*. Overrides now rehydrate from the stored breakdown.
6. **A second override recorded the first officer's number as the machine's** — provenance became
   fiction after two edits.
7. **`score_rationale` had two readers with different ideas of its shape.** The seed writes the
   pre-W2.3 list, the scorer writes the W2.3 object, and W2.4's new readers understood only the
   object — so every freshly seeded card would have shown zero evidence while the opportunity
   detail showed eleven. One defensive reader now, three callers.
8. **`gen-client` probed `localhost` while uvicorn bound `127.0.0.1`.** On Windows `localhost`
   resolves to `::1` first, so the target failed while the server it had just started sat there
   answering.

---

## 5. `TODO_VERIFY` — needs a human before rehearsal

### 5.1 Citations — unchanged from Week 1

**163 citations · 138 `VERIFIED` · 23 `TODO_VERIFY` · 2 `DO_NOT_CITE`.** No seeded row references a
non-`VERIFIED` entry, so the demo cannot reach one.

The 23 are **unreachable from this build network, not dead**: TCP 443 opens and the TLS handshake is
silently dropped, for every HTTP version and user-agent tried, while sibling origins verify normally.
Hosts: `www.industry.gov.au` (9), `www.jobsandskills.gov.au` (8), `www.dfat.gov.au` (2),
`www.education.gov.au` (1), `www.migrationdataportal.org` (1), `nipc.gov.ng` (2).
**Open each in a browser on the demo machine before rehearsal.**

The 2 `DO_NOT_CITE` (`ngsa.gov.ng`) have an **expired TLS certificate** — a browser shows a
full-page security interstitial before the content, which is worse in front of an Ambassador than a
missing citation. Excluded permanently unless the site is fixed.

### 5.2 Sourcing correction carried in BUILD_BIBLE §2

**Never conflate the concentrator with the refinery.** No public source states an Australian lithium
*refinery* is expanding — as of September 2026 WA refining is contracting (Albemarle Kemerton in
care and maintenance, Tianqi/IGO Kwinana Phase 2 halted). The true position is Covalent Mt Holland–
Kwinana: **refinery ramping toward nameplate, concentrator expanding 380 → 760 ktpa**. The reframe
is architect-approved (Q-16) and §2 carries the warning explicitly.

### 5.3 The hero corridor is a proposition, not a report

No public source connects any Australian lithium operator to Nigeria. This is deliberate and is the
cleanest demonstration in the build of *AI drafts, humans decide* — but it must be **said out loud
on stage**, not left to a badge. Q-17 is resolved on exactly that basis: the opportunity carries
`is_proposed_by_ai`, reads at lower confidence than its own signals, and both the board and the
command tile say "pending officer qualification".

---

## 6. Known limitations carried into Week 3

1. **The audit hash chain can fork under concurrent writes.** Documented in `W1_STATUS.md` §6.1
   with the proper fix (unique index on `prev_event_hash` plus retry). The bounded
   `pg_try_advisory_xact_lock` in place today narrows the window; it does not close it. Reset before
   a rehearsal that will surface "audit chain intact".
2. **`AI_GATEWAY_LIVE` is false.** A key is present in `.env` (gitignored) but nothing calls out.
   The live path is proven by SDK substitution rather than by a real call, so the first genuinely
   live generation will happen in Week 3 — budget rehearsal time for it.
3. **The organisation index filter is client-side** over the page the server returned. It cannot
   widen what the server sent; when the corpus outgrows one page this becomes the `q` query
   parameter the endpoint already accepts.
4. **No opportunity ↔ organisation join table.** A dossier reaches an opportunity as lead
   organisation, primary stakeholder, or through a recorded interaction. That covers the demo
   honestly and is how a real dossier connects a party the mission has worked with but not formally
   attached — but a many-to-many would model it properly.
5. **`main` is behind `week2-intelligence`** on a strictly linear history. The fast-forward was
   reported in Week 1 and has still not been executed.

---

## 7. Open questions

**14 open, 10 resolved** in `docs/OPEN_QUESTIONS.md`. None blocks Week 2.

Resolved this week: **Q-12** (§4a routing table), **Q-06** (`CONSULAR_SENSITIVE` never routes
external), **Q-02b** (matrix confirmed, `read:ai_trace` removed from `ADMIN`), **Q-16** (hero
reframe), **Q-17** (AI-proposed, lower confidence), **Q-13** (audit history volume), **Q-15** (SLA
demo defaults), **Q-04** (revert approved), **Q-02a** (`verb:object`), **Q-03** (partial).

Raised this week and still open:

- **Q-21** — do `meeting_followup` and `knowledge_answer` get the 25s heavy budget too? Assumed yes;
  a ruling would confirm it.
- **Q-22** — `BUILD_BIBLE.md` has no §12 streaming requirement to cite, though the W2.2 ruling
  refers to one. Streaming is built either way.

**Q-01** (exact tile metrics) matters most for Week 4: the tiles still use obviously-defensible
counts under assumption A-08 rather than an agreed definition of "health".

---

## 8. How to run it

```bash
docker compose up -d db     # Postgres 16 + pgvector, host port 5433
.\make.ps1 demo-reset       # drop, migrate, seed, ingest
.\make.ps1 demo-prewarm     # cache the hero briefs
.\make.ps1 dev              # api :8000 + web :3000
```

**`demo-reset` now ingests as its fourth step.** It previously did not, and that was a real trap:
the reset drops `document_chunks` with the schema and nothing else recreated them, so hybrid
retrieval answered every query with nothing at all — silently. The first sign would have been an
empty knowledge panel on stage rather than an error anybody saw coming. `make ingest` also exists
on its own.

See `docs/demo-runbook.md` for the rehearsal checklist and what prewarm does and does not
guarantee.
