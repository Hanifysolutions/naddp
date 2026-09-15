# Week 4 — Unified Story + Hardening · Status (progress so far)

**Date:** 2026-09-15 · **Branch:** `week4-unified` · **Scope so far:** `PROMPT_W4.1`–`PROMPT_W4.2`
**Verdict:** Two of the week's features are complete: diaspora capability search, and the Unified
Outcomes board (winning moment #3). The hardening half of the week has not started: the demo-reset
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

**Tests:** 1558 API tests are collected and all pass, including 42 in `tests/test_diaspora_search.py` and
35 in `tests/test_outcomes_board.py`. `ruff`, `ruff format` and `mypy` pass. Web `tsc` and `next lint`
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

## 3. Decisions and assumptions recorded this week

- **A-18.** Diaspora search is consent-gated in the query. Ranking is explainable: rarity-weighted
  key terms, facets covered first, at most 6 candidates. Location is coarse, results are candidates
  only, and search is never answered from a snapshot.
- **A-19.** Unified Outcomes uses one module per bounded context, each checking its own authorisation.
  The outcome metrics are a provisional answer to Q-01. Hero-thread anchors are named by seed slug
  in `data/demo-seed/hero_thread.json` and resolved through `manifest.json`. The board is not an
  audited read, following the A-09 / A-15 precedent.

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
