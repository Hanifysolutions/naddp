# Week 3 — AI + Citizen Operations · Status

**Date:** 2026-09-15 · **Branch:** `week3-operations` · **Scope:** `PROMPT_W3.1`–`PROMPT_W3.4`
**Verdict:** All four prompts complete. Every VERIFY item was printed and passing, checked with plain
pytest plus a few direct calls against the development API. Week 4 has not been started.

**Exit condition (ROADMAP):** *synthetic passport case runs NEW→RESOLVED with audit; a consequential
action is visibly blocked; knowledge answerer refuses when no source.* **Met.**
- Every one of the 20 consular case transitions, `NEW → TRIAGED → ASSIGNED → IN_REVIEW → RESOLVED → CLOSED`
  included, writes one `audit_events` row and one `case_events` row.
- A meeting follow-up cannot be sent without a named approver. The block is enforced by the server
  and the database, not just the UI.
- A knowledge question with no approved source gets a refusal that cites nothing and names an
  officer.

---

## 1. What landed

| Prompt | Commit | What it delivered |
|---|---|---|
| W3.1a Mission Slate | `80c864e` | `DESIGN_SYSTEM.md` v1.0 as the token layer. The shadcn aliases point at the Mission Slate scale, the command surfaces use `--slate-900`, and the SaaS tells are removed. |
| W3.1b Intelligence | `68d5530` | Morning-brief page, live. It shows grounded items with resolving citations. The AI-proposed corridor item is visually subordinate (Q-17), the DRAFT chip states the approval gate (Q-25), and the §4a routing badge has a drawer. |
| W3.2 Meetings | `ca9982f` | Meeting copilot: the pre-read plus the follow-up approval workflow. Send on an unapproved draft gets a 403 DENY row, submits the draft for review and answers 202. Self-approval is refused, and `DISCARDED` is an audited terminal (Q-05). Database constraints guarantee the send path. |
| W3.3 Consular | `6b9cd89` | Case state machine (20 rules) with an immutable `case_events` timeline. The SLA clock counts business days and pauses on the citizen. Also a consular dashboard, a case workspace, and a metadata-only AI triage on the CONSULAR-SENSITIVE no-external-model route, confirmed by a named officer. |
| W3.4 Knowledge | `b0a344c` | Grounded-or-refuse knowledge answers. The approved knowledge base is filtered before retrieval, approved text is quoted verbatim and cited, and a refusal cites nothing and names an officer. Also the Knowledge page and article view. |

**Tests:** 1478 API tests pass. `ruff`, `ruff format` and `mypy` pass. Web `tsc` and `next lint` are clean
against the regenerated OpenAPI client.

---

## 2. VERIFY results, by prompt

### W3.1 — design system and intelligence
The brief renders for AMBASSADOR with four grounded items. The AI-proposed item scores 61 against an
evidenced peak of 92. DEPUTY and DIASPORA_OFFICER get the mission-wide brief, labelled as such.
CONSULAR_OFFICER and ADMIN get a refusal, with no crash. All 12 citation URLs resolved with HTTP 200.

### W3.2 — the approval block (winning moment #2)
| Property | Result |
|---|---|
| TRADE_OFFICER presses Send on a draft | 202 and the send is refused. A `meeting_followup.sent` DENY row is written and the draft moves to `OFFICER_REVIEW`. |
| AMBASSADOR approves | `APPROVED → SENT`. `sent_at` is set and the audit row names the approver. |
| The drafter approves their own draft | 403 `separation_of_duties` |
| A send without approval, attempted at the database | Refused by `ck_meeting_followups_sent_requires_approval` |

### W3.3 — consular command, case workspace, metadata-only triage
| Property | Result |
|---|---|
| Triage badge and no model | Trace: `CONSULAR-SENSITIVE · no external route · metadata-only · generation withheld`. `model_requested` and `model_used` are null, `live` and `fallback` are false, and a provider tripwire never fires. |
| Narrative never reaches the Gateway | The Gateway context holds exactly the six allowlisted metadata facts. The case summary, subject name and file token are absent from context, trace and response. |
| A transition writes its audit row | All 20 legal transitions write one audit row and one timeline row. The HTTP assign updates the timeline. |
| An illegal transition is rejected by the server | 409 `illegal_transition` (or `terminal_state`), a DENY row, and the status is unchanged |
| The SLA clock pauses in `AWAITING_CITIZEN` | A week of waiting adds no chargeable time. Resuming keeps the paused days, and the due date moves by them. |
| Closure needs a named human | `closed_by` is recorded. The DB checks `ck_cases_closure_requires_human` and `…determination_requires_human` refuse otherwise. Triage, resolve and close are refused from inside an AI call. |
| A role without `read:consular_case` | TRADE_OFFICER, DIASPORA_OFFICER and ADMIN get 403 `permission_denied`, never a crash |

