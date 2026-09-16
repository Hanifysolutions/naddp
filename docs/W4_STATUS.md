# Week 4 — Unified Story + Hardening · Status (progress so far)

**Date:** 2026-09-15 · **Branch:** `week4-unified` · **Scope so far:** `PROMPT_W4.1`–`PROMPT_W4.4`
**Verdict:** All three of the week's features are complete: diaspora capability search, the Unified
Outcomes board (winning moment #3), and the Governance page, backed by an audit hash chain that
can no longer fork. Every nav item is now live, and the polish pass has run across all nine
surfaces. The hardening half of the week has not started: the demo-reset
and fallback pass, the security pass, citation verification, the dry-run and deploy. This document
is updated at the end of the week.

**Exit condition (ROADMAP):** *Definition of Done (BUILD_BIBLE §13) fully met; live rehearsal clean on
laptop + 1080p.* **Not yet met.** The feature scope for the three winning moments is now in place.

---

## 1. What landed

| Prompt | Commit | What it delivered |
|---|---|---|
| W4.1 Diaspora search | `b2038b8` | Consent-gated capability search. The consent predicate sits in the WHERE clause of the only statement that loads profiles, so a profile without consent is never read, ranked or counted. Location is coarse (state and country), results are candidates only, and no contact or outreach route exists. The hero search returns both the lithium-processing engineer and the migration-pathway academic. The seed adds two strong skills matches whose consent is not given or has been withdrawn, which prove the exclusion. |
| W4.2 Unified Outcomes | `0464ca0` | `GET /v1/outcomes` and the `/outcomes` board. Each of five domains is counted by its own module under its own authorisation, and the composer never queries. The lithium / skilled-migration corridor is traced across five contexts, from opportunity to stakeholder to meeting to diaspora experts to consular case. Withheld domains render as refusals, never as zeros. |
| W4.3 Governance + chain hardening | `58537f8` | `/governance`: the append-only audit log in plain language (actor, action, object, Allowed or Denied, classification, time), filtered and keyset-paged by the API, with an on-demand "Verify audit chain" action. The chain can no longer fork: a unique link index, plus a writer that re-links on conflict, resolves `docs/W1_STATUS.md` §6 item 1. Every nav item is live. |
| W4.4 Polish | `027fb31`, `348c238` | Cosmetic and UX only, no proven behaviour touched. One result card per query on Knowledge and Diaspora; amber reserved for what is genuinely at risk; command-tile labels wrap instead of clipping; the AI-proposed brief headline is sentence case; keyboard focus moves onto the approval block when a Send is refused; the diaspora depth floor rises to 65% so two generic terms no longer buy a place in the hero set. |

**Tests:** 1566 API tests pass, including 42 in `tests/test_diaspora_search.py`, 35 in
`tests/test_outcomes_board.py`, 6 in `tests/test_audit_chain_concurrency.py` and 2 in
`tests/test_audit_labels.py`. `ruff`, `ruff format` and `mypy` pass. Web `tsc` and `next lint`
are clean against the regenerated OpenAPI client.

---

## 2. VERIFY results, by prompt

### W4.1 — diaspora capability search, consent-gated
Checked with `tests/test_diaspora_search.py`, and again by direct calls against the development API:

| Property | Result |
|---|---|
| The hero search returns both | DIASPORA_OFFICER gets 6 consented candidates, including Ijeoma Nwachukwu (lithium processing, Western Australia) and Femi Balogun-Wright (migration pathways, Australian Capital Territory). The same holds for every role holding `search:diaspora_profile`. |
| A strong match without consent is never returned | Abdulmalik Jibril (`NOT_GIVEN`) and Adanna Chukwu (`WITHDRAWN`) are absent. Captured SQL shows `consent_status IN (…) AND is_tombstoned IS false` in the WHERE clause of every profile read, and no such row is loaded into the session. Giving them consent, inside a rolled-back transaction, is exactly what makes them appear. |
| Coarse location only | Results have no city, language, summary or contact field, and no city name appears in the response or the trace. |
| Candidates only | No contact, outreach, invite or message route exists anywhere in the OpenAPI document. |
| A role without the permission | CONSULAR_OFFICER and ADMIN get 403 `permission_denied`, not a crash. |

### W4.2 — Unified Outcomes board (winning moment #3)
Checked with `tests/test_outcomes_board.py`, and again by direct calls against the development API:

| Property | Result |
|---|---|
| Each figure is counted in its own context under its own check | Each outcomes module imports only its own context's models, and a test reads the imports. `board.py` imports no model and issues no statement. Captured SQL never touches two domains' tables in one statement. Removing one permission from an all-seeing principal withholds exactly that domain and leaves every other figure unchanged. The corridor match is its own `search:diaspora_profile` check inside the diaspora domain. |
| A role lacking a domain gets a refusal, not a zero, and nothing leaks | AMBASSADOR reads 5 of 5 domains, DIASPORA_OFFICER 4 of 5 (consular withheld), CONSULAR_OFFICER 2 of 5 (bilateral, diaspora and relationships withheld), and ADMIN 0 of 5. A withheld section has `figures: []` and `counted_across: []`, and its tables are never queried. The consular officer's payload contains no opportunity title, stakeholder or expert name. Consular counts also respect clearance: holding the permission without the consular compartment counts no CONSULAR-SENSITIVE case. |
| The hero thread is traceable across the board | As AMBASSADOR, all five steps are found. The corridor opportunity is AI-proposed, score 61, with its scoring trace. The counterpart is at Covalent Lithium. The meeting is recorded against the corridor opportunity, and its follow-up is drafted and needs a named approver. Both hero experts are named. The passport-renewal case shows metadata only. A withheld step carries nothing, and an anchor outside the caller's clearance reads "Not found in the records you are cleared to read." |
| Nothing is computed client-side beyond formatting | Section tones, `domains_readable` / `domains_total` and every fact sentence come from the API. A scan of `components/outcomes` finds no arithmetic on data, only display guards. |

---

### W4.3 — Governance page and audit chain hardening
Checked with `tests/test_audit_chain_concurrency.py`, `tests/test_audit_api.py` and
`tests/test_audit_labels.py`, and again by direct calls against the development API:

| Property | Result |
|---|---|
| Concurrent audit writes keep the chain linear | A burst of 48 writes from 6 connections lands every row in one chain, with no fork, no gap, one genesis and one head, and `verify_chain` reports it intact. A writer holding a stale head re-links behind the winner. The interleaving is deterministic, and the loser's id was minted before the winner's. Two writers racing for an empty chain cannot both become the genesis row. A writer blocked past its budget refuses with a 503 and never writes out of order. The live table carries `uq_audit_events_prev_event_hash … NULLS NOT DISTINCT` and refuses a forged second claim and a second genesis. |
| `GET /v1/audit/chain` over the seeded and live events | Intact over all 477 events in the log, after the migration and live API writes. |
| Plain-language actions, Allowed and Denied | Entries read "Role assumed", "Access refused", "Sensitive record read" and "Partnership concluded", and a denied entry gets a refused phrasing. The live log holds 446 Allowed and 34 Denied entries. The command-centre strip uses the same label map. |
| Role scoping | AMBASSADOR, DEPUTY and ADMIN can read the log. TRADE_OFFICER, CONSULAR_OFFICER and DIASPORA_OFFICER get 403 `permission_denied`: a refusal, not a crash. A direct call as TRADE_OFFICER got 403, missing `read:audit`. |
| Found while hardening | The test harness's session-scoped audit transaction (writes rolled back, never committed) stayed open for the whole run, so it held the chain's head throughout. The old lock let later writers fork past it; the unique index now refuses them. `tests/conftest.py` rolls it back after each test that used it, and its rows are still never committed. |

### W4.4 — polish pass
Checked with the full API suite, `tsc`, `next lint` and a `make demo-reset`:

| Property | Result |
|---|---|
| No behaviour changed | The full API suite passes unchanged. The edits are result-card state, tone selection, label wrapping, focus management, one seed headline and one selection threshold. No permission, workflow, trace, consent or audit path was touched. |
| One result per query | Knowledge and Diaspora each hold the current exchange or search, not a list, so a new ask or search replaces the previous card instead of stacking under it. |
| Amber only on risk | "Never contacted" is neutral; "Resolved within the service level" and "High-influence stakeholders engaged" are neutral unless everything measured met its mark. Amber stays on follow-ups awaiting approval, cases due within 48 hours, dormant relationships and the open backlog when cases are due soon; --risk stays on breached and overdue. |
| Labels are never clipped | Command-tile labels and hints wrap. "Overdue next action" and "sum of estimates, not probability-weighted" read in full at laptop and 1080p widths. |
| Approval block | Focus moves onto the block when a Send is refused, so a keyboard reader lands on the refusal rather than the top of the document; the `role="status"` announcement is unchanged. "Request changes" is a real transition to `DRAFTED` (`meeting_followup.changes_requested`), and the discard sheet already says the draft is kept on record. |
| Cleaner hero diaspora result | The corridor search returns 5 candidates rather than 6: Adaeze Onuoha, who matched only "migration" and "skills", falls below the 65% depth floor. Ijeoma Nwachukwu and Femi Balogun-Wright are both still returned, and the consent exclusions are unchanged. |
| Seed freshness | Nothing to fix: every seeded timestamp is already derived from the database clock at seed time (`SeedContext.now`), like the audit history. After `make demo-reset` the brief is dated today, 10 signals fall inside the last seven days and 3 meetings inside the next seven. A database seeded on an earlier day drifts, which is what `make demo-reset` before a rehearsal is for; one seed-integrity test (the trailing-eight-week audit window) is the tripwire for exactly that drift. |
| Consistency sweep | No all-caps eyebrows and no `A · B · C` meta strings outside comments and the verbatim section 4a badge. `:focus-visible` gives a 2px ring across the app. |

## 3. Decisions and assumptions recorded this week

- **A-18.** Diaspora search is consent-gated in the query. Ranking is explainable: rarity-weighted
  key terms, facets covered first, at most 6 candidates. Location is coarse, results are candidates
  only, and search is never answered from a snapshot.
- **A-19.** Unified Outcomes uses one module per bounded context, each checking its own authorisation.
  The outcome metrics are a provisional answer to Q-01. Hero-thread anchors are named by seed slug
  in `data/demo-seed/hero_thread.json` and resolved through `manifest.json`. The board is not an
  audited read, following the A-09 / A-15 precedent.

- **`docs/W1_STATUS.md` §6 item 1 resolved (W4.3).** The hash chain cannot fork. The best-effort
  advisory lock, which wrote unlocked once its budget ran out, is replaced by a unique index on
  `prev_event_hash` and a writer that re-links inside a savepoint when it loses the race. The
  wait is bounded by `lock_timeout`, and past its budget the writer raises `AuditChainBusyError`
  (503) rather than write out of order. The migration refuses to run over a chain that already
  forks.

## 4. Known gaps and `TODO_VERIFY`

- **The hero thread depends on the seed manifest.** `data/demo-seed/manifest.json` is git-ignored
  and written by `make seed`, so a fresh checkout shows "Not found" on every thread step until
  `make demo-reset` has run. The board's figures do not depend on it.
- **Seeded service level:** 1 of 3 resolved cases finished inside its budget. That is honest seed
  data rather than a defect, but it is the one figure on the closing screen with a warning tick.
- **Diaspora ranking is lexical.** One weak candidate, Adaeze Onuoha, matches the hero search on two
  generic terms. Q-07 (the embedder) is still open.
- **The live path is off in the demo.** `AI_GATEWAY_LIVE=false` is the rehearsed state.
- **Citation registry:** unchanged this week, with no citation edits. The W3 figures still stand:
  23 `TODO_VERIFY` entries, none of them cited. Clearing them is a Week 4 hardening item.
- **Not started:** the demo-reset and cached-fallback pass, the security pass, citation
  verification, the 10-minute dry-run, deploy and rehearsal.
- **Screenshots** were taken in the browser by the lead engineer, not by automation.
