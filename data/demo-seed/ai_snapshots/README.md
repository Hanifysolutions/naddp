# AI snapshots — the deterministic fallback set (ADR-0002)

These fourteen files are why the demo cannot dead-end. When the AI Gateway cannot serve a
live answer — the live path is disabled, there is no credential, the provider timed out,
rate-limited, errored, returned something that failed its schema, or cited evidence that
failed the post-check — it serves the snapshot for the call's `purpose + scenario` instead,
records `fallback = true` and a `fallback_reason` in `ai_traces`, and returns the same
`{result, evidence, trace_id, approval_status}` envelope as a live answer.

## Naming

```
{purpose}_{scenario}.json          e.g. morning_brief_ambassador.json
{purpose}___default__.json         the per-purpose last resort (three underscores:
                                   purpose + "_" + "__default__")
```

`purpose` is the lower-cased `AiPurpose` value. `scenario` is a pure function of
`(purpose, role, primary object)` — `app.ai.purposes.scenario_for` — so the same demo beat
resolves to the same file on every run. **The filename is the lookup key**, and a test
asserts that each file's declared `scenario` matches its own filename; a mismatch would
produce a file that is never served.

Lookup order: the exact scenario, then `__default__`, then a `BLOCKED` envelope with
`result: null` and a plain-English explanation. Never a stack trace, never prose, never a
half-populated result.

## Shape

```jsonc
{
  "$schema_version": "1.0",
  "purpose": "MORNING_BRIEF",           // must match the filename's purpose
  "scenario": "ambassador",             // must match the filename's scenario
  "approval_status": "NOT_REQUIRED",    // must equal what the purpose assigns
  "notes": "why this snapshot exists",  // for humans; never rendered
  "evidence_ids": ["..."],              // must equal what `result` actually cites
  "result": { }                         // validates against the purpose's Pydantic schema
}
```

**Evidence is stored as ids, not as rendered citation objects**, and this is a deliberate
deviation from ADR-0002's "serialised `GatewayResult` payload". Titles and URLs are
hydrated from `data/demo-seed/citations.json` at serve time, so a snapshot physically
cannot display a stale title or a dead link, and "every snapshot cites only VERIFIED ids"
is a one-line test rather than a deep comparison.

## Rules these files must satisfy (all enforced by `tests/test_gateway_snapshots.py`)

1. Every purpose has a `__default__` snapshot **and** at least one named scenario.
2. Every `result` validates against its purpose's schema, and every grounded result cites
   at least one evidence id.
3. Every cited id exists in `citations.json` **and** has `verification.status == VERIFIED`.
   A `TODO_VERIFY` id is research output, not demo content, and may 404 on stage.
4. `evidence_ids` equals what the result actually cites.
5. `approval_status` equals what the purpose assigns — the Gateway sets it, never a file.
6. **Never write that an Australian lithium refinery is expanding.** Concentrator and
   refinery are different assets (OPEN_QUESTIONS Q-16). The July 2026 Mt Holland approval
   doubles *concentrate* production; Australia's refining capacity is not growing. The test
   bans the phrase even inside a correct denial — see its docstring for why.
7. The Nigeria–Australia corridor is **AI-proposed, not reported** (Q-17). Any snapshot that
   mentions it must say so, and the scored opportunity carries lower confidence than every
   driver beneath it.
8. No email addresses and nothing shaped like an identity-document number.

## Editing

Snapshots are loaded through an in-process cache that also caches misses, so a file added
or changed while the API is running is not picked up until
`app.ai.fallback.clear_snapshot_cache()` runs. `make demo-reset` must call it.

A malformed file is treated as a **miss**, not an error: it is logged loudly at `error`
level with its path and the harness falls through to `__default__`. That is the correct
split — noisy in the terminal, invisible on stage.

When adding or editing a snapshot, run `uv run pytest tests/test_gateway_snapshots.py` from
`apps/api` before committing. Every rule above is checked there, so a snapshot that would
have failed on stage fails in CI instead — which is the whole point of writing them down.
