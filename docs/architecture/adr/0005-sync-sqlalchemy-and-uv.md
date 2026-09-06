# ADR-0005: Synchronous SQLAlchemy + psycopg 3, and uv over Poetry

- **Status:** Accepted
- **Date:** 2026-09-06
- **Deciders:** Lead Engineer, Solutions Architect
- **Informed:** All contributors
- **Tags:** `platform` `api`

## Context

Two platform choices in `apps/api` are unusual enough to be worth writing down, because both look
"behind the times" to a reader who has not seen the constraints.

The constraints:

- **The demo is one presenter, one laptop, one audience.** Concurrency is effectively 1. Peak load is a
  human clicking. There is no scalability problem to solve in Week 1.
- **The build environment is Windows 11.** The developer machine's system Python is 3.14, which does
  not yet have wheels for parts of this stack; `make` is not installed; Poetry is not installed.
- **The failure budget is zero.** `BUILD_BIBLE.md` §0 — the demo must be incapable of dead-ending. Every
  moving part is a thing that can misbehave in front of an Ambassador.
- **Nothing is throwaway.** The choice has to be defensible as the starting point of a production
  system, not only as a demo shortcut.

## Decision

### 1. SQLAlchemy 2.0 in **synchronous** mode, with **psycopg 3**

- `create_engine(...)` and `Session`, not `create_async_engine` / `AsyncSession`.
- Driver: `psycopg` (psycopg 3), URL scheme `postgresql+psycopg://`. Not `psycopg2`, which is in
  maintenance mode, and not `asyncpg`, which is async-only.
- **FastAPI route handlers are plain `def`, not `async def`.** FastAPI runs a `def` handler in its
  threadpool, so blocking database I/O never touches the event loop.
- Alembic's `env.py` is synchronous, using the same engine factory as the application.

The rule that makes this safe is a single one, and it is the only thing a contributor must remember:
**a handler that touches the database is `def`; if you write `async def`, you may not open a
session inside it.** An `async def` handler performing blocking I/O would stall the event loop for
every concurrent request — the classic, silent, hard-to-diagnose failure of mixed-mode FastAPI apps.
Making *all* DB handlers synchronous removes the category.

### 2. **uv** as the Python package and toolchain manager

- `uv` manages the virtual environment, resolves and installs from `pyproject.toml`, and writes
  `uv.lock`.
- `uv` also installs and pins the **interpreter**: `requires-python = ">=3.12,<3.13"` in
  `apps/api/pyproject.toml`, with `uv python install 3.12` providing it. The system's Python 3.14 is
  not used.
- Production images use `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` with
  `uv sync --frozen --no-dev` (`infra/railway/Dockerfile.api`).
- CI uses the same `uv sync --frozen`, so a lockfile drift is a build failure rather than a surprise.

## Consequences

### Positive

**Sync SQLAlchemy:**

- Stack traces are linear and readable. Debugging is stepping through a function, not reconstructing a
  coroutine graph.
- No `greenlet_spawn` errors, no accidental lazy-load-outside-await, no async context-manager
  mistakes — the three most common sources of confusing SQLAlchemy failures.
- Lazy loading of relationships works normally, which is a substantial ergonomic win when writing a
  domain model quickly.
- The test suite uses ordinary `pytest` with ordinary fixtures; no `pytest-asyncio`, no event-loop
  fixture scoping problems.
- Alembic's sync `env.py` is the documented, well-trodden path.
- Every SQLAlchemy 2.0 example, answer and tutorial applies directly.

**uv:**

- One tool for interpreter, virtualenv, resolution and install. On this machine that is decisive:
  Poetry is not present, and uv can provision the 3.12 interpreter that the stack needs.
- Resolution and install are roughly an order of magnitude faster than Poetry, which matters most in
  CI and in Docker layer rebuilds.
- `uv sync --frozen` gives a genuinely reproducible install from `uv.lock`.
- Standards-based: `pyproject.toml` with PEP 621 metadata, no tool-specific dependency table, so
  migrating away later is mechanical.
- The official `uv` Docker images make a clean multi-stage build straightforward.

### Negative / costs

Being honest about what this costs:

**Sync SQLAlchemy:**

