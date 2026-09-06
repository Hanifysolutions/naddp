# ADR-0007: ULID primary keys, and independent random public references

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Lead Engineer, Solutions Architect
- **Informed:** All contributors
- **Tags:** `data` `security`

## Context

Two identifier decisions, deliberately separated because they serve opposite goals.

`PROMPT_W1_foundation.md` Phase 2 requires UUID/ULID internal primary keys, and states that
`cases.public_ref` is a **separate random token**. That separation is the interesting part and the
reason this ADR exists.

The forces on **internal** identifiers:

- Rows are created across several tables in one seeding run and in one request; a database-generated
  sequence forces a round-trip before the ID is known, which complicates building an object graph in
  memory.
- Sequential integers leak volume ("we have 47 opportunities") and permit enumeration.
- Random UUIDv4 primary keys are index-hostile: every insert lands in a random position of the B-tree,
  causing page splits, poor cache locality and index bloat. At demo volume this is irrelevant; as a
  production-skeleton default it is a bad habit to bake in.
- Debugging and audit reading are far easier when identifiers sort in creation order.

The forces on the **citizen-facing** reference:

- A consular case reference is quoted in email, read over the phone, and typed into a citizen-facing
  status page. It is a lookup key held by a member of the public.
- Anything derived from a timestamp or a counter tells the holder — or anyone who obtains one — more
  than they should know: roughly when the case was opened, roughly how many cases the mission handles,
  and, crucially, what *other* references probably look like.
- A reference that can be guessed is a way to see other people's consular case status. That is a
  personal-data breach, and this is the highest-sensitivity data in the system (ADR-0006).

## Decision

### Internal primary keys: ULID rendered into a `uuid` column

Every table's primary key is a **ULID** stored in a Postgres `uuid` column.

- Generated in Python with `python-ulid` at object construction, not by the database.
- Stored as `uuid` (native 16-byte type) rather than `char(26)`: half the storage, native indexing,
  and it means every existing UUID tool and driver works unchanged. A ULID is 128 bits, exactly a
  UUID's width, so this is a rendering choice and not a conversion.
- The canonical **display** form is the 26-character Crockford base-32 ULID
  (`01K4Q7M2X8YB3F6N0R5T9WJHVD`); the canonical **storage and API** form is the hyphenated UUID
  string. Conversion is total and lossless in both directions, and lives in one place,
  `apps/api/app/core/ids.py`.
- The first 48 bits are a millisecond timestamp, so IDs are **monotonically sortable by creation
  time**. This is what makes `ORDER BY id` a valid chronological order on `audit_events` (ADR-0004)
  without trusting a separate clock column.

Consequences of ID-at-construction: a whole object graph can be built and related in memory, then
flushed in one transaction; the seed script can emit deterministic fixtures; and a test can assert on
an ID it created.

### Citizen-facing reference: `cases.public_ref`

`cases.public_ref` is a **separate column**, generated **independently** of the primary key, from a
**cryptographically secure random source**. It is not derived from the ULID, not a hash of it, not an
encoding of it, and shares no entropy with it.

- Source: `secrets.token_bytes`, not `random`.
- Format: `NADDP-XXXXXXXX-XXXX`, where the `X` positions are Crockford base-32 characters
  (excluding `I`, `L`, `O`, `U` — so it survives being read aloud and typed by hand without
  transcription errors), giving 60 bits of entropy in 12 characters.
- Constraints: `UNIQUE NOT NULL`, with a unique index — the column is a public lookup key.
- Generation retries on collision; at 60 bits and demo volumes a collision will not occur, but the
  loop is written rather than assumed.
- It is **opaque**: it encodes no timestamp, no sequence, no case type, no mission, no citizen
  attribute. Any structure would be an inference channel.
- It appears on the citizen surface. The internal ULID never does.

Why 60 bits: an attacker enumerating a 60-bit space at 1,000 requests per second needs on the order of
10^10 years to find a specific reference, and even finding *any* valid reference requires searching a
space vastly larger than the number of live cases. The lookup endpoint is additionally rate-limited
and its failures are audited, so a bulk-guessing attempt is loud as well as futile.

**Knowing a `public_ref` is not by itself authorisation.** It is a locator, not a credential. The
citizen-facing status view exposes only the minimum — a status label and a next step — never case
narrative, officer notes, or attachments. Anything more requires an authenticated principal passing
the ADR-0006 check.

### The general rule

> Internal identifiers optimise for **ordering and joinability**.
> Externally-held identifiers optimise for **unguessability and disclosure resistance**.
> When one identifier is asked to do both jobs, the second requirement loses. Use two columns.

This applies to any future citizen-facing or partner-facing handle — a shared brief link, a diaspora
self-service profile token — not only to `cases`.

