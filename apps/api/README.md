# `apps/api` — NADDP API

FastAPI + Pydantic v2 + SQLAlchemy 2.0 (synchronous) + Alembic, on
PostgreSQL 16 with pgvector. Serves the Nigeria–Australia Digital Diplomacy
Platform Ambassador demo: **synthetic data only**, but production's domain model
and API shape.

---

## Hard rule: the AI Gateway is a security boundary

> **Only `app/ai/gateway.py` may import the `anthropic` SDK. Nothing else. Ever.**

Application code calls `gateway.generate(purpose, data_class, context, user)` and
receives a validated `{ result, evidence, trace_id, approval_status }` envelope —
never raw prose. The gateway owns the purpose allowlist, the classification
check, the authorised-retrieval filter, the model route, structured-output
validation, the citation post-check, the ≤4 s deterministic fallback and the
`ai_traces` write.

CI enforces this. If you find yourself reaching for `import anthropic` anywhere
else, the answer is a new *purpose* in the gateway.

---

## Running it

Everything below runs from `apps/api`. Package manager is **uv**; Python is
pinned to **3.12** by `.python-version`.

```bash
uv sync                     # create .venv and install deps + dev group
uv run uvicorn app.main:app --reload --port 8000
```

| URL | What |
| --- | --- |
| `http://localhost:8000/health` | liveness + a real `SELECT 1` (always 200) |
| `http://localhost:8000/v1/meta` | build + demo metadata for the web shell |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/openapi.json` | schema consumed by `packages/contracts` |

Prefer the repo-root entry points, which wire the database and web app too:
`make dev` on Linux/CI, `.\make.ps1 dev` on Windows.

### Checks

```bash
uv run ruff check .          # lint, zero errors required
uv run ruff format --check .
uv run mypy                  # strict; files come from pyproject
uv run pytest                # unit suite, no database needed
uv run pytest -m integration # requires `docker compose up -d db`
```

### Migrations

```bash
uv run alembic revision --autogenerate -m "add opportunities"
uv run alembic upgrade head
uv run alembic downgrade -1
```

`alembic.ini` has **no** `sqlalchemy.url`. `alembic/env.py` reads
`app.core.config.get_settings().database_url`, so there is one source of truth
and no credentials in git.

### Configuration

`app/core/config.py` reads the process environment, falling back to the
**repo-root** `.env`. Paths (`storage_dir`, seed data, AI snapshots) resolve from
the repository root derived from the module's own location, so nothing depends on
the process working directory. Import `get_settings()` — never `os.environ`.

`DEMO_SESSION_SECRET` must be non-empty, and the placeholder value is refused
outside `APP_ENV=local|test`.

---

## Layering

```
HTTP request
  │
  ├─ app/core/request_context.py   X-Request-ID assigned, bound to structlog
  ├─ app/api/v1/<context>.py       routers: authorise, parse, delegate, serialise
  ├─ app/security/                 deny-by-default RBAC + classification checks
  ├─ app/services/<context>.py     business logic and workflow transitions
  ├─ app/domain/<context>.py       enums, value objects, state machines (pure)
  ├─ app/models/<context>.py       SQLAlchemy 2.0 ORM
  └─ app/audit/                    append-only audit_events on every transition
```

Rules that hold everywhere:

1. **No domain rules in route handlers.** A handler that contains an `if` about
   business state belongs in `app/services`.
2. **Authorisation filters run before the query,** not after — list and search
   endpoints must never fetch rows the caller may not see.
3. **Every consequential state transition writes an `audit_events` row.**
4. **Route handlers are plain `def`,** not `async def`. The SQLAlchemy session is
   synchronous, and FastAPI runs `def` handlers in a threadpool. This is a
   deliberate, locked decision: fewer moving parts, a demo that cannot deadlock.
5. **Bounded contexts are flat modules**, named identically across layers:
   `intelligence`, `opportunities`, `stakeholders`, `meetings`, `consular`,
   `diaspora`, `knowledge`, `governance`. So `app/models/consular.py`,
   `app/services/consular.py`, `app/api/v1/consular.py`.

### Registering a new router

`app/api/v1/__init__.py` owns the `/v1` router:

```python
from app.api.v1 import consular

router.include_router(consular.router)
```

`app/main.py` mounts it at `/v1`; nothing else needs to change.

---

## Identifiers

`app/core/ids.py` provides two *deliberately unrelated* kinds:

- `new_id() -> uuid.UUID` — a ULID rendered into a UUID column. Time-sortable,
  good index locality. Internal primary keys only.
- `new_public_ref(prefix)` — e.g. `NA-CS-7F3K9QX2`. Cryptographically random
  (`secrets`), unambiguous alphabet (no `0/O/1/I/L`).

A public reference is **never** derived from a primary key: a ULID leaks its
creation timestamp, which would let anyone holding a case number infer arrival
times, case volume and neighbouring records.

---

## Errors

`app/core/errors.py` renders every failure as RFC 9457 `application/problem+json`
carrying the `request_id`. Raise the typed errors — `NotFoundError`,
`PermissionDeniedError`, `ClassificationDeniedError`, `InvalidTransitionError`,
`GatewayError` — rather than `HTTPException`. Unexpected exceptions are logged
with a traceback server-side and returned as an opaque 500; internals never reach
a client.