- **Throughput ceiling.** Concurrency is bounded by FastAPI's threadpool (default 40 threads) and by
  the connection-pool size. A production NADDP with thousands of concurrent users and slow queries
  would want async, or more processes, or both. We are explicitly not building that this month.
- **Threads cost memory and context switches.** At demo scale this is invisible; at scale it is the
  reason async exists.
- **Migrating to async later is not free.** It touches the engine and session factory, every handler
  signature that opens a session, and Alembic's `env.py`. Mitigations: sessions are obtained only via
  a single dependency, and business logic lives in `app/services` operating on a passed-in `Session`
  (`CLAUDE.md` §5), so the conversion is confined to the edges rather than smeared through the domain.
- **A mixed-mode hazard remains.** Nothing in the type system prevents someone writing an `async def`
  handler that opens a sync session. This is why the rule is a lint/test check, below.
- Genuinely async work (calling the Anthropic API concurrently for several evidence chunks) has to be
  done with a threadpool rather than `asyncio.gather`. Acceptable at the volumes involved.

**uv:**

- **Younger tool, smaller ecosystem of recipes.** Poetry has years of accumulated answers; uv has
  fewer, and it moves fast. Pinning the uv version in CI and in the Dockerfile base image mitigates
  churn.
- **No plugin ecosystem** comparable to Poetry's, and no built-in publish workflow of the same
  maturity. Irrelevant here — `apps/api` is an application, not a library we publish.
- **Two package managers in one repository** (pnpm for JS, uv for Python) is inherent to a polyglot
  monorepo, but it does mean two lockfiles, two caches and two CI setup steps.
- `uv.lock` is not a standardised format, so it is a lock-in of the same kind `poetry.lock` would be.

### Neutral / follow-on work

- `make.ps1` and `Makefile` both delegate to the same `uv run ...` commands, so the Windows shim and
  the CI path cannot drift.
- If a future workload genuinely needs async, the migration is a scoped project with a clear boundary,
  and this ADR would be superseded rather than quietly ignored.

## Alternatives considered

### Async SQLAlchemy + asyncpg

The default modern choice, and the right one for a high-concurrency service. Rejected here: it adds a
class of failure modes (event-loop blocking, greenlet errors, async fixture scoping) that cost
debugging time we do not have, in exchange for throughput we do not need. The demo's requirement is
robustness, not scale.

### psycopg2

Rejected: maintenance mode, worse Windows wheel story, and no advantage over psycopg 3 for a new
project. psycopg 3 also gives a path to async later without changing driver.

### Poetry

Would be a reasonable default. Rejected on facts: not installed on the build machine, does not manage
interpreters (so the Python 3.14 problem remains unsolved), and materially slower in CI. uv solves
both the interpreter and the speed problem with one tool.

### pip + `requirements.txt` + `venv`

Zero new tooling. Rejected: no lockfile with hashes by default, no dependency-group separation for
dev dependencies, and no interpreter management. Reproducibility is worth one tool.

### Conda / pixi

Rejected: heavier than needed for a pure-Python service with no scientific native-dependency problem.

### Django ORM instead of SQLAlchemy

Rejected: the architecture is FastAPI + Pydantic v2, and SQLAlchemy 2.0's typed `Mapped[...]` models
integrate with that far better than an ORM designed around a different framework's request cycle.

## Enforcement

| Mechanism | Status |
|---|---|
| Test: no module under `apps/api/app/**` imports `create_async_engine`, `AsyncSession`, or `asyncpg`. | Week 1 |
| Test: every route handler declared in the router table that depends on the DB-session dependency is a plain function, not a coroutine function (`inspect.iscoroutinefunction` must be `False`). This is the check that catches the mixed-mode hazard. | Week 1 |
| `pyproject.toml` pins `requires-python = ">=3.12,<3.13"`; `uv sync --frozen` in CI fails on lockfile drift. | Week 1 |
| `Makefile` and `make.ps1` expose identical targets delegating to identical `uv run` commands; a CI check compares the target lists. | Week 1 |
| The Dockerfile builds from `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` and is built in CI, so the container path is exercised, not assumed. | Week 1 |

## References

- `BUILD_BIBLE.md` §0 (prime directive), §3 (config)
- `CLAUDE.md` §5 (domain logic in services, not handlers)
- `infra/railway/Dockerfile.api`
