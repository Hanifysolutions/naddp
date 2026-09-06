# `data/demo-seed/` — Synthetic Seed Dataset and Citation Registry

Everything the Ambassador demo shows comes from here. This directory is what makes the difference
between a demo that survives ten minutes of questions and one that does not.

> **This README is written ahead of the code it describes.** `seed.py`, `citations.json` and
> `ai_snapshots/` are owned by the seed track and are not yet present. This file is the contract they
> are built to.

## Contents

| Path | Owner | Purpose |
|---|---|---|
| `README.md` | this track | The rules below. |
| `seed.py` | seed track | Loads the full synthetic dataset. Idempotent; safe to re-run. |
| `citations.json` | seed track | The citation registry — every real public URL the seed may use. |
| `ai_snapshots/{purpose}_{scenario}.json` | AI track | Deterministic Gateway fallbacks (ADR-0002). |
| `manifest.json` | seed track | Generated. Maps stable seed slugs to the ULIDs of the current seed run, so eval cases and snapshots can reference `sig-au-lithium-refinery-expansion` rather than an ID that changes on every reset. |

---

## 1. The citation rule

**Every intelligence signal cites a real, public, resolving URL. No exceptions, and no inventions.**

This is `CLAUDE.md` §2.6 and it is the load-bearing rule of the entire demo. Winning moment #1 is
*"not a chatbot"* — the Ambassador clicks a citation and a real page opens. One 404, one plausible-looking
URL that goes nowhere, and the claim collapses for the rest of the session. It is not recoverable by
explanation.

The mechanism:

1. **`citations.json` is the only source of URLs.** `seed.py` may not contain a URL literal, and may
   not construct one. It references citation entries by id. A test asserts that no URL string appears
   anywhere in `seed.py`.
2. **Every citation entry carries a `verified` state.** An entry is one of:

   | State | Meaning |
   |---|---|
   | `VERIFIED` | A human has opened the URL, confirmed it resolves, and confirmed it says what the signal claims it says. `verified_by` and `verified_at` are set. |
   | `TODO_VERIFY` | The URL is believed correct but has not been checked. Permitted during development; **must be zero before the Week 4 rehearsal** (`ROADMAP.md`). |

3. **Never invent a URL.** If the right source cannot be located, the entry is created with
   `TODO_VERIFY` and a note describing what is being looked for — or the signal is written to cite a
   source that *is* available. A guessed URL that happens to 404 is a demo failure; a guessed URL that
   happens to resolve to something *else* is worse, because nobody notices until the Ambassador reads it.
4. **Verification means reading the page, not pinging it.** A 200 response is not verification. The
   check is: does this page support the specific claim the signal makes? A citation that resolves but
   does not support its claim is *attribution laundering* — see `evals/grounding/README.md`.
5. **The count of outstanding `TODO_VERIFY` entries is reported** in each `docs/W{n}_STATUS.md` and
   tracked in `docs/OPEN_QUESTIONS.md` §4.

Sources to draw on are public-agency, institutional and statistical pages — Australian trade and
geoscience agencies, Nigerian mission and government pages, university programme pages, and national
statistical offices. Prefer a stable institutional page over a news article: news URLs rot and move
behind paywalls, and a demo that must survive to a rehearsal weeks later needs durable links.

**Note for adversarial fixtures.** `evals/security/` requires documents carrying prompt-injection
payloads. Those documents keep a real citation *and* carry `synthetic_injection: true`, because the
injected paragraph is ours, not the source's. Attributing adversarial text to a real organisation's
page — even in a fixture — would be indefensible.

---

## 2. The hero-thread requirement

`BUILD_BIBLE.md` §2 defines one narrative, and **all** seed data hangs off it. Volume without a
narrative is noise; the demo is one story told through eight bounded contexts.

Dual sector: **critical minerals (lithium)** + **education / skilled migration**.

The chain the seed must make traversable, end to end, in three clicks or fewer from the dashboard:

| Beat | Context | What must exist in the seed |
|---|---|---|
| Signal | intelligence | An Australian lithium refinery expansion needing processing talent and training pipelines, citing a real public source. |
| Opportunity | opportunities | "AU lithium value-chain partnership + Nigerian skilled-migration training corridor", linked to the signal, scored with a rationale. |
| Stakeholders | stakeholders | The Australian company and a Nigerian counterpart institution, with interaction history that makes relationship health non-trivial. |
| Meeting | meetings | A Trade Officer pre-read, and a follow-up draft that **blocks on approval** — winning moment #2. |
| Consular | consular | A synthetic passport-renewal case for a Nigerian student in Australia, tying the migration thread to citizen services. |
| Diaspora | diaspora | Profiles carrying **both** `XP_LITHIUM_PROCESSING_ENG` and `XP_MIGRATION_PATHWAY_ACADEMIC`, so search returns a processing engineer **and** a migration-pathway academic. |
| Outcomes | all | All four metric families coexisting on one board — winning moment #3. |

