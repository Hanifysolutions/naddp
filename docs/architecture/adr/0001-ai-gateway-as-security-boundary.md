# ADR-0001: AI Gateway as the single security boundary for model access

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Solutions Architect, Lead Engineer
- **Consulted:** Consular stakeholder (demo buyer), Mission IT
- **Informed:** All contributors
- **Tags:** `ai` `security` `governance`

## Context

NADDP puts a language model in front of diplomatic and consular material. The buyer for this demo is
security-minded: a consular officer will not accept a system where "an AI has access to the case file"
is an unbounded statement. `BUILD_BIBLE.md` §4 therefore specifies a Gateway, and `CLAUDE.md` §2.1
states the rule in its strongest form — *application code never imports the Anthropic SDK*.

The forces:

- **Authorisation must precede retrieval, not follow generation.** If the model sees a
  CONSULAR_SENSITIVE document and we filter the *output*, the sensitive content has already crossed
  into a prompt, a provider request log, and possibly a cached completion. Redaction after the fact
  is not a control.
- **Every AI claim in this product must be attributable.** `BUILD_BIBLE.md` §4 fixes the response
  envelope at `{ result, evidence, trace_id, approval_status }`. A shape like that only holds if there
  is exactly one place that constructs it.
- **The controls must be demonstrable.** §6 lists actions that may never be autonomous. A reviewer
  will ask "show me where that is enforced". Pointing at one module is a credible answer; pointing at
  "we are careful in each route handler" is not.
- **Scattered SDK calls are unauditable by construction.** If seven modules can call the model, the
  security review surface is seven modules, and it grows with every feature.

## Decision

We will route **all** model access through a single module, `apps/api/app/ai/gateway.py`, exposing one
function:

```python
def generate(
    purpose: Purpose,
    data_class: Classification,
    context: GatewayContext,
    user: Principal,
) -> GatewayResult: ...
```

`gateway.py` is the **only** file in the repository permitted to import the `anthropic` SDK (or any
future model-provider SDK). No route handler, service, model, schema, task, or script may import it,
directly or transitively.

The Gateway implements a fixed nine-stage pipeline. Every stage runs, in order, for every call:

1. **Purpose resolution.** `purpose` must be a member of the registered allowlist —
   `morning_brief`, `opportunity_score`, `meeting_prep`, `meeting_followup`, `consular_triage`,
   `knowledge_answer`, `diaspora_match`. An unregistered purpose is a hard error, never a free-form
   prompt. There is no "general chat" purpose and there will not be one.
2. **Classification gate.** The declared `data_class` is checked against the caller's clearance
   (ADR-0006). A caller who may not read the zone may not generate over it. Failures are recorded
   refusals, not exceptions swallowed into an empty result.
3. **Retrieval authorisation.** Candidate evidence is fetched through the same authorisation filter
   the REST list endpoints use, applied **in the query**, not after it (`CLAUDE.md` §5). The Gateway
   never receives a pre-fetched context blob from a caller; it retrieves its own, under the caller
   identity.
4. **Context assembly.** Retrieved evidence is bound to stable evidence IDs. Retrieved document text
   is wrapped as untrusted data, never concatenated as instructions (see `evals/security/`).
5. **Model route decision.** The chosen model and the reason for it are computed and recorded before
   the call. Routing is a function of purpose and classification — the highest-classification item in
   the assembled context dominates.
6. **Generation.** The provider call, wrapped by the fallback harness of ADR-0002.
7. **Structured-output validation.** The response is parsed against the purpose Pydantic schema.
   Nothing untyped escapes the Gateway; a validation failure is a fallback trigger, not a
   pass-through of raw prose.
