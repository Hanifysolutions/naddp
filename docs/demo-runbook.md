# Demo runbook — the order things must happen in

Short on purpose. This is what you run before an Ambassador is in the room, and why.

```bash
docker compose up -d db        # Postgres 16 + pgvector on host port 5433
.\make.ps1 demo-reset          # drop, migrate, seed  -> clean synthetic dataset
.\make.ps1 demo-prewarm        # generate and cache the hero briefs   <- NEW, W2.3
.\make.ps1 dev                 # api :8000 + web :3000
```

On Linux/macOS/CI substitute `make <target>`; the targets are identical by design.

**`demo-prewarm` must run AFTER `demo-reset`.** Reset drops the schema, so anything warmed
before it is gone.

---

## Why prewarm exists

The AI Gateway gives heavy generative purposes a **25-second budget** (architect ruling
after the W2.2 review). That is the right budget for a real generation and the wrong thing
to do on stage: nobody watches a screen for 25 seconds.

So the hero artefacts are generated *before* the demo, while a slow network costs nothing,
and the on-stage call is a database read.

| | Budget | Streaming | Purposes |
|---|---:|---|---|
| **Heavy** (generates prose) | 25s | yes | `morning_brief`, `meeting_prep`, `meeting_followup`, `knowledge_answer` |
| **Light** (returns a score) | 4s | no | `opportunity_score`, `diaspora_match`, `consular_triage` |

The budget used is recorded on every `ai_traces` row, because "it fell back" means something
different at 4s than at 25s and a reader cannot tell which without knowing what the call was
allowed.

## What prewarm does and does not guarantee

It does **not** make the model fast. It makes the demo fast by having already asked.

- After a prewarm, each hero brief exists as a persisted `DRAFT` with its items and
  resolving citations. Opening it on stage is a read.
- If prewarm never ran, **the demo still works**: the Gateway falls back to its
  deterministic snapshot inside the budget. Prewarm improves the demo; it is not
  load-bearing for it.
- Prewarm exits non-zero if any warmed brief has an item with no evidence. A brief that
  cannot cite itself is not a brief.

## Reading the output

```
  AMBASSADOR
      cached: 5 items in 1.4s
      route : PUBLIC · external · claude-sonnet-5 · strong
      served: fallback snapshot
```

- **`route`** is the BUILD_BIBLE §4a badge, rendered exactly as the trace drawer shows it.
- **`served: fallback snapshot`** means no live model was called — either `AI_GATEWAY_LIVE`
  is false or no API key is set. This is the normal state for a machine with no key, and it
  is not a failure.
- **`served: live model`** means a real call succeeded inside the budget.

## Before a rehearsal

1. `make demo-reset && make demo-prewarm`, and read the output — every role should report
   `all cited: True`.
2. Open the 23 `TODO_VERIFY` citations in a browser (see `docs/W1_STATUS.md` §4.1). They are
   unreachable from the build network, not dead, but nobody has confirmed that by eye.
3. If the trace drawer surfaces "audit chain intact", reset first — the chain can fork under
   concurrent writes (`docs/W1_STATUS.md` §6.1).

## Going live

The Gateway is live for **PUBLIC** and **MISSION_INTERNAL** only. To enable it:

```
AI_GATEWAY_LIVE=true
ANTHROPIC_API_KEY=<key>
```

`CONFIDENTIAL` stays deterministic (its restricted lane does not exist in the demo),
`CONSULAR-SENSITIVE` never leaves the mission, and `consular_triage` declines its external
lane in every band. Those three are deliberate — see `docs/OPEN_QUESTIONS.md`.
