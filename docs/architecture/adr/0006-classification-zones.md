# ADR-0006: Four classification zones, with propagation to derived content

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Solutions Architect, Lead Engineer
- **Consulted:** Consular stakeholder (demo buyer)
- **Informed:** All contributors
- **Tags:** `security` `data` `ai`

## Context

`BUILD_BIBLE.md` §5 fixes four data zones and requires that they be enforced *even in the demo*, and
that the routing decision be visible in a UI trace drawer. `CLAUDE.md` §2.7 repeats the requirement.

Classification is the axis RBAC (ADR-0003) does not cover. A permission answers *"may this role
perform this verb on this object type"*. It cannot answer *"this particular case attachment is
consular-sensitive; may this particular principal see its contents"* — because sensitivity is a
property of the content, not of the endpoint.

The specific force that shapes this ADR: **once an AI system summarises a classified document, the
summary is classified too.** Any system that classifies storage but not derived content leaks by
design, because the leak path is the feature. This is the property a consular buyer will probe.

Note on spelling: `BUILD_BIBLE.md` §5 writes the zones with hyphens (`MISSION-INTERNAL`,
`CONSULAR-SENSITIVE`). Identifiers in code, database enums and API payloads use underscores
(`MISSION_INTERNAL`, `CONSULAR_SENSITIVE`); the hyphenated forms are display labels only. Only the
underscored forms are valid on the wire.

## Decision

### The four zones

| Code | Rank | Meaning |
|---|---|---|
| `PUBLIC` | 0 | Already published, or intended for publication. Cited public sources, published trade statistics, public university and agency pages, anything on the citizen-facing surface. Disclosure causes no harm because it has already occurred. |
| `MISSION_INTERNAL` | 10 | Ordinary working material of the mission. Draft briefs, pipeline notes, internal meeting agendas, stakeholder contact records. Not secret, but not for publication; disclosure is embarrassing and erodes working candour. The default zone for anything a staff member authors. |
| `CONFIDENTIAL` | 30 | Material whose disclosure would damage the bilateral relationship or a commercial party: negotiating positions, unannounced commercial intentions, candid political assessments, third-party commercially-confidential information shared in trust. |
| `CONSULAR_SENSITIVE` | 20 + compartment | Personal information about identifiable individuals in a consular context: case files, welfare and detention matters, travel-document applications, next-of-kin details. Distinguished from `CONFIDENTIAL` not by how damaging disclosure is in diplomatic terms, but by **whose** interests are harmed — a private person's, who did not choose to be in the system. |

### Dominance ordering

For **labelling and propagation**, the zones form a total order:

```
PUBLIC  <  MISSION_INTERNAL  <  CONSULAR_SENSITIVE  <  CONFIDENTIAL
```

with ranks `0 < 10 < 20 < 30`. The dominance operator is `max`: any aggregate takes the highest
classification among its parts.

For **read authorisation**, the order alone is not sufficient. `CONSULAR_SENSITIVE` is a
**compartment**, not merely a higher tier. Clearing someone to read candid political assessments does
not mean clearing them to read a citizen's welfare file — those are different needs-to-know serving
different people's interests. So the access rule has two parts:

```
may_read(principal, zone) :=
      principal.clearance_rank >= zone.min_role_rank_to_read
  AND (zone.compartment is None OR zone.compartment in principal.compartments)
  OR   an explicit per-object grant exists for (principal, object)
```

The dominance order is used for propagation (`max` over parts); the two-part rule is used for access.
Keeping them separate is what stops "higher rank" from silently becoming "may read everything".

### Role ranks and compartments

| Role | Clearance rank | Compartments | Rationale |
|---|---|---|---|
| `AMBASSADOR` | 40 | `consular` | Head of mission; accountable for everything the mission does. |
| `DEPUTY` | 30 | `consular` | Acts for the Ambassador; needs the same reach. |
| `TRADE_OFFICER` | 20 | — | Full working access to trade and intelligence material; no consular need-to-know. |
| `DIASPORA_OFFICER` | 20 | — | Same tier; diaspora profiles are consent-governed rather than consular. |
| `CONSULAR_OFFICER` | 20 | `consular` | Working tier plus the consular compartment. Deliberately **not** rank 30 — a consular officer's need-to-know is deep, not broad. |
| `ADMIN` | 10 | — | Platform administration and audit reading. Explicitly **not** a content super-user (ADR-0003). |

Minimum rank per zone: `PUBLIC` 0, `MISSION_INTERNAL` 10, `CONSULAR_SENSITIVE` 20 + `consular`
compartment, `CONFIDENTIAL` 30.

Consequences worth stating out loud, because they are the interesting cases:

- A `TRADE_OFFICER` **cannot** read a consular case file. This is the refusal exercised in
  `evals/security/prompt_injection.example.jsonl` and it is a demonstrable denial.
- A `CONSULAR_OFFICER` **cannot** read a `CONFIDENTIAL` negotiating position by role alone. If the
  work requires it, an explicit per-object grant is issued and audited — which is a better story than
  a blanket clearance.
- An `ADMIN` **cannot** read case content. Separation of duties.

Per-object grants are the pressure valve that keeps ranks honest instead of inflating. Every grant is
an audited event (`grant.issued`, ADR-0004) with an actor and a reason.

### Propagation: derived content inherits

**A document's classification propagates to everything derived from it.** Concretely:

1. **Aggregates take the maximum.** A brief assembled from a `PUBLIC` signal and a
   `MISSION_INTERNAL` note is `MISSION_INTERNAL`. A stakeholder dossier is classified at the maximum
   of its constituent interactions.