8. **Citation post-check.** Every claim-bearing field must reference evidence IDs that were actually
   in stage 3's authorised set. A citation to an ID the user could not read, or to an ID that does not
   exist, fails the response. This is what makes "not a chatbot" (winning moment #1) true rather than
   asserted.
9. **Trace write.** One `ai_traces` row: purpose, data class, model route, evidence IDs, latency,
   `fallback` flag, validation outcome, `trace_id`. The `trace_id` is returned to the caller and
   surfaced in the UI trace drawer (`BUILD_BIBLE.md` §5).

`approval_status` is set by the Gateway, not by the caller. Purposes that produce a consequential
artefact (`meeting_followup`, `consular_triage`) return a pending-approval status; the artefact is
persisted as a draft and cannot be actioned until a human with the right permission approves it (§6).

## Consequences

### Positive

- The security review surface for AI is one file plus its schema directory.
- The response envelope is guaranteed by construction, so the web client renders evidence, approval
  state and a trace link generically for every AI feature.
- Prompt-injection defence, PII handling, model routing, retries, cost accounting and rate limiting
  each have exactly one place to live.
- Turning the Gateway from stubbed to live (Week 2, then Week 3) is a change inside one function;
  callers do not change.

### Negative / costs

- `gateway.py` is a chokepoint and will accumulate responsibility. It must be split into
  `gateway.py` (orchestration) + `purposes/` (per-purpose schema, prompt, routing hint) +
  `fallback.py` before it stops being reviewable.
- Per-purpose schemas are more work than free-form prose, and constrain what a purpose may return.
  We accept the constraint: unconstrained output is precisely the thing we are refusing to ship.
- A developer wanting a quick model experiment must add a purpose. This friction is intentional.
- Stage 3 means the Gateway needs read access to the retrieval layer, creating a dependency edge from
  `app/ai/` into `app/services/`. We accept it; the alternative — callers passing context in —
  destroys the guarantee. To keep the edge acyclic, services must never import `app.ai`; they receive
  Gateway results through the router layer.

### Neutral / follow-on work

- `evals/security/prompt_injection.example.jsonl` exercises stages 4 and 8.
- The UI trace drawer is a Week 3 deliverable and consumes the stage-9 row.
- Cost and token accounting attaches to the same trace row; no new seam needed.
- Embeddings are model-provider access too. Whichever provider supplies the 1536-dimension vectors
  must be called from inside `app/ai/`, under the same rule. See `docs/OPEN_QUESTIONS.md` — the
  provider is not yet chosen.

## Alternatives considered

### A thin client wrapper (a `call_claude()` helper) that services import

Cheapest, and the common pattern. Rejected: a helper is a convenience, not a boundary. Nothing stops
a service passing its own unfiltered context, and the authorisation-before-retrieval property — the
one that actually matters — cannot be guaranteed by a function that receives context as an argument.

### A separate AI microservice over HTTP

Gives a genuine process boundary and independent scaling. Rejected for the demo: it adds a network
hop, a second deployment, service-to-service auth, and a distributed failure mode, all in the demo's
critical path, in exchange for isolation we do not yet need. The module boundary is drawn so that
extracting a service later is a transport change, not a redesign.

### A provider-agnostic orchestration framework as the boundary

Rejected: such frameworks abstract over *prompting*, not over *authorisation*. They would add a large
dependency surface and a second, weaker place where retrieval happens, and would obscure the one
property we need to be able to point at in a security review.

### Do nothing (call the SDK where convenient)

Fastest to Week 2, and unrecoverable afterwards. The demo's central claim is governance. A system that
cannot say where its model calls happen has no governance story to demo.

## Enforcement

| Mechanism | Status |
|---|---|
| Test `tests/test_ai_boundary.py` walks `apps/api/app/**/*.py`, parses each file with `ast`, and fails if any module other than `app/ai/gateway.py` imports `anthropic`. AST rather than grep, so a comment or a docstring does not trip it and an aliased import cannot evade it. | Week 1 |
| Ruff `flake8-tidy-imports` `banned-api` on `anthropic`, with a single per-file exemption for `app/ai/gateway.py`; runs in CI. | Week 1 |
| A test asserts every registered purpose has both a Pydantic output schema and a fallback snapshot. | Week 1 |
| A test asserts a `GatewayResult` cannot be constructed citing an evidence ID absent from its own evidence set. | Week 1 |
| Code review: any diff touching `app/ai/` requires a second reviewer. | Ongoing |

## References

- `BUILD_BIBLE.md` §4 (Gateway contract), §5 (data zones), §6 (non-negotiable controls)
- `CLAUDE.md` §2.1, §2.2, §5
- ADR-0002 (fallback harness), ADR-0004 (audit), ADR-0006 (classification)