### W3.4 — knowledge answers, grounded or refused
Checked with `tests/test_knowledge_answers.py`, and again by direct calls against the development API:

| Property | Result |
|---|---|
| A question with an approved source | CONSULAR_OFFICER, *"What evidence is needed for an emergency travel document when a passport has been lost?"*: `answered_from_approved_sources: true`, quoted verbatim from `emergency-travel-document-guidance` v1. It cites `nigeria-hc-canberra-emergency-travel-certificate` → `https://nigeria-can.org.au/ova_sev/emergency-travel-certificate/`. Trace badge `INTERNAL · external-noret · claude-sonnet-5`, `fallback=true (LIVE_DISABLED)`, no snapshot consulted. |
| A question with no approved source | *"Can a Nigerian citizen living in Australia vote in Nigerian elections from abroad?"*: `answered_from_approved_sources: false`, `citations: []`, `evidence: []`, and a refusal reason ("the closest covers 26% of its key terms; 50% is required"). Referred to Tunde Bakare, Deputy Head of Mission. No model was asked (`live=false`, `fallback=false`, `model_used=null`), even with the live path switched on and a provider tripwire installed. |
| An excluded article, even though relevant | The same ETD question is refused when the article is expired, not yet valid, in review, or retired. It is also refused for TRADE_OFFICER (written for the consular audience) and with a Nigeria-only jurisdiction filter (the article restates an AU source). In every case the article is absent from retrieval's candidate list, not filtered out afterwards. |
| A role without `read:knowledge_article` | ADMIN gets 403 `permission_denied` on the answer, the corpus and the article routes. It is a deny, not a crash. |
| The demo questions file | Each seeded question does what it states for every role listed: 3 grounded, 2 refused. |

---

## 3. Decisions and assumptions recorded this week

- **Q-05 resolved:** `DISCARDED` is confirmed. A drafted diplomatic communication is never deleted.
- **Q-21–Q-25 rulings applied.** Heavy budgets, streaming, the brief evidence shape, the latest brief
  served, and no brief status filter.
- **A-12 reversed.** Consular triage is declared `CONSULAR_SENSITIVE` and answered by metadata-only
  rules, with no model.
- **A-16.** The SLA clock uses business days on a fixed UTC+10 calendar, with no holidays and no
  daylight saving. The lapse period is 5 business days.
- **A-17.** Grounded-or-refuse. The filter runs before retrieval. An article supports a question if
  it holds at least 50% of the rarity-weighted key terms and at least 2 of them. A refusal is
  referred to the Deputy Head of Mission. Knowledge is never answered from a snapshot, and an article
  with no registry citation cannot ground an answer.

## 4. Known gaps and `TODO_VERIFY`

- **Citation registry:** 138 `VERIFIED`, 23 `TODO_VERIFY` and 2 `DO_NOT_CITE`. The seed, the snapshots
  and every generated answer cite `VERIFIED` ids only, and the test suite enforces this. The 23
  `TODO_VERIFY` entries are research output and are not cited anywhere.
- **Q-10 is still open.** No knowledge-article approval workflow was built; articles are seeded
  already approved, with a named approver. The database still refuses `APPROVED` without one.
- **The live path is off in the demo.** `AI_GATEWAY_LIVE=false` is the rehearsed state, and every
  trace says `fallback=true (LIVE_DISABLED)` where a model could have been asked. The live path is
  exercised in tests with provider stubs, and CONSULAR-SENSITIVE never goes live, by design.
- **The knowledge support test is lexical.** It matches weighted term overlap over Postgres lexemes,
  because the embedder is the local hashed model (Q-07 is still open). A paraphrase that shares no
  terms with the approved text is refused rather than guessed at. That is the safe failure direction.
- **The urgent consular hero is time-sensitive.** It is seeded about 11 weekday hours from breach, so
  run `make demo-reset` shortly before a rehearsal.
- **Other open questions:** Q-01, Q-03, Q-07, Q-08, Q-09, Q-10, Q-11, Q-14, Q-18, Q-19, Q-20 (see
  `docs/OPEN_QUESTIONS.md` §1).
- **Screenshots** were taken in the browser by the lead engineer, not by automation.
