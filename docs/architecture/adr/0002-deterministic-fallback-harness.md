# ADR-0002: Deterministic fallback harness for every AI call

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Solutions Architect, Lead Engineer
- **Consulted:** Demo presenter (Pascal)
- **Informed:** All contributors
- **Tags:** `ai` `ops` `demo-safety`

## Context

The artefact being built is a **ten-minute live demonstration to an Ambassador**, on mission wifi,
in one take, with no second chance. `BUILD_BIBLE.md` §0 states the requirement bluntly: *the demo must
be physically incapable of dead-ending*. `CLAUDE.md` §2.5 fixes the mechanism: every AI call has a
deterministic cached fallback, a 4-second timeout, and `fallback=true` recorded in the trace.

The forces:

- **A live third-party API is a dependency we do not control.** Latency is long-tailed; rate limits,
  transient 5xx, and regional incidents all occur. The probability that at least one of roughly a
  dozen model calls in a ten-minute script misbehaves is not small.
- **A spinner is worse than a stub.** An Ambassador watching a loading state for fifteen seconds has
  already formed a judgement, and it is not about the model.
- **The failure must be honest.** Silently serving a canned answer while implying it is live would be
  precisely the kind of thing this product exists to argue against. The fallback must be recorded,
  visible in the trace drawer, and truthful.
- **Determinism has a rehearsal value beyond outage cover.** With `DEMO_MODE=true` and the live path
  disabled, the entire script is byte-identical on every run, which is what makes rehearsal and
  screenshot-based regression checking possible at all.

## Decision

Every model call inside the Gateway (ADR-0001, stage 6) is wrapped in a fallback harness with the
following behaviour.

**Budget.** A hard wall-clock budget of **4 seconds** covers the provider call including connection,
streaming and retries. The budget is enforced by the harness, not by trusting the SDK timeout. On
expiry the in-flight request is abandoned and the fallback is served. There is no retry-then-fallback
ladder inside the budget: a single attempt, then fall back. Retrying inside a 4-second window buys
little and risks exceeding it.

**Snapshot store.** Deterministic snapshots live at:

```
data/demo-seed/ai_snapshots/{purpose}_{scenario}.json
```

The key is `purpose + scenario`. `purpose` is the registered Gateway purpose. `scenario` is a stable,
low-cardinality string derived from the caller's context — the demo beat being exercised (for example
`morning_brief_ambassador_default`, `diaspora_match_lithium_migration`,
`consular_triage_passport_renewal`). Scenario derivation is a pure function of
`(purpose, role, primary_object_id_or_slug)`, and is defined per purpose alongside its schema.

**Snapshot contents.** A snapshot is a serialised, already-schema-valid `GatewayResult` payload:
`result` (matching the purpose's Pydantic schema), `evidence` (evidence IDs that exist in the seed and
resolve to citations in `data/demo-seed/citations.json`), and the intended `approval_status`. It does
**not** contain a `trace_id` — that is minted per call.

**Post-fallback invariants.** A served fallback is not a bypass. Stages 1, 2, 3 and 8 of the pipeline
still apply:

- The classification gate has already run before generation, so a fallback can never leak a zone the
  caller could not read.
- The snapshot's evidence IDs are re-checked against the caller's authorised retrieval set. If the
  snapshot cites evidence this caller may not see, the response is **refused**, not downgraded.
  A snapshot is a cached answer, not an authorisation exemption.
- `approval_status` is still applied, so a fallback follow-up draft still blocks on human approval.

**Trace.** Every fallback writes an `ai_traces` row with `fallback=true`, the `fallback_reason`
(one of the enumerated modes below), the snapshot key used, and the elapsed time. The UI trace drawer
shows this. A demo run in which fallbacks occurred is a demo run in which the audience can see that
fallbacks occurred.

**Coverage requirement.** Every registered purpose must have at least one snapshot for every scenario
reachable by the demo script. This is a CI check, not a convention.

**Missing snapshot.** If no snapshot exists for the key, the harness serves the purpose's
`__default__` scenario. If that too is missing, the endpoint returns a structured, non-crashing
"unavailable" envelope with `result: null`, `approval_status: BLOCKED`, and a trace row — a visible,
explained empty state, never a stack trace or an infinite spinner.

### Failure modes covered

| Mode | Trigger | `fallback_reason` |
|---|---|---|
| Timeout | 4s budget expires | `TIMEOUT` |
| API error | Provider 4xx/5xx, malformed response, connection reset, DNS failure | `PROVIDER_ERROR` |
| Rate limit | HTTP 429 / provider overloaded | `RATE_LIMITED` |
| Schema validation failure | Stage 7 rejects the response against the purpose schema | `SCHEMA_INVALID` |
| Citation post-check failure | Stage 8 finds a hallucinated or unauthorised evidence ID | `CITATION_INVALID` |
| Missing credential | `ANTHROPIC_API_KEY` absent or empty | `NO_CREDENTIAL` |
| Deliberate demo mode | Live path disabled by config for rehearsal | `DEMO_MODE` |

