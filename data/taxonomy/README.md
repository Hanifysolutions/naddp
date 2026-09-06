# `data/taxonomy/` — Controlled Vocabularies

These files are **inputs to the seed, not generated output.** They are hand-authored, reviewed, and
committed. `data/demo-seed/seed.py` reads them and writes rows; nothing writes back here. If a
taxonomy is wrong, it is fixed here and the seed is re-run — never edited in the database.

| File | Rows | Seeds | Referenced by |
|---|---|---|---|
| `sectors.json` | 28 | `sectors` reference table | signals, opportunities, stakeholders, expertise tags |
| `expertise_tags.json` | 44 | `expertise_tags` | `diaspora_profiles` (many-to-many) |
| `consular_case_types.json` | 8 | consular case-type reference | `cases` (type, SLA budget, default classification) |
| `classifications.json` | 4 zones + 6 role ranks | the `classification` enum and the RBAC clearance table | every classified table, and `app/security` |

---

## Why these are files rather than migrations

Three reasons, and they are the reasons this directory exists at all:

1. **They are content, and content gets edited.** A sector label or a case-type description will change
   during rehearsal. A JSON edit plus `make seed` is a two-second loop; a migration is not.
2. **They are reviewable as content.** A reviewer can read `consular_case_types.json` and judge whether
   the SLA budgets and determination flags are defensible. The same information spread across
   `INSERT` statements in a migration is not readable by the person best placed to check it.
3. **They are shared inputs.** The eval suites, the seed and the API's enum definitions all need the
   same vocabulary. One file, read by all three, cannot drift; three copies will.

The one exception is `classifications.json`, which *also* has a Postgres enum behind it. A test asserts
the JSON, the Python `Classification` enum and the database enum agree exactly. Adding a fifth zone is
deliberately a three-place change requiring a migration — that friction is the point (ADR-0006).

---

## Rules

- **Codes are permanent identifiers.** `code` is a stable key referenced by seed data, eval cases,
  snapshots and audit rows. Never rename a code. Labels and descriptions are free to change.
- **No URLs.** URLs live in `data/demo-seed/citations.json`, which is owned by the seed track and is
  subject to the `TODO_VERIFY` discipline (`CLAUDE.md` §2.6). A URL in a taxonomy file would sit
  outside that verification process, which is exactly how an unverified link reaches a demo.
- **No personal data.** These are vocabularies. Names of real people or real companies do not belong
  here — the demo's stakeholders and diaspora profiles are synthetic and live in the seed.
- **Referential integrity is checked, not assumed.** Every `parent` in `sectors.json` resolves to a
  sector code; every `sector_code` in `expertise_tags.json` resolves; every `classification` in
  `consular_case_types.json` is one of the four zones. The seed fails loudly on a dangling reference
  rather than skipping the row.
- **Two-level sector hierarchy only.** A sub-sector's `parent` must be a top-level sector
  (`parent: null`). Grandchildren are not supported, and the seed rejects them — a deeper tree makes
  roll-up metrics ambiguous for no analytic gain at this scale.
- **Content must be defensible.** These describe a real bilateral relationship. Every sector, tag and
  case type here is one a mission would actually recognise. Nothing is filler.

---

## File notes

### `sectors.json`

Two levels. Top-level sectors have `parent: null`; sub-sectors name their parent.

The hero thread (`BUILD_BIBLE.md` §2) runs through two of them, and they are marked in their
descriptions so the seed and the eval suites can find them without hard-coded string matching:

- `CM_LITHIUM` (under `CRITICAL_MINERALS`) — the Australian lithium value chain.
- `ED_SKILLED_MIGRATION` (under `EDUCATION_SKILLS`) — the Nigerian skilled-migration training corridor.

The other twelve top-level sectors are genuine features of the bilateral relationship: agribusiness
(Australia is a significant grain supplier to Nigeria), mining equipment and services, energy, ICT,
financial services, health, infrastructure, water, creative industries, professional services and
environment/climate. Sub-sectors exist only where a distinction changes how an opportunity would be
handled.

### `expertise_tags.json`

Each tag names a **capability**, not a job title — a tag should answer *"what could this person be
asked to do"*. Every tag maps to exactly one sector code, including sub-sector codes, so the diaspora
capability tile can roll up either way.

Two tags are load-bearing for the demo and must both be returned by the hero-thread diaspora search
(`evals/grounding/cases.example.jsonl`, case `gnd-003`):

- `XP_LITHIUM_PROCESSING_ENG` — lithium and battery-materials processing engineering.
- `XP_MIGRATION_PATHWAY_ACADEMIC` — migration-pathway and education-policy research.

A search that returns only the first is a *silent omission* failure — well-formed, correctly cited,
and missing the point of the dual-sector narrative.

### `consular_case_types.json`

`requires_human_determination` encodes `BUILD_BIBLE.md` §6: no consular determination may be
autonomous. Seven of the eight types are marked `true`; the exception is `VISA_ENQUIRY_REFERRAL`,
because the mission determines nothing there — it provides accurate information and refers on. That
single `false` is the reason the flag is a field rather than a constant.

For a `true` type, the `consular_triage` Gateway purpose may only ever return a *proposal*
(`approval_status = PENDING_APPROVAL`); the case does not move until a human with
`triage:consular_case` accepts it (`docs/workflows.md` §3).

`default_sla_days` is a calendar-day budget from intake to resolution, and **pauses** while the case
sits in `AWAITING_CITIZEN` — delay attributable to the citizen must not count against the mission's
service standard.

> ⚠ **The SLA values are demo defaults**, chosen to produce a realistic ageing spread on the consular
> dashboard. They are not published service standards and must be confirmed before any non-demo use.
> Tracked as Q-15 in `docs/OPEN_QUESTIONS.md`.

Every type is `CONSULAR_SENSITIVE`, including the referral type. This is deliberate fail-closed
behaviour: anything ingested into a consular context is personal information about an identifiable
individual until someone with authority decides otherwise (ADR-0006).

### `classifications.json`

Carries three things:

1. **The four zones** with `rank` (dominance, used for propagation) and `min_role_rank_to_read` plus
   `compartment` (used for access). These are separate on purpose — `CONSULAR_SENSITIVE` is a
   compartment, not simply a higher tier, so seniority alone never opens a case file.
2. **`role_ranks`** — the clearance rank and compartments of each of the six roles. Note that `ADMIN`
   has rank 10 and no compartment: it administers the platform and reads the audit log, but is not a
   content super-user.
3. **`access_rule`** — the `may_read` predicate in one line, so the file is self-describing and the
   implementation has an unambiguous target.

`display_label` holds the hyphenated forms used in `BUILD_BIBLE.md` §5 (`MISSION-INTERNAL`,
`CONSULAR-SENSITIVE`) for use in the UI. `code` — underscored — is the only form valid in the
database, in Python, and on the wire.

---

## Changing a taxonomy

1. Edit the JSON. Keep the file sorted logically (top-level sector followed by its children; tags
   grouped by sector).
2. Run `make seed` and confirm the referential-integrity checks pass.
3. If you touched `classifications.json`, also update the Python enum and the Alembic enum, and run the
   agreement test — all three must match.
4. If a code you removed is referenced by an eval case or a fallback snapshot, fix those in the same
   commit. A dangling reference in an eval is reported as a case **error**, not a failure, but it still
   means the case is not testing anything.