Two seed-level constraints follow from this:

- Every entity in the chain must be **reachable from the one before it** by a link the UI actually
  renders. A seeded relationship that exists only in the database does not serve the demo.
- The chain must survive `make demo-reset`. Slugs in `manifest.json` are stable across runs; ULIDs are
  not.

The rest of the volume (below) exists to make the hero thread look like it lives in a real system
rather than in an empty one — it is context, not decoration.

---

## 3. Volume targets (`BUILD_BIBLE.md` §10)

| Entity | Target |
|---|---|
| Intelligence signals | 20–30 |
| Opportunities | 12–15 |
| Stakeholders | 30 |
| Consular cases | 15 |
| Diaspora profiles | 40 |
| Knowledge articles | 20 |
| Meetings | 10 |
| Audit events | "believable history" — see `docs/OPEN_QUESTIONS.md` Q-13 |

`make seed` prints actual counts against these targets and **fails** if any is outside range. A silent
under-seed produces an empty-looking tile at the worst possible moment.

Distribution matters as much as volume:

- Signals, opportunities and stakeholders spread across several sectors from `data/taxonomy/sectors.json` —
  a pipeline that is entirely lithium is not credible and makes the hero thread look like the only thing
  in the system.
- Cases spread across the eight types in `consular_case_types.json`, with an ageing profile that puts a
  few near SLA breach and one over it, so the Citizen Service Health tile shows a real distribution
  rather than all-green.
- Opportunities spread across the pipeline states in `docs/workflows.md`, including at least one in
  every non-terminal state and at least one `PARTNERED` and one `CLOSED`.
- Classification spread across all four zones, including `CONSULAR_SENSITIVE` and `CONFIDENTIAL` items
  that specific demo roles **cannot** see — the denial in `evals/security/` case `sec-002` needs real
  data behind it.
- Audit history including `policy_result = DENY` rows. A log with no denials looks untested.

---

## 4. Synthetic-data rules

`BUILD_BIBLE.md` §11 — non-negotiable:

- **No real citizen data.** No real passport numbers, no real case references, no real personal
  identifiers. Names of individuals are invented; `cases.public_ref` is generated (ADR-0007).
- **No real private contact details.** No real email addresses, phone numbers or home addresses. Use
  clearly non-routable placeholders.
- **Real *organisations* only where they are already public actors** and the statement made about them
  comes from the cited public source. Do not attribute an intention, a negotiating position, or a
  commitment to a real company. Where the narrative needs a counterpart with attributed intent, the
  counterpart is invented and labelled synthetic.
- **Diaspora profiles are entirely invented**, including their consent states. They must not resemble
  identifiable real people.
- **Every screen carries the DEMO / SYNTHETIC badge.** The seed is not the only place this is
  enforced, but the data is why it matters.

---

## 5. Entry points

| Command | What it does |
|---|---|
| `make seed` / `.\make.ps1 seed` | Loads the dataset into the current database. Idempotent — re-running does not duplicate rows. Prints counts against §3 targets and the outstanding `TODO_VERIFY` count. Fails on a dangling taxonomy reference or an unresolvable citation id. |
| `make demo-reset` / `.\make.ps1 demo-reset` | The rehearsal command. Drops and recreates the schema, empties `storage/`, re-runs `seed`. Guarded to refuse unless `DEMO_MODE=true`. This is also the only sanctioned way `audit_events` rows are ever removed (ADR-0004) — and it removes all of them, never a selection. |

Both targets exist identically in the `Makefile` and in `make.ps1` and delegate to the same underlying
commands. `make demo-reset` is run before every rehearsal and before the demo itself; it must be fast
enough that running it is never a judgement call.

---

## 6. Determinism

The seed is deterministic. A fixed random seed, fixed slugs, and dates computed **relative to the run
date** rather than hard-coded, so a case seeded "four days old" is still four days old at rehearsal
three weeks later. A dataset whose ageing profile drifts turns the Citizen Service Health tile red on
its own, which is a confusing way to lose a demo.

Determinism is also what lets the AI fallback snapshots (ADR-0002) reference specific evidence: their
slugs resolve through `manifest.json` on every run.

---

## References

- `BUILD_BIBLE.md` §2 (hero thread), §10 (volumes), §11 (safety rails)
- `CLAUDE.md` §2.6 (no fabricated data)
- `data/taxonomy/README.md` (the vocabularies this seed reads)
- `docs/workflows.md` (the states seeded objects must be distributed across)
- `evals/README.md` (why slugs, not ULIDs)
- ADR-0002 (snapshots), ADR-0004 (audit history, `demo-reset`), ADR-0007 (`public_ref`)
- `docs/OPEN_QUESTIONS.md` Q-13 (audit-history volume and epoch)