The last two matter operationally: a contributor with no API key still gets a fully working
application, and `DEMO_MODE` makes the deterministic path a first-class, testable state rather than an
accident.

### Failure modes NOT covered

State this plainly, because a control whose limits are undocumented gets over-trusted:

- **Database unavailability.** No snapshot helps; the Gateway cannot retrieve evidence, cannot verify
  citations, and cannot write a trace. This is an application-level outage and is handled by the
  demo's own resilience posture (local Postgres, `make demo-reset`), not here.
- **A wrong-but-well-formed live answer.** If the model returns schema-valid, correctly-cited, and
  substantively poor content, no timer detects it. Grounding quality is the job of `evals/grounding/`,
  not of this harness.
- **Authorisation defects.** If the retrieval filter is wrong, the fallback is wrong in the same way,
  because it re-uses the same check. The harness makes availability robust, not correctness.
- **Stale snapshots.** A snapshot captured against an older seed may cite evidence IDs that no longer
  exist. Mitigated by a CI check that every snapshot's evidence IDs resolve in the current seed, but
  the snapshot's *prose* can still drift out of date relative to the seed. It is a demo asset with a
  maintenance cost.
- **Front-end or network failure between browser and API.** Out of scope for this ADR.
- **Provider returning success but empty content.** Treated as `SCHEMA_INVALID`; noted here because it
  is easy to forget that a 200 response is not the same as a usable one.

## Consequences

### Positive

- Worst-case AI latency is bounded at roughly 4 seconds plus local overhead, for every screen.
- The demo has a hard floor on quality: the fallback content *is* the rehearsed content.
- Contributors and CI need no API key. Tests run deterministically and offline.
- `fallback=true` in the trace turns an outage into a feature demonstration about observability.

### Negative / costs

- Snapshots are a second body of content to author and keep in step with the seed. They will rot if
  not CI-checked.
- A 4-second budget will cut off some legitimately slow live generations, so the audience may see the
  cached answer where the live one would have been better. Accepted deliberately: predictability beats
  peak quality in this setting.
- Scenario keying introduces a mild coupling between demo narrative and code. Keys must stay
  low-cardinality or the snapshot set becomes unmaintainable.
- There is a real temptation to let snapshots substitute for making the live path good. Mitigation:
  Weeks 2 and 3 explicitly move purposes to live, and the trace drawer makes fallback frequency
  visible during rehearsal.

### Neutral / follow-on work

- Week 4 includes a cache/fallback pass over every beat of the demo script.
- Streaming responses, if introduced, need a first-token budget rather than a total budget; this ADR
  would be superseded rather than amended.

## Alternatives considered

### Retry with exponential backoff, no snapshot

Standard practice, and correct for a background job. Rejected here: backoff spends exactly the
resource the demo does not have. Three retries at 2, 4 and 8 seconds is fourteen seconds of silence.

### Long timeout with a good loading state

Rejected for the same reason. A well-designed spinner is still a spinner, and the failure is still
unbounded.

### Fully pre-recorded demo (no live calls at all)

Safest, and dishonest. The claim being made to the Ambassador is that this is a working system.
`BUILD_BIBLE.md` §3 commits to the live Anthropic API. The harness lets us be live *and* safe.

### Snapshot keyed by a hash of the full prompt

More precise, and useless in practice: any seed or prompt edit invalidates every snapshot silently,
and the key is unreadable, so a missing snapshot is undiagnosable. `purpose + scenario` is stable
across prompt iteration, which is the property that matters.

### Serve the last successful live response from a runtime cache

Attractive, and it fails in exactly the wrong situation — the first run after a fresh
`make demo-reset`, which is how every rehearsal and every demo begins. File-backed snapshots are
present from a cold start.

## Enforcement

| Mechanism | Status |
|---|---|
| CI test: every registered purpose has a `__default__` snapshot; every snapshot parses against its purpose schema. | Week 1 |
| CI test: every evidence ID in every snapshot exists in the seeded dataset and resolves to a citation. | Week 1 (asserted once seed lands) |
| CI test: with the live path disabled, calling each purpose returns a result and writes an `ai_traces` row with `fallback=true`. This is VERIFY item 5 of `PROMPT_W1_foundation.md`. | Week 1 |
| Test: a snapshot citing evidence outside the caller's authorised set produces a refusal, not a downgraded answer. | Week 1 |
| The harness owns the clock; no purpose may pass its own timeout. Enforced by the function signature. | Week 1 |

## References

- `BUILD_BIBLE.md` §0 (prime directive), §4 (fallback rule), §11 (demo safety rails)
- `CLAUDE.md` §2.5
- `PROMPT_W1_foundation.md` Phase 4, VERIFY item 5
- ADR-0001 (Gateway pipeline), ADR-0006 (classification propagation)