## Consequences

### Positive

- Index locality of a sequential key with the collision-freedom of a random one: inserts append to the
  right-hand edge of the B-tree.
- `ORDER BY id` is chronological, which makes audit and case timelines correct and cheap.
- IDs are known before flush, so object graphs and deterministic seeds are straightforward.
- No integer enumeration surface anywhere in the API.
- The citizen reference reveals nothing: not when the case opened, not how many cases exist, not what
  another reference might be.
- Rotating a leaked `public_ref` is a one-column update that breaks no foreign key, because nothing
  references it internally.

### Negative / costs

- **ULIDs leak creation time by design.** Anyone holding an internal ID can read its millisecond
  timestamp. This is fine for internal IDs behind authorisation, and is precisely why `public_ref`
  cannot be a ULID. It does mean internal IDs must never be exposed on an unauthenticated surface —
  a rule that needs enforcing, not just stating.
- Two identifiers on `cases` means two things to keep straight; a developer could accidentally expose
  the ULID in a citizen-facing response. Enforced by a test on the citizen schema, below.
- The display/storage duality (base-32 ULID vs hyphenated UUID) is a small ongoing cognitive tax.
  Contained by keeping all conversion in `app/core/ids.py` and using the UUID form on the wire.
- Application-generated IDs mean a buggy generator produces duplicates the database will reject at
  insert; the primary key constraint catches it, but the error surfaces late.
- 12-character references are longer to read aloud than a 6-digit number. That is the trade being
  made deliberately, and it is the right one.
- ULID monotonicity within the same millisecond depends on the library's monotonic generator; across
  multiple processes, two IDs in the same millisecond may not order by true creation order. Audit
  reading tolerates millisecond ties, and `occurred_at` remains the semantic timestamp.

### Neutral / follow-on work

- `app/core/ids.py` owns `new_id()`, `to_display(uuid)`, `from_display(str)` and
  `new_public_ref()`. Nothing else generates identifiers.
- Rate limiting and audit on the public case-lookup endpoint are Week 3 work, tracked with the
  consular surface.

## Alternatives considered

### `bigserial` integer primary keys

Fastest and smallest. Rejected: enumerable, volume-disclosing, requires a round-trip before the ID is
known, and painful if data is ever merged across environments.

### Random UUIDv4 primary keys

The common default. Rejected on index behaviour — random inserts scatter across the B-tree — and
because it forfeits the free chronological ordering that makes audit timelines simple.

### UUIDv7

Genuinely the closest alternative: same time-ordered property, and now standardised, with native
Postgres generation arriving. Rejected for now on tooling maturity across the specific pinned stack,
and because ULID's base-32 display form is materially better for humans reading logs and traces.
The storage type is identical (`uuid`), so switching generators later is a change to `new_id()` alone
and needs no migration. If UUIDv7 support is uniformly available at pilot time, this ADR should be
revisited.

### Derive `public_ref` from the primary key (hash or HMAC of the ULID)

Tempting: one source of truth, no extra column, and an HMAC with a server secret is not reversible.
Rejected for two reasons. First, it couples the public reference to the internal key, so rotating a
leaked reference means either rotating the secret (invalidating *every* reference) or adding the extra
column anyway. Second, it invites a truncation shortcut — hashing then taking 8 characters — which
silently reduces entropy while looking secure. An independent random column has none of these
failure modes and costs 20 bytes.

### Use the ULID itself as the citizen reference

Rejected: it discloses the case creation time to the public, and ULIDs generated close together share
a visible prefix, which hands an attacker a much smaller search space than 128 bits suggests.

### Sequential human-friendly reference (`CASE-2026-00147`)

What many real systems do, and the worst option here: it discloses case volume, discloses timing, and
is trivially enumerable — the exact combination that makes other citizens' case status guessable.

## Enforcement

| Mechanism | Status |
|---|---|
| All ID generation goes through `app/core/ids.py`; a test asserts no other module imports `ulid` or `secrets` for identifier generation. | Week 1 |
| Test: `cases.public_ref` has a `UNIQUE NOT NULL` constraint; 10,000 generated references are unique and match the format regex. | Week 1 |
| Test: `public_ref` is statistically independent of `id` — generating many cases with controlled IDs yields references with no shared prefix structure. | Week 1 |
| Test: the citizen-facing case schema contains `public_ref` and does **not** contain `id`. | Week 3 (with the citizen surface) |
| Every table's PK column is `uuid`, asserted by a schema test over the metadata. | Week 1 |

## References

- `PROMPT_W1_foundation.md` Phase 2
- `BUILD_BIBLE.md` §11 (no real citizen data)
- ADR-0004 (`ORDER BY id` on `audit_events`), ADR-0006 (consular sensitivity)