2. **AI output inherits the maximum classification of its retrieved context.** If the Gateway
   retrieved a `CONFIDENTIAL` document, the generated result is `CONFIDENTIAL` — regardless of how
   anodyne the generated text looks. A summary of a secret is a secret. This is enforced in
   `gateway.py` stage 5 and recorded in the `ai_traces` row alongside the model route.
3. **Evidence lists inherit too.** The evidence array returned with an AI response is itself
   classified content: the *existence* of a document and its title can disclose. Evidence the caller
   may not read is not returned, not returned-as-redacted, and not counted in a total.
4. **Excerpts and embeddings inherit.** A chunk carries its parent document's zone; an embedding is
   derived content and is filtered by the same rule in the vector search predicate, before ranking.
5. **Downgrade is a deliberate, audited act, never automatic.** Nothing computes its way down the
   lattice. A human with the right permission may reclassify an object; it writes an audit row
   (`object.reclassified`) carrying both zones and a reason.
6. **Default on ingest is not `PUBLIC`.** Unclassified new material defaults to `MISSION_INTERNAL`;
   anything ingested into a consular context defaults to `CONSULAR_SENSITIVE`. Failing closed on
   ingest is the whole point.

### Where it is enforced

- **In the query, not after it.** Every list, search and vector query includes the classification
  predicate in SQL (`CLAUDE.md` §5). A row the caller may not read is never loaded, so it cannot leak
  through a count, a facet, a pagination total, or a "3 more results" affordance.
- **In the Gateway**, at stages 2, 3, 5 and 8 (ADR-0001).
- **In the response serialiser**, as a final assertion: a schema carrying a `classification` field is
  checked against the requesting principal before the response leaves the process. This is a
  backstop, not the control — if it ever fires, a query predicate is missing and that is the bug.

## Consequences

### Positive

- The buyer's central question — "can the AI see the case file?" — has a precise, mechanical answer.
- Zones are visible in the UI and in the trace drawer, which turns an invisible control into a
  demonstrable one.
- `max`-propagation is simple enough to hold in the head and to test exhaustively.
- Compartmenting `CONSULAR_SENSITIVE` rather than stacking it above `CONFIDENTIAL` produces the
  correct, and more impressive, behaviour: seniority alone does not open the case file.

### Negative / costs

- Two-axis authorisation (permission *and* classification) means two checks, two test matrices and two
  ways to get it wrong.
- `max`-propagation is conservative and will over-classify. A brief that touched one `CONFIDENTIAL`
  note becomes `CONFIDENTIAL` in full, which in real use drives demand for per-section classification
  and redaction. Out of scope for the demo; flagged for the pilot.
- Per-object grants need a table, a UI and a lifecycle (expiry, revocation). Week 3+ at the earliest;
  until then, ranks are the only mechanism and the over-classification pain is real.
- Every new query is a place the predicate can be forgotten. Mitigated by a shared query helper and by
  the serialiser backstop, but this is the standing risk of the design.
- Classification is per-object, not per-field. A case row is `CONSULAR_SENSITIVE` in its entirety even
  though its status and SLA date are not personally sensitive. This forces the consular dashboard's
  aggregate metrics to be computed by a service that reads at a higher clearance and returns only
  counts — a seam that must be built carefully, since it is the one legitimate path from
  `CONSULAR_SENSITIVE` data to a number a `TRADE_OFFICER` can see on the Outcomes board.

### Neutral / follow-on work

- `data/taxonomy/classifications.json` is the machine-readable definition seeded into the database and
  is the single source for ranks, compartments and labels.
- The aggregate-metrics seam above is required for winning moment #3 (one governed picture) and needs
  its own review; noted in `docs/OPEN_QUESTIONS.md`.

## Alternatives considered

### A single linear clearance rank, no compartments

Simpler, and it produces the wrong answer: it makes seniority sufficient for consular access, which is
exactly the assumption a consular officer will challenge.

### Per-field classification / redaction

More precise, and much more machinery: field-level policy, redaction rendering, and an AI pipeline that
can partially mask context. Right for the pilot, wrong for a four-week demo.

### ABAC with a policy engine

The natural home for per-object grants and expiry. Deferred with ADR-0003 for the same reasons;
`may_read` is written as one function so a policy engine can replace its body.

### Classify only at rest; filter AI output

Rejected outright — this is the leak-by-design case from Context. Output filtering happens after the
content has already entered a prompt and a provider log.

### Adopt the mission's real protective-marking scheme

Attractive for realism, and rejected: mapping to a real national classification scheme implies
accreditation claims we cannot make about a demo holding synthetic data. Four neutral, clearly-defined
zones say what we mean without borrowing authority we do not have.

## Enforcement

| Mechanism | Status |
|---|---|
| Postgres enum type with exactly the four values; a migration is required to add a fifth. | Week 1 |
| `data/taxonomy/classifications.json` is the seed source; a test asserts the DB enum, the Python enum and the JSON agree exactly. | Week 1 |
| Table-driven test over role × zone asserting `may_read` for all 24 cells. | Week 1 |
| Test: `TRADE_OFFICER` requesting a `CONSULAR_SENSITIVE` object receives a refusal and an audit row with `policy_result = DENY`. | Week 1 |
| Test: a Gateway call whose retrieved context includes a higher-zone document produces a result labelled at that zone. | Week 1 |
| Test: evidence lists never contain an ID the caller cannot read, and totals do not count filtered rows. | Week 2 (with retrieval) |
| Test: no reclassification path exists that does not write an audit row. | Week 1 |

## References

- `BUILD_BIBLE.md` §5 (data zones), §6 (controls), §11 (no real citizen data)
- `CLAUDE.md` §2.7, §5
- `data/taxonomy/classifications.json`
- ADR-0001 (Gateway stages 2/3/5/8), ADR-0003 (RBAC), ADR-0004 (audit)
